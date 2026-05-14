"""
狼人杀场景：标准 8 人局。

角色配置：2 狼人 + 1 预言家 + 1 女巫 + 1 猎人 + 3 村民 + 1 主持人（上帝）。
多轮夜晚/白天循环，支持信息隔离、私密行动和投票放逐机制。
"""
from __future__ import annotations

import logging
import random
import re
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..event_bus import GroupEventBusPool
    from ..models import GroupSession

from ..pipelines import announce, sequential, fanout, vote
from ..prompts import build_role_context
from .base import Scenario, register

log = logging.getLogger(__name__)

MAX_ROUNDS = 3  # Max night/day cycles to prevent infinite games

ROLE_NAMES = {
    "wolf": "狼人",
    "seer": "预言家",
    "witch": "女巫",
    "hunter": "猎人",
    "villager": "村民",
}


@register("werewolf", "狼人杀", "werewolves")
class WerewolfScenario(Scenario):
    """狼人杀游戏场景。

    流程：角色分配 → 循环（夜晚阶段 → 白天阶段）→ 胜负判定。
    夜晚：狼人杀人 → 预言家查验 → 女巫救/毒。
    白天：宣布死讯 → 全员讨论 → 投票放逐。
    信息隔离：狼人讨论仅狼人可见，特殊角色行动仅自己可见。
    """

    def initialize(self, activity_rules: str) -> dict:
        """随机分配角色给玩家（主持人除外）。"""
        session = self.session
        host = session.host
        players = [p for p in session.participants if p != host]

        # Ensure at least 5 players for a meaningful game
        if len(players) < 5:
            log.warning("Werewolf: only %d players, need at least 5", len(players))
            # Pad roles for small games
            roles_pool = ["wolf", "wolf", "seer", "villager", "villager"]
        elif len(players) <= 6:
            roles_pool = ["wolf", "wolf", "seer", "witch", "villager", "villager"]
        else:
            # Standard 8-player setup
            roles_pool = ["wolf", "wolf", "seer", "witch", "hunter"]
            roles_pool += ["villager"] * (len(players) - len(roles_pool))

        random.shuffle(roles_pool)
        role_map = {emp: role for emp, role in zip(players, roles_pool)}

        wolves = [e for e, r in role_map.items() if r == "wolf"]
        seer = next((e for e, r in role_map.items() if r == "seer"), "")
        witch = next((e for e, r in role_map.items() if r == "witch"), "")
        hunter = next((e for e, r in role_map.items() if r == "hunter"), "")
        villagers = [e for e, r in role_map.items() if r == "villager"]

        log.info("Werewolf: roles=%s wolves=%s seer=%s witch=%s hunter=%s",
                 role_map, wolves, seer, witch, hunter)

        return {
            "roles": role_map,
            "wolves": wolves,
            "seer": seer,
            "witch": witch,
            "hunter": hunter,
            "villagers": villagers,
            "alive": list(players),
            "witch_heal": True,
            "witch_poison": True,
            "round": 0,
            "winner": "",
        }

    async def run(self, bus_pool: "GroupEventBusPool") -> None:
        """执行完整的狼人杀游戏流程。"""
        session = self.session
        state = session.game_state
        host = session.host

        # 开场：主持人宣布游戏开始，私密通知每人身份
        await announce(session, host, bus_pool, context=(
            "你是狼人杀游戏的上帝（主持人）。游戏即将开始！\n"
            "宣布：「各位玩家，狼人杀游戏开始！请大家确认自己的身份牌，"
            "天黑请闭眼。」简短开场，30字以内。"
        ))

        # Privately tell each player their role
        for emp in state["alive"]:
            role = state["roles"][emp]
            role_name = ROLE_NAMES[role]
            extra = ""
            if role == "wolf":
                partner = [w for w in state["wolves"] if w != emp]
                extra = f"你的狼人同伴是：{', '.join(partner)}。" if partner else ""
            await announce(
                session, host, bus_pool,
                context=(
                    f"私密通知{emp}的身份。直接说："
                    f"「你的身份是【{role_name}】。{extra}"
                    f"请记住身份，不要透露给其他人。」20字以内。"
                ),
                visible_to=[emp, host],
            )

        # Main game loop
        while state["round"] < MAX_ROUNDS:
            state["round"] += 1
            log.info("Werewolf: === Round %d ===", state["round"])

            # Night phase
            killed_tonight = await self._night_phase(bus_pool)

            # Check win after night
            winner = self._check_win()
            if winner:
                state["winner"] = winner
                break

            # Day phase
            await self._day_phase(bus_pool, killed_tonight)

            # Check win after day
            winner = self._check_win()
            if winner:
                state["winner"] = winner
                break

        # Game over announcement
        winner = state.get("winner", "")
        roles_reveal = "、".join(
            f"{e}={ROLE_NAMES[r]}" for e, r in state["roles"].items()
        )
        if winner == "wolves":
            result = "狼人阵营获胜！"
        elif winner == "villagers":
            result = "好人阵营获胜！"
        else:
            result = "游戏超时结束，平局！"

        await announce(session, host, bus_pool, context=(
            f"游戏结束！{result}\n"
            f"公布所有身份：{roles_reveal}\n"
            "做一个简短有趣的总结点评。50字以内。"
        ))
        log.info("Werewolf: game over, winner=%s", winner)

    # ── Night Phase ───────────────────────────────────────────────────────────

    async def _night_phase(self, bus_pool: "GroupEventBusPool") -> list[str]:
        """执行夜晚阶段：狼人杀人 → 预言家查验 → 女巫行动。返回今晚死亡的玩家列表。"""
        session = self.session
        state = session.game_state
        host = session.host
        wolves = [w for w in state["wolves"] if w in state["alive"]]
        dead_tonight: list[str] = []

        # Host announces night
        await announce(session, host, bus_pool, context=(
            f"第{state['round']}个夜晚降临。「天黑请闭眼。」5字以内。"
        ))

        # 1. Wolves choose a target (private to wolves)
        if wolves:
            alive_non_wolves = [p for p in state["alive"] if p not in wolves]
            candidates = ", ".join(alive_non_wolves)

            # Wolves discuss briefly (visible only among wolves)
            await sequential(
                session, wolves, bus_pool,
                role_context_fn=lambda emp, sess, i: (
                    f"你是狼人。现在是夜晚，只有狼人同伴能看到这段对话。\n"
                    f"存活的非狼人玩家：{candidates}\n"
                    f"讨论要杀谁。在回复最后写【杀:目标key】。30字以内。"
                ),
                visible_to=wolves + [host],
            )

            # Extract kill target from last wolf's message
            kill_target = self._extract_target(wolves, "杀")
            if kill_target and kill_target in alive_non_wolves:
                dead_tonight.append(kill_target)
                log.info("Werewolf night: wolves kill %s", kill_target)

        # 2. Seer checks one person (private)
        seer = state["seer"]
        if seer and seer in state["alive"]:
            alive_others = [p for p in state["alive"] if p != seer]
            candidates = ", ".join(alive_others)
            resp = await announce(
                session, seer, bus_pool,
                context=(
                    f"你是预言家。选择一人查验身份。存活玩家：{candidates}\n"
                    f"在回复最后写【查验:目标key】。10字以内。"
                ),
                visible_to=[seer, host],
                viewer=seer,
            )
            # Tell seer the result
            check_target = self._extract_action(resp or "", "查验")
            if check_target and check_target in state["roles"]:
                is_wolf = state["roles"][check_target] == "wolf"
                result = "狼人" if is_wolf else "好人"
                await announce(
                    session, host, bus_pool,
                    context=f"告诉预言家查验结果：「{check_target} 是{result}。」10字以内。",
                    visible_to=[seer, host],
                )

        # 3. Witch action (private)
        witch = state["witch"]
        if witch and witch in state["alive"]:
            heal_info = ""
            if dead_tonight and state["witch_heal"]:
                heal_info = f"今晚被杀的是 {dead_tonight[0]}。你有解药可以救人。"
            poison_info = ""
            if state["witch_poison"]:
                poison_info = "你有毒药可以毒一人。"

            if heal_info or poison_info:
                resp = await announce(
                    session, witch, bus_pool,
                    context=(
                        f"你是女巫。{heal_info} {poison_info}\n"
                        f"选择行动：【救:{dead_tonight[0] if dead_tonight else '无'}】或"
                        f"【毒:目标key】或【跳过】。20字以内。"
                    ),
                    visible_to=[witch, host],
                    viewer=witch,
                )
                if resp:
                    # Process witch actions
                    if "救" in resp and dead_tonight and state["witch_heal"]:
                        save_target = self._extract_action(resp, "救")
                        if save_target and save_target in dead_tonight:
                            dead_tonight.remove(save_target)
                            state["witch_heal"] = False
                            log.info("Werewolf night: witch saves %s", save_target)
                    if "毒" in resp and state["witch_poison"]:
                        poison_target = self._extract_action(resp, "毒")
                        if poison_target and poison_target in state["alive"] and poison_target != witch:
                            dead_tonight.append(poison_target)
                            state["witch_poison"] = False
                            log.info("Werewolf night: witch poisons %s", poison_target)

        # Apply deaths
        for dead in dead_tonight:
            if dead in state["alive"]:
                state["alive"].remove(dead)

        # Hunter's last shot if killed at night
        hunter = state["hunter"]
        if hunter and hunter in dead_tonight and hunter not in state["alive"]:
            shot = await self._hunter_shot(bus_pool, hunter)
            if shot:
                dead_tonight.append(shot)

        return dead_tonight

    # ── Day Phase ─────────────────────────────────────────────────────────────

    async def _day_phase(self, bus_pool: "GroupEventBusPool", killed_tonight: list[str]) -> None:
        """执行白天阶段：宣布死讯 → 全员讨论 → 投票放逐。"""
        session = self.session
        state = session.game_state
        host = session.host
        alive = state["alive"]

        # Announce deaths
        if killed_tonight:
            dead_names = "、".join(killed_tonight)
            await announce(session, host, bus_pool, context=(
                f"天亮了。昨晚 {dead_names} 死了。"
                f"请存活玩家发表意见，讨论谁是狼人。10字以内宣布。"
            ))
        else:
            await announce(session, host, bus_pool, context=(
                "天亮了。昨晚是平安夜，无人死亡。请开始讨论。10字以内。"
            ))

        # Discussion: all alive players speak sequentially (public)
        await sequential(
            session, alive, bus_pool,
            role_context_fn=lambda emp, sess, i: (
                f"你是{ROLE_NAMES[state['roles'][emp]]}（但不能直接说出身份）。\n"
                f"白天讨论环节，分析局势，推测谁是狼人。\n"
                f"结合你了解的信息发言。40字以内。"
            ),
        )

        # Vote to exile
        winner, votes = await vote(
            session, alive, alive, bus_pool,
            context_template=(
                "投票环节。从存活玩家中选择一人放逐：{candidates}\n"
                "根据讨论内容做出判断。在回复最后写【投票:目标key】。20字以内。"
            ),
        )

        if winner:
            state["alive"].remove(winner)
            role_name = ROLE_NAMES[state["roles"][winner]]
            await announce(session, host, bus_pool, context=(
                f"投票结果：{winner} 被放逐。"
                f"翻牌：{winner} 的身份是{role_name}。简短宣布，15字以内。"
            ))
            log.info("Werewolf day: %s (%s) exiled", winner, role_name)

            # Hunter last shot if voted out
            hunter = state["hunter"]
            if winner == hunter:
                await self._hunter_shot(bus_pool, hunter)
        else:
            await announce(session, host, bus_pool, context=(
                "投票平票，无人被放逐。简短宣布，10字以内。"
            ))

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _hunter_shot(self, bus_pool: "GroupEventBusPool", hunter: str) -> str | None:
        """猎人的最后一枪：选择一名存活玩家带走。"""
        session = self.session
        state = session.game_state
        host = session.host
        alive = state["alive"]

        if not alive:
            return None

        candidates = ", ".join(alive)
        resp = await announce(
            session, hunter, bus_pool,
            context=(
                f"你是猎人，你已经出局了！你可以开枪带走一人。\n"
                f"存活玩家：{candidates}\n"
                f"在回复最后写【开枪:目标key】。15字以内。"
            ),
            visible_to=None,  # public
        )
        if resp:
            target = self._extract_action(resp, "开枪")
            if target and target in alive:
                state["alive"].remove(target)
                await announce(session, host, bus_pool, context=(
                    f"猎人 {hunter} 开枪带走了 {target}！简短宣布，10字以内。"
                ))
                log.info("Werewolf: hunter %s shoots %s", hunter, target)
                return target
        return None

    def _check_win(self) -> str:
        """检查胜负条件。返回 'wolves'（狼人胜）、'villagers'（好人胜）或 ''（未结束）。"""
        state = self.session.game_state
        wolves_alive = [w for w in state["wolves"] if w in state["alive"]]
        good_alive = [p for p in state["alive"] if p not in state["wolves"]]

        if not wolves_alive:
            return "villagers"
        if len(wolves_alive) >= len(good_alive):
            return "wolves"
        return ""

    def _extract_target(self, speakers: list[str], action: str) -> str | None:
        """从指定发言者的最新消息中提取动作目标（如【杀:xxx】）。"""
        for msg in reversed(self.session.history):
            if msg.sender in speakers:
                return self._extract_action(msg.content, action)
        return None

    def _extract_action(self, text: str, action: str) -> str | None:
        """从文本中提取【动作:目标】格式的内容，如【杀:algorithm】→ 'algorithm'。"""
        m = re.search(rf"【{action}[:：](\w+)】", text)
        return m.group(1) if m else None

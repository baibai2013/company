"""
狼人杀场景：标准 8 人局，支持 AI 和真实用户混合参与。

角色配置：2 狼人 + 1 预言家 + 1 女巫 + 1 猎人 + 3 村民 + 1 主持人（上帝）。
多轮夜晚/白天循环，支持信息隔离、私密行动和投票放逐机制。
通过 Participant 抽象统一处理 AI 员工和真实用户。
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

from ..participant import Participant
from ..pipelines import announce, speak_sequential
from .base import Scenario, register

log = logging.getLogger(__name__)

MAX_ROUNDS = 3

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

    通过 Participant 抽象，AI 员工和真实用户以统一接口参与所有阶段。
    """

    def initialize(self, activity_rules: str) -> dict:
        """随机分配角色给玩家（主持人除外）。"""
        session = self.session
        host = session.host
        players = [p for p in session.participants if p != host]

        if len(players) < 5:
            log.warning("Werewolf: only %d players, need at least 5", len(players))
            roles_pool = ["wolf", "wolf", "seer", "villager", "villager"]
        elif len(players) <= 6:
            roles_pool = ["wolf", "wolf", "seer", "witch", "villager", "villager"]
        else:
            roles_pool = ["wolf", "wolf", "seer", "witch", "hunter"]
            roles_pool += ["villager"] * (len(players) - len(roles_pool))

        random.shuffle(roles_pool)
        role_map = {emp: role for emp, role in zip(players, roles_pool)}

        wolves = [e for e, r in role_map.items() if r == "wolf"]
        seer = next((e for e, r in role_map.items() if r == "seer"), "")
        witch = next((e for e, r in role_map.items() if r == "witch"), "")
        hunter = next((e for e, r in role_map.items() if r == "hunter"), "")

        log.info("Werewolf: roles=%s wolves=%s seer=%s witch=%s hunter=%s",
                 role_map, wolves, seer, witch, hunter)

        return {
            "roles": role_map,
            "wolves": wolves,
            "seer": seer,
            "witch": witch,
            "hunter": hunter,
            "alive": list(players),
            "witch_heal": True,
            "witch_poison": True,
            "round": 0,
            "winner": "",
        }

    def _p(self, key: str) -> Participant:
        """快捷获取 Participant 实例。"""
        return Participant.from_key(key)

    def _alive_participants(self) -> list[Participant]:
        """获取存活玩家的 Participant 列表。"""
        return [self._p(k) for k in self.session.game_state["alive"]]

    async def run(self, bus_pool: "GroupEventBusPool") -> None:
        """执行完整的狼人杀游戏流程。"""
        session = self.session
        state = session.game_state
        host = session.host

        # 开场
        await announce(session, host, bus_pool, context=(
            "你是狼人杀游戏的上帝（主持人）。游戏即将开始！\n"
            "宣布：「各位玩家，狼人杀游戏开始！请大家确认自己的身份牌，"
            "天黑请闭眼。」简短开场，30字以内。"
        ))

        # 私密通知每人身份
        for key in state["alive"]:
            p = self._p(key)
            role = state["roles"][key]
            role_name = ROLE_NAMES[role]
            extra = ""
            if role == "wolf":
                partner = [w for w in state["wolves"] if w != key]
                extra = f"你的狼人同伴是：{', '.join(partner)}。" if partner else ""
            await announce(
                session, host, bus_pool,
                context=(
                    f"私密通知{p.display_name}的身份。直接说："
                    f"「{p.display_name}，你的身份是【{role_name}】。{extra}"
                    f"请记住身份，不要透露给其他人。」20字以内。"
                ),
                visible_to=[key, host],
            )

        # 主循环
        while state["round"] < MAX_ROUNDS:
            state["round"] += 1
            log.info("Werewolf: === Round %d ===", state["round"])

            killed_tonight = await self._night_phase(bus_pool)

            winner = self._check_win()
            if winner:
                state["winner"] = winner
                break

            await self._day_phase(bus_pool, killed_tonight)

            winner = self._check_win()
            if winner:
                state["winner"] = winner
                break

        # 游戏结束
        winner = state.get("winner", "")
        roles_reveal = "、".join(
            f"{self._p(e).display_name}={ROLE_NAMES[r]}"
            for e, r in state["roles"].items()
        )
        result = {"wolves": "狼人阵营获胜！", "villagers": "好人阵营获胜！"}.get(
            winner, "游戏超时结束，平局！"
        )
        await announce(session, host, bus_pool, context=(
            f"游戏结束！{result}\n公布所有身份：{roles_reveal}\n"
            "做一个简短有趣的总结点评。50字以内。"
        ))
        log.info("Werewolf: game over, winner=%s", winner)

    # ── Night Phase ───────────────────────────────────────────────────────────

    async def _night_phase(self, bus_pool: "GroupEventBusPool") -> list[str]:
        """夜晚阶段：狼人杀人 → 预言家查验 → 女巫行动。"""
        session = self.session
        state = session.game_state
        host = session.host
        wolves = [w for w in state["wolves"] if w in state["alive"]]
        dead_tonight: list[str] = []

        await announce(session, host, bus_pool, context=(
            f"第{state['round']}个夜晚降临。「天黑请闭眼。」5字以内。"
        ))

        # 1. 狼人选目标
        if wolves:
            alive_non_wolves = [p for p in state["alive"] if p not in wolves]
            candidates = ", ".join(self._p(c).display_name for c in alive_non_wolves)
            wolf_participants = [self._p(w) for w in wolves]

            # 狼人讨论+投票（统一接口）
            await speak_sequential(
                wolf_participants, session, bus_pool,
                context_fn=lambda p: (
                    f"你是狼人。现在是夜晚，只有狼人同伴能看到这段对话。\n"
                    f"存活的非狼人玩家：{candidates}\n"
                    f"讨论要杀谁。在回复最后写【杀:目标key】。30字以内。"
                ),
                visible_to=wolves + [host],
                timeout=60,
            )

            # 提取杀人目标
            kill_target = self._extract_target(wolves, "杀")
            if kill_target and kill_target in alive_non_wolves:
                dead_tonight.append(kill_target)
                log.info("Werewolf night: wolves kill %s", kill_target)

        # 2. 预言家查验
        seer = state["seer"]
        if seer and seer in state["alive"]:
            seer_p = self._p(seer)
            alive_others = [p for p in state["alive"] if p != seer]
            candidates = ", ".join(self._p(c).display_name for c in alive_others)

            resp = await seer_p.speak(
                session, bus_pool,
                context=(
                    f"你是预言家。选择一人查验身份。存活玩家：{candidates}\n"
                    f"在回复最后写【查验:目标key】。10字以内。"
                ),
                visible_to=[seer, host],
                timeout=60,
            )
            check_target = self._extract_action(resp or "", "查验")
            if check_target and check_target in state["roles"]:
                is_wolf = state["roles"][check_target] == "wolf"
                result = "狼人" if is_wolf else "好人"
                await announce(
                    session, host, bus_pool,
                    context=f"告诉预言家查验结果：「{check_target} 是{result}。」10字以内。",
                    visible_to=[seer, host],
                )

        # 3. 女巫行动
        witch = state["witch"]
        if witch and witch in state["alive"]:
            witch_p = self._p(witch)
            heal_info = ""
            if dead_tonight and state["witch_heal"]:
                heal_info = f"今晚被杀的是 {self._p(dead_tonight[0]).display_name}。你有解药可以救人。"
            poison_info = ""
            if state["witch_poison"]:
                poison_info = "你有毒药可以毒一人。"

            if heal_info or poison_info:
                resp = await witch_p.speak(
                    session, bus_pool,
                    context=(
                        f"你是女巫。{heal_info} {poison_info}\n"
                        f"选择行动：【救:{dead_tonight[0] if dead_tonight else '无'}】或"
                        f"【毒:目标key】或【跳过】。20字以内。"
                    ),
                    visible_to=[witch, host],
                    timeout=60,
                )
                if resp:
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

        # 执行死亡
        for dead in dead_tonight:
            if dead in state["alive"]:
                state["alive"].remove(dead)

        # 猎人被杀时开枪
        hunter = state["hunter"]
        if hunter and hunter in dead_tonight and hunter not in state["alive"]:
            shot = await self._hunter_shot(bus_pool, hunter)
            if shot:
                dead_tonight.append(shot)

        return dead_tonight

    # ── Day Phase ─────────────────────────────────────────────────────────────

    async def _day_phase(self, bus_pool: "GroupEventBusPool", killed_tonight: list[str]) -> None:
        """白天阶段：宣布死讯 → 全员讨论 → 投票放逐。"""
        session = self.session
        state = session.game_state
        host = session.host
        alive = state["alive"]

        # 宣布死讯
        if killed_tonight:
            dead_names = "、".join(self._p(d).display_name for d in killed_tonight)
            await announce(session, host, bus_pool, context=(
                f"天亮了。昨晚 {dead_names} 死了。"
                f"请存活玩家发表意见，讨论谁是狼人。10字以内宣布。"
            ))
        else:
            await announce(session, host, bus_pool, context=(
                "天亮了。昨晚是平安夜，无人死亡。请开始讨论。10字以内。"
            ))

        # 讨论：所有存活者统一接口发言
        alive_participants = self._alive_participants()
        await speak_sequential(
            alive_participants, session, bus_pool,
            context_fn=lambda p: (
                f"你是{ROLE_NAMES[state['roles'][p.key]]}（但不能直接说出身份）。\n"
                f"白天讨论环节，分析局势，推测谁是狼人。40字以内。"
            ),
            timeout=90,
        )

        # 投票：所有存活者统一投票
        candidates_text = ", ".join(p.display_name for p in alive_participants)
        votes: dict[str, str] = {}

        await speak_sequential(
            alive_participants, session, bus_pool,
            context_fn=lambda p: (
                f"投票环节。从存活玩家中选择一人放逐：{candidates_text}\n"
                f"在回复最后写【投票:目标key】。20字以内。"
            ),
            timeout=60,
        )

        # 从 history 中提取所有投票
        for key in alive:
            vote_target = self._extract_vote_from_history(key, alive)
            if vote_target:
                votes[key] = vote_target

        # 计票
        if votes:
            counter = Counter(votes.values())
            top = counter.most_common(2)
            winner = top[0][0] if (len(top) == 1 or top[0][1] > top[1][1]) else None
        else:
            winner = None

        if winner:
            state["alive"].remove(winner)
            role_name = ROLE_NAMES[state["roles"][winner]]
            winner_p = self._p(winner)
            await announce(session, host, bus_pool, context=(
                f"投票结果：{winner_p.display_name} 被放逐。"
                f"翻牌：{winner_p.display_name} 的身份是{role_name}。简短宣布，15字以内。"
            ))
            log.info("Werewolf day: %s (%s) exiled", winner, role_name)

            # 猎人被投出时开枪
            if winner == state["hunter"]:
                await self._hunter_shot(bus_pool, winner)
        else:
            await announce(session, host, bus_pool, context=(
                "投票平票，无人被放逐。简短宣布，10字以内。"
            ))

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _hunter_shot(self, bus_pool: "GroupEventBusPool", hunter: str) -> str | None:
        """猎人的最后一枪。"""
        state = self.session.game_state
        host = self.session.host
        alive = state["alive"]
        if not alive:
            return None

        hunter_p = self._p(hunter)
        candidates = ", ".join(self._p(c).display_name for c in alive)

        resp = await hunter_p.speak(
            self.session, bus_pool,
            context=(
                f"你是猎人，你已经出局了！你可以开枪带走一人。\n"
                f"存活玩家：{candidates}\n"
                f"在回复最后写【开枪:目标key】。15字以内。"
            ),
            timeout=60,
        )
        if resp:
            target = self._extract_action(resp, "开枪")
            if target and target in alive:
                state["alive"].remove(target)
                await announce(self.session, host, bus_pool, context=(
                    f"{hunter_p.display_name} 开枪带走了 {self._p(target).display_name}！10字以内。"
                ))
                log.info("Werewolf: hunter %s shoots %s", hunter, target)
                return target
        return None

    def _check_win(self) -> str:
        """检查胜负。返回 'wolves'/'villagers'/''。"""
        state = self.session.game_state
        wolves_alive = [w for w in state["wolves"] if w in state["alive"]]
        good_alive = [p for p in state["alive"] if p not in state["wolves"]]
        if not wolves_alive:
            return "villagers"
        if len(wolves_alive) >= len(good_alive):
            return "wolves"
        return ""

    def _extract_target(self, speakers: list[str], action: str) -> str | None:
        """从指定发言者的最新消息中提取目标。"""
        for msg in reversed(self.session.history):
            if msg.sender in speakers:
                return self._extract_action(msg.content, action)
        return None

    def _extract_action(self, text: str, action: str) -> str | None:
        """从文本中提取【动作:目标】。"""
        m = re.search(rf"【{action}[:：](\w+)】", text)
        return m.group(1) if m else None

    def _extract_vote_from_history(self, voter_key: str, candidates: list[str]) -> str | None:
        """从 history 中找到 voter 最近的投票。"""
        for msg in reversed(self.session.history):
            if msg.sender == voter_key:
                m = re.search(r"【投票[:：](\w+)】", msg.content)
                if m and m.group(1) in candidates:
                    return m.group(1)
                break
        return None

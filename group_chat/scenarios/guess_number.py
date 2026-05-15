"""
猜数字场景：主持人心中选定一个秘密数字，玩家轮流猜测，
主持人每轮给出偏大/偏小提示，猜中则游戏结束。
支持 AI 员工和真实用户混合参与。
"""
from __future__ import annotations

import logging
import random
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..event_bus import GroupEventBusPool
    from ..models import GroupSession

from ..participant import Participant
from ..pipelines import announce
from ..prompts import build_role_context
from .base import Scenario, register

log = logging.getLogger(__name__)


@register("guess_number", "guess", "guessing")
class GuessNumberScenario(Scenario):
    """猜数字游戏场景。

    流程：主持人宣布规则 → 玩家逐一猜测（AI 和真实用户统一接口）→ 猜对结束。
    数字比较在 Python 中确定性完成，不依赖 LLM 做算术。
    """

    def initialize(self, activity_rules: str) -> dict:
        """从活动规则中解析数字范围，生成随机秘密数字。"""
        lo, hi = 0, 100
        m = re.search(r"(\d+)\s*[-–~]\s*(\d+)", activity_rules)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
        secret = random.randint(lo, hi)
        log.info("GuessNumber: secret=%d range=%d-%d", secret, lo, hi)
        return {"secret_number": secret, "lo": lo, "hi": hi}

    async def run(self, bus_pool: "GroupEventBusPool") -> None:
        """执行猜数字游戏：主持人开场 → 循环让玩家猜测+反馈 → 猜对才结束。"""
        session = self.session
        secret = session.game_state["secret_number"]
        lo = session.game_state["lo"]
        hi = session.game_state["hi"]

        host = session.host
        # 构建 Participant 列表（AI 和用户统一接口）
        players = [
            Participant.from_key(p) for p in session.participants if p != host
        ]

        # Phase 1: Host announces rules (with secret injected privately)
        host_opening_ctx = (
            build_role_context(host, session)
            + f"\n\n【主持人私有信息 — 绝对不能在发言中说出这个数字】\n"
            f"你心里选定的秘密数字是 {secret}（范围 {lo}-{hi}）。\n"
            f"请宣布游戏规则和猜测范围，邀请大家猜测，但绝对不要透露 {secret} 这个具体数值。\n"
            f"提示：本局有 {len(players)} 位玩家参与。"
        )
        await announce(session, host, bus_pool, context=host_opening_ctx)

        # Phase 2: 循环让玩家猜，直到有人猜对为止
        while True:
            for p in players:
                # 统一接口：AI 通过 LLM 生成回复，用户等待飞书消息
                guess_ctx = (
                    f"猜数字游戏进行中，范围 {lo}-{hi}。"
                    f"请猜一个数字，结合你的专业背景说出选这个数的理由。30字以内。"
                )
                content = await p.speak(
                    session, bus_pool, context=guess_ctx, timeout=120,
                )
                if not content:
                    log.info("GuessNumber: %s timeout, skipping", p.key)
                    continue

                guess = self._extract_guess(lo, hi)
                if guess is None:
                    # 无法解析数字，LLM 兜底判断
                    fb_ctx = (
                        f"你是游戏主持人，你的秘密数字是 {secret}（范围 {lo}-{hi}，"
                        f"绝对不能说出数字本身）。\n"
                        "看最新一条猜测，给出简短反馈：\n"
                        "- 猜的数 > 秘密数 → 回复「偏大了」\n"
                        "- 猜的数 < 秘密数 → 回复「偏小了」\n"
                        "- 猜对了 → 大力表扬并宣布游戏结束\n"
                        "20字以内，口语活泼。"
                    )
                    await announce(session, host, bus_pool, context=fb_ctx)
                    continue

                if guess == secret:
                    await announce(
                        session, host, bus_pool,
                        context=(
                            f"你是游戏主持人（范围 {lo}-{hi}，绝对不能说出秘密数字本身）。\n"
                            f"{p.display_name}猜对了！大力表扬，宣布游戏结束。20字以内，口语活泼。"
                        ),
                    )
                    log.info("GuessNumber: game_won! %s guessed %d", p.key, guess)
                    return

                direction = "偏大了" if guess > secret else "偏小了"
                await announce(
                    session, host, bus_pool,
                    context=(
                        f"你是游戏主持人（范围 {lo}-{hi}，绝对不能说出秘密数字本身）。\n"
                        f"{p.display_name}的猜测{direction}！你要明确告诉对方「{direction}」，"
                        f"可以加一句鼓励或调侃。20字以内，口语活泼。"
                    ),
                )
                log.info("GuessNumber: %s guessed %d → %s", p.key, guess, direction)

    def _extract_guess(self, lo: int, hi: int) -> int | None:
        """从最新一条历史消息中提取猜测的数字。优先选取范围内的数字。"""
        if not self.session.history:
            return None
        latest = self.session.history[-1].content
        nums = [int(n) for n in re.findall(r"\d+", latest)]
        # Prefer first number within valid range
        for n in nums:
            if lo <= n <= hi:
                return n
        return nums[0] if nums else None

"""
猜数字场景：主持人心中选定一个秘密数字，玩家轮流猜测，
主持人每轮给出偏大/偏小提示，猜中则游戏结束。
"""
from __future__ import annotations

import logging
import random
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..event_bus import GroupEventBusPool
    from ..models import GroupSession

from ..pipelines import announce, sequential
from ..prompts import build_role_context
from .base import Scenario, register

log = logging.getLogger(__name__)


@register("guess_number", "guess", "guessing")
class GuessNumberScenario(Scenario):
    """猜数字游戏场景。

    流程：主持人宣布规则 → 玩家逐一猜测 → 主持人每次给出偏大/偏小反馈 → 猜对结束。
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
        """执行猜数字游戏：主持人开场 → 逐人猜测+反馈 → 猜对结束或全部猜完揭晓。"""
        session = self.session
        secret = session.game_state["secret_number"]
        lo = session.game_state["lo"]
        hi = session.game_state["hi"]

        host = session.host
        players = [p for p in session.participants if p != host]

        # Phase 1: Host announces rules (with secret injected privately)
        host_opening_ctx = (
            build_role_context(host, session)
            + f"\n\n【主持人私有信息 — 绝对不能在发言中说出这个数字】\n"
            f"你心里选定的秘密数字是 {secret}（范围 {lo}-{hi}）。\n"
            f"请宣布游戏规则和猜测范围，邀请大家猜测，但绝对不要透露 {secret} 这个具体数值。"
        )
        await announce(session, host, bus_pool, context=host_opening_ctx)

        # Phase 2: Each player guesses, host gives feedback
        for emp in players:
            # Player guesses
            await sequential(session, [emp], bus_pool)

            # Extract guess and compare (deterministic, not LLM)
            guess = self._extract_guess(lo, hi)
            if guess is None:
                # Can't parse number — ask host to judge via LLM fallback
                fb_ctx = (
                    f"你是游戏主持人，你的秘密数字是 {secret}（范围 {lo}-{hi}，"
                    f"绝对不能说出数字本身）。\n"
                    "看最新一条猜测，给出简短反馈：\n"
                    "- 猜的数 > 秘密数 → 回复「偏大了」\n"
                    "- 猜的数 < 秘密数 → 回复「偏小了」\n"
                    "- 猜对了 → 大力表扬\n"
                    "20字以内，口语活泼。"
                )
                await announce(session, host, bus_pool, context=fb_ctx)
                continue

            if guess == secret:
                # Correct! Host congratulates and game ends
                await announce(
                    session, host, bus_pool,
                    context=(
                        f"你是游戏主持人（范围 {lo}-{hi}，绝对不能说出秘密数字本身）。\n"
                        f"对方猜对了！大力表扬，宣布游戏结束。20字以内，口语活泼。"
                    ),
                )
                log.info("GuessNumber: game_won after %s guessed %d", emp, guess)
                return

            # Wrong guess — host gives hint
            direction = "偏大了" if guess > secret else "偏小了"
            await announce(
                session, host, bus_pool,
                context=(
                    f"你是游戏主持人（范围 {lo}-{hi}，绝对不能说出秘密数字本身）。\n"
                    f"对方的猜测{direction}！你要明确告诉对方「{direction}」，"
                    f"可以加一句鼓励或调侃。20字以内，口语活泼。"
                ),
            )
            log.info("GuessNumber: %s guessed %d → %s", emp, guess, direction)

        # Phase 3: Nobody guessed correctly — host reveals
        await announce(
            session, host, bus_pool,
            context=(
                f"你是游戏主持人。所有人都猜完了，无人猜对。\n"
                f"公布答案：秘密数字是 {secret}。\n"
                f"做一个简短有趣的总结，表扬猜得最接近的人。50字以内。"
            ),
        )
        log.info("GuessNumber: nobody guessed correctly, revealed %d", secret)

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

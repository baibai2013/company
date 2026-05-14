"""
Guess Number scenario: host picks a secret number, players guess,
host gives 偏大/偏小 feedback after each guess, game ends on correct guess.
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
    """0-N 猜数字：主持人心中有秘密数字，逐人猜，主持人给偏大/偏小提示。"""

    def initialize(self, activity_rules: str) -> dict:
        lo, hi = 0, 100
        m = re.search(r"(\d+)\s*[-–~]\s*(\d+)", activity_rules)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
        secret = random.randint(lo, hi)
        log.info("GuessNumber: secret=%d range=%d-%d", secret, lo, hi)
        return {"secret_number": secret, "lo": lo, "hi": hi}

    async def run(self, bus_pool: "GroupEventBusPool") -> None:
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
        """Extract the guessed number from the latest history message."""
        if not self.session.history:
            return None
        latest = self.session.history[-1].content
        nums = [int(n) for n in re.findall(r"\d+", latest)]
        # Prefer first number within valid range
        for n in nums:
            if lo <= n <= hi:
                return n
        return nums[0] if nums else None

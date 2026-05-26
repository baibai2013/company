"""提案 3 · §4.4 — 评测 batch CLI 入口(Wave 3 stub)。

跑法:
    # 默认跑全部 fixture(任意 tier),git_sha 自动取 HEAD
    python -m scripts.evals_run_batch

    # 只跑 tier 1
    python -m scripts.evals_run_batch --tier 1

    # 标记触发源
    python -m scripts.evals_run_batch --triggered-by ci-pr-42

输出:打印 batch 汇总 JSON(batch_id / pass_rate / 各 fixture verdict)。
依赖:dev pg 已建好五张表(Wave 0)且 fixture 已 seed(scripts/evals_seed.py)。

⚠️ Wave 3 是 stub:run_fixture **不**真 spawn agent,只造 fake artifacts → verifier。
真 spawn 走 cc_bridge 留 Wave 4(见 evals_runner.py 模块 docstring)。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import sys

from backend.services import evals_batch


def _git_sha() -> str:
    """取当前仓库 HEAD;失败兜底 'unknown'。"""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run an evals batch (Wave 3 stub).")
    p.add_argument("--tier", type=int, default=None, help="只跑某 tier(1/2/3),默认全部")
    p.add_argument(
        "--triggered-by", type=str, default="manual",
        help="触发源标识(manual / cron / ci-pr-N),默认 manual",
    )
    p.add_argument(
        "--git-sha", type=str, default=None,
        help="覆盖默认 HEAD sha,主要给 CI 用",
    )
    p.add_argument(
        "--fixture", action="append", default=None,
        help="只跑指定 fixture id(可重复传)",
    )
    return p.parse_args()


async def main() -> int:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args()

    git_sha = args.git_sha or _git_sha()
    result = await evals_batch.run_batch(
        git_sha=git_sha,
        tier=args.tier,
        triggered_by=args.triggered_by,
        fixture_ids=args.fixture,
    )

    # 把 results 里每个 run_fixture 的字典展开打印,方便人眼看
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))

    # exit code:任何 fixture fail 也保持 0(不阻塞 manual 跑);
    # CI gate 留给 Wave 4 evals_diff.py 处理。
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

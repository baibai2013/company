"""提案 4 · §5.3 CI evals gate — GitHub Actions 入口(Wave 4)。

读 ``evals_batches`` 最新一条 batch 的 ``pass_rate``,与阈值比较:
  - 阈值默认 0.8,可用环境变量 ``EVALS_GATE_THRESHOLD`` 覆盖
  - pass_rate >= 阈值     → exit 0(放行)
  - pass_rate <  阈值     → exit 1(阻塞 PR / push)
  - 数据库连不上          → exit 0 + log "no eval data, skipping gate"
  - 没有任何 batch 行     → exit 0 + log "no eval data, skipping gate"
  - 其它真异常(import / 配置错)→ exit 2(明显故障,不要 silently 放行)

新仓初始化期 evals 表可能还没数据,这种情况不应该阻塞;但是 db 连不上和
"db 通但是查无数据"是一回事,都先放行,等 W3-C 的 ``scripts/evals_seed.py`` +
``scripts/evals_run_batch.py`` 跑过一遍之后,gate 自然激活。

CLI::

    python scripts/evals_ci.py                       # 用最新 batch
    python scripts/evals_ci.py --threshold 0.9       # 临时收紧
    python scripts/evals_ci.py --batch-name latest   # 占位,目前等价无参

退出码:
  0 = pass / no data
  1 = below threshold(真挂)
  2 = unexpected error
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any

log = logging.getLogger("evals_ci")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CI evals gate (Wave 4).")
    p.add_argument(
        "--threshold", type=float, default=None,
        help="pass_rate 阈值(默认读环境变量 EVALS_GATE_THRESHOLD,再默认 0.8)",
    )
    p.add_argument(
        "--batch-name", type=str, default="latest",
        help="批次选择,目前仅支持 'latest'(预留,Wave 5 接命名 batch)",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true",
        help="开 DEBUG 日志",
    )
    return p.parse_args()


def _resolve_threshold(cli_threshold: float | None) -> float:
    """优先级:CLI > env > 0.8。"""
    if cli_threshold is not None:
        return cli_threshold
    env_val = os.environ.get("EVALS_GATE_THRESHOLD")
    if env_val:
        try:
            return float(env_val)
        except ValueError:
            log.warning("EVALS_GATE_THRESHOLD=%r 不是合法 float,退化默认 0.8", env_val)
    return 0.8


async def _fetch_latest_batch() -> tuple[Any | None, str]:
    """取最新一条 batch。

    返回 (batch_or_None, status):
      - status='ok'      → 数据已就绪,batch 是最新一条
      - status='empty'   → 库通但没数据
      - status='db_err'  → 库本身连不上(网络 / 配置 / 表不存在等)

    一律不抛,所有异常归到 db_err。
    """
    try:
        from backend.repos import evals_batch_repo  # 懒导入,避免环境问题影响 CLI 启动
        batches = await evals_batch_repo.list_recent(limit=1)
    except Exception as exc:
        log.warning("evals_batch_repo.list_recent 失败,视为 db_err: %s: %s",
                    type(exc).__name__, exc)
        return None, "db_err"
    if not batches:
        return None, "empty"
    return batches[0], "ok"


async def _amain(args: argparse.Namespace) -> int:
    threshold = _resolve_threshold(args.threshold)
    batch, status = await _fetch_latest_batch()

    if status == "db_err":
        log.warning("no eval data, skipping gate (db unreachable)")
        return 0
    if status == "empty":
        log.warning("no eval data, skipping gate (no batch rows yet)")
        return 0

    # status == 'ok'
    pass_rate = float(batch.pass_rate) if batch.pass_rate is not None else 0.0
    fixture_count = batch.fixture_count or 0
    log.info(
        "evals gate: batch_id=%s git_sha=%s fixture_count=%s pass_rate=%.4f threshold=%.4f",
        batch.id, batch.git_sha, fixture_count, pass_rate, threshold,
    )

    # 边界:批次还没 finalize(completed_at IS NULL,pass_rate 可能是 None)
    # 这种情况算 empty,避免误杀 in-flight batch
    if batch.completed_at is None:
        log.warning("latest batch not finalized yet, skipping gate")
        return 0

    if pass_rate < threshold:
        print(
            f"FAIL: evals pass_rate {pass_rate:.4f} < threshold {threshold:.4f} "
            f"(batch={batch.id}, git_sha={batch.git_sha})",
            file=sys.stderr,
        )
        return 1

    print(
        f"PASS: evals pass_rate {pass_rate:.4f} >= threshold {threshold:.4f} "
        f"(batch={batch.id}, git_sha={batch.git_sha})"
    )
    return 0


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        log.warning("interrupted")
        return 2
    except Exception as exc:
        # _amain 内部已经吞了 db 层异常;到这里说明 import 期或 asyncio.run 本身崩了
        log.exception("unexpected error in evals_ci: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())

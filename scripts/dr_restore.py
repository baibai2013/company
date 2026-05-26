"""Wave 4 · 提案 4 §5.5 — 灾备恢复 wrapper。

流程:
  1. ``dr_backup_repo.latest()`` 取最新一条 ``status='success'`` 记录
  2. 解析 ``s3_key`` 是 ``s3://bucket/key`` 还是本地路径
  3. boto3 从 S3 / MinIO 下载到 ``/tmp/dr_restore/<key>``(本地路径直接复用)
  4. 调 ``infra/dr/pg_restore.sh <local_path> [--dry-run]``

用法::

    python scripts/dr_restore.py [--dry-run] [--backup-id N]

``--dry-run`` 只跑 pg_restore --dry-run(打命令不真执行)。
``--backup-id`` 指定一条非最新的备份(运维场景:回滚到上周的)。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("dr_restore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

REPO_ROOT = Path(__file__).resolve().parent.parent
RESTORE_SH = REPO_ROOT / "infra" / "dr" / "pg_restore.sh"


def _parse_s3_key(s3_key: str) -> tuple[str | None, str | None, str | None]:
    """``s3://bucket/key`` → (bucket, key, None);本地路径 → (None, None, abspath)。"""
    if s3_key.startswith("s3://"):
        rest = s3_key[len("s3://"):]
        bucket, _, key = rest.partition("/")
        return bucket, key, None
    return None, None, s3_key


def _download_from_s3(bucket: str, key: str, dest: Path) -> Path:
    """从 MinIO/S3 下载到本地。boto3 不可用直接抛(restore 不能降级)。"""
    try:
        import boto3  # noqa: WPS433
    except ImportError as exc:
        raise RuntimeError("boto3 未安装,无法 restore S3 备份") from exc

    endpoint = os.getenv("S3_ENDPOINT", "http://minio:9000")
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "minioadmin"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(bucket, key, str(dest))
    log.info("下载完成 %s://%s/%s → %s", endpoint, bucket, key, dest)
    return dest


def _run_restore_sh(dump_path: Path, *, dry_run: bool) -> int:
    """调 pg_restore.sh,返回退码。"""
    if not RESTORE_SH.exists():
        raise FileNotFoundError(f"找不到 pg_restore.sh: {RESTORE_SH}")

    cmd = ["bash", str(RESTORE_SH), str(dump_path)]
    if dry_run:
        cmd.append("--dry-run")

    log.info("执行 %s", " ".join(cmd))
    res = subprocess.run(cmd, capture_output=False, text=True, check=False)
    return res.returncode


async def _resolve_backup(backup_id: int | None):
    """从 dr_backup_repo 拿要恢复的那条记录。"""
    from backend.repos import dr_backup_repo

    if backup_id is not None:
        # 简化:用 list_recent 找;真投产应有 get(id)
        rows = await dr_backup_repo.list_recent(limit=200)
        for r in rows:
            if r.id == backup_id:
                return r
        raise RuntimeError(f"找不到 backup id={backup_id}")
    row = await dr_backup_repo.latest()
    if row is None:
        raise RuntimeError("dr_backups 表里没有 status='success' 的记录")
    return row


async def run(*, dry_run: bool, backup_id: int | None) -> int:
    try:
        row = await _resolve_backup(backup_id)
    except Exception as exc:
        log.error("解析备份记录失败:%s", exc)
        return 1

    log.info("拟恢复 backup id=%s s3_key=%s created_at=%s",
             row.id, row.s3_key, row.created_at)

    bucket, key, local_path = _parse_s3_key(row.s3_key)
    if local_path is not None:
        # 本地降级路径:dump 还在 /tmp/dr/
        dump_path = Path(local_path)
        if not dump_path.exists():
            log.error("本地 dump 不存在(可能跨机或已清):%s", dump_path)
            return 4
    else:
        try:
            dump_path = _download_from_s3(
                bucket=bucket,  # type: ignore[arg-type]
                key=key,        # type: ignore[arg-type]
                dest=Path("/tmp/dr_restore") / Path(key).name,  # type: ignore[arg-type]
            )
        except Exception as exc:
            log.error("S3 下载失败:%s", exc)
            return 5

    return _run_restore_sh(dump_path, dry_run=dry_run)


def main() -> int:
    p = argparse.ArgumentParser(description="DR restore wrapper")
    p.add_argument("--dry-run", action="store_true", help="只打印 pg_restore 命令,不真跑")
    p.add_argument("--backup-id", type=int, default=None,
                   help="指定 dr_backups.id;不传则取最新成功一条")
    args = p.parse_args()
    return asyncio.run(run(dry_run=args.dry_run, backup_id=args.backup_id))


if __name__ == "__main__":
    sys.exit(main())

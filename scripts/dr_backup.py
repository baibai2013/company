"""Wave 4 · 提案 4 §5.5 — 灾备 wrapper:跑 pg_backup.sh + 上传 S3/MinIO + 元数据。

调用顺序:
  1. ``dr_backup_repo.create(s3_key=...)`` 占位一行 in_progress
  2. ``subprocess.run(infra/dr/pg_backup.sh /tmp/dr)`` 出 dump 文件
  3. boto3 上传到 ``s3://${S3_BUCKET}/<basename>``;失败 → 只本地存
  4. ``dr_backup_repo.mark_success(...)`` 收尾
  5. 任意失败 → ``dr_backup_repo.mark_failed(...)``

用法::

    python scripts/dr_backup.py [--out-dir /tmp/dr] [--bucket company-dr]

环境变量:
  S3_ENDPOINT          默认 http://minio:9000;dev 单跑改 http://localhost:9000
  S3_BUCKET            默认 company-dr
  AWS_ACCESS_KEY_ID    minioadmin / 真 IAM key
  AWS_SECRET_ACCESS_KEY
  POSTGRES_*           供 pg_backup.sh 用
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("dr_backup")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 仓库根 / pg_backup.sh 的绝对路径(相对脚本位置可定位,跨容器/host 都 work)
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKUP_SH = REPO_ROOT / "infra" / "dr" / "pg_backup.sh"


def _run_pg_dump(out_dir: Path) -> Path:
    """执行 pg_backup.sh,返回输出 dump 文件绝对路径。

    pg_backup.sh 末尾会把绝对路径打到 stdout 最后一行,我们解析它。
    """
    if not BACKUP_SH.exists():
        raise FileNotFoundError(f"找不到 pg_backup.sh: {BACKUP_SH}")
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("执行 %s %s", BACKUP_SH, out_dir)

    res = subprocess.run(
        ["bash", str(BACKUP_SH), str(out_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"pg_backup.sh 退出码 {res.returncode}:\nstdout=\n{res.stdout}\nstderr=\n{res.stderr}"
        )
    # 取 stdout 最后一非空行作为路径
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError("pg_backup.sh 没有输出 dump 路径")
    return Path(lines[-1].strip())


def _upload_to_s3(local_path: Path, *, bucket: str, key: str) -> bool:
    """上传到 MinIO/S3。boto3 不可用或链路失败 → 返回 False(降级 local-only)。"""
    try:
        import boto3  # noqa: WPS433
    except ImportError:
        log.warning("boto3 未安装,跳过 S3 上传(只本地存)")
        return False

    endpoint = os.getenv("S3_ENDPOINT", "http://minio:9000")
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "minioadmin"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin"),
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        )
        # bucket 不存在自动建一次(MinIO 友好;真 S3 第一次失败再手建)
        try:
            s3.head_bucket(Bucket=bucket)
        except Exception:
            try:
                s3.create_bucket(Bucket=bucket)
            except Exception as exc:  # pragma: no cover
                log.warning("建桶失败(可能已存在):%s", exc)

        s3.upload_file(str(local_path), bucket, key)
        log.info("上传成功 %s://%s/%s", endpoint, bucket, key)
        return True
    except Exception as exc:
        log.warning("S3 上传失败,降级 local-only:%s", exc)
        return False


async def _record_in_db(*, s3_key: str, dump_path: Path, uploaded: bool) -> None:
    """写元数据。dev pg 不可用就 swallow + log.warning。"""
    try:
        from backend.repos import dr_backup_repo
    except Exception as exc:
        log.warning("import dr_backup_repo 失败,跳过元数据:%s", exc)
        return

    note = "uploaded=true" if uploaded else "uploaded=false (local-only fallback)"
    try:
        row = await dr_backup_repo.create(s3_key=s3_key, note=note)
    except Exception as exc:
        log.warning("dr_backup_repo.create 失败,跳过元数据:%s", exc)
        return

    try:
        size = dump_path.stat().st_size if dump_path.exists() else 0
        await dr_backup_repo.mark_success(
            row.id,
            size_bytes=size,
            pg_db_size_at_backup=None,  # 真投产可加 SELECT pg_database_size(...)
        )
        log.info("元数据写入完成 id=%s size=%d", row.id, size)
    except Exception as exc:
        log.warning("dr_backup_repo.mark_success 失败:%s", exc)
        try:
            await dr_backup_repo.mark_failed(row.id, note=str(exc))
        except Exception:  # pragma: no cover
            pass


async def run(out_dir: Path, bucket: str) -> int:
    """主流程:dump → 上传 → 写元数据。返回 exit code。"""
    try:
        dump_path = _run_pg_dump(out_dir)
    except Exception as exc:
        log.error("pg_dump 失败:%s", exc)
        return 3

    key = dump_path.name  # 用文件名做 s3 key(已包含 UTC 时间戳)
    uploaded = _upload_to_s3(dump_path, bucket=bucket, key=key)
    s3_key = f"s3://{bucket}/{key}" if uploaded else str(dump_path)

    await _record_in_db(s3_key=s3_key, dump_path=dump_path, uploaded=uploaded)
    log.info("DR 备份完成:%s", s3_key)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="DR backup wrapper")
    p.add_argument("--out-dir", default="/tmp/dr", help="本地 dump 输出目录")
    p.add_argument("--bucket", default=os.getenv("S3_BUCKET", "company-dr"))
    args = p.parse_args()
    return asyncio.run(run(Path(args.out_dir), args.bucket))


if __name__ == "__main__":
    sys.exit(main())

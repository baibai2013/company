"""L2 公司规范全量回填 — 提案 4 §1.5 / §1.8 阶段 4.1.B。

用法::

    python -m scripts.kb_backfill \\
        --root /Users/liyijiang/work/company \\
        --include "employees/**/*.md,doc/**/*.md" \\
        --dry-run

参数:
    --root      扫描根目录(默认当前工作目录)
    --include   逗号分隔的 glob,默认 ``employees/**/*.md,doc/**/*.md``
    --exclude   逗号分隔的 glob,命中即跳过(默认空)
    --dry-run   只打印将要 ingest 的路径,不真插库

source_type 推导:路径含 ``employees/`` → ``duties``;其它 → ``adr``。
``role_filter`` 由 :func:`backend.services.kb_ingest.derive_role_filter_from_path`
自动推导(``employees/<role>/*`` → ``[role]``,``doc/*`` → 全员)。
"""
from __future__ import annotations

import argparse
import asyncio
import fnmatch
import sys
from pathlib import Path

from backend.services.kb_ingest import ingest_path


def _iter_files(root: Path, include: list[str], exclude: list[str]) -> list[Path]:
    """按 include/exclude glob 列文件。include 用 root.glob,exclude 用
    fnmatch 过滤(对 root-relative 字符串路径)。"""
    found: set[Path] = set()
    for pat in include:
        for p in root.glob(pat):
            if p.is_file():
                found.add(p.resolve())
    excluded: set[Path] = set()
    if exclude:
        for p in list(found):
            rel = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
            for pat in exclude:
                if fnmatch.fnmatch(rel, pat):
                    excluded.add(p)
                    break
    return sorted(found - excluded)


def _derive_source_type(path: Path) -> str:
    s = str(path).replace("\\", "/")
    if "/employees/" in s:
        return "duties"
    if "/doc/" in s:
        return "adr"
    return "adr"


async def _async_main(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.exists():
        print(f"[kb_backfill] root 不存在: {root}", file=sys.stderr)
        return 2

    include = [p.strip() for p in args.include.split(",") if p.strip()]
    exclude = [p.strip() for p in (args.exclude or "").split(",") if p.strip()]

    files = _iter_files(root, include, exclude)
    print(f"[kb_backfill] 找到 {len(files)} 个文件,root={root}")
    if args.dry_run:
        for f in files:
            print(f"  [dry] {f.relative_to(root) if f.is_relative_to(root) else f}")
        return 0

    total_chunks = 0
    for f in files:
        rel = f.relative_to(root) if f.is_relative_to(root) else f
        src_type = _derive_source_type(f)
        try:
            n = await ingest_path(
                str(rel),  # 入库用相对路径,跨机器语义稳
                source_type=src_type,
                domain_tag=None,    # L2
                role_filter=None,   # 让 ingest_path 自动按路径推导
            )
            total_chunks += n
            print(f"  [ok] {rel} → {n} chunks")
        except Exception as e:  # 单文件失败不影响其它
            print(f"  [err] {rel} → {e}", file=sys.stderr)
    print(f"[kb_backfill] 完成,共入库 {total_chunks} chunks")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L2 公司规范全量回填")
    parser.add_argument("--root", default=".", help="扫描根目录")
    parser.add_argument(
        "--include",
        default="employees/**/*.md,doc/**/*.md",
        help="逗号分隔的 include glob",
    )
    parser.add_argument("--exclude", default="", help="逗号分隔的 exclude glob")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印不真插")
    args = parser.parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())

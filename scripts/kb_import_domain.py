"""L3 领域知识人工 import — 提案 4 §1.5 的 CLI。

用法::

    python -m scripts.kb_import_domain \\
        --domain mechanical \\
        --file papers/quadruped_kinematics.pdf \\
        --role mechanical,algorithm

支持文件类型:
    - ``.md`` / ``.markdown`` / ``.txt``:utf-8 直接读
    - ``.pdf``:用 ``pypdf`` 提文本(未安装时给友好错误)。
      抽出的文本落到 ``<src>.extracted.md`` 旁路文件,以该路径作为
      ``source_path`` 入库,便于事后人工 review 抽取质量。

写入 ``kb_documents`` 时:
    - ``domain_tag = <--domain>``
    - ``source_type = 'domain'``
    - ``role_filter = <--role>``(逗号分隔的员工 key,留空=全员)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from backend.services.kb_ingest import ingest_path


def _extract_pdf_to_markdown(src: Path) -> Path:
    """PDF → 文本,写到旁路 ``<src>.extracted.md``,返回该 Path。"""
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "处理 PDF 需要 pypdf,请先 `pip install pypdf` 再重试"
        ) from e
    reader = PdfReader(str(src))
    pieces: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        # 用 ## 页号 当 heading,让 chunk_by_heading 切片
        text = page.extract_text() or ""
        pieces.append(f"## Page {i}\n{text.strip()}")
    out = src.with_suffix(src.suffix + ".extracted.md")
    out.write_text("\n\n".join(pieces), encoding="utf-8")
    return out


async def _async_main(args: argparse.Namespace) -> int:
    src = Path(args.file)
    if not src.exists():
        print(f"[kb_import_domain] 找不到文件: {src}", file=sys.stderr)
        return 2

    role_filter: list[str] | None = None
    if args.role:
        role_filter = [r.strip() for r in args.role.split(",") if r.strip()]

    suffix = src.suffix.lower()
    if suffix == ".pdf":
        target = _extract_pdf_to_markdown(src)
        print(f"[kb_import_domain] PDF → 抽文本到 {target}")
    elif suffix in (".md", ".markdown", ".txt"):
        target = src
    else:
        print(f"[kb_import_domain] 暂不支持后缀 {suffix},仅 md/txt/pdf",
              file=sys.stderr)
        return 2

    n = await ingest_path(
        str(target),
        source_type="domain",
        domain_tag=args.domain,
        role_filter=role_filter,
    )
    print(
        f"[kb_import_domain] 入库 {n} chunks → domain={args.domain} "
        f"role={role_filter} path={target}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L3 领域知识 import")
    parser.add_argument("--domain", required=True,
                        help="领域标签:mechanical | firmware | algorithm | ...")
    parser.add_argument("--file", required=True, help="文档路径(md/txt/pdf)")
    parser.add_argument("--role", default="",
                        help="逗号分隔的员工 key 列表;留空 = 全员可召回")
    args = parser.parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())

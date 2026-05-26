"""KB 入库流水线 — 读文件 → chunk → embed → upsert。

提案 4 §1.5 写入流水线的 Python 入口。两个对外接口:

- :func:`chunk_by_heading` 按 markdown heading 切片
- :func:`ingest_path` 一站式:读文件 → 切片 → embed → 写库
- :func:`derive_role_filter_from_path` 从路径推导可见角色

入库幂等:同一个 ``source_path`` 重复 ingest 会先 DELETE 旧 chunk 再 INSERT。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from backend.repos import kb_repo
from backend.services.embeddings import embed_batch

log = logging.getLogger(__name__)


# 按 markdown 1-3 级 heading 切片,heading 行本身保留为 chunk title
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)


def chunk_by_heading(text: str, max_chars: int = 1600) -> list[tuple[str, str]]:
    """按 markdown heading(``#`` / ``##`` / ``###``)切片。

    每个切片 ``(title, body)``,``body`` 的字符数不超过 ``max_chars``;超长则
    继续按段落或硬切。无任何 heading 的文档退化为一片(title 为空)。

    切片策略:
    1. 找出所有 1-3 级 heading 的位置
    2. 第 i 个 heading 到第 i+1 个 heading 之前是它的 body
    3. 单 chunk 超 ``max_chars`` 时,按双换行(段落)二次切;若仍超长就硬切

    Args:
        text: 原文(markdown 优先,纯文本也可)
        max_chars: 单 chunk 字符上限。默认 1600 ≈ 400 token,与提案 §1.5 一致。

    Returns:
        ``[(title, body), ...]``;若文档为空字符串,返回空列表。
    """
    if not text or not text.strip():
        return []

    matches = list(_HEADING_RE.finditer(text))

    raw_chunks: list[tuple[str, str]] = []
    if not matches:
        # 无 heading:整篇一片,title 为空
        raw_chunks.append(("", text.strip()))
    else:
        # 第一个 heading 之前的 preface 也保留(若非空)
        first_start = matches[0].start()
        preface = text[:first_start].strip()
        if preface:
            raw_chunks.append(("", preface))

        for i, m in enumerate(matches):
            title = m.group(2).strip()
            body_start = m.end()
            body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[body_start:body_end].strip()
            # body 含 heading 行本身:为方便检索,把 heading 当成上下文头
            full_body = f"{m.group(0).strip()}\n{body}".strip() if body else m.group(0).strip()
            raw_chunks.append((title, full_body))

    # 二次切超长 chunk
    out: list[tuple[str, str]] = []
    for title, body in raw_chunks:
        if len(body) <= max_chars:
            out.append((title, body))
            continue
        # 先按段落切
        parts = re.split(r"\n\s*\n", body)
        buf = ""
        for p in parts:
            if not p.strip():
                continue
            if buf and len(buf) + len(p) + 2 > max_chars:
                out.append((title, buf.strip()))
                buf = p.strip()
            else:
                buf = (buf + "\n\n" + p).strip() if buf else p.strip()
        if buf:
            # buf 仍可能超 max_chars(单段落超长),硬切
            while len(buf) > max_chars:
                out.append((title, buf[:max_chars]))
                buf = buf[max_chars:]
            if buf:
                out.append((title, buf))
    return out


def derive_role_filter_from_path(path: str) -> list[str] | None:
    """从路径推导 ``role_filter``。

    规则(命中第一个返回):
    - ``employees/<name>/...`` → ``[name]``
    - ``doc/...``、其它顶层路径 → ``None``(全员可召回)

    Args:
        path: 文件路径(可以是绝对、相对或仅项目内相对)

    Returns:
        员工 key 列表,或 None。
    """
    norm = path.replace("\\", "/")
    # 兼容绝对路径:截到 .../employees/<x>/... 的相对部分
    parts = norm.split("/")
    for i, seg in enumerate(parts):
        if seg == "employees" and i + 1 < len(parts):
            role = parts[i + 1]
            if role and role not in (".", ".."):
                return [role]
        if seg == "doc":
            return None
    return None


async def ingest_path(
    path: str,
    *,
    source_type: str = "duties",
    domain_tag: str | None = None,
    role_filter: list[str] | None = None,
) -> int:
    """读文件 → 切片 → embed → 写库,返回入库 chunk 数。

    Args:
        path: 文件路径(读 utf-8)
        source_type: ``'duties' | 'adr' | 'contract' | 'domain' | 'past_pr'`` 等
        domain_tag: L3 用,L2 传 None
        role_filter: 显式覆盖路径推导;None 时按 :func:`derive_role_filter_from_path`

    Returns:
        实际入库的 chunk 行数(0 = 文件为空或纯空白)。
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    chunks = chunk_by_heading(text)
    if not chunks:
        log.info("kb_ingest: 文件为空,跳过 path=%s", path)
        # 仍然清掉旧记录(可能是文件被改空)
        await kb_repo.upsert_chunks(
            source_path=str(path),
            source_type=source_type,
            domain_tag=domain_tag,
            role_filter=role_filter,
            chunks=[],
        )
        return 0

    bodies = [body for _title, body in chunks]
    embeddings = await embed_batch(bodies)

    if role_filter is None:
        role_filter = derive_role_filter_from_path(str(path))

    payload: list[tuple[str, str, list[float], dict]] = []
    for (title, body), emb in zip(chunks, embeddings):
        meta = {"chars": len(body)}
        payload.append((title, body, emb, meta))

    n = await kb_repo.upsert_chunks(
        source_path=str(path),
        source_type=source_type,
        domain_tag=domain_tag,
        role_filter=role_filter,
        chunks=payload,
    )
    log.info(
        "kb_ingest: ingest path=%s chunks=%d domain=%s role=%s",
        path, n, domain_tag, role_filter,
    )
    return n


__all__ = ["chunk_by_heading", "ingest_path", "derive_role_filter_from_path"]

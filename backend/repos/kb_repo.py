"""KB 仓库 — kb_documents 写/查与 kb_retrieval_log 写入。

提案 4 §1 RAG 的数据访问层。

为什么用同步 psycopg + thread executor 而不是 SQLAlchemy async ORM:
    pgvector 的 asyncpg binary codec 已经在 ``backend/core/db.py`` 全局注册,
    与 SQLAlchemy ``Vector`` 列的 bind_processor(把 list 转成 ``"[...]"`` 字符串)
    路径冲突 — server 期望 binary 但客户端发了 text。短期最稳的做法是绕开
    SQLAlchemy 这层,直接用 psycopg + ``::vector`` cast(与
    ``backend/tests/test_wave0_schema.py`` 同路径)。

读写约定:
- L2 = 公司规范:``domain_tag IS NULL``
- L3 = 领域知识:``domain_tag IS NOT NULL``
- ``role_filter NULL`` = 全员可召回
- 写入幂等:同一 ``source_path`` 的旧 chunk 用 ``LIKE 'source_path#%'`` 删后再插
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row

from backend.core.config import settings

log = logging.getLogger(__name__)


# ── 同步内核(在 thread executor 里跑)──────────────────────────────────────


def _conn() -> psycopg.Connection:
    """打开一个同步 psycopg 连接(短连接,call/close)。"""
    url = settings.database_url_sync.replace("+psycopg", "")
    return psycopg.connect(url)


def _vec_literal(vec: list[float]) -> str:
    """list[float] → ``"[0.1,0.2,...]"`` 文本字面量,供 ``::vector`` cast。"""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def _upsert_chunks_sync(
    *,
    source_path: str,
    source_type: str,
    domain_tag: str | None,
    role_filter: list[str] | None,
    chunks: list[tuple[str, str, list[float], dict]],
) -> int:
    with _conn() as c, c.cursor() as cur:
        # 先清旧:既清"无 heading 单 chunk"(source_path 本身),也清
        # "有 heading 多 chunk"(source_path#title)。
        cur.execute(
            "DELETE FROM kb_documents WHERE source_path = %s OR source_path LIKE %s",
            (source_path, f"{source_path}#%"),
        )
        inserted = 0
        for title, body, embedding, meta in chunks:
            full_path = f"{source_path}#{title}" if title else source_path
            cur.execute(
                """
                INSERT INTO kb_documents
                  (source_path, source_type, domain_tag, role_filter,
                   title, body, embedding, meta)
                VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s::jsonb)
                """,
                (
                    full_path,
                    source_type,
                    domain_tag,
                    role_filter,
                    title or None,
                    body,
                    _vec_literal(embedding),
                    json.dumps(meta) if meta else None,
                ),
            )
            inserted += 1
        c.commit()
    return inserted


def _search_by_embedding_sync(
    *,
    query_embedding: list[float],
    layer: str,
    role: str | None,
    top_k: int,
) -> list[dict]:
    if layer not in ("L2", "L3"):
        raise ValueError(f"layer 必须是 L2 或 L3,收到 {layer!r}")
    sql = """
        SELECT id, source_path, title, body, domain_tag,
               1 - (embedding <=> %(q)s::vector) AS score
        FROM kb_documents
        WHERE embedding IS NOT NULL
          AND (role_filter IS NULL OR %(role)s = ANY(role_filter))
          AND (
            (%(layer)s = 'L2' AND domain_tag IS NULL)
            OR (%(layer)s = 'L3' AND domain_tag IS NOT NULL)
          )
        ORDER BY embedding <=> %(q)s::vector
        LIMIT %(top_k)s
    """
    params = {
        "q": _vec_literal(query_embedding),
        "role": role,
        "layer": layer,
        "top_k": top_k,
    }
    with _conn() as c, c.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    # 把 score 强制转 float(psycopg 可能返回 Decimal)
    out = []
    for r in rows:
        r = dict(r)
        r["score"] = float(r["score"]) if r.get("score") is not None else None
        out.append(r)
    return out


def _log_retrieval_sync(
    *,
    task_id: str | None,
    employee_key: str,
    query: str,
    layer: str,
    hit_doc_ids: list[int],
    hit_scores: list[float],
    injected_chars: int,
) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO kb_retrieval_log
              (task_id, employee_key, query, layer,
               hit_doc_ids, hit_scores, injected_chars)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                task_id,
                employee_key,
                query,
                layer,
                hit_doc_ids or None,
                hit_scores or None,
                injected_chars,
            ),
        )
        c.commit()


# ── async 包装(thread executor)────────────────────────────────────────────


async def upsert_chunks(
    *,
    source_path: str,
    source_type: str,
    domain_tag: str | None,
    role_filter: list[str] | None,
    chunks: list[tuple[str, str, list[float], dict]],
) -> int:
    """幂等写入一批 chunk。

    Args:
        source_path: 文件原路径(不含 ``#section``),例如
            ``employees/mechanical/duties.md``。
        source_type: ``'duties' | 'adr' | 'contract' | 'domain' | 'past_pr'`` 等。
        domain_tag: L3 用的领域标签;L2 传 None。
        role_filter: 可召回的员工 key 列表;None 表示全员。
        chunks: ``(title, body, embedding_vector, meta_dict)`` 元组列表;
            ``embedding_vector`` 长度必须为 1536。

    Returns:
        新插入的 chunk 行数。

    幂等策略:
        DELETE FROM kb_documents
         WHERE source_path = '<p>' OR source_path LIKE '<p>#%'
    """
    n = await asyncio.to_thread(
        _upsert_chunks_sync,
        source_path=source_path,
        source_type=source_type,
        domain_tag=domain_tag,
        role_filter=role_filter,
        chunks=chunks,
    )
    log.info(
        "kb_repo: upsert source_path=%s chunks=%d domain=%s role=%s",
        source_path, n, domain_tag, role_filter,
    )
    return n


async def search_by_embedding(
    *,
    query_embedding: list[float],
    layer: str,
    role: str | None,
    top_k: int = 5,
) -> list[dict]:
    """按 cosine 距离查 top_k。

    SQL:
        SELECT id, source_path, title, body, domain_tag,
               1 - (embedding <=> :q) AS score
        FROM kb_documents
        WHERE embedding IS NOT NULL
          AND (role_filter IS NULL OR :role = ANY(role_filter))
          AND ((:layer='L2' AND domain_tag IS NULL)
               OR (:layer='L3' AND domain_tag IS NOT NULL))
        ORDER BY embedding <=> :q
        LIMIT :top_k

    Returns:
        ``{id, source_path, title, body, score, domain_tag}`` 列表;
        ``score`` 越大越相关(已转换为 ``1 - distance``)。
    """
    return await asyncio.to_thread(
        _search_by_embedding_sync,
        query_embedding=query_embedding,
        layer=layer,
        role=role,
        top_k=top_k,
    )


async def log_retrieval(
    *,
    task_id: str | None,
    employee_key: str,
    query: str,
    layer: str,
    hit_doc_ids: list[int],
    hit_scores: list[float],
    injected_chars: int,
) -> None:
    """写一条 RAG 召回审计 — ``kb_retrieval_log`` 表。

    后续提案 3 retro_agent 会读这张表分析"哪些 KB 命中了仍出错"。
    """
    await asyncio.to_thread(
        _log_retrieval_sync,
        task_id=task_id,
        employee_key=employee_key,
        query=query,
        layer=layer,
        hit_doc_ids=hit_doc_ids,
        hit_scores=hit_scores,
        injected_chars=injected_chars,
    )


__all__ = ["upsert_chunks", "search_by_embedding", "log_retrieval"]

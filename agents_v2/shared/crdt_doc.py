"""共享文档 CRDT 底层封装(基于 pycrdt + redis)。

设计:
  - 文档 ydoc 状态存 redis: doc:{doc_id}:state(base64)
  - 增量 update 通过 redis stream: doc:{doc_id}:stream
  - 元数据(title/structure/sections): doc:{doc_id}:meta(JSON)
  - shadow markdown: shared/<doc_id>.md(由 doc_server 周期性 dump)

并发模型:
  - 多个 cli 子进程同时调用 → 各自本地 Doc apply 当前 state → 修改 → push update
  - doc_server 后台进程持续 XREAD → apply 到 master Doc → 更新 state + dump md
  - CRDT 保证 update 无序 apply 结果一致(commutative)
  - 不需要锁

API 用法:
    DocStore() 实例化
    .create(doc_id, title, structure="freeform", sections=None)
    .read_state(doc_id) → 返回当前 ydoc 完整 state bytes
    .push_update(doc_id, update_bytes) → 推 redis stream
    .render_markdown(doc_id) → 用最新 state 渲染 markdown
    .list() → 当前所有活跃文档元数据
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Iterable

import redis
from pycrdt import Doc, Text, Array, Map

log = logging.getLogger("agents_v2.crdt_doc")

_REDIS_URL = "redis://localhost:6379/0"
_DOC_TTL_SEC = 30 * 86400  # 文档 30 天 TTL


# ── Redis key 命名 ────────────────────────────────────────────────────────────

def _k_meta(doc_id: str) -> str:    return f"doc:{doc_id}:meta"
def _k_state(doc_id: str) -> str:   return f"doc:{doc_id}:state"
def _k_stream(doc_id: str) -> str:  return f"doc:{doc_id}:stream"
def _k_index() -> str:              return "doc:index"  # set,所有 doc_id


# ── 元数据 ────────────────────────────────────────────────────────────────────

@dataclass
class DocMeta:
    """文档元数据(独立于 CRDT 内容,简单 JSON 存 redis)。"""
    doc_id: str
    title: str = ""
    structure: str = "freeform"        # freeform | sectioned | list | qa
    sections: list[str] = field(default_factory=list)  # sectioned 时的 section_id 列表
    created_at: float = 0.0
    updated_at: float = 0.0
    creator: str = ""

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "structure": self.structure,
            "sections": self.sections,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "creator": self.creator,
        }

    @classmethod
    def from_json(cls, raw: str) -> "DocMeta":
        d = json.loads(raw)
        return cls(**d)


# ── ydoc 初始结构 ──────────────────────────────────────────────────────────────

def _init_ydoc(meta: DocMeta) -> Doc:
    """按 structure 类型在 ydoc 里铺好顶层容器骨架(不预填子 Text/项目)。

    pycrdt 注意:每次 _init_ydoc 创建新 Doc,顶层容器(Map/Array/Text)用 root key
    挂上去,通过 root key 名字相同保证多 Doc 间合并。但**子项**(如 sections 内的
    Text)必须在 op 里**第一次写**,而不是 init 时预填,否则不同 init 创建不同
    Text 实例会 LWW 冲突。
    """
    doc = Doc()
    if meta.structure == "sectioned":
        doc["sections"] = Map()        # 空 Map,子 Text 在 op_append 时创建
    elif meta.structure == "list":
        doc["items"] = Array()
    elif meta.structure == "qa":
        doc["questions"] = Array()
        doc["answers"] = Array()
    else:  # freeform
        doc["body"] = Text()
    doc["annotations"] = Array()
    return doc


# ── 渲染:ydoc → markdown ─────────────────────────────────────────────────────

def render_markdown(meta: DocMeta, doc: Doc) -> str:
    """把 ydoc 当前状态序列化成 markdown 字符串。"""
    lines: list[str] = []
    lines.append(f"# {meta.title or meta.doc_id}")
    lines.append(f"_doc_id: {meta.doc_id}  · structure: {meta.structure}_")
    lines.append("")

    if meta.structure == "sectioned":
        sections_map = (doc["sections"] if "sections" in doc else None)
        for sec in meta.sections:
            content = ""
            if sections_map is not None and sec in sections_map:
                content = str(sections_map[sec])
            lines.append(f"## {sec}")
            lines.append(content.strip() or "_(待签到)_")
            lines.append("")
    elif meta.structure == "list":
        items_arr = (doc["items"] if "items" in doc else None)
        if items_arr is not None:
            for it in items_arr:
                lines.append(f"- {it}")
        else:
            lines.append("_(列表为空)_")
        lines.append("")
    elif meta.structure == "qa":
        q_arr = (doc["questions"] if "questions" in doc else None)
        a_arr = (doc["answers"] if "answers" in doc else None)
        lines.append("## 问题")
        if q_arr is not None and len(q_arr) > 0:
            for i, q in enumerate(q_arr, 1):
                lines.append(f"{i}. {q}")
        else:
            lines.append("_(暂无问题)_")
        lines.append("")
        lines.append("## 回答")
        if a_arr is not None and len(a_arr) > 0:
            for i, a in enumerate(a_arr, 1):
                lines.append(f"{i}. {a}")
        else:
            lines.append("_(暂无回答)_")
        lines.append("")
    else:  # freeform
        body = (doc["body"] if "body" in doc else None)
        body_text = str(body) if body is not None else ""
        lines.append(body_text.strip() or "_(空)_")
        lines.append("")

    # 批注追加在末尾(任何 structure 都有)
    annot_arr = (doc["annotations"] if "annotations" in doc else None)
    if annot_arr is not None and len(annot_arr) > 0:
        lines.append("---")
        lines.append("## 📝 批注")
        for a in annot_arr:
            lines.append(f"- {a}")
        lines.append("")

    return "\n".join(lines)


# ── DocStore: redis 接口 ──────────────────────────────────────────────────────

class DocStore:
    def __init__(self, url: str = _REDIS_URL):
        self.r = redis.Redis.from_url(url, decode_responses=True)
        # 二进制 redis(用于 state base64 反着存原 bytes 时复用)
        self.rb = redis.Redis.from_url(url, decode_responses=False)

    def create(
        self,
        doc_id: str,
        title: str = "",
        structure: str = "freeform",
        sections: list[str] | None = None,
        creator: str = "",
    ) -> DocMeta:
        if self.r.exists(_k_meta(doc_id)):
            # 已存在,直接返回当前 meta(不覆盖)
            return self.read_meta(doc_id)
        now = time.time()
        meta = DocMeta(
            doc_id=doc_id, title=title or doc_id,
            structure=structure,
            sections=list(sections or []),
            created_at=now, updated_at=now, creator=creator,
        )
        # 初始化 ydoc 状态
        doc = _init_ydoc(meta)
        # 预填 sectioned 的子 Text:create 时只发生一次,
        # 多 cli 进程后续从 state load 时拿到的是同一 Text CRDT 实例,
        # 多人 append 不再竞争创建,内容自动合并。
        if meta.structure == "sectioned":
            sections_map = doc["sections"]
            for sec in meta.sections:
                sections_map[sec] = Text()
        self._save_meta(meta)
        self._save_state(doc_id, doc.get_update())
        self.r.sadd(_k_index(), doc_id)
        log.info("doc created: id=%s structure=%s sections=%s",
                 doc_id, structure, meta.sections)
        return meta

    def read_meta(self, doc_id: str) -> DocMeta | None:
        raw = self.r.get(_k_meta(doc_id))
        if not raw:
            return None
        return DocMeta.from_json(raw)

    def _save_meta(self, meta: DocMeta) -> None:
        meta.updated_at = time.time()
        self.r.set(_k_meta(meta.doc_id),
                   json.dumps(meta.to_dict(), ensure_ascii=False),
                   ex=_DOC_TTL_SEC)

    def read_state(self, doc_id: str) -> bytes:
        """返回当前 ydoc 的完整 update bytes。新 cli 进程用此重建本地 doc。"""
        raw = self.rb.get(_k_state(doc_id))
        return raw or b""

    def _save_state(self, doc_id: str, update_bytes: bytes) -> None:
        self.rb.set(_k_state(doc_id), update_bytes, ex=_DOC_TTL_SEC)

    def load_doc(self, doc_id: str) -> Doc | None:
        """从 redis state 重建 Doc 实例(只读用,修改要 push_update)。"""
        meta = self.read_meta(doc_id)
        if not meta:
            return None
        doc = _init_ydoc(meta)
        state = self.read_state(doc_id)
        if state:
            doc.apply_update(state)
        return doc

    def push_update(self, doc_id: str, update_bytes: bytes, author: str = "") -> str:
        """推一条 update 到 redis stream,doc_server 后台合并到 master state。"""
        if not update_bytes:
            return ""
        msg_id = self.r.xadd(_k_stream(doc_id), {
            "u": base64.b64encode(update_bytes).decode("ascii"),
            "author": author,
            "ts": str(time.time()),
        }, maxlen=10000, approximate=True)
        return msg_id

    def render_markdown(self, doc_id: str) -> str:
        meta = self.read_meta(doc_id)
        if not meta:
            return f"❌ doc_id={doc_id} 不存在"
        doc = self.load_doc(doc_id)
        if doc is None:
            return f"❌ 加载失败 doc_id={doc_id}"
        return render_markdown(meta, doc)

    def list(self) -> list[DocMeta]:
        ids = self.r.smembers(_k_index())
        out: list[DocMeta] = []
        for did in ids:
            m = self.read_meta(did)
            if m:
                out.append(m)
        out.sort(key=lambda m: m.updated_at, reverse=True)
        return out

    def consume_stream(
        self,
        doc_id: str,
        last_id: str = "0",
        block_ms: int = 1000,
        count: int = 100,
    ) -> tuple[str, list[bytes]]:
        """供 doc_server 调用:从 stream 拉新 update bytes 列表。
        返回 (new_last_id, [bytes,...])。
        """
        raw = self.r.xread(
            {_k_stream(doc_id): last_id},
            count=count, block=block_ms,
        )
        if not raw:
            return last_id, []
        # XREAD 返回 [(stream_name, [(msg_id, {fields}), ...])]
        new_last = last_id
        updates: list[bytes] = []
        for _stream, entries in raw:
            for msg_id, fields in entries:
                new_last = msg_id
                u_b64 = fields.get("u", "")
                if u_b64:
                    try:
                        updates.append(base64.b64decode(u_b64))
                    except Exception as exc:
                        log.warning("decode update failed: %s", exc)
        return new_last, updates

    def consolidate_state(self, doc_id: str) -> None:
        """从 stream 拉所有 update 合并到一个 ydoc,写回 state。
        op 函数完成后调一次,保证不依赖 doc_server 也能立即可读。
        多进程并发时 redis SET 后写覆盖,但 stream 是真理之源,
        doc_server 重放能补全(退化降级安全)。
        """
        meta = self.read_meta(doc_id)
        if not meta:
            return
        # 从 0 开始拉全部 stream
        last_id, updates = self.consume_stream(doc_id, last_id="0", block_ms=0, count=10000)
        if not updates:
            return
        master = _init_ydoc(meta)
        # 先 apply 已有 state 当 base
        existing = self.read_state(doc_id)
        if existing:
            master.apply_update(existing)
        for u in updates:
            try:
                master.apply_update(u)
            except Exception as exc:
                log.warning("apply update failed doc=%s: %s", doc_id, exc)
        self._save_state(doc_id, master.get_update())


# ── 操作辅助:cli 工具用,执行某个 op,产生增量 update,push redis ──────────

def _local_doc_with_state(meta: DocMeta, state: bytes) -> Doc:
    """从当前 redis state bytes 重建一个本地 Doc(供后续 mutate)。

    重要:client_id 用 secrets 强随机生成。
    pycrdt Doc() 默认 client_id 在多 subprocess 同时启动时可能撞,
    撞了会导致 CRDT 把不同 actor 的 op 当同一 actor 的覆盖,丢数据。
    """
    import secrets
    # 53 位随机(JS Number safe int 上限),保证多进程间唯一
    client_id = secrets.randbits(53)
    doc = Doc(client_id=client_id)
    if meta.structure == "sectioned":
        doc["sections"] = Map()
    elif meta.structure == "list":
        doc["items"] = Array()
    elif meta.structure == "qa":
        doc["questions"] = Array()
        doc["answers"] = Array()
    else:
        doc["body"] = Text()
    doc["annotations"] = Array()
    if state:
        doc.apply_update(state)
    return doc


def op_append(store: DocStore, doc_id: str, text: str,
              section: str = "", author: str = "") -> str:
    """在文档末尾追加文本(structure 决定追加到哪)。
    - sectioned + section: 追加到该 section 末尾
    - list: 追加一个 list item
    - qa: section="questions"|"answers" 追加到对应数组
    - freeform 或 sectioned 不带 section: 追加到 body 末尾(freeform)或全文末尾
    """
    meta = store.read_meta(doc_id)
    if not meta:
        return f"❌ doc {doc_id} 不存在"
    doc = _local_doc_with_state(meta, store.read_state(doc_id))
    state_before = doc.get_state()

    # pycrdt 嵌套修改坑: 必须先取 ref 再 mutate,
    # 链式 d['m']['k'] += 创建临时代理后丢失
    if meta.structure == "sectioned":
        sections = (doc["sections"] if "sections" in doc else None)
        if not section:
            return "❌ sectioned 文档 doc_append 必须传 section 参数"
        if sections is None:
            return "❌ sections 容器丢失"
        if section not in sections:
            sections[section] = Text()
        text_ref = sections[section]
        # 多人同时 append 时,各自看 cur 都是空,如果只在前面加 \n 会丢分隔。
        # 改成内容尾部固定加 \n,每段独立,合并后多人内容用换行分开。
        text_ref += text + "\n"
    elif meta.structure == "list":
        arr = (doc["items"] if "items" in doc else None)
        if arr is None:
            return "❌ items 数组丢失"
        arr.append(text)
    elif meta.structure == "qa":
        target = section or "answers"
        if target not in ("questions", "answers"):
            return f"❌ qa 文档 section 必须是 questions 或 answers,得到 {target}"
        arr = (doc[target] if target in doc else None)
        if arr is None:
            return f"❌ {target} 数组丢失"
        arr.append(text)
    else:  # freeform
        body = (doc["body"] if "body" in doc else None)
        if body is None:
            return "❌ body 丢失"
        existing = str(body)
        sep = "\n" if existing and not existing.endswith("\n") else ""
        body += sep + text

    update = doc.get_update(state_before)
    msg_id = store.push_update(doc_id, update, author=author)
    store.consolidate_state(doc_id)   # MVP: 同步合并(单进程)
    store._save_meta(meta)
    return f"✅ append 完成 stream_id={msg_id} bytes={len(update)}"


def op_replace_section(store: DocStore, doc_id: str, section: str,
                       text: str, author: str = "") -> str:
    """整段替换某 section 内容(只对 sectioned 文档有效)。"""
    meta = store.read_meta(doc_id)
    if not meta:
        return f"❌ doc {doc_id} 不存在"
    if meta.structure != "sectioned":
        return f"❌ structure={meta.structure} 不支持 replace_section"
    doc = _local_doc_with_state(meta, store.read_state(doc_id))
    state_before = doc.get_state()

    sections = (doc["sections"] if "sections" in doc else None)
    if sections is None:
        return "❌ sections 容器丢失"
    if section not in sections:
        sections[section] = Text()
    text_ref = sections[section]
    text_ref.clear()
    text_ref += text

    update = doc.get_update(state_before)
    msg_id = store.push_update(doc_id, update, author=author)
    store.consolidate_state(doc_id)
    store._save_meta(meta)
    return f"✅ replace_section({section}) 完成 stream_id={msg_id}"


def op_insert_after(store: DocStore, doc_id: str, after_marker: str,
                    text: str, author: str = "") -> str:
    """在 freeform 文档 body 中,某 marker(必须是已存在的子串)后插入新文本。"""
    meta = store.read_meta(doc_id)
    if not meta:
        return f"❌ doc {doc_id} 不存在"
    if meta.structure != "freeform":
        return f"❌ structure={meta.structure} insert_after 仅 freeform 支持"
    doc = _local_doc_with_state(meta, store.read_state(doc_id))
    state_before = doc.get_state()

    body = (doc["body"] if "body" in doc else None)
    if body is None:
        return "❌ body 丢失"
    # pycrdt Text.insert 用 UTF-8 字节位置(不是字符位置),用 bytes find
    cur_bytes = str(body).encode("utf-8")
    marker_bytes = after_marker.encode("utf-8")
    pos = cur_bytes.find(marker_bytes)
    if pos < 0:
        return f"❌ marker {after_marker!r} 不在文档中"
    insert_pos = pos + len(marker_bytes)
    body.insert(insert_pos, "\n" + text)

    update = doc.get_update(state_before)
    msg_id = store.push_update(doc_id, update, author=author)
    store.consolidate_state(doc_id)
    store._save_meta(meta)
    return f"✅ insert_after({after_marker[:20]}) at pos {insert_pos}"


def op_annotate(store: DocStore, doc_id: str, target: str,
                comment: str, author: str = "") -> str:
    """给文档加批注(任意 structure 都支持,append 到 annotations 数组)。"""
    meta = store.read_meta(doc_id)
    if not meta:
        return f"❌ doc {doc_id} 不存在"
    doc = _local_doc_with_state(meta, store.read_state(doc_id))
    state_before = doc.get_state()
    annot = (doc["annotations"] if "annotations" in doc else None)
    if annot is None:
        return "❌ annotations 数组丢失"
    entry = f"[{target}] @{author or 'unknown'}: {comment}"
    annot.append(entry)
    update = doc.get_update(state_before)
    msg_id = store.push_update(doc_id, update, author=author)
    store.consolidate_state(doc_id)
    store._save_meta(meta)
    return f"✅ annotate 已追加: {entry[:80]}"

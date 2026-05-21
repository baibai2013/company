"""共享文档 server: 维护 master ydoc,合并 op stream,定期 dump markdown。

职责:
1. 监听所有活跃 doc 的 redis stream(doc:{id}:stream)
2. 拉新 update bytes,apply 到 master ydoc
3. 每 1 秒 dump markdown 到 shared/<doc_id>.md(用户能实时看)
4. 定期把 master state 写回 redis(让新 cli 进程 load)

注意 cli 进程已经在 op 函数内 consolidate_state 一次,
本 server 主要是:
- 兜底处理"cli 进程死掉没 consolidate"的情况
- 周期 dump markdown(cli 不写文件,只 push redis)

启动: nohup python -m scripts.doc_server > logs/doc_server.log 2>&1 &
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from agents_v2.shared.crdt_doc import (
    DocStore, _init_ydoc, render_markdown,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(name)s  %(message)s",
)
log = logging.getLogger("doc_server")

SHARED_DIR = Path("/Users/liyijiang/work/robot-dog/shared")
DUMP_INTERVAL_SEC = 1.0
SCAN_INTERVAL_SEC = 5.0    # 每 5s 扫一遍 doc:index 看有没有新 doc


async def consume_one_doc(store: DocStore, doc_id: str, stop_evt: asyncio.Event):
    """一个协程持续消费一个 doc 的 stream + 1s dump markdown。"""
    last_id = "0"
    last_dump_at = 0.0
    last_md = ""
    log.info("consumer started doc=%s", doc_id)
    while not stop_evt.is_set():
        try:
            new_last, updates = await asyncio.to_thread(
                store.consume_stream, doc_id, last_id, 500, 100,
            )
            if updates:
                last_id = new_last
                # apply 所有 update + 写回 state
                meta = store.read_meta(doc_id)
                if meta is None:
                    log.warning("doc=%s meta gone, exit consumer", doc_id)
                    return
                master = _init_ydoc(meta)
                existing = store.read_state(doc_id)
                if existing:
                    master.apply_update(existing)
                for u in updates:
                    try:
                        master.apply_update(u)
                    except Exception as exc:
                        log.warning("apply update failed: %s", exc)
                store._save_state(doc_id, master.get_update())

            # 1s 内最多 dump 一次
            now = time.monotonic()
            if now - last_dump_at >= DUMP_INTERVAL_SEC:
                last_dump_at = now
                meta = store.read_meta(doc_id)
                if meta is None:
                    return
                doc = store.load_doc(doc_id)
                if doc is None:
                    continue
                md = render_markdown(meta, doc)
                if md != last_md:
                    md_path = SHARED_DIR / f"{doc_id}.md"
                    md_path.parent.mkdir(parents=True, exist_ok=True)
                    md_path.write_text(md, encoding="utf-8")
                    last_md = md
                    log.debug("dumped %s (%d chars)", md_path.name, len(md))
        except Exception as exc:
            log.warning("consumer doc=%s error: %s", doc_id, exc)
            await asyncio.sleep(1)


async def main():
    store = DocStore()
    stop_evt = asyncio.Event()
    consumers: dict[str, asyncio.Task] = {}

    log.info("doc_server starting, shared dir=%s", SHARED_DIR)
    SHARED_DIR.mkdir(parents=True, exist_ok=True)

    while not stop_evt.is_set():
        try:
            # 扫 doc 索引,新 doc 起 consumer
            ids = await asyncio.to_thread(lambda: list(store.r.smembers("doc:index")))
            for did in ids:
                if did not in consumers:
                    consumers[did] = asyncio.create_task(
                        consume_one_doc(store, did, stop_evt)
                    )
                    log.info("spawn consumer for doc=%s", did)
            # 清理已完成/已失败的 consumer
            done = [d for d, t in consumers.items() if t.done()]
            for d in done:
                consumers.pop(d, None)
                log.info("consumer doc=%s ended", d)
            await asyncio.sleep(SCAN_INTERVAL_SEC)
        except KeyboardInterrupt:
            stop_evt.set()
            break
        except Exception as exc:
            log.error("main loop error: %s", exc)
            await asyncio.sleep(1)

    log.info("doc_server stopping...")
    for t in consumers.values():
        t.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

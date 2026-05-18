"""H-mock: 4 并发 fake task 真实发卡到测试 chat，观察并发负载下飞书行为。
- mock claude_runner.run() 模拟 8 工具 + 文本流，避免真烧 token / 长时间运行
- 4 个 fake (chat_id, sender_id) 共用同一真实 CHAT_ID（测 chat 级限频）
- 隔离 PERSIST_PATH 避免污染生产 ~/.claude/cc_bridge_threads.json
"""
import asyncio, json, os, tempfile, time, pathlib
from contextlib import contextmanager
from dotenv import load_dotenv
load_dotenv(pathlib.Path('infra/.env'))

# 隔离持久化路径（必须在 import router 之前）
from feishu.cc_bridge import thread_router
TEST_PERSIST = pathlib.Path(tempfile.mkdtemp()) / "threads.json"
thread_router.PERSIST_PATH = TEST_PERSIST

import lark_oapi as lark
from feishu.cc_bridge.message_handler import handle_message
from feishu.cc_bridge.claude_runner import ClaudeRunner

CHAT_ID = "oc_af3a7f6a7f5226d988a2881d340895ca"
N_PARALLEL = 4
TOOLS_PER_TASK = 8

# 自建 lark client（不复用 cc_bridge.main 避免单例冲突）
client = (lark.Client.builder()
    .app_id(os.getenv("CC_BRIDGE_APP_ID"))
    .app_secret(os.getenv("CC_BRIDGE_APP_SECRET"))
    .log_level(lark.LogLevel.WARNING).build())

# 监控 patch_card 实际行为的 hook
patch_calls: list = []   # [(task_idx, ts, ok, code)]
real_patch = client.im.v1.message.patch
def wrapped_patch(req):
    t0 = time.time()
    resp = real_patch(req)
    patch_calls.append((time.time(), resp.success(), resp.code))
    return resp
client.im.v1.message.patch = wrapped_patch

# mock claude_runner.run：模拟 8 工具调用 + 文本流，~5s 完成
async def fake_run(self, text, cwd, session_id, image_paths,
                   on_tool_start, on_tool_result, on_text, on_thinking):
    task_id = text.split()[-1] if " " in text else "x"
    # 5 个 TaskCreate + 5 个 TaskUpdate（测 todo 块） + 3 Bash
    for i in range(3):
        tu_id = f"{task_id}_create_{i}"
        await on_tool_start(tu_id, "TaskCreate", {"subject": f"task{task_id}-{i}"})
        await asyncio.sleep(0.15)
        await on_tool_result(tu_id, f"Task #{i+1} created successfully: task{task_id}-{i}")
        await asyncio.sleep(0.05)
    for i in range(3):
        await on_tool_start(f"{task_id}_update_{i}", "TaskUpdate",
                           {"taskId": str(i+1), "status": "in_progress"})
        await asyncio.sleep(0.3)
    for i in range(3):
        await on_tool_start(f"{task_id}_update_done_{i}", "TaskUpdate",
                           {"taskId": str(i+1), "status": "completed"})
        await asyncio.sleep(0.2)
    # 最后一段文本
    await on_text("压测完成")
    return "压测完成", [], f"fake-session-{task_id}"

# patch ClaudeRunner.run 为 fake
real_run = ClaudeRunner.run
ClaudeRunner.run = fake_run

async def main():
    print(f"启动 {N_PARALLEL} 个 fake task 并发到 chat={CHAT_ID[-8:]}…")
    t0 = time.time()
    results = await asyncio.gather(*[
        handle_message(client, CHAT_ID, f"压测 {i}", sender_id=f"u_load_{i}")
        for i in range(N_PARALLEL)
    ], return_exceptions=True)
    elapsed = time.time() - t0
    errs = [r for r in results if isinstance(r, Exception)]
    print(f"\n4 个 task 完成，总耗时 {elapsed:.1f}s")
    if errs:
        for e in errs:
            print(f"  ❌ exception: {e!r}")

    # 统计 patch 行为
    total = len(patch_calls)
    ok = sum(1 for _, success, _ in patch_calls if success)
    fails = [(c, s) for c, s in [(call[2], call[1]) for call in patch_calls] if not s]
    fail_codes = {}
    for code, _ in fails:
        fail_codes[code] = fail_codes.get(code, 0) + 1
    if total > 0:
        # 每秒 patch 数（直方图，1 秒桶）
        ts = [t for t, _, _ in patch_calls]
        first = min(ts)
        buckets = {}
        for t in ts:
            b = int(t - first)
            buckets[b] = buckets.get(b, 0) + 1
        peak = max(buckets.values()) if buckets else 0
        avg = total / max(1, max(buckets.keys()) + 1)
        print(f"\npatch 总数: {total}  成功: {ok}  失败: {len(fails)}")
        if fail_codes:
            print(f"  错误码分布: {fail_codes}")
        print(f"  patch QPS：peak={peak}/s  avg={avg:.1f}/s")
        print(f"  按秒分布: {dict(sorted(buckets.items()))}")

ClaudeRunner.run = real_run  # 恢复

try:
    asyncio.run(main())
finally:
    # 清理临时持久化文件
    try:
        os.unlink(TEST_PERSIST)
        os.rmdir(TEST_PERSIST.parent)
    except OSError:
        pass

"""
AgentScheduler — 嵌入 agent 进程的定时任务框架。

根据 behavior.scheduled_tasks 配置创建 asyncio 后台循环，
到时间时按 execution_mode 分发执行：
  - agent（默认）：走 LLM SmartGraph，适合需要推理/润色的任务
  - direct：跳过 LLM，按顺序直接调工具，零 token
  - webhook：HTTP POST 外部 URL

用法（在 generic/main.py lifespan 里）：
    scheduler = AgentScheduler(key, agent_fn, output_fn, tools=tools)
    await scheduler.start()
    ...
    await scheduler.stop()
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from croniter import croniter

log = logging.getLogger("scheduler")


def _sync_insert_run(
    dsn: str,
    employee_key: str,
    task_id: str,
    task_name: str,
    triggered_at: datetime,
    finished_at: datetime,
    duration_ms: int,
    status: str,
    result_text: str,
    output_to: str,
    execution_mode: str,
) -> None:
    """同步写 agent_task_runs（在线程池中调用，不占 event loop）。"""
    import psycopg  # noqa: PLC0415
    sql = """
        INSERT INTO agent_task_runs
            (employee_key, task_id, task_name, triggered_at, finished_at,
             duration_ms, status, result_text, output_to, execution_mode)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    employee_key, task_id, task_name,
                    triggered_at, finished_at, duration_ms,
                    status, result_text, output_to, execution_mode,
                ))
            conn.commit()
    except Exception as e:
        # 表不存在时优雅降级，不影响调度主流程
        log.debug("agent_task_runs insert skipped: %s", e)


def _interpolate(args: dict, context: dict) -> dict:
    """替换 direct_actions 参数里的 {{tool_name.result}} 占位符。"""
    result = {}
    for k, v in args.items():
        if isinstance(v, str):
            def _replace(m, ctx=context):
                parts = m.group(1).split(".")
                if len(parts) == 2:
                    return str(ctx.get(parts[0], {}).get(parts[1], m.group(0)))
                return m.group(0)
            result[k] = re.sub(r"\{\{([^}]+)\}\}", _replace, v)
        else:
            result[k] = v
    return result


class AgentScheduler:
    """管理一个 agent 进程内的所有定时任务。"""

    def __init__(
        self,
        employee_key: str,
        agent_fn: Callable[[str, dict], Awaitable[str]],
        output_fn: Callable[[str, str, str], Awaitable[None]],
        tools: list | None = None,
    ):
        """
        Args:
            employee_key: 员工 key
            agent_fn: 异步函数 (prompt, context_dict) → result_text
            output_fn: 异步函数 (output_to, task_name, result_text) → None
            tools: 工具列表，供 direct 模式直接调用
        """
        self.key = employee_key
        self.agent_fn = agent_fn
        self.output_fn = output_fn
        self._tools = {t.name: t for t in (tools or [])}
        self._loops: dict[str, asyncio.Task] = {}
        self._config: list[dict] = []
        self._status: dict[str, dict] = {}  # id → {last_run, last_result, next_run, running}

    @staticmethod
    def _trigger_type(cfg: dict) -> str:
        """P4.4 统一 trigger 字段解析。优先读 trigger.type，兼容旧 cron/delay_seconds 字段。"""
        trigger = cfg.get("trigger", {})
        if trigger:
            return trigger.get("type", "cron")
        # 向后兼容：无 trigger 字段时按旧格式判断
        if cfg.get("delay_seconds"):
            return "delay"
        if cfg.get("cron"):
            return "cron"
        return "cron"

    async def start(self):
        """从配置启动所有 enabled 任务。从后端 API 获取最新配置，避免直接 DB 访问冲突。"""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"http://localhost:8000/api/employees/{self.key}/scheduled-tasks")
                tasks = resp.json() if resp.status_code == 200 else []
        except Exception as e:
            log.warning("[%s] failed to fetch scheduled tasks: %s", self.key, e)
            tasks = []
        self._config = tasks
        for task_cfg in self._config:
            if not task_cfg.get("enabled"):
                continue
            task_id = task_cfg.get("id") or str(uuid.uuid4())[:8]
            task_cfg["id"] = task_id
            self._status[task_id] = {
                "name": task_cfg.get("name", task_id),
                "cron": task_cfg.get("cron", ""),
                "last_run": None,
                "last_result": None,
                "next_run": None,
                "running": False,
            }
            ttype = self._trigger_type(task_cfg)
            if ttype in ("event", "webhook"):
                # P4.4: 事件/webhook 触发任务不创建定时循环，等待外部触发
                log.info("[%s] registered %s-triggered task: %s", self.key, ttype, task_cfg.get("name"))
                continue
            loop = asyncio.create_task(self._cron_loop(task_id, task_cfg))
            self._loops[task_id] = loop
        if self._loops:
            log.info("[%s] scheduler started: %d timer tasks", self.key, len(self._loops))

    async def stop(self):
        """取消所有运行中的循环。"""
        for t in self._loops.values():
            t.cancel()
        self._loops.clear()
        log.info("[%s] scheduler stopped", self.key)

    async def reload(self):
        """配置变更时差量更新：删除取消、新增启动、关键字段变化（cron/trigger/prompt/output_to）取消重建。

        重建走 skip_catchup=True 路径，避免配置改动被误判为"漏执行"立刻补跑。
        """
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"http://localhost:8000/api/employees/{self.key}/scheduled-tasks")
                new_tasks = resp.json() if resp.status_code == 200 else []
        except Exception as e:
            log.warning("[%s] reload: failed to fetch tasks: %s", self.key, e)
            return

        new_map = {t["id"]: t for t in new_tasks if t.get("id") and t.get("enabled")}
        old_map = {t.get("id"): t for t in self._config if t.get("id")}
        old_ids = set(self._loops.keys())
        new_ids = set(new_map.keys())

        # 取消已删除或禁用的任务
        for tid in old_ids - new_ids:
            self._loops[tid].cancel()
            del self._loops[tid]
            self._status.pop(tid, None)
            log.info("[%s] scheduler removed task %s", self.key, tid)

        def _changed(old: dict, new: dict) -> bool:
            return (
                old.get("cron") != new.get("cron")
                or old.get("trigger") != new.get("trigger")
                or old.get("prompt") != new.get("prompt")
                or old.get("output_to") != new.get("output_to")
                or old.get("execution_mode") != new.get("execution_mode")
                or old.get("direct_actions") != new.get("direct_actions")
            )

        # 已存在但关键字段变了 → 取消旧 loop，让下面统一走重建分支
        for tid in old_ids & new_ids:
            if _changed(old_map.get(tid, {}), new_map[tid]):
                self._loops[tid].cancel()
                del self._loops[tid]
                log.info("[%s] scheduler restarting task %s (config changed)", self.key, tid)

        running_ids = set(self._loops.keys())

        # 启动需要重建的（含全新 + 配置变更）；纯未变更的跳过
        for tid, task_cfg in new_map.items():
            if tid in running_ids:
                continue
            self._status[tid] = {
                "name": task_cfg.get("name", tid),
                "cron": task_cfg.get("cron", ""),
                "last_run": None,
                "last_result": None,
                "next_run": None,
                "running": False,
            }
            ttype = self._trigger_type(task_cfg)
            if ttype in ("event", "webhook"):
                log.info("[%s] registered %s-triggered task: %s", self.key, ttype, task_cfg.get("name"))
                continue
            # reload 路径下重建的 loop 跳过 catch-up，避免改 cron 后立刻被判"漏执行"补跑
            loop = asyncio.create_task(self._cron_loop(tid, task_cfg, skip_catchup=True))
            self._loops[tid] = loop
            log.info("[%s] scheduler added/reloaded task %s", self.key, tid)

        # 同步 _config 为最新完整列表
        self._config = list(new_map.values())

        log.info("[%s] scheduler reloaded: %d active tasks", self.key, len(self._loops))

    def list_jobs(self) -> list[dict]:
        """返回所有任务当前状态。"""
        result = []
        for task_cfg in self._config:
            task_id = task_cfg.get("id", "")
            status = self._status.get(task_id, {})
            ttype = self._trigger_type(task_cfg)
            trigger = task_cfg.get("trigger", {})
            delay_s = trigger.get("delay_seconds") or task_cfg.get("delay_seconds")
            cron_val = trigger.get("cron") or task_cfg.get("cron", "")
            mode = task_cfg.get("execution_mode", "agent")

            if ttype == "event":
                cron_display = f"event:{trigger.get('event_type', '?')}"
            elif ttype == "webhook":
                cron_display = f"webhook:{trigger.get('webhook_token', '?')[:8]}"
            elif delay_s:
                cron_display = f"一次性延时 {delay_s // 60}分钟"
            else:
                cron_display = cron_val

            result.append({
                "id": task_id,
                "name": task_cfg.get("name", ""),
                "trigger_type": ttype,
                "cron": cron_display,
                "output_to": task_cfg.get("output_to", "log"),
                "execution_mode": mode,
                "enabled": task_cfg.get("enabled", False),
                "prompt": task_cfg.get("prompt", "")[:100],
                **status,
            })
        return result

    async def run_once(self, task_id: str) -> str | None:
        """立即执行一次指定任务，返回结果。"""
        task_cfg = next((t for t in self._config if t.get("id") == task_id), None)
        if not task_cfg:
            return None
        return await self._execute(task_id, task_cfg)

    # ── 内部 ────────────────────────────────────────────────────────────────

    async def _cron_loop(self, task_id: str, cfg: dict, skip_catchup: bool = False):
        """单个任务的 cron 循环。支持 once=true 的一次性任务。P4.4: 兼容新 trigger 字段。

        skip_catchup=True：reload 路径用，跳过"上次漏执行"补跑，避免改 cron 后立刻被触发。
        """
        trigger = cfg.get("trigger", {})
        # 优先读 trigger.cron / trigger.delay_seconds，兼容旧顶层字段
        cron_expr = trigger.get("cron") or cfg.get("cron", "0 * * * *")
        initial_delay = cfg.get("initial_delay", 10)
        once = cfg.get("once", False)
        # 一次性延时任务
        delay_seconds = trigger.get("delay_seconds") or cfg.get("delay_seconds")

        await asyncio.sleep(initial_delay)

        try:
            if delay_seconds:
                # 纯延时模式：优先用 fire_at（绝对时间）计算剩余，避免 reload 重置倒计时
                fire_at_str = cfg.get("fire_at")
                if fire_at_str:
                    try:
                        fire_at = datetime.fromisoformat(fire_at_str)
                        remaining = (fire_at - datetime.now(timezone.utc)).total_seconds()
                    except Exception:
                        remaining = delay_seconds
                else:
                    remaining = delay_seconds

                # Bug fix: 一次性任务且 fire_at 已过，且 last_run_at 有值说明已执行过
                # 跳过重复执行，直接清理，避免每次重启重复触发
                last_run_at_str = cfg.get("last_run_at")
                if once and remaining < 0 and last_run_at_str:
                    log.info(
                        "[%s] one-shot task %s: already executed at %s, skipping re-execution",
                        self.key, task_id, last_run_at_str[:19],
                    )
                    await self._auto_delete(task_id)
                    return

                self._status[task_id]["next_run"] = (
                    datetime.now(timezone.utc) + __import__('datetime').timedelta(seconds=max(remaining, 0))
                ).isoformat()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                await self._execute(task_id, cfg)
                if once:
                    await self._auto_delete(task_id)
                return

            # P1.2 补跑：重启后检查是否有漏执行的 cron 触发（保守策略，只补最近一次）
            # reload 路径不走这里（skip_catchup=True），改 cron 不会被误判为漏执行
            _CATCHUP_TOLERANCE_S = 60  # 容忍窗口：60s 内的漏执行不补（避免重启后瞬间触发）
            last_run_at_str = cfg.get("last_run_at")
            if not skip_catchup and last_run_at_str and cron_expr:
                try:
                    last_run_at = datetime.fromisoformat(last_run_at_str)
                    # 将 last_run_at 转为 naive（croniter 使用 naive datetime）
                    if last_run_at.tzinfo is not None:
                        last_run_at = last_run_at.replace(tzinfo=None)
                    # 计算上一个应触发时刻
                    prev_cron = croniter(cron_expr, datetime.now())
                    prev_trigger = prev_cron.get_prev(datetime)
                    # 若上次执行早于上一个触发时刻超过容忍窗口，立即补跑
                    gap_s = (prev_trigger - last_run_at).total_seconds()
                    if gap_s > _CATCHUP_TOLERANCE_S:
                        log.info(
                            "[%s] catch-up: task %s missed trigger at %s (last_run=%s, gap=%.0fs)",
                            self.key, task_id,
                            prev_trigger.strftime("%m-%d %H:%M"),
                            last_run_at.strftime("%m-%d %H:%M"),
                            gap_s,
                        )
                        await self._execute(task_id, cfg)
                        # Bug fix: once=True 的 cron 任务补跑后立即删除，不能 fall-through 到 while 再触发一次
                        if once:
                            await self._auto_delete(task_id)
                            return
                except Exception as e:
                    log.warning("[%s] catch-up check failed for %s: %s", self.key, task_id, e)

            while True:
                now = datetime.now()
                cron = croniter(cron_expr, now)
                next_time = cron.get_next(datetime)
                self._status[task_id]["next_run"] = next_time.isoformat()

                delay = (next_time - now).total_seconds()
                if delay > 0:
                    await asyncio.sleep(delay)

                await self._execute(task_id, cfg)

                if once:
                    await self._auto_delete(task_id)
                    return
        except asyncio.CancelledError:
            raise  # 正确传播取消，避免 "Task was destroyed but pending"
        except Exception as e:
            log.error("[%s] cron loop error for %s: %s", self.key, task_id, e)

    async def _auto_delete(self, task_id: str):
        """一次性任务执行完后自动从 DB 删除。"""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                await client.delete(
                    f"http://localhost:8000/api/employees/{self.key}/scheduled-tasks/{task_id}"
                )
            log.info("[%s] one-shot task %s auto-deleted", self.key, task_id)
        except Exception as e:
            log.warning("[%s] auto-delete failed for %s: %s", self.key, task_id, e)

    async def _persist_run_status(self, task_id: str, status: str) -> None:
        """将 last_run_at / last_run_status 持久化到 DB（P1.1）。

        异步 fire-and-forget，失败仅记日志，不阻塞任务循环。
        注意：PATCH 会触发 PG NOTIFY → reload()，但 diff reload 不会打扰已有任务。
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                await client.patch(
                    f"http://localhost:8000/api/employees/{self.key}/scheduled-tasks/{task_id}",
                    json={"last_run_at": now_iso, "last_run_status": status},
                )
        except Exception as e:
            log.warning("[%s] persist run status failed for %s: %s", self.key, task_id, e)

    async def _insert_task_run(
        self,
        task_id: str,
        cfg: dict,
        triggered_at: datetime,
        finished_at: datetime,
        status: str,
        result_text: str,
    ) -> None:
        """向 agent_task_runs 表插入一条执行记录（P1.4）。Fire-and-forget。"""
        duration_ms = int((finished_at - triggered_at).total_seconds() * 1000)
        try:
            import psycopg
            from backend.core.config import settings
            dsn = settings.database_url_sync.replace("postgresql+psycopg://", "postgresql://")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: _sync_insert_run(
                dsn, self.key, task_id,
                cfg.get("name", task_id),
                triggered_at, finished_at, duration_ms,
                status,
                result_text[:2000] if result_text else "",
                cfg.get("output_to", ""),
                cfg.get("execution_mode", "agent"),
            ))
        except Exception as e:
            log.warning("[%s] insert task run failed for %s: %s", self.key, task_id, e)

    async def _publish_task_event(
        self, task_id: str, cfg: dict, event_type: str, status: str
    ) -> None:
        """P4.3 任务完成后向 backend 发布事件，触发订阅该事件的其他员工任务。"""
        try:
            import httpx
            payload = {
                "event_type": event_type,          # task_completed / task_failed
                "employee": self.key,
                "task_id": task_id,
                "task_name": cfg.get("name", task_id),
                "status": status,
            }
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(
                    "http://localhost:8000/api/scheduler/events",
                    json=payload,
                )
        except Exception as e:
            log.debug("[%s] publish task event failed: %s", self.key, e)

    async def fire_event(self, event: dict) -> str | None:
        """P4.3 接收来自 backend 的事件，立即执行匹配的 event-triggered 任务。

        event 格式:
          {"event_type": "task_completed", "employee": "data_engineer",
           "task_id": "etl_daily", "status": "success", ...}
        """
        triggered = []
        for cfg in self._config:
            trigger = cfg.get("trigger", {})
            if trigger.get("type") != "event":
                continue
            if not cfg.get("enabled"):
                continue
            # 检查 event_type 匹配
            if trigger.get("event_type") != event.get("event_type"):
                continue
            # 检查 filter KV 全部匹配
            flt = trigger.get("filter", {})
            if any(event.get(k) != v for k, v in flt.items()):
                continue
            task_id = cfg.get("id", "")
            log.info(
                "[%s] event trigger matched: task=%s, event=%s",
                self.key, cfg.get("name"), event.get("event_type"),
            )
            result = await self._execute(task_id, cfg)
            triggered.append(task_id)

        return f"triggered {len(triggered)} tasks: {triggered}" if triggered else None

    async def _execute(self, task_id: str, cfg: dict) -> str:
        """执行入口：按 execution_mode 分发，完成后持久化状态和执行记录。"""
        mode = cfg.get("execution_mode", "agent")
        task_name = cfg.get("name", task_id)
        triggered_at = datetime.now(timezone.utc)

        self._status[task_id]["running"] = True
        log.info("[%s] executing task: %s (mode=%s)", self.key, task_name, mode)

        run_status = "success"
        result = ""
        try:
            if mode == "direct":
                result = await self._execute_direct(task_id, cfg)
            elif mode == "webhook":
                result = await self._execute_webhook(task_id, cfg)
            else:
                result = await self._execute_agent(task_id, cfg)

            if result.startswith("ERROR:") or "超时" in result:
                run_status = "timeout" if "超时" in result else "error"

            self._status[task_id]["last_run"] = datetime.now(timezone.utc).isoformat()
            self._status[task_id]["last_result"] = result[:500] if result else ""
            self._status[task_id]["running"] = False
            log.info("[%s] task %s done (mode=%s, status=%s)", self.key, task_name, mode, run_status)

        except Exception as e:
            run_status = "error"
            result = f"ERROR: {e}"
            self._status[task_id]["running"] = False
            self._status[task_id]["last_result"] = result
            log.error("[%s] task %s failed: %s", self.key, task_name, e)

        finished_at = datetime.now(timezone.utc)
        # P1.1 持久化 last_run_at 到 DB（异步，不阻塞循环）
        asyncio.create_task(self._persist_run_status(task_id, run_status))
        # P1.4 写执行历史记录（异步，表不存在时自动降级）
        asyncio.create_task(self._insert_task_run(
            task_id, cfg, triggered_at, finished_at, run_status, result
        ))
        # P4.3 发布任务完成事件，供跨员工 DAG 依赖使用
        event_type = "task_completed" if run_status == "success" else "task_failed"
        asyncio.create_task(self._publish_task_event(task_id, cfg, event_type, run_status))
        return result

    # P4.2: 用于检测 agent 是否确实发送了消息
    _SEND_SUCCESS_PATTERNS = re.compile(
        r"已成功发送|发送成功|sent successfully|send.*success", re.IGNORECASE
    )

    async def _execute_agent(self, task_id: str, cfg: dict) -> str:
        """agent 模式：走 LLM SmartGraph，agent 自主思考、润色、调工具发送。

        timeout_seconds（默认 300）控制最长等待时间，0 表示不限制。

        P4.2 output_fallback / output_required：
          - output_fallback: 主渠道失败时的备用渠道提示（注入 prompt）
          - output_required=true: agent 执行后检查是否发送，未发则 scheduler 兜底
        """
        prompt = cfg.get("prompt", "")
        output_to = cfg.get("output_to", "log")
        output_fallback = cfg.get("output_fallback", "")
        output_required = cfg.get("output_required", False)
        task_name = cfg.get("name", task_id)
        timeout_s = cfg.get("timeout_seconds", 300)
        feishu_chat_id = cfg.get("feishu_chat_id", "")

        _SEND_HINT = {
            "feishu":     "send_feishu_message",
            "group_chat": "send_group_chat_message",
            "kanban":     "send_group_chat_message",
            "log":        "",
        }
        primary_tool = _SEND_HINT.get(output_to, "send_feishu_message 或 send_group_chat_message")
        fallback_tool = _SEND_HINT.get(output_fallback, "") if output_fallback else ""

        fallback_hint = (
            f"；{primary_tool} 失败时自动切换到 {fallback_tool}"
            if fallback_tool else
            "；发送失败请重试或换备用渠道"
        )

        send_line = (
            f"【推送目标】{output_to} — 请用 {primary_tool} 发送你的内容{fallback_hint}"
            if primary_tool else
            "【推送目标】log — 直接输出内容即可，无需调用发送工具"
        )

        chat_hint = (
            f"【飞书回复目标】feishu_chat_id={feishu_chat_id}（调用 send_feishu_message 时必须传入此 feishu_chat_id 参数）\n"
            if feishu_chat_id and output_to == "feishu" else ""
        )
        full_prompt = (
            f"【定时任务触发】{task_name}\n"
            f"{send_line}\n"
            f"{chat_hint}"
            f"【注意】用你的性格和角色润色内容后再发送\n\n"
            f"{prompt}"
        )

        context = {
            "task_id": f"sched_{task_id}_{int(datetime.now(timezone.utc).timestamp())}",
            "chat_id": feishu_chat_id,
            "session_config": {"source": "scheduler"},
        }

        coro = self.agent_fn(full_prompt, context)
        if timeout_s and timeout_s > 0:
            try:
                result = await asyncio.wait_for(coro, timeout=timeout_s)
            except asyncio.TimeoutError:
                result = f"任务超时（>{timeout_s}s），已中止"
                log.warning("[%s] task %s timed out after %ss", self.key, task_name, timeout_s)
        else:
            result = await coro

        # P4.2 output_required 兜底：若 agent 未发送，scheduler 直接补发
        if output_required and output_to != "log":
            sent = self._SEND_SUCCESS_PATTERNS.search(result or "")
            if not sent:
                fallback_channel = output_fallback or output_to
                log.warning(
                    "[%s] task %s: output_required=true but no send detected, fallback → %s",
                    self.key, task_name, fallback_channel,
                )
                await self.output_fn(fallback_channel, task_name, result or "（无内容）", feishu_chat_id=feishu_chat_id)

        await self.output_fn("log", task_name, result)
        return result

    async def _execute_direct(self, task_id: str, cfg: dict) -> str:
        """direct 模式：跳过 LLM，按顺序直接调工具，支持 {{tool.result}} 插值。

        配置示例：
            "execution_mode": "direct",
            "direct_actions": [
                {"tool": "get_metrics", "args": {}},
                {"tool": "send_feishu_message",
                 "args": {"content": "{{get_metrics.result}}", "title": "系统指标"}}
            ]
        """
        actions = cfg.get("direct_actions", [])
        if not actions:
            return "direct 模式缺少 direct_actions 配置"

        if not self._tools:
            return "direct 模式无可用工具（scheduler 未注入 tools）"

        ctx: dict[str, dict] = {}  # {tool_name: {result: str}}
        lines = []

        for action in actions:
            tool_name = action.get("tool", "")
            tool_fn = self._tools.get(tool_name)
            if not tool_fn:
                lines.append(f"[跳过] 未知工具: {tool_name}")
                log.warning("[%s] direct mode: unknown tool '%s'", self.key, tool_name)
                continue

            args = _interpolate(action.get("args", {}), ctx)
            try:
                # 工具是同步函数，在线程池中运行避免阻塞事件循环
                loop = asyncio.get_event_loop()
                raw = await loop.run_in_executor(None, lambda fn=tool_fn, a=args: fn.invoke(a))
                result_str = str(raw)
                ctx[tool_name] = {"result": result_str}
                lines.append(f"[{tool_name}] {result_str[:200]}")
                log.info("[%s] direct action %s ok", self.key, tool_name)
            except Exception as e:
                lines.append(f"[{tool_name}] 失败: {e}")
                log.error("[%s] direct action %s failed: %s", self.key, tool_name, e)

        return "\n".join(lines)

    async def _execute_webhook(self, task_id: str, cfg: dict) -> str:
        """webhook 模式：HTTP POST（或任意方法）到外部 URL，不经过 LLM。

        配置示例：
            "execution_mode": "webhook",
            "webhook_url": "https://ci.example.com/trigger/build",
            "webhook_method": "POST",
            "webhook_headers": {"Authorization": "Bearer xxx"},
            "webhook_body": {"ref": "main"}
        """
        import httpx

        url = cfg.get("webhook_url", "")
        if not url:
            return "webhook 模式缺少 webhook_url 配置"

        method = cfg.get("webhook_method", "POST").upper()
        headers = cfg.get("webhook_headers", {})
        body = cfg.get("webhook_body", {})

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.request(method, url, json=body, headers=headers)
            result = f"webhook {method} {url} → HTTP {resp.status_code}"
            log.info("[%s] %s", self.key, result)
            return result
        except Exception as e:
            return f"webhook 请求失败: {e}"

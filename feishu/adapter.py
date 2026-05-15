"""飞书平台适配器。

封装 employee_bot 的 group listener 逻辑，实现 PlatformAdapter 接口。
每个员工 bot 在独立的 daemon 线程中运行自己的 asyncio 事件循环。
"""
from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING

from group_chat.platform import PlatformAdapter

if TYPE_CHECKING:
    from group_chat.event_bus import GroupEventBusPool

log = logging.getLogger(__name__)


class FeishuAdapter(PlatformAdapter):
    """飞书平台适配器。

    启动后，每个员工 bot 在独立线程里监听：
      - 飞书 WebSocket（接收用户消息 → publish group_msg:{chat_id}）
      - Redis speak_req（收到发言请求 → 调用飞书 SDK 回复 → publish speak_resp）
    """

    platform_id = "feishu"

    def __init__(self, employees: list[str] | None = None):
        """
        employees: 要启动的员工 key 列表；None 表示全量启动（从 EMPLOYEE_CONFIG 读取）。
        """
        self._employees = employees
        self._threads: list[threading.Thread] = []

    async def start(self, bus_pool: "GroupEventBusPool") -> None:
        from group_chat.models import EMPLOYEE_CONFIG
        from feishu.employee_bot import _make_client, _get_bot_open_id, _start_group_listener
        import lark_oapi as lark
        from feishu.employee_bot import make_on_message

        employees = self._employees or list(EMPLOYEE_CONFIG.keys())

        for employee in employees:
            env_prefix = employee.upper()
            app_id     = os.getenv(f"{env_prefix}_APP_ID", "")
            app_secret = os.getenv(f"{env_prefix}_APP_SECRET", "")
            if not app_id or not app_secret:
                log.warning("FeishuAdapter: skipping %s — no APP_ID/APP_SECRET", employee)
                continue

            client = _make_client(app_id, app_secret)
            bot_open_id = _get_bot_open_id(app_id, app_secret)

            # 群聊 SpeakRequest 监听线程（包含进出两个方向的 Redis 交互）
            t = threading.Thread(
                target=_start_group_listener,
                args=(employee, client, app_id, app_secret),
                daemon=True,
                name=f"feishu-group-{employee}",
            )
            t.start()
            self._threads.append(t)

            # 飞书 WebSocket 消息接收线程
            handler = (
                lark.EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(
                    make_on_message(employee, client, bot_open_id)
                )
                .register_p2_im_chat_member_bot_deleted_v1(lambda _: None)
                .register_p2_im_message_reaction_created_v1(lambda _: None)
                .register_p2_im_message_reaction_deleted_v1(lambda _: None)
                .build()
            )
            ws_client = lark.ws.Client(
                app_id=app_id,
                app_secret=app_secret,
                event_handler=handler,
            )
            wt = threading.Thread(
                target=ws_client.start,
                daemon=True,
                name=f"feishu-ws-{employee}",
            )
            wt.start()
            self._threads.append(wt)
            log.info("FeishuAdapter: started %s (group_listener + ws)", employee)

    async def stop(self) -> None:
        # daemon 线程随主进程退出，无需显式 cancel
        # 如需优雅关闭可在此实现 shutdown event
        self._threads.clear()
        log.info("FeishuAdapter: stopped")

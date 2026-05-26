"""提案 4 §2.3 — messaging MCP server(飞书消息相关 6 个工具)。

工具列表:
- send_feishu_message    富文本卡片(优先 reply 到 trigger_message_id)
- reply_feishu_short     纯文本短回复(挂在原消息 thread)
- react_emoji            飞书 emoji 反应
- send_group_chat_message 看板群聊文字
- send_feishu_image      上传本地图片
- send_feishu_file       上传本地文件 / 视频(短视频走 media)

启动:
    EMPLOYEE_KEY=mechanical TASK_ID=... \
    python -m mcp_servers.messaging.server

环境变量(透传给业务模块,语义保持兼容老 company_tools/server.py):
    EMPLOYEE_KEY                 必填
    EMPLOYEE_CHAT_ID             默认 chat_id 兜底
    EMPLOYEE_TRIGGER_MESSAGE_ID  用户原消息 id(走 reply 时必需)
    FEISHU_CHAT_ID               infra/.env 兜底
"""

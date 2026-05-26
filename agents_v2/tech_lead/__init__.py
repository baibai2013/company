"""提案 4 §3 阶段二 — TechLead Supervisor 包。

本包提供:
  - supervisor.build_supervisor_graph: 5 节点 LangGraph
  - routing.choose_employee: 任务关键词 → 候选员工的规则路由 stub

阶段三(把 8 员工拆成 8 个 A2A server)留 Wave 4,本包不起进程、不接
真 SSE,只做"路由 + 派活 + 触发 verifier"的最小可行 supervisor。
"""

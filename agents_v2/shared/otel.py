"""OpenTelemetry tracer 初始化(agent 进程侧)。

设计要点:
  * **不**强制启动 OTel exporter:若环境变量 ``OTEL_EXPORTER_OTLP_ENDPOINT``
    不存在,tracer 落 NoOp,函数全部正常工作。
  * 三层 span 命名规范(提案 4 §5.1):

    .. code-block:: text

        task.<task_id>                  顶层 task
          employee.<key>                员工 graph 执行
            llm.call                    单次 claude code 调用
            tool.<server>.<name>        MCP 工具调用
            rag.retrieve                KB / lessons 检索
            verifier.<gate>             提案 2 三闸

  * 提供方便函数,业务代码不用直接碰 ``opentelemetry.trace`` API。

实现策略:
  核心实现统一放在 :mod:`backend.core.otel`,本模块只做两件事:

  1. 重导出所有 helper(``task_span`` / ``employee_span`` / ...)。
  2. 用一层薄 wrapper 把 ``init_tracer`` 的默认 ``service_name``
     从 ``"company-backend"`` 改为 ``"company-agents"``。

  方向是单向的:``agents_v2.shared.otel`` → ``backend.core.otel``,
  反向 ``backend.core.otel`` 不引用 ``agents_v2``,所以不存在依赖循环。
"""

from __future__ import annotations

from typing import Any

from backend.core.otel import (
    _reset_for_tests as _reset_for_tests,  # 仅供测试
    employee_span as employee_span,
    get_tracer as get_tracer,
    init_tracer as _init_tracer_impl,
    llm_call_span as llm_call_span,
    rag_span as rag_span,
    task_span as task_span,
    tool_call_span as tool_call_span,
    verifier_span as verifier_span,
)


def init_tracer(
    service_name: str = "company-agents",
    otlp_endpoint: str | None = None,
    span_processors: list[Any] | None = None,
) -> None:
    """幂等初始化(agent 进程默认 service_name="company-agents")。

    其余语义与 :func:`backend.core.otel.init_tracer` 完全一致,
    详见该函数 docstring。
    """
    _init_tracer_impl(
        service_name=service_name,
        otlp_endpoint=otlp_endpoint,
        span_processors=span_processors,
    )


__all__ = [
    "init_tracer",
    "get_tracer",
    "task_span",
    "employee_span",
    "llm_call_span",
    "tool_call_span",
    "rag_span",
    "verifier_span",
]

"""OpenTelemetry tracer(提案 4 §5.1)单元测试。

只验证内部行为,**不**真连 OTLP collector。所有 span 通过 ``InMemorySpanExporter``
留在内存里,断言其 name / parent / attributes。
"""

from __future__ import annotations

import pytest

# 缺包时整个文件 skip,而不是让 collect 失败 —
# 这样在精简环境(只装基础 deps)里跑别的测试也不会拖崩。
pytest.importorskip("opentelemetry.sdk")

from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from backend.core import otel as otel_backend
from agents_v2.shared import otel as otel_agents


@pytest.fixture
def exporter():
    """每个 test 一个干净的 InMemorySpanExporter + 重置 tracer 全局状态。"""
    otel_backend._reset_for_tests()
    exp = InMemorySpanExporter()
    otel_backend.init_tracer(
        service_name="company-test",
        span_processors=[SimpleSpanProcessor(exp)],
    )
    yield exp
    otel_backend._reset_for_tests()


# ---------------------------------------------------------------------------
# init_tracer 幂等性
# ---------------------------------------------------------------------------


def test_init_tracer_is_idempotent():
    """重复调用 init_tracer 不应抛错,也不会替换已装好的 provider。"""
    otel_backend._reset_for_tests()
    exp = InMemorySpanExporter()
    otel_backend.init_tracer(
        service_name="company-test",
        span_processors=[SimpleSpanProcessor(exp)],
    )
    tracer1 = otel_backend.get_tracer()

    # 再调一次:应该被忽略,tracer 单例不变
    otel_backend.init_tracer(
        service_name="company-test-other",
        span_processors=[SimpleSpanProcessor(InMemorySpanExporter())],
    )
    tracer2 = otel_backend.get_tracer()

    assert tracer1 is tracer2, "init_tracer 必须幂等,不能在第二次调用时替换 tracer"

    # 在第一次装好的 exporter 里能看到 span,说明真的复用了第一次的 provider
    with otel_backend.task_span("idempotent-check"):
        pass
    spans = exp.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "task.idempotent-check"

    otel_backend._reset_for_tests()


def test_init_tracer_without_endpoint_is_noop():
    """既不给 endpoint 也不给 span_processors 时,tracer 应退化为 NoOp。"""
    otel_backend._reset_for_tests()
    otel_backend.init_tracer(service_name="company-test-noop")
    tracer = otel_backend.get_tracer()

    # NoOp tracer:start_as_current_span 仍可用,但不 record 到任何 exporter
    with otel_backend.task_span("noop-task") as span:
        # NoOp span 也允许 set_attribute,不应抛错
        span.set_attribute("foo", "bar")

    # 类型检查:opentelemetry 自带的 NoOpTracer
    from opentelemetry.trace import NoOpTracer

    assert isinstance(tracer, NoOpTracer)
    otel_backend._reset_for_tests()


# ---------------------------------------------------------------------------
# 单层 span:name + 顶层无 parent
# ---------------------------------------------------------------------------


def test_task_span_creates_one_span_with_correct_name(exporter):
    with otel_backend.task_span("t1"):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "task.t1"
    assert span.parent is None, "顶层 task span 不应有 parent"
    # 属性 task.id 应被 helper 写进去
    assert span.attributes.get("task.id") == "t1"


# ---------------------------------------------------------------------------
# 嵌套 span:三层 parent 链
# ---------------------------------------------------------------------------


def test_nested_task_employee_llm_spans_have_parent_chain(exporter):
    """task → employee → llm.call 三层 span,父子链路应该正确连起来。"""
    with otel_backend.task_span("t1"):
        with otel_backend.employee_span("mechanical"):
            with otel_backend.llm_call_span("claude-opus-4-7"):
                pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 3, "应有 task / employee / llm.call 共 3 个 span"

    # finished_spans 顺序:**最里层先结束**,所以是 llm.call → employee → task
    by_name = {s.name: s for s in spans}
    assert set(by_name.keys()) == {"task.t1", "employee.mechanical", "llm.call"}

    task = by_name["task.t1"]
    emp = by_name["employee.mechanical"]
    llm = by_name["llm.call"]

    # 所有 span 必须共享同一个 trace_id
    assert task.context.trace_id == emp.context.trace_id == llm.context.trace_id

    # parent 链:llm.parent == employee, employee.parent == task, task.parent == None
    assert task.parent is None
    assert emp.parent is not None and emp.parent.span_id == task.context.span_id
    assert llm.parent is not None and llm.parent.span_id == emp.context.span_id


# ---------------------------------------------------------------------------
# 各 helper 的属性写入
# ---------------------------------------------------------------------------


def test_llm_call_span_records_model_attribute(exporter):
    with otel_backend.llm_call_span("claude-opus-4-7", **{"llm.input_tokens": 1234}):
        pass

    span = exporter.get_finished_spans()[0]
    assert span.name == "llm.call"
    assert span.attributes.get("llm.model") == "claude-opus-4-7"
    # 透传的 kwargs 也应进入属性
    assert span.attributes.get("llm.input_tokens") == 1234


def test_tool_call_span_attributes(exporter):
    with otel_backend.tool_call_span("company_tools", "post_message"):
        pass

    span = exporter.get_finished_spans()[0]
    assert span.name == "tool.company_tools.post_message"
    assert span.attributes.get("tool.server") == "company_tools"
    assert span.attributes.get("tool.name") == "post_message"


def test_rag_span_kind_attribute(exporter):
    with otel_backend.rag_span("lessons", **{"rag.query": "wheel"}):
        pass

    span = exporter.get_finished_spans()[0]
    assert span.name == "rag.retrieve"
    assert span.attributes.get("rag.kind") == "lessons"
    assert span.attributes.get("rag.query") == "wheel"


def test_verifier_span_gate_attribute(exporter):
    with otel_backend.verifier_span("static"):
        pass

    span = exporter.get_finished_spans()[0]
    assert span.name == "verifier.static"
    assert span.attributes.get("verifier.gate") == "static"


def test_employee_span_key_attribute(exporter):
    with otel_backend.employee_span("firmware"):
        pass

    span = exporter.get_finished_spans()[0]
    assert span.name == "employee.firmware"
    assert span.attributes.get("employee.key") == "firmware"


# ---------------------------------------------------------------------------
# agents_v2.shared.otel 与 backend.core.otel 共享同一份 tracer
# ---------------------------------------------------------------------------


def test_agents_otel_reexports_share_state(exporter):
    """agents_v2 侧的 helper 应与 backend 侧产生同一棵 trace 树。"""
    with otel_agents.task_span("shared-t"):
        with otel_agents.employee_span("algorithm"):
            with otel_backend.llm_call_span("claude-opus-4-7"):
                pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 3
    trace_ids = {s.context.trace_id for s in spans}
    assert len(trace_ids) == 1, "agents 与 backend 的 helper 必须共用同一 tracer 实例"

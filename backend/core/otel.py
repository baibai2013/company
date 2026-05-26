"""OpenTelemetry tracer 初始化(backend 进程侧,核心实现)。

本模块同时被 ``agents_v2/shared/otel.py`` 复用。两者只有 service_name 默认值不同,
其它 API 完全一致。

设计要点:
  * **不强制启动 exporter**:若环境变量 ``OTEL_EXPORTER_OTLP_ENDPOINT`` 不存在
    且调用方也没显式传 ``otlp_endpoint`` / ``span_processors``,
    则 tracer 退化为 NoOp(opentelemetry 自带),helper 函数仍然返回可用的
    ``contextmanager``,不会抛错也不会拖慢业务。
  * **依赖软引入**:opentelemetry-* 系列包若未安装,本模块仍能 import,
    init_tracer 落 NoOp + log warning,helper 用 ``contextlib.nullcontext``。
    这样 sub-agent 不需要去抢 pyproject.toml 的写权限,主进程可以稍后统一加依赖。
  * **三层 span 命名规范**(提案 4 §5.1):

    .. code-block:: text

        task.<task_id>                  顶层 task
          employee.<key>                员工 graph 执行
            llm.call                    单次 claude code 调用
            tool.<server>.<name>        MCP 工具调用
            rag.retrieve                KB / lessons 检索
            verifier.<gate>             提案 2 三闸

  * **幂等**:重复调用 ``init_tracer`` 安全;在测试中可通过
    ``_reset_for_tests`` 强制重置,允许换 in-memory exporter。

测试钩子:
  调用 ``init_tracer(span_processors=[...])`` 可以注入任意 ``SpanProcessor``。
  典型用法是在测试里塞 ``SimpleSpanProcessor(InMemorySpanExporter())``,
  这样不需要真连 OTLP collector 就能验证 span 链路。
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator

log = logging.getLogger("company.otel")

# ----------------------------------------------------------------------------
# 软依赖:opentelemetry 整套若未装,模块仍可 import,helper 全部走 nullcontext。
# ----------------------------------------------------------------------------
try:
    from opentelemetry import trace as _otel_trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanProcessor

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - 软降级路径
    _otel_trace = None  # type: ignore[assignment]
    Resource = None  # type: ignore[assignment,misc]
    TracerProvider = None  # type: ignore[assignment,misc]
    BatchSpanProcessor = None  # type: ignore[assignment,misc]
    SpanProcessor = None  # type: ignore[assignment,misc]
    _OTEL_AVAILABLE = False


# ----------------------------------------------------------------------------
# 全局状态:tracer 单例 + 一次性初始化锁
# ----------------------------------------------------------------------------
_lock = threading.Lock()
_initialized: bool = False
_tracer: Any = None  # 真 tracer 或 NoOpTracer
_provider: Any = None  # 真 TracerProvider 或 None(NoOp 模式不创建)


def _build_noop_tracer() -> Any:
    """构造一个 NoOp tracer。

    opentelemetry 装好时直接用 ``trace.NoOpTracer``;
    包没装时返回一个简易对象,其 ``start_as_current_span`` 方法
    返回 ``contextlib.nullcontext`` 兼容的对象。
    """
    if _OTEL_AVAILABLE:
        return _otel_trace.NoOpTracer()

    class _NoOpSpan:
        def set_attribute(self, *_a: Any, **_k: Any) -> None:
            pass

        def set_attributes(self, *_a: Any, **_k: Any) -> None:
            pass

        def record_exception(self, *_a: Any, **_k: Any) -> None:
            pass

        def __enter__(self) -> "_NoOpSpan":
            return self

        def __exit__(self, *exc_info: Any) -> None:
            return None

    class _NoOpTracer:
        def start_as_current_span(self, *_a: Any, **_k: Any) -> _NoOpSpan:
            return _NoOpSpan()

    return _NoOpTracer()


def init_tracer(
    service_name: str = "company-backend",
    otlp_endpoint: str | None = None,
    span_processors: list[Any] | None = None,
) -> None:
    """幂等初始化 tracer。

    参数:
        service_name: 服务名(填到 OTel resource 的 ``service.name`` 属性)。
            backend 进程默认 ``"company-backend"``;
            agents_v2 进程默认 ``"company-agents"``。
        otlp_endpoint: OTLP gRPC endpoint;若为 ``None`` 则读环境变量
            ``OTEL_EXPORTER_OTLP_ENDPOINT``;再 None 则不启 OTLP exporter。
        span_processors: 额外注入的 ``SpanProcessor`` 列表,**主要给测试用**
            (例如 ``SimpleSpanProcessor(InMemorySpanExporter())``)。
            生产环境一般不传。

    行为:
        - 若 opentelemetry-* 未装 → log.warning,tracer 落 NoOp,直接返回。
        - 若既无 ``otlp_endpoint`` 又无 ``span_processors`` → 退化为 NoOp。
        - 否则:创建 ``TracerProvider``、装 resource、依次装上 OTLP exporter
          (如有)和外部 processors(如有),然后 ``set_tracer_provider``。
        - 重复调用安全:第一次的 provider/tracer 会被复用。
    """
    global _initialized, _tracer, _provider

    with _lock:
        if _initialized:
            return

        if not _OTEL_AVAILABLE:
            log.warning(
                "opentelemetry SDK 未安装,tracer 走 NoOp;装包后 init_tracer 会自动启用真实 exporter"
            )
            _tracer = _build_noop_tracer()
            _initialized = True
            return

        # 解析 endpoint:显式参数 > 环境变量 > None
        endpoint = otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        has_external_processors = bool(span_processors)

        # 都没有 → NoOp,既不创建 provider 也不污染全局
        if not endpoint and not has_external_processors:
            _tracer = _build_noop_tracer()
            _initialized = True
            return

        # 真实 provider:装 resource(service.name 等)
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        # OTLP exporter(可选)— 用 BatchSpanProcessor 异步导出,生产推荐
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )

                exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
                provider.add_span_processor(BatchSpanProcessor(exporter))
                log.info("OTel OTLP exporter 已启用,endpoint=%s", endpoint)
            except ImportError:
                log.warning(
                    "opentelemetry-exporter-otlp 未装,跳过 OTLP exporter(其它 processor 仍生效)"
                )

        # 外部 processors(典型场景:测试里的 InMemorySpanExporter)
        for proc in span_processors or []:
            provider.add_span_processor(proc)

        # 注:OTel 的 ``trace.set_tracer_provider`` 全局只允许设一次,
        # 重复设会被静默忽略(只 log 一条 warning)。我们仍然尝试设一次,
        # 让 langchain_anthropic 等三方库的 ``trace.get_tracer()`` 能拿到
        # 同一个 provider;但**我们自己的 ``_tracer`` 直接从 provider 取**,
        # 不依赖全局,这样测试里 ``_reset_for_tests`` + 重新 ``init_tracer``
        # 也能拿到新 provider 的 tracer。
        try:
            _otel_trace.set_tracer_provider(provider)
        except Exception:  # pragma: no cover - 防御性
            log.debug("set_tracer_provider 失败(可能已被设过)", exc_info=True)
        _provider = provider
        _tracer = provider.get_tracer(service_name)
        _initialized = True
        log.info("OTel tracer 初始化完成,service=%s", service_name)


def get_tracer() -> Any:
    """返回 tracer 单例。

    若 ``init_tracer`` 未调用过,自动用默认参数惰性初始化(典型走 NoOp)。
    """
    if not _initialized:
        init_tracer()
    return _tracer


def _reset_for_tests() -> None:
    """**仅供测试**:重置全局状态,以便下一次 ``init_tracer`` 重新配置。

    会顺手把 provider 上的 span processors 全部 shutdown,避免泄漏后台线程。
    """
    global _initialized, _tracer, _provider
    with _lock:
        if _provider is not None:
            try:
                _provider.shutdown()
            except Exception:  # pragma: no cover - shutdown 异常不应影响测试
                log.exception("provider.shutdown 失败")
        _initialized = False
        _tracer = None
        _provider = None


# ----------------------------------------------------------------------------
# helper:三层 span 命名规范(提案 4 §5.1)
# 业务代码只用这些 helper,不需要直接 import opentelemetry.trace。
# ----------------------------------------------------------------------------


def _start_span(name: str, attrs: dict[str, Any]) -> Any:
    """统一封装 ``tracer.start_as_current_span(name)``,把 attrs 灌进去。

    NoOp tracer 也支持 ``start_as_current_span``,所以无需分支。
    """
    tracer = get_tracer()
    cm = tracer.start_as_current_span(name)
    return _SpanCtx(cm, attrs)


class _SpanCtx:
    """包一层 contextmanager,在 __enter__ 时把 attrs 写到 span 上。

    这样调用方不用关心是 NoOp 还是真 span。
    """

    __slots__ = ("_cm", "_attrs", "_span")

    def __init__(self, cm: Any, attrs: dict[str, Any]) -> None:
        self._cm = cm
        self._attrs = attrs
        self._span: Any = None

    def __enter__(self) -> Any:
        self._span = self._cm.__enter__()
        # NoOp / 真 span 都有 set_attribute
        for k, v in self._attrs.items():
            if v is None:
                continue
            try:
                self._span.set_attribute(k, v)
            except Exception:  # pragma: no cover - 防御性
                log.debug("set_attribute(%s) 失败", k, exc_info=True)
        return self._span

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Any:
        return self._cm.__exit__(exc_type, exc_val, exc_tb)


@contextmanager
def task_span(task_id: str, **attrs: Any) -> Iterator[Any]:
    """顶层 task span,name = ``task.<task_id>``。

    通常在 supervisor 派活入口或 backend API task handler 处打开。
    """
    span_attrs = {"task.id": task_id, **attrs}
    with _start_span(f"task.{task_id}", span_attrs) as span:
        yield span


@contextmanager
def employee_span(employee_key: str, **attrs: Any) -> Iterator[Any]:
    """员工 graph 执行 span,name = ``employee.<key>``。

    在 ``claude_pool.acquire`` 之前 / LangGraph 子图入口处打开。
    """
    span_attrs = {"employee.key": employee_key, **attrs}
    with _start_span(f"employee.{employee_key}", span_attrs) as span:
        yield span


@contextmanager
def llm_call_span(model: str, **attrs: Any) -> Iterator[Any]:
    """单次 LLM(claude code)调用 span,name = ``llm.call``。

    属性 ``llm.model`` 用于按模型聚合延迟 / token 成本。
    """
    span_attrs = {"llm.model": model, **attrs}
    with _start_span("llm.call", span_attrs) as span:
        yield span


@contextmanager
def tool_call_span(server: str, tool: str, **attrs: Any) -> Iterator[Any]:
    """MCP 工具调用 span,name = ``tool.<server>.<tool>``。

    属性 ``tool.server`` / ``tool.name`` 用于按 server 拆分调用面板。
    """
    span_attrs = {"tool.server": server, "tool.name": tool, **attrs}
    with _start_span(f"tool.{server}.{tool}", span_attrs) as span:
        yield span


@contextmanager
def rag_span(kind: str, **attrs: Any) -> Iterator[Any]:
    """RAG / KB / lessons / memory 检索 span,name = ``rag.retrieve``。

    参数:
        kind: 检索类型,通常是 ``"lessons"`` / ``"kb"`` / ``"memory"``;
            写到属性 ``rag.kind``。
    """
    span_attrs = {"rag.kind": kind, **attrs}
    with _start_span("rag.retrieve", span_attrs) as span:
        yield span


@contextmanager
def verifier_span(gate: str, **attrs: Any) -> Iterator[Any]:
    """提案 2 三闸 verifier span,name = ``verifier.<gate>``。

    参数:
        gate: 闸名,例如 ``"static"`` / ``"runtime"`` / ``"human"``;
            写到属性 ``verifier.gate``。
    """
    span_attrs = {"verifier.gate": gate, **attrs}
    with _start_span(f"verifier.{gate}", span_attrs) as span:
        yield span


__all__ = [
    "init_tracer",
    "get_tracer",
    "task_span",
    "employee_span",
    "llm_call_span",
    "tool_call_span",
    "rag_span",
    "verifier_span",
    "_reset_for_tests",
]

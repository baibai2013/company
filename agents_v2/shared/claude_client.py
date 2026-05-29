from pathlib import Path

from anthropic import Anthropic
from langchain_anthropic import ChatAnthropic
from pydantic_settings import BaseSettings


class LLMSettings(BaseSettings):
    ANTHROPIC_API_KEY: str
    ANTHROPIC_BASE_URL: str = ""
    ANTHROPIC_EXTRA_HEADERS: dict = {}

    model_config = {
        "env_file": str(Path(__file__).parent.parent.parent / "infra" / ".env"),
        "extra": "ignore",
    }


_settings = LLMSettings()


def make_langchain_llm(
    model: str = "claude-sonnet-4-6",
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> ChatAnthropic:
    kwargs: dict = {
        "model": model,
        "anthropic_api_key": _settings.ANTHROPIC_API_KEY,
        "base_url": _settings.ANTHROPIC_BASE_URL or None,
        "default_headers": _settings.ANTHROPIC_EXTRA_HEADERS or {},
        "timeout": 120.0,
        # 代理(lumos)的 opus 后端会间歇性返回 500/overloaded;anthropic SDK 对
        # 429/500/overloaded 自带指数退避重试。2 次不够吃掉抽风(实测连续 500
        # 会把整个 A2A 调用打成硬失败),提到 6。(2026-05-29)
        "max_retries": 6,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return ChatAnthropic(**kwargs)


def make_anthropic_client() -> Anthropic:
    return Anthropic(
        api_key=_settings.ANTHROPIC_API_KEY,
        base_url=_settings.ANTHROPIC_BASE_URL or None,
    )

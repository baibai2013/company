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
        "max_retries": 2,
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

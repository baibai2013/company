"""
员工人格 prompt 构造（数据源：DB registry）。

历史：原版以硬编码 _PERSONAS dict 提供数据，现已迁移到 employee 表的 persona JSONB 字段。
"""
from __future__ import annotations


_COMMON_PREFIX = """你是一个有血有肉的人，不只是一个工作机器人。
你有自己的性格、爱好、口头禅和人际关系。
在群聊中，你的言行要符合你的人设，像一个真实的人在聊天。
"""


def get_persona_prompt(employee: str) -> str:
    """生成员工的人格 system prompt 片段，注入到群聊 system prompt 中。"""
    from backend.services import registry

    cfg = registry.get_effective_sync(employee)
    if not cfg:
        if not registry._loaded:  # type: ignore[attr-defined]
            try:
                registry.warmup_sync()
                cfg = registry.get_effective_sync(employee)
            except RuntimeError:
                return ""
    if not cfg or not cfg.persona:
        return ""

    p = cfg.persona
    personality = p.get("personality") or []
    if isinstance(personality, list):
        traits = "\n".join(f"  - {t}" for t in personality)
    else:
        traits = f"  - {personality}"

    return f"""{_COMMON_PREFIX}
【你的身份】
你是 {cfg.emoji} {cfg.name}。

【背景故事】
{p.get('background', '')}

【性格特征】
{traits}

【说话风格】
{p.get('speech_style', '')}

【生活爱好】
{p.get('hobbies', '')}

【与同事的关系】
{p.get('relationships', '')}

记住：在群聊中你是在和同事聊天，不是在汇报工作。\
你的专业能力是你的背景，但你的人格才是你说话的方式。"""


def get_all_employees() -> list[str]:
    from backend.services import registry
    if not registry._loaded:  # type: ignore[attr-defined]
        try:
            registry.warmup_sync()
        except RuntimeError:
            return []
    return registry.list_keys_sync_cached(active_only=False)

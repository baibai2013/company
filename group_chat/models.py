"""
Data models for group chat orchestration.
Sections IV, XIII of doc/design/group-chat-redesign.md.
"""
from dataclasses import dataclass, field


@dataclass
class ConversationMessage:
    id: str
    session_id: str
    sender: str                # employee key or "user"
    sender_name: str           # display name
    content: str
    platform_message_id: str     # platform message_id for thread replies
    created_at: float          # unix timestamp
    role: str = "user"         # "user" | "assistant"
    visible_to: list[str] = field(default_factory=list)  # empty = visible to all
    marks: list[str] = field(default_factory=list)       # semantic tags, e.g. "wolf_night", "system_event"

    def is_visible_to(self, viewer: str) -> bool:
        """空列表 = 全员可见；否则 viewer 必须在列表里。"""
        return not self.visible_to or viewer in self.visible_to


@dataclass
class SessionRole:
    employee: str
    role_name: str             # "主持人" | "正方" | "反方" | "狼人" etc.
    role_desc: str             # behavioural instruction for this role
    visible_to: list[str] = field(default_factory=list)  # factions that can see this role, [] = everyone
    faction: str = ""          # "werewolf" | "village" | "affirmative" | "negative"


@dataclass
class GroupSession:
    id: str = ""               # = trigger message platform message_id
    chat_id: str = ""
    mode: str = "single"       # "single" | "sequential" | "parallel"
    status: str = "active"     # "active" | "speaking" | "done"
    participants: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    history: list[ConversationMessage] = field(default_factory=list)
    trigger_message_id: str = ""
    created_at: float = 0.0
    ttl: int = 1800            # 30 min
    template: str = "free"     # "brainstorm" | "debate" | "werewolf" | "review" | "free"
    activity_rules: str = ""   # host-defined rules of this session, broadcast to all participants
    host: str = ""             # employee key of the host/organizer (often project_manager)
    game_state: dict = field(default_factory=dict)  # e.g. {"secret_number": 42}
    role_assignments: dict[str, SessionRole] = field(default_factory=dict)
    role_history: list[tuple[float, dict]] = field(default_factory=list)
    summary: str = ""


@dataclass
class SpeakRequest:
    session_id: str
    chat_id: str
    employee: str
    history_text: str
    trigger_message_id: str
    image_base64: str = ""
    order: int = 0
    summary_mode: bool = False
    role_context: str = ""     # injected role-awareness context


@dataclass
class SpeakResponse:
    session_id: str
    chat_id: str
    employee: str
    content: str
    success: bool = True


@dataclass
class OrchestratorDecision:
    mode: str                  # "single" | "sequential" | "parallel" | "ignore"
    participants: list[str]
    reason: str


@dataclass
class MessageEvent:
    message_id: str
    chat_id: str
    sender: str                # "user"
    text: str
    image_base64: str = ""
    mentions: list[str] = field(default_factory=list)


# ── Session templates (section 13.2) ──────────────────────────────────────────

SESSION_TEMPLATES: dict = {
    "brainstorm": {
        "desc": "头脑风暴，自由发散",
        "roles": {
            "moderator": {
                "desc": "主持讨论，引导话题，最后做总结",
                "fixed": "project_manager",
                "count": 1,
            },
            "contributor": {
                "desc": "从你的专业角度提出想法，鼓励大胆发散",
                "count": "auto",
            },
        },
    },
    "debate": {
        "desc": "正反方辩论，有主持人裁判",
        "roles": {
            "moderator": {
                "desc": "主持辩论，控制发言时间，最后裁判",
                "fixed": "project_manager",
                "count": 1,
            },
            "affirmative": {
                "desc": "正方：支持并论证议题，反驳负方观点",
                "count": "1-3",
            },
            "negative": {
                "desc": "反方：质疑并挑战议题，揭示风险和问题",
                "count": "1-3",
            },
        },
    },
    "werewolf": {
        "desc": "狼人杀游戏",
        "roles": {
            "host": {
                "desc": "游戏主持人，推进流程，不参与投票",
                "fixed": "project_manager",
                "count": 1,
            },
            "werewolf": {
                "desc": "狼人：白天伪装，夜晚杀人，说服村民投票杀好人",
                "count": "1-2",
                "visible_to": ["werewolf"],
                "faction": "werewolf",
            },
            "villager": {
                "desc": "普通村民：通过逻辑分析找出狼人",
                "count": "2-4",
                "faction": "village",
            },
            "witch": {
                "desc": "巫师：有一瓶解药一瓶毒药，用时机决定胜负",
                "count": "0-1",
                "faction": "village",
            },
            "seer": {
                "desc": "预言家：每晚可以查验一人身份",
                "count": "0-1",
                "faction": "village",
            },
        },
    },
    "review": {
        "desc": "方案评审，多角度审视",
        "roles": {
            "presenter": {"desc": "方案提出者，介绍并捍卫方案"},
            "advocate":  {"desc": "支持者，强化方案优点"},
            "critic":    {"desc": "批评者，挖掘风险和缺陷"},
            "neutral":   {"desc": "中立评估，综合判断可行性"},
            "moderator": {"fixed": "project_manager", "desc": "主持评审流程"},
        },
    },
    "free": {
        "desc": "自由群聊，无固定角色",
        "roles": {
            "participant": {"desc": "自由发言，保持专业身份"},
        },
    },
}


# ── Employee identity (sourced from DB registry, dict-compatible) ─────────────
#
# Backed by backend.services.registry. Auto-warms the cache on first access.
# Hot-reloads when DB changes (PG NOTIFY → registry.invalidate).
#
# Old shape preserved so existing callers (.get / .items / 'x in dict' / dict[k])
# continue to work.

class _RegistryDict:
    """dict-like view over registry, refreshed on every access."""

    def __init__(self, getter):
        self._getter = getter

    def _data(self):
        from backend.services import registry
        if not registry._loaded:  # type: ignore[attr-defined]
            try:
                registry.warmup_sync()
            except RuntimeError:
                # Inside event loop — caller must have warmed up via await.
                pass
        return self._getter()

    def __getitem__(self, key):
        return self._data()[key]

    def get(self, key, default=None):
        return self._data().get(key, default)

    def __contains__(self, key):
        return key in self._data()

    def __iter__(self):
        return iter(self._data())

    def __len__(self):
        return len(self._data())

    def items(self):
        return self._data().items()

    def keys(self):
        return self._data().keys()

    def values(self):
        return self._data().values()

    def __repr__(self):
        return f"_RegistryDict({self._data()!r})"


def _employee_config():
    from backend.services import registry
    return registry.employee_config_compat_sync()


def _role_descriptions():
    from backend.services import registry
    return registry.role_descriptions_compat_sync()


EMPLOYEE_CONFIG: "dict[str, tuple[str, str]]" = _RegistryDict(_employee_config)  # type: ignore[assignment]
ROLE_DESCRIPTIONS: "dict[str, str]"           = _RegistryDict(_role_descriptions)  # type: ignore[assignment]
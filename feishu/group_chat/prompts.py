"""
Prompts for the group chat orchestrator.
Sections VIII, XIII, XIV of doc/design/group-chat-redesign.md.
"""
import json as _json
import re as _re
from datetime import datetime, timezone

from .models import (
    ConversationMessage, EMPLOYEE_CONFIG, GroupSession,
    ROLE_DESCRIPTIONS, SessionRole,
)

# ── Decide prompt (section 13.4) ──────────────────────────────────────────────
# Dynamically built from registry; can also be overridden via system_config.global_prompts.decide_prompt.

_DECIDE_PROMPT_TEMPLATE = """你是群聊调度员，根据最新消息决定如何响应。

员工列表：
{employee_list}

消息中的 [@名字] 标签表示用户 @了该员工。可识别的中英文别名：
{name_aliases}

路由规则（按优先级）：
1. 消息含 [全员] → mode=sequential, participants=全部员工
2. 消息含 [@具体人] → mode=single, participants=[该人]（该人负责组织/回答）
3. "头脑风暴/大家说说/集思广益/brainstorm" → mode=sequential, 选≤6个最相关专家
4. "同时/并行/大家一起" → mode=parallel, 选相关专家
5. 游戏/互动/娱乐/趣味活动/猜谜/投票/竞猜/比赛 → mode=sequential, participants=全部员工（全员参与最有趣）
6. 技术问题但无明确@人 → mode=single, participants=[最相关专家]
7. 项目进度/任务分配/里程碑/协调类（明确与工作相关） → mode=single, participants=[project_manager]
8. 纯闲聊/表情/打卡 → mode=ignore

输出格式（只输出 JSON）：
{{"mode": "single|sequential|parallel|ignore", "participants": [...], "reason": "一句话理由"}}"""


def build_decide_prompt() -> str:
    """Render the decide prompt from the current registry state.

    Honors system_config.global_prompts.decide_prompt if explicitly set.
    """
    from backend.services import registry

    if not registry._loaded:  # type: ignore[attr-defined]
        try:
            registry.warmup_sync()
        except RuntimeError:
            pass

    glob = registry.global_sync()
    override = (glob.get("global_prompts") or {}).get("decide_prompt")
    if override and "{employee_list}" not in override:
        return override

    employees = sorted(registry.list_keys_sync_cached(active_only=True))
    cfg_map = registry.employee_config_compat_sync()
    role_map = registry.role_descriptions_compat_sync()

    employee_list = "\n".join(
        f"- {key:<18} {cfg_map.get(key, ('👤', key))[1]}  {role_map.get(key, '')}"
        for key in employees
    )
    name_aliases = "  ".join(
        f"[@{cfg_map.get(k, ('👤', k))[1]}]→{k}" for k in employees
    )
    template = override or _DECIDE_PROMPT_TEMPLATE
    return template.format(employee_list=employee_list, name_aliases=name_aliases)


# Backwards-compatible module-level lazy attribute
def __getattr__(name: str):
    if name == "DECIDE_PROMPT":
        return build_decide_prompt()
    raise AttributeError(name)


# ── Role decide prompt (section 13.3) ─────────────────────────────────────────

ROLE_DECIDE_PROMPT = """你为本次群聊互动现场设计「活动规则」和「角色分配」。

【你的输出由两部分组成】：

1. **activity_rules**：本次活动的可执行玩法说明，所有参与者都会看到这段文字
   - 游戏类（猜数字、狼人杀、真心话、谁是卧底、成语接龙等）：写清完整玩法、范围、判定标准、轮次结构
   - 辩论/角色扮演：说明立场分配、发言顺序、目标
   - 工作头脑风暴/严肃讨论：可设为空字符串 ""
   - 不要超过 150 字，要让每位成员一眼看懂自己该做什么

2. **roles**：每位参与者在本场活动中的具体角色
   - 角色描述必须可执行（"你应该做什么"，而不是"你是怎样的人"）
   - 如果活动需要主持/裁判/出题人，把该员工 key 填到顶层 host 字段；优先选 project_manager
   - 主持人的 role_desc 要包含独有职责（出题、判定、公布答案、推进流程等）
   - 普通参与者的 role_desc 鼓励他们结合自己的专业身份创意发挥

【角色类型可以是】：
- 预设角色：主持人、正方、反方、挑战者、支持者、裁判、出题人、答题者、玩家
- 自由角色："用户视角代言人"、"成本杀手"、"技术乐观派"等
- 游戏角色：狼人、村民、预言家、巫师、骑士、卧底等
- 隐藏信息游戏（狼人杀等）通过 visible_to 限制角色可见性

【输出 JSON 示例】：

示例 A — 0-100 猜数字游戏：
{
  "template": "guess_number",
  "host": "project_manager",
  "activity_rules": "0-100 猜数字游戏。芳芳已选定一个秘密数字（不会透露），范围 0-100。其他成员每人猜一个整数，结合自己的专业背景说出选这个数的理由（脑洞越大越好）。最后由芳芳公布答案、宣布最接近者并表扬。",
  "roles": {
    "project_manager": {"role_name": "游戏主持人", "role_desc": "你已经在心里选定了一个 0-100 的秘密数字（自行选定，不要透露具体值）。本轮发言宣布游戏规则、数字范围 0-100，邀请大家猜测，但绝不透露答案。"},
    "algorithm": {"role_name": "玩家", "role_desc": "猜一个 0-100 的整数，从算法/概率角度脑洞解释为什么猜这个数。"}
  }
}

示例 B — 头脑风暴（无规则）：
{
  "template": "brainstorm",
  "host": "",
  "activity_rules": "",
  "roles": {
    "mechanical": {"role_name": "可行性派", "role_desc": "从工程可行性角度评估方案。", "faction": "negative"}
  }
}

⚠️ 严格只输出 JSON，不要任何其他说明文字。"""

EXPLICIT_ROLE_EXTRACT_PROMPT = """判断用户是否在消息中显式指定了某人的角色。

员工列表：
{employee_list}

如果用户在消息中指定了角色（如"让Dave当反方"、"大法师当主持人"、"让喵喵球做预言家"），
输出 JSON 格式的角色分配。如果用户没有显式指定角色，输出 null。

示例输出：
{{"mechanical": {{"role_name": "反方", "role_desc": "质疑和挑战方案"}}}}

如果没有指定角色，只输出：null"""


# ── Group speak prefix (section 13.2) ─────────────────────────────────────────

GROUP_SPEAK_PREFIX = """
【当前场景：群聊发言】

行为要求：
- 发言控制在 150 字以内，群聊不适合长篇大论
- 直接说结论和理由，不要废话
- 如果认同前面某人的观点，可以简短说"同意 Dave 的方案"再补充
- 如果有不同意见，直接指出"我觉得 Dave 说的连杆方案有个问题：..."
- 用第一人称口语化表达，体现你的人设和专业立场

【严格禁止以下行为，违反即为错误输出】：
- 禁止无意义自我介绍（不需要说"我是XXX，负责XXX"，但有角色时必须按格式声明）
- 禁止 @ 任何人（不要写 @某人 或 @_user_1 等任何@符号）
- 禁止建议找其他人、重定向或安排任务给他人
- 禁止说"这个问题应该由XXX来回答"
"""


# ── Summary prompt ────────────────────────────────────────────────────────────

_SUMMARY_PROMPT_TEMPLATE = """你是项目经理芳芳，请根据上面的群聊内容做收尾发言。

{activity_block}

【收尾要求】：
- 直接给结论，不要复述每个人的原话
- 200 字以内（如果是游戏/活动收尾，60~100 字即可）
- 口语化、活泼自然，像真实的 PM 在群里收尾

⚠️ 严格遵守活动规则中分配给你的主持人/出题人/裁判职责（如：公布答案、宣布最接近者、给予表扬、判定胜负等）。"""


def build_summary_prompt(session) -> str:
    """Render the summary prompt, injecting the session's activity_rules so 芳芳
    knows whether this is a game (announce result) or a discussion (give recap).
    """
    if session.activity_rules:
        activity_block = (
            "【本场活动规则（你之前已宣布的玩法）】\n"
            f"{session.activity_rules}\n"
        )
    else:
        activity_block = (
            "【场景类型】\n"
            "工作讨论/头脑风暴。请列出主要观点、共识、分歧点和下一步建议。\n"
        )
    return _SUMMARY_PROMPT_TEMPLATE.format(activity_block=activity_block)


# Backwards-compatible static fallback (used only when session is unavailable)
SUMMARY_PROMPT = _SUMMARY_PROMPT_TEMPLATE.format(
    activity_block="【场景类型】\n根据上下文判断（游戏则公布答案宣布获胜者，讨论则做要点小结）。\n"
)


# ── History formatting (section 13.1) ─────────────────────────────────────────

def format_history(
    history: list[ConversationMessage],
    current_employee: str = "",
    viewer: str = "",
) -> str:
    """Format conversation history for LLM context.

    Args:
        viewer: If set, filter out messages whose visible_to doesn't include this viewer.
                Messages with empty visible_to are always included (public).
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    lines = [f"【群聊记录 · {now}】"]
    for msg in history:
        # Visibility filter: skip private messages not meant for this viewer
        if viewer and msg.visible_to and viewer not in msg.visible_to:
            continue
        emoji, name = EMPLOYEE_CONFIG.get(msg.sender, ("👤", msg.sender))
        label = "用户" if msg.sender == "user" else f"{emoji} {name}（{ROLE_DESCRIPTIONS.get(msg.sender, '')}）"
        lines.append("─────────────────────────────")
        lines.append(f"{label}:")
        for line in msg.content.split("\n"):
            lines.append(f"  {line}")

    if current_employee:
        emoji, name = EMPLOYEE_CONFIG.get(current_employee, ("👤", current_employee))
        lines.append("─────────────────────────────")
        lines.append(f"【当前轮到你发言】你是 {emoji} {name}")
        lines.append("请基于上面的讨论，发表你的专业意见。")
        lines.append("不要重复别人说过的内容，可以补充、质疑或提出新角度。")

    return "\n".join(lines)


# ── Role context (section 13.4) ───────────────────────────────────────────────

def build_role_context(current_employee: str, session: GroupSession) -> str:
    """Build role-aware context for an employee's speak request."""
    emoji, name = EMPLOYEE_CONFIG.get(current_employee, ("👤", current_employee))
    role_desc = ROLE_DESCRIPTIONS.get(current_employee, "")

    # Check if there are session roles
    my_role = session.role_assignments.get(current_employee)

    # Activity rules — broadcast to ALL participants so everyone plays the same game
    activity_block = []
    if session.activity_rules:
        activity_block = [
            "【本场活动规则（所有人共同遵守）】",
            session.activity_rules,
        ]
        if session.host:
            host_emoji, host_name = EMPLOYEE_CONFIG.get(session.host, ("👤", session.host))
            activity_block.append(f"主持人：{host_emoji} {host_name}")
        activity_block.append("")

    others_info = []
    for emp in session.participants:
        if emp == current_employee:
            continue
        o_emoji, o_name = EMPLOYEE_CONFIG.get(emp, ("👤", emp))
        o_role = session.role_assignments.get(emp)
        if o_role:
            can_see = (
                not o_role.visible_to
                or (my_role and my_role.faction in o_role.visible_to)
            )
            if can_see:
                others_info.append(f"  {o_emoji} {o_name} → {o_role.role_name}")
            else:
                others_info.append(f"  {o_emoji} {o_name} → 身份未知")
        else:
            o_role_desc = ROLE_DESCRIPTIONS.get(emp, "")
            others_info.append(f"  {o_emoji} {o_name}（{o_role_desc}）")

    lines = list(activity_block) + [
        "【你的双重身份】",
        f"职业身份：{emoji} {name}（{role_desc}）",
    ]

    if my_role:
        lines.append(f"本场角色：{my_role.role_name}")
        lines.append("")
        lines.append("角色任务：")
        lines.append(my_role.role_desc)
        lines.append("")

    lines.append("本场其他参与者：")
    lines.extend(others_info)

    if my_role:
        lines.append("")
        lines.append("⚠️ 在本场讨论中，你的角色任务优先于职业身份。")
        lines.append("   但你的专业知识是你完成角色任务的工具。")
        lines.append("")
        lines.append(f"【发言格式要求】第一句必须是：「{my_role.role_name}，{name}：」然后再表达观点。")
        lines.append("   例如：「正方，小米：我认为机器狗在家庭场景非常受欢迎，因为...」")
    else:
        lines.append("")
        lines.append("你的发言应该聚焦在你的专业领域，避免重复其他人已覆盖的内容。")

    return "\n".join(lines)


# ── Simple role context (without session roles, section 13.6) ─────────────────

def build_simple_role_context(current_employee: str, participants: list[str]) -> str:
    """Build role context when no session roles are assigned."""
    curr_emoji, curr_name = EMPLOYEE_CONFIG.get(current_employee, ("👤", current_employee))
    curr_desc = ROLE_DESCRIPTIONS.get(current_employee, "")

    others = [
        f"  - {EMPLOYEE_CONFIG[e][0]} {EMPLOYEE_CONFIG[e][1]}（{ROLE_DESCRIPTIONS.get(e, '')}）"
        for e in participants if e != current_employee
    ]

    return f"""
【你在这次讨论中的角色】
你是 {curr_emoji} {curr_name}，负责：{curr_desc}

本次讨论的其他参与者：
{chr(10).join(others) if others else '  无'}

你的发言应该聚焦在你的专业领域，避免重复其他人已覆盖的内容。
如果你的专业与其他人有交叉，从你独特的角度补充即可。
"""




# ── Explicit role extraction ──────────────────────────────────────────────────

async def extract_explicit_roles(
    text: str, mentions: list[str], llm,
) -> dict[str, SessionRole] | None:
    """Use LLM to check if the user explicitly assigned roles. Returns None if not."""
    employee_list = "\n".join(
        f"- {key}: {emoji} {name}"
        for key, (emoji, name) in EMPLOYEE_CONFIG.items()
    )
    prompt = EXPLICIT_ROLE_EXTRACT_PROMPT.format(employee_list=employee_list)

    from langchain_core.messages import HumanMessage, SystemMessage

    resp = await llm.ainvoke([
        SystemMessage(prompt),
        HumanMessage(text),
    ])

    try:
        content = resp.content.strip()
        if content.lower() == "null" or content == "":
            return None
        m = _re.search(r"\{.*\}", content, _re.DOTALL)
        if not m:
            return None
        data = _json.loads(m.group())
        if not data or not isinstance(data, dict):
            return None

        roles = {}
        for emp_key, role_data in data.items():
            if emp_key not in EMPLOYEE_CONFIG:
                continue
            roles[emp_key] = SessionRole(
                employee=emp_key,
                role_name=role_data.get("role_name", ""),
                role_desc=role_data.get("role_desc", ""),
                visible_to=role_data.get("visible_to", []),
                faction=role_data.get("faction", ""),
            )
        return roles if roles else None
    except Exception:
        return None
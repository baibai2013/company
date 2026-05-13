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
5. 技术问题但无明确@人 → mode=single, participants=[最相关专家]
6. 项目进度/协调类 → mode=single, participants=[project_manager]
7. 纯闲聊/表情/打卡 → mode=ignore

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

ROLE_DECIDE_PROMPT = """根据话题和参与者，为每个人分配一个最能推动讨论的角色。

角色可以是：
- 预设角色：主持人、正方、反方、挑战者、支持者、裁判
- 自由角色：任何你认为合适的角色描述，如"用户视角代言人"、"成本杀手"、"技术乐观派"
- 游戏角色：狼人、村民、预言家、巫师、骑士 等

输出 JSON：
{
  "template": "debate | brainstorm | werewolf | free",
  "roles": {
    "mechanical": {
      "role_name": "技术悲观派",
      "role_desc": "从工程可行性角度质疑方案，找出难以实现的部分",
      "faction": "negative",
      "visible_to": []
    }
  }
}

注意：如果是狼人等需要信息隐藏的游戏，设置 visible_to 限制谁能看到谁的角色。"""

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

SUMMARY_PROMPT = """你是项目经理芳芳，请根据以上群聊讨论内容，做一段简短的总结。

要求：
- 200 字以内
- 列出主要观点和共识
- 指出分歧点（如有）
- 给出下一步建议
- 口语化风格，像一个真实的 PM 在群里做总结"""


# ── History formatting (section 13.1) ─────────────────────────────────────────

def format_history(history: list[ConversationMessage], current_employee: str = "") -> str:
    """Format conversation history for LLM context."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    lines = [f"【群聊记录 · {now}】"]
    for msg in history:
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

    lines = [
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
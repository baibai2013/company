"""提案 4 §3 — TechLead Supervisor 的任务路由策略(规则 stub)。

本 wave 不调 LLM,纯关键词命中。命中数最多者胜;同分按字典序;0 命中
兜底到 tech_lead 自己接(代表"先放着,等下一轮再分")。

设计取舍:
  - 关键词表写死在本文件 EMPLOYEE_KEYWORDS,后续 Wave 可挪到
    config/routing_keywords.yaml + DB 配置;阶段一不上动态化避免引入
    新依赖。
  - 大小写不敏感,中文关键词原样匹配。
  - 候选名单 = 所有命中员工(含 tech_lead 兜底场景),按命中数降序后
    取 chosen,reason 描述命中关键词。
"""
from __future__ import annotations

from typing import Iterable

# ── 路由关键词表 ────────────────────────────────────────────────────
# value 列表内的关键词命中即记 1 分。tech_lead 留空作为 fallback。
EMPLOYEE_KEYWORDS: dict[str, list[str]] = {
    "mechanical":      ["腿", "结构", "尺寸", "step", "bom", "装配"],
    "firmware":        ["esp32", "rtos", "驱动", "i2c", "gpio"],
    "algorithm":       ["urdf", "pybullet", "控制", "步态", "力矩"],
    "hardware":        ["电机", "电源", "pcb"],
    "test_engineer":   ["测试", "评测", "回归"],     # 与 a2a_router PORT_MAP 全名对齐
    "cost_engineer":   ["成本", "采购", "bom 价格"],  # 同上
    "product_manager": ["进度", "排期", "需求"],     # 同上
    "tech_lead":       [],  # fallback,无关键词
    "project_manager": ["项目", "里程碑"],
}


def _count_hits(text: str, keywords: Iterable[str]) -> tuple[int, list[str]]:
    """计算 text 中命中关键词的次数 + 命中明细。

    大小写不敏感(对英文关键词);中文关键词按原文匹配。
    一个关键词在 text 中出现多次仍只记 1(避免长文本拉偏分)。
    """
    text_lower = text.lower()
    hits: list[str] = []
    for kw in keywords:
        kw_l = kw.lower()
        if kw_l and kw_l in text_lower:
            hits.append(kw)
    return len(hits), hits


def choose_employee(
    task_title: str,
    task_content: str,
) -> tuple[str, list[str], str]:
    """根据任务标题 + 内容,返回 (chosen, candidates, reason)。

    Args:
        task_title: 任务标题
        task_content: 任务正文

    Returns:
        (chosen, candidates, reason):
          - chosen: 选中的员工 key(命中最多者;同分按字典序;0 命中 → tech_lead)
          - candidates: 命中 ≥1 的员工列表(无命中时为 ['tech_lead'])
          - reason: 决策理由(自然语言简短解释,落 routing_decisions.reason)
    """
    text = f"{task_title}\n{task_content}"

    # 计算每个员工的命中分
    scored: list[tuple[str, int, list[str]]] = []
    for emp, keywords in EMPLOYEE_KEYWORDS.items():
        if not keywords:
            continue  # tech_lead 不参与命中计算,只做兜底
        hits_count, hits_list = _count_hits(text, keywords)
        if hits_count > 0:
            scored.append((emp, hits_count, hits_list))

    # 0 命中 → 兜底 tech_lead
    if not scored:
        return (
            "tech_lead",
            ["tech_lead"],
            "no keyword hit, fallback to tech_lead",
        )

    # 按 (-命中数, 字典序) 排序,取第一个为 chosen
    scored.sort(key=lambda x: (-x[1], x[0]))
    chosen = scored[0][0]
    chosen_hits = scored[0][2]
    candidates = [s[0] for s in scored]

    reason = (
        f"chosen={chosen} hit {len(chosen_hits)} keyword(s): "
        f"{', '.join(chosen_hits)}; candidates={candidates}"
    )
    return chosen, candidates, reason


__all__ = ["EMPLOYEE_KEYWORDS", "choose_employee"]

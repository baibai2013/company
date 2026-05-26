"""提案 4 §3 — TechLead Supervisor 路由策略 routing.choose_employee 单测。

覆盖:
  1. 命中单一员工(机械关键词 → mechanical)
  2. 平局按字典序(同 1 命中:firmware vs hardware → 字典序 firmware)
  3. 0 命中 → 兜底 tech_lead
  4. 多关键词命中累计计数
  5. 大小写不敏感(英文)
"""
from __future__ import annotations

from agents_v2.tech_lead.routing import EMPLOYEE_KEYWORDS, choose_employee


def test_single_hit_mechanical():
    """机械关键词单命中 → 选 mechanical。"""
    chosen, candidates, reason = choose_employee(
        task_title="实现机械腿装配",
        task_content="需要画 step 模型并出 BOM",
    )
    assert chosen == "mechanical"
    assert "mechanical" in candidates
    # 命中关键词应在 reason 里
    assert "腿" in reason or "step" in reason.lower() or "bom" in reason.lower()


def test_tie_breaks_by_alphabetical_order():
    """平局走字典序:firmware 与 hardware 同 1 命中,firmware 字典序在前胜出。"""
    # gpio → firmware (1) ; 电机 → hardware (1) → tie → firmware (字典序在前)
    chosen, candidates, _ = choose_employee(
        task_title="gpio 接电机",
        task_content="",
    )
    assert chosen == "firmware"
    assert "firmware" in candidates
    assert "hardware" in candidates


def test_no_hit_falls_back_to_tech_lead():
    """0 命中 → tech_lead 兜底。"""
    chosen, candidates, reason = choose_employee(
        task_title="随便聊聊天气",
        task_content="今天天气不错",
    )
    assert chosen == "tech_lead"
    assert candidates == ["tech_lead"]
    assert "fallback" in reason.lower() or "no keyword" in reason.lower()


def test_multiple_hits_accumulate_for_winner():
    """同员工多关键词命中累计;命中数最多者胜。"""
    chosen, candidates, reason = choose_employee(
        task_title="算法步态控制 urdf 调试",
        task_content="跑 pybullet 仿真",
    )
    # algorithm 关键词:urdf, pybullet, 控制, 步态 → 4 命中,远多于其它
    assert chosen == "algorithm"
    assert "algorithm" in candidates


def test_case_insensitive_for_english_keywords():
    """英文关键词大小写不敏感。"""
    chosen, _, _ = choose_employee(
        task_title="ESP32 RTOS task scheduling",
        task_content="",
    )
    # 关键词存的是 'esp32' / 'rtos',大写应同样命中
    assert chosen == "firmware"


def test_employee_keywords_table_consistency():
    """关键词表自身一致性:tech_lead 必须存在且为空 fallback。"""
    assert "tech_lead" in EMPLOYEE_KEYWORDS
    assert EMPLOYEE_KEYWORDS["tech_lead"] == []
    # 至少 mechanical / firmware / algorithm 三个核心员工有关键词
    assert EMPLOYEE_KEYWORDS["mechanical"]
    assert EMPLOYEE_KEYWORDS["firmware"]
    assert EMPLOYEE_KEYWORDS["algorithm"]

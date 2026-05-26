"""提案 3 · §4.4 — 评测 fixture 种子脚本(Wave 3 手编 5 条)。

跑法:
    python -m scripts.evals_seed

幂等:写入走 ON CONFLICT (id) DO UPDATE,反复执行只覆盖不报错。

覆盖员工:
  - mechanical(2 条:1 tier=1 mechanical_step + 1 tier=2 generic 占位)
  - firmware(2 条:tier=2 blink + tier=2 rtos init)
  - algorithm(1 条:tier=1 urdf 验证)
  - testing(1 条:tier=3 回归用例)

Wave 4 计划扩到 30-50 条覆盖跨域协作场景(见提案 §4.4)。
"""
from __future__ import annotations

import asyncio
import json

from backend.repos import evals_fixture_repo


# ── fixtures 定义 ──────────────────────────────────────────────────
# acceptance_spec 字段约定见提案 02 §3 + Wave 2 checkers 注册表
# (deliverable_type 没有注册的 checker 时,闸 2 自动 skip,verdict 走闸 1 stub)
FIXTURES: list[dict] = [
    {
        "id": "mech-leg-v1",
        "title": "机械腿 STEP 模型导出(后腿 v1)",
        "employee_key": "mechanical",
        "input_prompt": (
            "用 build123d 设计 4-DOF 后腿,腿长 250mm,导出 leg_back.STEP。"
            "要求髋/膝/踝三个挂载点齐全。"
        ),
        "acceptance_spec": {
            "deliverable_type": "mechanical_step",
            "expects": ["leg_back.STEP"],
        },
        "golden_outputs": None,
        "tier": 1,
    },
    {
        "id": "firmware-blink-v1",
        "title": "ESP32 LED blink 固件",
        "employee_key": "firmware",
        "input_prompt": (
            "在 ESP32 上写一个最小 blink 例子,500ms 翻转一次 GPIO2,提交可烧录的 firmware.bin。"
        ),
        "acceptance_spec": {
            "deliverable_type": "firmware_bin",
            "expects": ["firmware.bin"],
        },
        "golden_outputs": None,
        "tier": 2,
    },
    {
        "id": "firmware-rtos-init-v1",
        "title": "FreeRTOS 任务初始化骨架",
        "employee_key": "firmware",
        "input_prompt": (
            "搭一个 FreeRTOS app_main,创建 2 个 task(motor_ctrl 优先级 5、log 优先级 2),"
            "提交 main.c 与 sdkconfig diff。"
        ),
        "acceptance_spec": {
            "deliverable_type": "firmware_source",
            "expects": ["main.c"],
        },
        "golden_outputs": None,
        "tier": 2,
    },
    {
        "id": "algo-urdf-v1",
        "title": "四足整机 URDF 自检",
        "employee_key": "algorithm",
        "input_prompt": (
            "把 mechanical 给的 STEP 拼成整机 URDF,导出 quad.urdf,joint_limits 必须落在 ±π。"
        ),
        "acceptance_spec": {
            "deliverable_type": "urdf",
            "expects": ["quad.urdf"],
        },
        "golden_outputs": None,
        "tier": 1,
    },
    {
        "id": "test-regression-v1",
        "title": "整机站立测试回归用例",
        "employee_key": "testing",
        "input_prompt": (
            "在 mujoco 里跑一遍上一版站立控制器,记录 30s 内 base_link Z 漂移,"
            "若 > 5mm 视为回归。提交 regression_report.md。"
        ),
        "acceptance_spec": {
            "deliverable_type": "test_report",
            "expects": ["regression_report.md"],
        },
        "golden_outputs": None,
        "tier": 3,
    },
]


async def seed() -> list[dict]:
    """把 FIXTURES 全部 upsert,返回写入的 id + tier + employee 摘要。"""
    out: list[dict] = []
    for f in FIXTURES:
        row = await evals_fixture_repo.upsert(
            f["id"],
            title=f["title"],
            employee_key=f["employee_key"],
            input_prompt=f["input_prompt"],
            acceptance_spec=f["acceptance_spec"],
            golden_outputs=f.get("golden_outputs"),
            tier=f["tier"],
        )
        out.append({
            "id": row.id,
            "tier": row.tier,
            "employee_key": row.employee_key,
            "title": row.title,
        })
    return out


def main() -> None:
    summary = asyncio.run(seed())
    print(json.dumps(
        {"seeded": len(summary), "fixtures": summary},
        indent=2,
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()

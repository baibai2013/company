# build123d-cad → 硬件设计 Super Skill 改造（协作文档集）

> 本目录是「build123d-cad 改造」的工作文档集。每份文档相互独立、各有负责人，
> 可由对应员工**并行认领、独立撰写/扩充**，互不阻塞。
> 完整技术方案底稿见 `~/.claude/plans/mossy-orbiting-river.md`（plan v3.1）。

## 一句话目标

把现有 `build123d-cad`（单 skill 纵向吃透 build123d）改造成
**「一个父 super skill 内含 N 个子技能（monorepo）」**的硬件设计平台：
每个子技能独立 `SKILL.md / scripts / tests / benchmarks`，可单独 `pytest`，
后续扩 PCB / 电子 / 固件域 = 新增一个 `skills/<name>/` 目录。

参照对象：`earthtojake/text-to-cad`（横向 9 个 sibling skill）的能力**全部内化复刻**，
但组织成模块化子技能，而非分散安装。

## 文档清单与分工

| 文档 | 内容 | 负责人 | 协作人 | 优先级 |
|---|---|---|---|---|
| [00-总览与目标架构](00-总览与目标架构.md) | 改造背景、目标骨架、父 SKILL 路由、五大原则、不做什么 | tech_lead | 全员 | P0 |
| [01-分工与排期](01-分工与排期.md) | P0/P1/P2/P3 任务拆解、Owner 认领表、里程碑、依赖图 | project_manager | 全员 | P0 |
| [02-mechanical子技能迁移](02-mechanical子技能迁移.md) | 现 references/scripts/assets 迁入 + Playbook 路径改写 | mechanical (Dave) | tech_lead | P0 |
| [03-viewer多引擎子技能](03-viewer多引擎子技能.md) | 网页预览容器：CAD/PCB/原理图/仿真 多引擎 + 后缀路由 | fullstack | mechanical | P0 |
| [04-机器人描述子技能](04-机器人描述子技能-urdf-srdf-sdf.md) | URDF / SRDF / SDF 生成子技能复刻 | algorithm | mechanical | P0/P1 |
| [05-制造出工链路](05-制造出工链路-gcode-sendcutsend-bambu-parts.md) | gcode 切片预检 / 激光切割 / Bambu / parts-catalog | cost | mechanical | P0/P1 |
| [06-电子域扩展](06-电子域扩展-pcb-eda-drc.md) | P3 PCB / EDA / DRC / 元件库 路线图（先占位） | hardware | firmware | P3 |
| [07-测试与验证基建](07-测试与验证基建.md) | 子技能 tests / benchmarks 10 题 / agent-eval 回归 | testing | 全员 | P0 |
| [08-shared跨子技能协议](08-shared跨子技能协议.md) | handoff / router / dependencies 三份共享协议 | tech_lead | 全员 | P0 |

## 协作规范

1. **认领**：在 [01-分工与排期](01-分工与排期.md) 的 Owner 认领表里写上自己的名字 + 日期。
2. **独立写**：每份文档自带「目标 / 现状 / 规格 / 任务 / 验收 / 依赖」结构，
   认领后直接在本文档内补充，不要去改别人的文档（跨文档接口走「依赖」章节声明）。
3. **不撞车**：子技能之间**不直接互引用**，跨技能数据交换一律走
   [08-shared跨子技能协议](08-shared跨子技能协议.md) 的文件接口。
4. **状态**：每份文档头部维护 `状态：草稿 / 评审中 / 已定稿`。
5. **改路径前先看现状**：动手前用 Read/Grep 查清现有 skill 实际内容，不要凭印象。

## 现状基线（动手前必读）

- skill 根目录：`/Users/liyijiang/.agents/skills/build123d-cad/`
- 现有 `SKILL.md` ≈ 1535 行（待拆为父 200 行 + mechanical 子 350 行）
- 现有能力扎实区：5 个 Playbook 协议、13 示例零件、视觉验证系统、Dave Cowden / Peter Corke 哲学
- 短板区：`benchmarks/` 空、`code-sources/robotics.md` 等被引用但不存在、`data-sources/` 仅 4 类
- earthtojake 子 skill 已在本机：`cad / cad-viewer / urdf / srdf / sdf / gcode / sendcutsend / bambu-labs / step-parts`，可直接 Read 参考

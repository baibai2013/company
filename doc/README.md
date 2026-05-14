# 公司 AI 多智能体系统 — 文档索引

## 项目概述

一套基于 **LangGraph + A2A 协议**的多智能体协作系统，模拟公司各职能员工（产品、机械、硬件、固件、算法、测试、成本、项目经理、系统运维、技术负责人），通过飞书机器人接受指令，自动完成从需求分析到技术落地的完整工作流。

---

## 文档目录

### 架构文档（详细）

| 文件 | 内容 |
|------|------|
| [architecture/01-overview.md](architecture/01-overview.md) | 系统全貌、技术栈、端口分配 |
| [architecture/02-agents.md](architecture/02-agents.md) | Agent 设计、SmartGraph、A2A 协议 |
| [architecture/03-feishu.md](architecture/03-feishu.md) | 飞书集成、消息路由、员工 Bot |
| [architecture/04-backend.md](architecture/04-backend.md) | Backend API、数据模型、SSE 事件 |
| [architecture/05-infrastructure.md](architecture/05-infrastructure.md) | Docker 服务、PostgreSQL、Redis |
| [architecture/06-data-flows.md](architecture/06-data-flows.md) | 核心数据流、时序图 |

### 使用教程

| 文件 | 内容 |
|------|------|
| [usage.md](usage.md) | 安装、启动、飞书交互方式 |

### 优化与演进

| 文件 | 内容 |
|------|------|
| [optimization-agentscope.md](optimization-agentscope.md) | 借鉴 AgentScope 的 4 个优化方向（Tracing/Memory Marks/可测试性/MsgHub） |

### 注意事项

| 文件 | 内容 |
|------|------|
| [notes.md](notes.md) | 已知限制、运维陷阱、常见问题 |

---

## 快速启动

```bash
cd /Users/liyijiang/work/company
./start.sh              # 启动全部服务
./start.sh --no-docker  # 跳过 Docker（已在运行时）
./stop.sh               # 停止全部服务
```

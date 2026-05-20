# blueprint.am 调研笔记

> 调研时间：2026-05-18 / 修订：2026-05-19（v2 加了 Producthunt + 3E8 about 页 + GitHub 反查）
> v1 错："是 Hack Club 学生项目"——只查了 apex 没查 `www.` 子域
> v2 错：Twitter handle 写 `@davidfeldt`——HTML meta 是 typo，**正确应为 `@david_feldt3`**（Producthunt 修正）
> v3 信息源覆盖：首页 SPA bundle + Producthunt + 3e8robotics.com about + GitHub davidfeldt 反查

---

## 1. 一句话

**Blueprint.am 是 3E8 Robotics（Founders Inc. 投资的硅谷配送机器人公司）孵化的免费 AI 硬件设计工具**：从一句 prompt 生成 BOM + 采购链接 + 接线图 + 装配指南 + 粗略机械渲染图，面向 maker / 工程师 / 创业者**在投钱投时间前快速验证硬件想法的可行性**。

## 2. 硬事实

### 2.1 产品

| 项 | 值 | 来源 |
|---|---|---|
| 产品名 | Blueprint.am | `<title>` |
| 站内 Tagline | "AI Hardware Design Tool" | `<title>` |
| **Producthunt Tagline** | **"From hardware idea to build plan with a single prompt"** | PH 页面 |
| **核心痛点** | "Most hardware builders waste weeks researching parts, reading datasheets, and comparing specs before they even know if their idea is feasible" | PH 页面 |
| **核心价值** | **快速验证硬件想法的可行性**（不是出最终设计） | PH 页面 |
| **5 大产物** | ① Full parts list ② Sourcing links ③ Wiring diagrams ④ Build instructions ⑤ **Rough mechanical render** | PH 页面 |
| 类型 | WebApplication / DesignApplication | schema.org JSON-LD |
| 价格 | 名义免费，但 bundle 里有 free / pro / ultra 三档 + Stripe + credit 制（详见 `blueprint-am-pipeline.md` §6） | bundle 反编译 |
| 目标用户 | maker / engineer / founder（验证硬件 idea） | PH + meta keywords |
| Producthunt 上线 | **2026-04-24 左右**（PH 显示 launched 25d ago，调研日期 2026-05-19） | PH |
| Producthunt 数据 | 1 upvote / 3 followers / **0 reviews**——**冷启动失败**，几乎无人发现 | PH |
| 技术栈（前端） | Vite SPA / React / react-three-fiber（3D） / ELK Layered（接线图布局） / JetBrains Mono / Google Tag Manager / PostHog / Sentry | bundle 反编译 |
| 后端 LLM | **Google Gemini**（异步 jobs 模式，不是 GPT 不是 Claude） | bundle 反编译 |
| 托管 | **Vercel**（响应头 `server: Vercel`，`x-vercel-id`） | HTTP 头 |
| 数据库 | **Supabase**（profiles / projects / stars / subscriptions 4 张表） | bundle 反编译 |

### 2.2 公司

| 项 | 值 | 来源 |
|---|---|---|
| 母公司 | **3E8 Robotics** | meta author + schema creator |
| 主营产品 | **Elly** —— 自主室内配送机器人（"第一个像人类一样用电梯的机器人"） | 3e8robotics.com |
| Elly 定位 | "Autonomous Vertical Deliveries"，为酒店 / 办公楼 / 住宅塔楼设计 | 3e8robotics.com |
| **创立 / 起源** | **多伦多 → 现硅谷**，由 4 个朋友联合创立 | 3e8robotics.com about |
| **融资** | venture-backed，**Founders, Inc. 加速器**（旧金山著名 maker 友好基金）投资 | 3e8robotics.com about |
| **商业进展** | 与多家"领先酒店和住宅塔楼"合作，**SF 已开始早期试点** | 3e8robotics.com about |
| 公司站 | https://www.3e8robotics.com/ | DNS |
| Blueprint.am 在公司里的位置 | 副产品 / 工具线 / **品牌 + 招聘 pipeline 入口**（推断） | 推断 |
| 名字典故 | 3e8 = 3×10⁸ ≈ 光速 m/s，理工梗 | — |

### 2.3 团队 / 联系

| 项 | 值 | 来源 |
|---|---|---|
| **Twitter handle**（修正） | **`@david_feldt3`**（不是 meta 里写的 `@davidfeldt`，那是 typo） | Producthunt |
| Maker 1 | David Feldt（@david_feldt3） | PH |
| Maker 2 | Sajeel Purewal（@sajeel_p）—— 推测为 4 个 co-founder 之一 | PH + 3E8 about |
| 团队规模 | 4 个 co-founder + roboticists / engineers / builders | 3e8robotics.com about |
| Discord 社区 | https://discord.gg/QKpqjqhKKU | bundle |
| 域名注册人 | Domain Privacy 隐藏（注册商：ABCDomain LLC） | whois |
| 域名注册时间 | 2026-03-04（约 2 个半月前；PH 上线又晚了 50 天） | whois |
| **同名异人警告** | GitHub `davidfeldt` 是另一个 David Feldt（JazLabs CEO，React Native / PHP），**与 3E8 / blueprint 无关** | GitHub 反查 |

### 2.4 域名结构

| 入口 | 解析 | 状态 |
|---|---|---|
| `blueprint.am` | 216.150.1.1（同 IP 反查到 `hackclub.com`，可能是占位 / Anycast / DNS 配置遗留） | 80/443 连接超时 |
| `www.blueprint.am` | `f1634e69e8108a26.vercel-dns-017.com.` → 216.150.16.129 / 216.150.1.129（Vercel 边缘） | 200 OK，正常服务 |

**坑**：apex 没配 redirect 到 www，浏览器直接打 `blueprint.am` 会卡超时——他们品牌名都用 `Blueprint.am`，但实际服务在 `www.blueprint.am`。这是个产品体验 bug。

## 3. 商业模式推断

3E8 Robotics 的主商业模式是 **B2B 卖配送机器人**（Elly，目标：酒店 / 办公楼）。Blueprint.am 当前免费，可能性：

1. **流量入口 + 品牌建设**：吸引 maker 社区注意，长期沉淀对 3E8 的认知 / 招聘 pipeline
2. **内部工具对外免费版**：公司内部设计 Elly 时用的 AI 设计辅助工具的简化版，开放给社区
3. **GTM 工具试水**：测试 AI hardware design 这条赛道有没有付费意愿，将来可能上付费 tier

按"免费 + 公司只放官方域名"这种轻量姿态，**最可能是第 1 种（品牌 / 流量）**。

## 4. 推荐看什么 / 不要看什么

**值得跟进**（如果你关心 maker tooling / hardware AI）：
- 主页跑一下生成流程，看 prompt → BOM → 接线图的实际质量
- 看接线图引擎是 LLM 直接出还是 LLM + KiCad / Fritzing 后端
- 看 BOM 是不是实际可下单（有没有接 Mouser / DigiKey API）

**不值得花时间**：
- 主营业务 Elly 配送机器人——跟我们做的仿生四足狗品类完全不同（轮式 vs 腿式 / 室内配送 vs 通用 / B2B vs 探索），也不是直接竞品

## 5. 与本仓的关联

**间接相关**：3E8 Robotics 是机器人公司，做的是"轮式室内配送"，跟我们四足腿式仿生狗在同一大行业但不同细分。值得记住的是他们的 **AI hardware design 工具思路**——一句话生成接线图 / BOM / 装配指南，对我们设计机器狗硬件原型时的内部加速有借鉴价值。

如果将来要做"硬件团队内部 AI 设计助手"，blueprint.am 是值得对标的 reference 产品。

## 6. 调研路径回顾（带教训）

### 6.1 这次的关键失误

只 curl `https://blueprint.am`（apex），看到超时就走了 IP / Wayback / Shodan / whois 推断之路。**正确做法：apex 不通时立刻试 `www.` 子域**，因为现代静态站经常只在 `www.` 上配 SSL + CDN，apex 是死的或指错地方。

### 6.2 标准调研流程（修订版）

```
1. curl -sIL https://<domain>          # apex
2. curl -sIL https://www.<domain>      # ★ www 子域，必试
3. dig +short <domain> www.<domain>    # 比对 DNS
4. curl -sL https://www.<domain> | grep -E 'title|description|og:|application/ld+json'
                                        # ★ HTML head 通常一锅端给你产品名/公司/作者
5. 看 HTML 引用的 favicon / canonical / og:image 路径，能逆推托管
6. 跑 Twitter handle / Author 到 x.com / linkedin / github
7. 看公司主站（meta author / og:site_name 给的线索）
```

第 2 步 + 第 4 步合起来就是 80% 信息，前面那一长串 whois / Shodan / IP 反查只在 step 1+2 都失败时才需要。

### 6.3 教训

- 域名超时不等于站点不存在，先试 `www.`
- HTML head 的 schema.org JSON-LD + og: + meta description 是 SEO 标配，几乎所有产品站都有，比 whois 更直接
- 推断链一旦走偏（比如基于"hackclub IP"那条线），后续越走越远；定期回头核对最基础的事实（"我连首页都没打开过"）
- **网站 meta 里的 social handle 可能是 typo**——交叉对照 Producthunt / 公司 about 页确认；GitHub 同名账号要反查 bio 验证是不是同一个人

## 7. 怎么用（用户视角操作流）

PH 页面给的官方流程：

```
1. 输入一句自然语言 prompt（"build me a smart home temperature monitor"）
2. 等异步 job 跑完（Gemini 后端，可能 30s-2min）
3. 拿到 5 个产物：
   ① Full parts list（含元件类别、关键 spec、datasheet 链接）
   ② Sourcing links（DigiKey / Mouser / Adafruit / SparkFun / Amazon / AliExpress 多源比价）
   ③ Wiring diagrams（ELK 自动布局的 SVG）
   ④ Build instructions（step-by-step，含工具 / 材料）
   ⑤ Rough mechanical render（react-three-fiber 在浏览器跑的 3D 视图）
4. 可保存项目（Supabase `projects` 表）/ publish 公开 / star 收藏 / 用 username 做社交
5. 免费配额用完进 Stripe 订阅（free / pro / ultra 三档）
```

**它不解决**：PCB 走线 / 实际机箱设计 / 固件代码——只到"可行性 + 概念图"层级。

## 8. 来源

- https://www.blueprint.am/ — 产品首页（HTML + 3.7MB SPA bundle 反编译）
- https://www.producthunt.com/products/blueprint-am — Producthunt 页面（tagline / 团队 / launch 时间）
- https://www.3e8robotics.com/ — 母公司主页（Elly 产品）
- https://www.3e8robotics.com/about — 公司 about 页（创立 / 融资 / 商业进展）
- https://x.com/david_feldt3 — 创始人 X handle（修正版）
- https://github.com/davidfeldt — **同名异人**反查（确认与本案无关）
- https://discord.gg/QKpqjqhKKU — Blueprint.am Discord 社区
- `doc/blueprint-am-pipeline.md` — 配套技术拆解

# Blueprint.am 技术拆解：它怎么从一句话生成硬件设计

> 调研时间：2026-05-19
> 数据源：站点首页 SPA bundle（3.7MB Vite 编译产物，未混淆变量但代码体仍是压缩态）
> 配套文档：`doc/blueprint-am-analysis.md`（产品 / 公司视角）

---

## 1. 总览：4 段流水线

```
用户 prompt：
"做一个 ESP32 + DHT22 温湿度上报到 MQTT"
        │
        ▼
┌───────────────────────────────────────────────┐
│ ① BOM 生成：Gemini → 元件清单 + 类别 + 供应商   │
│   /api/gemini  (异步 jobs)                     │
└──────────────────┬────────────────────────────┘
                   │
                   ▼
┌───────────────────────────────────────────────┐
│ ② 元件深化：每个元件 4-prompt 流水线            │
│   pM(类别)  → 推荐主货号                        │
│   uS(类别)  → 提取关键技术规格                   │
│   dM(类别)  → 找 datasheet / 文档              │
│   yV(类别)  → 找入门教程 / 项目示例              │
└──────────────────┬────────────────────────────┘
                   │
                   ▼
┌───────────────────────────────────────────────┐
│ ③ 接线图：LLM 出 nodes/edges → ELK 自动布局    │
│   Eclipse Layout Kernel (layered algorithm)    │
│   渲染成 SVG                                    │
└──────────────────┬────────────────────────────┘
                   │
                   ▼
┌───────────────────────────────────────────────┐
│ ④ 装配指南：Gemini → step 列表（含工具/材料）  │
│   3D 视图：react-three-fiber 渲染              │
│   外链 GrabCAD 让用户找模型                     │
└───────────────────────────────────────────────┘
```

## 2. 后端架构

### 2.1 LLM 引擎：Google Gemini（不是 GPT 不是 Claude）

证据：bundle 里只见 `/api/gemini` 和 `/api/gemini/jobs`，零 OpenAI / Anthropic 引用。

- 用 **Gemini Jobs API**（异步任务模式）：用户 prompt 提交后返回 job id，前端轮询拿结果
- 模型版本未在前端暴露（应该在后端固定，可能是 Gemini 2.0 / 2.5 Flash / Pro）

**为什么选 Gemini**：推测是性价比 + 长上下文 + 多模态（看接线图回写）。也可能 Google 给了 startup credit 优惠。

### 2.2 后端：Vercel Serverless + Supabase

**Vercel API Routes**：
```
/api/gemini              生成主入口
/api/gemini/jobs         异步任务轮询
/api/projects/save       保存项目
/api/publish             发布
/api/unpublish           取消发布
/api/star                项目点赞
/api/auto-username       自动分配用户名
/api/set-username        改用户名
/api/broadcast           站内广播
/api/check-subscription  订阅状态查询
/api/consume-credit      扣点
/api/get-credits         查余额
/api/stripe/checkout-session  开通订阅
/api/stripe/portal-session    管理订阅
```

**Supabase 表结构**（从 `.from()` 调用反推）：
- `profiles` 用户档案
- `projects` 用户的硬件项目
- `stars` 项目收藏
- `subscriptions` 订阅状态

### 2.3 监控 / 分析栈

- **Sentry** 前端错误上报
- **PostHog** 产品分析（事件埋点）
- **Discord** 社区入口（`discord.gg/QKpqjqhKKU`）

## 3. BOM 生成 pipeline 细节

### 3.1 元件分类体系

代码里见到的明确 component category 共 **12 类**：

| 类别 | 例子 | 推断的代码常量 |
|---|---|---|
| `microcontroller` (MCU) | ESP32, RP2040, STM32 | xV |
| `sensor` | DHT22, BMP280, MPU6050 | rRn |
| `actuator` | 步进电机, 舵机 | sRn |
| `power` | LDO, DC-DC, 锂电管理 | oRn |
| `module` | WiFi/BT, LoRa, 4G | aRn |
| `display` | OLED, TFT, e-paper | — |
| `structural` | 铝型材, 角铝 | — |
| `enclosure` | 外壳, 面板 | — |
| `mechanism` | 轴承, 齿轮, 联轴器 | — |
| `hardware` | 螺丝, 螺母 | — |
| `3D-printed` | 自打印件 | — |
| `generic` | 兜底 | — |

每类有独立的 4-prompt 套件（详见 §3.2）。

### 3.2 每元件 4-prompt 流水线

每个生成出的元件会跑 4 个独立 LLM 调用（前端 bundle 暴露的提示词模板）：

#### Prompt 1：`uS()` —— Key specs（关键规格）

举例（MCU）：
> "Key specs for this MCU: clock speed, number of GPIO pins, flash/SRAM size, operating voltage, built-in peripherals (ADC, UART, SPI, I2C, WiFi, BLE). Include 6-10 of the most important specs."

举例（sensor）：
> "Key specs for this sensor: measurement range, accuracy/precision, resolution, output interface (I2C/SPI/analog), operating voltage, response time, power consumption."

举例（actuator）：
> "Key specs for this actuator: torque (stall and rated), speed/RPM, step angle (if stepper), operating voltage, current draw (no-load and stall), weight, shaft diameter."

每个类别都列出"对硬件工程师最有用的 6-10 个参数维度"——这是这套系统的**核心 know-how 注入点**：硬编码了"什么类别要看什么参数"的工程师常识。

#### Prompt 2：`dM()` —— Datasheet 查找

举例（MCU/IC）：
> "Find the datasheet PDF, thermal derating curves, application circuit recommendations, capacitor selection guides, and PCB layout guidelines."

举例（结构件）：
> "Find material grade, tensile/yield strength, finish/coating details, and relevant standards (ISO, DIN, ASTM) for this structural component."

举例（外壳）：
> "Find material specifications (ABS/polycarbonate/aluminum), IP/NEMA rating, UV resistance, flammability rating, and gasket/seal details."

输出：`datasheetUrl` 字段（前端会拼成可点链接）。

#### Prompt 3：`yV()` —— Tutorials / examples（教程）

举例（MCU）：
> "Find beginner-friendly tutorials: blink, WiFi connectivity, peripheral usage (I2C sensors, SPI displays, PWM), and complete project examples using this MCU."

举例（sensor）：
> "Find tutorials showing how to read data from this sensor, calibration procedures, data logging projects, and integration with displays or IoT platforms."

输出：教程链接列表。**这是 maker 体验的关键加分项**：不只给元件还给"怎么用"。

#### Prompt 4：`pM()` —— 主推荐（具体型号 + 货源）

输入是元件类别常量（xV/rRn/sRn/oRn/aRn 等），输出推荐的具体型号和供应商链接。

### 3.3 供应商体系（vendor 列表硬编码）

代码里 hardcode 两套 vendor：

**电子元件**：
```
DigiKey, Mouser, Adafruit, SparkFun, Amazon, AliExpress
```

**结构件 / 五金**：
```
Amazon, Home Depot, McMaster-Carr, Grainger, AliExpress
```

DigiKey / Mouser 给认真用户、AliExpress 给预算敏感、Adafruit / SparkFun 给 maker、Amazon 给即时到手——**做了精细的 vendor 分层**。

## 4. 接线图：ELK Layered Layout

### 4.1 为什么是 ELK 不是 dagre / cytoscape

**ELK = Eclipse Layout Kernel**，是 Eclipse 基金会开源的**专业级图论自动布局算法库**。比 dagre / d3-force 更工业化，特别擅长 **layered**（层式）布局——适合电路图（电源 → 元件 → 地）这种有方向的 DAG。

### 4.2 ELK 配置（前端实际用到的参数）

```javascript
{
  "elk.algorithm": "layered",
  "elk.direction": "...",      // 横向 / 纵向
  "elk.edgeRouting": "...",    // 边的绕线策略（orthogonal / polyline / spline）
  "elk.layered.crossingMinimization.strategy": "...",
  "elk.layered.nodePlacement.strategy": "...",
  "elk.layered.spacing.edgeEdgeBetweenLayers": ...,
  "elk.layered.spacing.edgeNodeBetweenLayers": ...,
  "elk.layered.spacing.nodeNodeBetweenLayers": ...,
  "elk.spacing.edgeEdge": ...,
  "elk.spacing.edgeNode": ...,
  "elk.spacing.nodeNode": ...,
}
```

层式布局 + 正交边路由 + 三种 spacing 微调——这是标准电路图美学。

### 4.3 数据流

```
LLM 输出（结构化 JSON）：
{
  "components": [
    {"id": "mcu1", "name": "ESP32", "pins": ["GND", "3V3", "GPIO4", ...]},
    {"id": "sensor1", "name": "DHT22", "pins": ["VCC", "DATA", "GND"]}
  ],
  "connections": [
    {"from": "mcu1.3V3", "to": "sensor1.VCC"},
    {"from": "mcu1.GPIO4", "to": "sensor1.DATA"},
    {"from": "mcu1.GND", "to": "sensor1.GND"}
  ]
}

   ↓ 前端转换

ELK graph：
{
  children: [
    { id: "mcu1", labels: [{text: "ESP32"}], ports: [...], width, height },
    { id: "sensor1", ... }
  ],
  edges: [
    { id: "e1", sources: ["mcu1.3V3"], targets: ["sensor1.VCC"] },
    ...
  ]
}

   ↓ ELK.layout(graph)

带坐标的 graph
   ↓ 渲染
SVG 接线图
```

**关键洞察**：LLM 不直接画图，只输出**结构化的 nodes + edges**，由 ELK 算法生成坐标。这是 LLM-to-CAD 的标配模式——AI 出语义，专业引擎出几何。

## 5. 3D 装配指南

### 5.1 渲染：react-three-fiber

`react-three-fiber` 是 Three.js 的 React 封装，前端直接在浏览器跑 WebGL 渲染。

### 5.2 3D 模型来源：GrabCAD（外链，不是 API 集成）

代码里只有 `https://grabcad.com/library` 这种**静态链接**，**没有**调 GrabCAD API 拉模型的痕迹。推测：

- GrabCAD 是个 3D 模型社区，不开放免费 API
- Blueprint.am 大概率**不直接载入 3D 模型**，而是给用户**链接到 GrabCAD 让自己找**
- 真正的 3D 视图可能是**简化的几何体 placeholder**（盒子 / 圆柱代表元件），而非真实 3D 模型

**这是产品的薄弱环节**：装配指南所谓的"3D"可能很基础。

### 5.3 装配步骤数据结构（推断）

代码字段见到 `guide` `instructions` `materials` `step` `tools`，推断 schema：

```typescript
interface AssemblyStep {
  step: number;
  instructions: string;        // "Connect the DHT22 VCC to the ESP32 3V3..."
  tools: string[];              // ["jumper wires", "breadboard"]
  materials: string[];          // 引用 BOM 中的元件
  // 可能还有 image / 3d view 引用
}
```

## 6. 商业模式

### 6.1 不是免费——是 credit 制

schema.org 写 "free / 0 USD" 但 bundle 里大量 credit 逻辑：

- `/api/check-subscription` — 检查订阅
- `/api/consume-credit` — 扣点
- `/api/get-credits` — 查余额
- `/api/stripe/checkout-session` — 开通订阅
- 三档 tier：**free / pro / ultra**
- 月付 / 年付切换

**模型成本驱动的 freemium**：每个生成消耗多次 LLM 调用（一个项目 N 个元件 × 每元件 4-prompt = 4N 次 Gemini 调用），免费额度用完必须付费。

### 6.2 社交属性

- `profiles` 表 + auto-username + set-username
- 项目可 publish / unpublish + star
- 站内 broadcast

**像 GitHub for hardware projects**——不只是工具，是带社交的项目库。

## 7. 关键架构选择 / 给我们的启发

### 7.1 LLM-to-CAD 的标准模式

> AI 出语义结构（JSON），专业引擎出几何 / 物理。

**不要让 LLM 直接画图**。让它输出 `{components, connections}` 这种结构化数据，扔给 ELK / KiCad / FreeCAD / OpenSCAD 这种几十年沉淀的图论 / CAD 库来出最终视觉产物。

我们做四足狗时如果做"AI 辅助硬件设计"——同样的范式：LLM 出 BOM + 接线 schema，扔给 KiCad / Altium / FreeCAD 出最终图纸。

### 7.2 元件分类驱动 prompt 工程

12 类元件 × 4 个 prompt 角度（spec / datasheet / tutorials / 主推荐）= **48 个高度专业的 prompt 模板**。这套硬编码"什么类别要看什么参数"的常识，是 Blueprint.am 比"GPT-4 直接出 BOM"领先的关键。

**借鉴**：我们做内部硬件助手时，第一件事是定义清楚**元件类别本体**（taxonomy），然后给每类写专门的 prompt，而不是一个通用 prompt 应付一切。

### 7.3 异步 jobs 模式 + credit 制

LLM 调用昂贵且慢，**绝不能同步等待 + 真免费**。Blueprint.am 的实现：

- 提交 → 拿 job id → 轮询（异步）
- 免费用户 N 次 / 月，超出付费

这是任何 LLM 重度依赖产品的标配。我们做 cc_bridge 也是异步 + 不限速但有内部预算。

### 7.4 Vercel + Supabase + 前端 SPA

整套技术栈轻得不像在做"严肃硬件设计工具"——零自建后端基础设施，几个 Vercel Serverless function 加 Supabase 表搞定一切。**精益创业的样板**。

### 7.5 哪些方面有限 / 不要崇拜

1. **3D 模型外链 GrabCAD 而非内置**——装配指南的"3D"可能就是几何体占位
2. **接线图布局靠 ELK 自动**——美观不如工程师手工调
3. **Datasheet / tutorial 查找靠 LLM**——容易给出过期或编造的链接（产品里大概率有"链接已失效"问题）
4. **没有真 PCB 设计**——只有 breadboard 接线层级，要做 PCB 还得自己手画

## 8. 如果我们要复刻 / 借鉴

**最有借鉴价值的两点**：
1. **元件分类 × 多 prompt 角度的 prompt 工程模式**（§3.2 §7.2）
2. **LLM 出语义结构 + 专业引擎出几何 的解耦模式**（§4 §7.1）

**最不值得抄的**：
- 直接用 Gemini Jobs API（我们公司已有 Claude / 通义千问基建）
- GrabCAD 外链方案（应该自建简易元件库或对接 KiCad symbol library）

## 9. 来源

- https://www.blueprint.am/ — SPA 首页
- https://www.blueprint.am/assets/index-BrGD2-lh.js — 编译后 bundle（3.7MB，2026-05-19 抓取）
- https://www.eclipse.org/elk/ — ELK 布局引擎文档
- https://docs.pmnd.rs/react-three-fiber — react-three-fiber 文档

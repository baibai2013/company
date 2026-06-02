# 05 · 制造出工链路（gcode / sendcutsend / bambu-labs / parts-catalog）

- 负责人：cost
- 协作人：mechanical（提供待制造件 STEP/STL/DXF）
- 优先级：P0（parts-catalog）/ P1（gcode · sendcutsend）/ P2（bambu-labs）
- 状态：草稿
- 依赖：P0-1 骨架、mechanical 子技能(02)、[08-shared协议](08-shared跨子技能协议.md)

---

## 1. 目标与范围

把「设计完之后怎么造出来」的链路补齐 —— 这是现 build123d-cad 最大的短板（只讲规则没有脚本）。
四个子技能，各管一段：

- **parts-catalog（P0）**：复刻 step-parts，在线找现成标准件 STEP（买得到的别自己建）。
- **gcode（P1）**：FDM 切片预检 —— 调 PrusaSlicer/OrcaSlicer CLI，报回估时 + 支撑用量 + 悬臂违规面。
- **sendcutsend（P1）**：激光切割预检 —— 导出前自动查最小切宽 / 连体岛 / 字体转曲。
- **bambu-labs（P2）**：Bambu 打印机对接（接到设备后再做）。

成本工程师牵头是因为这条链路直接关联**报价 / 工时 / 材料用量**，与成本估算同源。

## 2. 现状

- 本机已有 earthtojake `gcode / sendcutsend / bambu-labs / step-parts` skill，可直接复刻
- 现 build123d-cad `references/process/laser.md` 只讲规则，**没有导出前自动检查脚本**
- 现有本地 `build123d-parts-lib`（servos/bearings/fasteners/seals，含预生成 STEP cache）—— parts-catalog 与之互补：本地库优先，找不到再上线查

## 3. 目标目录

```
skills/parts-catalog/   (P0)    # 复刻 step-parts
├── SKILL.md / README.md
├── scripts/find_part.py        # 关键词/型号 → 在线 STEP 候选
└── tests/test_find.py

skills/gcode/           (P1)    # FDM 切片预检
├── scripts/slice_precheck.py   # 调 PrusaSlicer/OrcaSlicer CLI，回报估时/支撑/悬臂
└── tests/test_slice.py

skills/sendcutsend/     (P1)    # 激光切割预检
├── scripts/dxf_precheck.py     # 最小切宽 / 连体岛 / 字体转曲 / 闭合轮廓
└── tests/test_dxf.py

skills/bambu-labs/      (P2)    # 打印机对接
```

## 4. 任务拆解

- [ ] **T1 parts-catalog 复刻（P0）**：复刻 step-parts；策略「本地 build123d-parts-lib 优先 → 在线查兜底」
- [ ] **T2 gcode 切片预检（P1）**：`slice_precheck.py` 调切片器 CLI，输出 估时 / 支撑用量 / 悬臂违规面，**不做** Bambu 上传（设备绑定）
- [ ] **T3 sendcutsend 预检（P1）**：`dxf_precheck.py` 导出前检查最小切宽、连体岛、字体转曲、轮廓闭合
- [ ] **T4 bambu-labs（P2）**：接到打印机后复刻，做发送打印作业
- [ ] **T5 成本钩子**：切片/切割预检结果回填 估时 + 材料克重 → 供成本估算复用

## 5. 验收标准

```bash
# parts-catalog
cd skills/parts-catalog && pytest tests/   # 给型号能返回 STEP 候选

# gcode 切片预检
python skills/gcode/scripts/slice_precheck.py /tmp/part.stl
# 期望输出 JSON：{est_time, support_g, overhang_faces[]}

# sendcutsend 预检
python skills/sendcutsend/scripts/dxf_precheck.py /tmp/plate.dxf
# 期望报出违规项（最小切宽/未闭合轮廓等）或 PASS
```

## 6. 与其他文档的接口

- 上游 **mechanical(02)**：消费其 STEP/STL（→ gcode）、DXF（→ sendcutsend）
- 预览 **viewer(03)**：gcode 产物 `.gcode` 走 cad 引擎 toolpath ribbon；dxf 走 flat 2D
- 成本：预检的估时/克重输出，供公司成本估算流程复用（跨项目接口，非本 skill 内）

## 7. 不做什么

- ❌ gcode 不做 Bambu 上传（那归 bambu-labs，P2，且设备绑定）
- ❌ sendcutsend 不做真实下单（只做导出前预检与报价估算）

## 8. 议题决议(原「待讨论」)

> 2026-06-02 cost 认领后从「待讨论」翻成「决议」。两条议题在 [01 §8 跨文档汇总](01-分工与排期.md#8-跨文档待讨论汇总项目经理建议结论) 已有项目经理建议结论,本节落到 cost 自己语境里,补本机实测 + 矩阵 + 触发条件,作为 Gate 1 通过条件 1 的两个 blocker 解锁材料。
> 任何后续调整通过 [08 §8 CHANGELOG](08-shared跨子技能协议.md#8-变更登记sharedchangelogmd) 登记并通知 mechanical / fullstack / testing。

### 8.0 议题决议总表(对齐 01 §8 风格)

| 议题 ID | 议题 | 决议 | 决策权 | 状态 |
|---|---|---|---|---|
| 05-Q1 | 切片器选 PrusaSlicer 还是 OrcaSlicer 作为默认 CLI | **OrcaSlicer**(Bambu 生态友好,机器狗后续打 Bambu X1C/P1S 直接对齐),PrusaSlicer 仅作降级备选;本机未装,P0 内 cost 委托 sysadmin 安装 | cost(已认领) | 已决议(待安装) |
| 05-Q2 | parts-catalog 在线数据源优先级 | **本地 build123d-parts-lib → McMaster → step.parts → 厂商官网**,逐级回退;每级标程序化检索 / 登录 / 策略 / 法律风险四维度,见 §8.2 矩阵 | cost(已认领) | 已决议(P0 实施时核 §8.2 ⚠️ 项) |

---

### 8.1 议题 05-Q1 详细决议 — 切片器 CLI 选型

**决策**:**OrcaSlicer 作为默认 CLI**,headless 模式调 `orcaslicer --slice <stl> --load <profile.json>` 输出 G-code。PrusaSlicer 仅作降级备选,Cura / Slic3r 不选。

**理由**(三条):
1. **Bambu 生态原生**:OrcaSlicer 由 SoftFever 维护,fork 自 Bambu Studio,切片参数 / 配置 profile / 3MF 容器与 Bambu 打印机原生兼容。机器狗后续若选 Bambu X1C / P1S 作内部打印机(P2 bambu-labs 子技能落地),无需再做参数迁移。
2. **CLI 字段稳定**:OrcaSlicer / PrusaSlicer 都从 Slic3r 派生,G-code 末尾 `; estimated printing time` / `; filament used` 注释字段一致,降级到 PrusaSlicer 时 `slice_precheck.py` 解析逻辑可零修改复用。
3. **不选 Cura / Slic3r**:Cura `CuraEngine` CLI 输出 JSON 字段不规整且依赖 Cura 配置树,解析成本高;原始 Slic3r 上游已多年不动,Bambu 生态也跑不起来。

**触发条件 / 实施门槛**:
- 触发:任何 STL/3MF 进 gcode 子技能(`skills/gcode/scripts/slice_precheck.py`),都默认走 OrcaSlicer。
- 降级:OrcaSlicer CLI 退出码非 0、超时(>120s)、或本机彻底未装时,自动切到 `prusa-slicer --export-gcode`,并在结果 JSON 标 `slicer: prusa-fallback`。
- 阻塞:OrcaSlicer 未装 → `slice_precheck.py` 直接报错(不静默)+ 给安装命令,见下面「本机实测」。

**本机实测(2026-06-02)**:

```
$ which orcaslicer       → not found
$ which OrcaSlicer       → not found
$ which orca-slicer      → not found
$ which orca_slicer      → not found
$ ls /Applications | grep -iE 'orca|slicer'  → 无匹配
$ find /Applications -maxdepth 2 -iname '*orca*'  → 空
$ brew list --cask | grep -i orca  → brew cask 无 orca
```

**结论:本机未安装(任何变体)。**

**安装计划(谁负责装 / 时间点)**:
- 负责人:**cost 委托 sysadmin**(已写入本节,委托动作见 §8.5 行动项)
- 时间点:**2026-06-03 EOD 前**(P0-5 截止日同期),不阻塞 P0-5(parts-catalog 不依赖切片器),只阻塞 P1-1 gcode T2(`slice_precheck.py` CLI 调用)
- 安装方式(优先级):
  1. `brew install --cask orcaslicer` —— 首选,Homebrew Cask 已收录,版本 ≥ 2.1
  2. GitHub Release DMG <https://github.com/SoftFever/OrcaSlicer/releases> —— brew 跑挂时手动拖
- 安装后 cost 回到本节 §8.3 把「本机状态」从 ❌ 翻 ✅,贴 `orcaslicer --version` 输出与 CLI 路径(预期 `/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer`),作为 CI 复现基线
- CI 镜像层(testing 关注,P1 阶段):Linux runner 用 OrcaSlicer AppImage 或社区 Docker 镜像 `softfever/orcaslicer`,P0 内不卡

---

### 8.2 议题 05-Q2 详细决议 — parts-catalog 在线数据源优先级

**决策**:**本地 build123d-parts-lib → McMaster-Carr → step.parts → 厂商官网**,`find_part.py` 严格按此顺序回退,每级返回 ≥1 候选则截断,不再向下探。

**理由**(三条):
1. **本地优先 = 零延迟 + 零法律风险**:`~/work/build123d-parts-lib/` 已有自建 servos / bearings / fasteners / seals 的 STEP cache,license 自管(LGPL/MIT),没有抓站、登录、UA、速率问题。
2. **McMaster-Carr 是工业件命中率最高的二级源**:覆盖公制螺丝 / 型材 / 轴承 / 紧固件几乎所有常用规格,STEP / 2D DXF 双格式,反爬温和。
3. **step.parts 是 McMaster 没收录的亚洲件 / 电子件外形的兜底**:聚合 SnapEDA / GrabCAD 等 STEP 资源,但限流偏紧、维护波动大,只做次级回退。**厂商官网**最末位:每家接口都不同,代码代价高,且涉及厂商爬虫合规风险,只对前三都拿不到的专有件用。

**触发条件 / 实施门槛**:
- 触发:`find_part(query, kind=...)` 收到查询,**严格按 §8.2 矩阵顺序**逐级试探,本级有结果就 return,不并发。
- 降级:某级 timeout / HTTP 4xx 5xx / 解析失败,**写日志但不静默**,跳到下一级;全部跑完仍无结果,return 空数组并标 `exhausted: true`。
- 阻塞:McMaster 反爬升级 / step.parts 站点关停 → 写到 [08 §8 CHANGELOG](08-shared跨子技能协议.md#8-变更登记sharedchangelogmd) ⚠️ 标记,触发 cost 重新审议本表。

**8.2.1 数据源可用性矩阵(4 维度,cost 实测 + 资料调研 2026-06-02)**:

| 顺序 | 来源 | 程序化检索 | 是否需登录 | API / 爬虫策略 | 法律风险 | 综合可用性 |
|---|---|---|---|---|---|---|
| 1 | **本地 build123d-parts-lib** | ✅ Python import 直接调,有索引 | ❌ 不需要 | 无外网调用,直接读 `~/work/build123d-parts-lib/` 缓存目录 | 极低,license 自管(LGPL / MIT,见 06 license 矩阵) | ✅ 落地可用 |
| 2 | **McMaster-Carr** | ⚠️ 半程序化:无官方公开 API,但 `mcmaster-carr-api`(GitHub 第三方包)可抓 STEP,稳定 | ⚠️ 需登录(免费账号),session cookie 复用 | URL 模板 `https://www.mcmaster.com/<sku>/`,STEP 路径 `<sku>?type=cad`;速率 ≤ 1 req/s + UA 伪装即可,反爬温和 | ⚠️ 中:McMaster ToS 禁止「批量下载用于分发」,但允许「单件设计用途下载」。本项目用途即「单件用于设计」,合规;**禁止把抓下来的 STEP 重分发到公开仓库**,仅本机 cache | ⚠️ 实测可用,P0 阶段先用账号 cookie 走通 |
| 3 | **step.parts** | ✅ 有 search endpoint,返回 JSON;earthtojake `step-parts` skill 已有抓取脚本可复刻 | ❌ 不需要(公开聚合站) | URL `https://step.parts/api/search?q=<query>`(逆向出),速率 ≤ 0.5 req/s + 重试 backoff(指数 2/4/8s)避免限流;UA 用真实浏览器串 | ⚠️ 中低:聚合站本身合法,但底层资源来自厂商 / 第三方,部分件可能有版权水印。下载到的 STEP **仅设计用,不得用于商业分发模型** | ⚠️ 实测可用,但站点偶发 5xx,需重试 + 缓存 |
| 4 | **厂商官网**(BAUMER / iFM / Murata / SICK / igus 等) | ❌ 大部分需要逐家 case-by-case:有的有 REST(igus),有的纯爬(SICK),有的强制下载表单(BAUMER) | ⚠️ 视厂商:igus 不需要,BAUMER / iFM 多数要邮箱注册 | 白名单驱动:只对 §8.2.2 列入的厂商写专用抓取器;非白名单返回「请人工到 <vendor URL> 下载」 | ⚠️ 高:每家 ToS 不同,部分明确禁爬。**严格遵守 robots.txt;User-Agent 暴露 build123d-cad 来源 + 联系邮箱**,不批量并发 | ❌ 散点维护负担大,只对前三都失败的专有件触发 |

**8.2.2 厂商兜底白名单(初版,P1 内确认)**:

| 厂商 | 件类 | 接入方式 | 稳定性 | 备注 |
|---|---|---|---|---|
| igus | 直线导轨 / 衬套 / 拖链 | REST(`https://www.igus.com/contentData/download.html`) | ✅ 高 | 有官方 STEP 下载端点 |
| SICK | 传感器(IO-Link / 距离 / 视觉) | 网页爬(产品页 → 下载区) | ⚠️ 中 | 需要解析下载令牌 |
| BAUMER | 编码器 / 光电传感器 | 表单提交(邮箱 + 用途说明) | ⚠️ 低 | 仅适合手工触发 |
| iFM | 流量 / 压力传感器 | 网页爬(需登录) | ⚠️ 中 | session 抓取后落 cache |
| Murata | 电容 / 电感 / 振荡器(电子件外形) | REST(`https://www.murata.com/api/...`) | ✅ 高 | 主要给 P3 PCB 域用 |

> 第一版仅对 igus / Murata 实装抓取器(预期机器狗用得到的频率最高),其他厂商先返回「人工下载提示」,P1 末再扩。

**`find_part.py` 路由逻辑**(雏形,P0 实施时落到 `skills/parts-catalog/scripts/find_part.py`):

```python
# 伪代码
def find_part(query: str, kind: str | None = None) -> list[StepCandidate]:
    # 1. 本地优先(零延迟、零风险)
    local = build123d_parts_lib.search(query, kind=kind)
    if local:
        return [StepCandidate(src="local", path=p, cost=0) for p in local]

    # 2. McMaster(需 cookie,速率 ≤ 1 req/s)
    mcm = mcmaster.search(query, session=login_session())
    if mcm:
        return [StepCandidate(src="mcmaster", url=u, sku=s) for u, s in mcm]

    # 3. step.parts(指数 backoff,容忍偶发 5xx)
    sp = stepparts.search(query, retry=3, backoff_base=2)
    if sp:
        return [StepCandidate(src="step.parts", url=u) for u in sp]

    # 4. 厂商兜底(仅白名单,非白名单返人工提示)
    return vendor_fallback(query, kind=kind)  # 见 §8.2.2 白名单
```

**返回 schema(对接 mechanical 消费)**:

```yaml
# StepCandidate
src: local | mcmaster | step.parts | vendor:<igus|sick|baumer|ifm|murata>
path: 本地路径(src=local 时)
url:  下载 URL(src≠local 时)
sku:  型号
license: 厂家声明(local 默认 LGPL/MIT,外部抓的逐家确认 cost 在 06 跟踪)
fetched_at: ISO8601
cache_path: 下载后落到 ~/.cache/parts-catalog/<sha1>.step
exhausted: bool  # 全部源跑完仍无命中时为 true
```

**8.2.3 法律风险闸门(cost 操作纪律)**:

- ✅ 抓到的 STEP **仅本机 cache 用**,不 commit 到任何公开仓库(`~/.cache/parts-catalog/` 列入全局 `.gitignore`)
- ✅ User-Agent 标识本项目 + 联系邮箱(便于厂商投诉路径),不伪装成主流浏览器
- ✅ 严格按 robots.txt;McMaster / 厂商单线程低速访问,不并发
- ❌ 不批量预热整个 catalog(仅按需查),不二次分发抓到的 STEP
- ❌ 不绕过付费墙 / 验证码;遇到立即返回「人工触发」标记

### 8.3 gcode / sendcutsend 工具链可用性矩阵雏形

| 子技能 | 工具 | 本机状态 | 申请/获取 | 证据复用 |
|---|---|---|---|---|
| gcode | OrcaSlicer CLI | ❌ 未装 | brew install --cask orcaslicer(P0 内) | `orcaslicer --slice` 输出 G-code + 末尾估时注释 |
| gcode | (备选)PrusaSlicer | 待查 | brew install --cask prusaslicer | 字段同上,降级用 |
| gcode | 悬臂检查 | 自实现(基于 build123d 几何) | 无外部依赖 | 法线投影 + 角度阈值,直接读 STL |
| sendcutsend | ezdxf(DXF 解析) | 待查 `pip show ezdxf` | `pip install ezdxf` | 检查闭合轮廓 / 最小切宽 / 字体转曲 |
| sendcutsend | SCS 在线报价(可选) | 无官方 API | 网页爬虫(P2 再做,不卡 P1) | 报价复用到成本钩子 |
| parts-catalog | mcmaster 抓取 | 待查 `pip show mcmaster` | `pip install mcmaster` 或自实现 | STEP 文件下载 + sku |
| parts-catalog | step.parts 抓取 | 复刻 earthtojake step-parts | 无需新装,直接搬 scripts | 同上 |

> cost 在 P0-5 / P1-1 实施时把「本机状态」列逐项核实(`pip show` / `brew list`),并把缺的写到 sysadmin 申请单。
> 未核实打 ⚠️,核实通过打 ✅,缺失打 ❌。

### 8.4 与成本钩子(T5)的接口预告

切片预检 + 切割预检的输出会回填到公司「成本估算流程」(跨项目接口,不在本 skill 内,但本 skill 输出 schema 必须对齐):

```yaml
# 切片预检输出(gcode)
estimated_print_time_min: 142
filament_used_g: 38.5
filament_used_m: 12.7
support_g: 4.2
overhang_violations:
  - face_id: 17
    angle_deg: 62
    area_mm2: 124
recommended_orientation: [0, 0, 1]  # 给 viewer 默认视角

# 切割预检输出(sendcutsend)
material: aluminum_5052
thickness_mm: 3.0
total_cut_length_mm: 1820
violations:
  - kind: min_kerf
    location_xy: [12.4, 33.1]
    actual_mm: 0.6
    min_allowed_mm: 0.8
  - kind: open_contour
    contour_id: 7
estimated_quote_usd: null  # P1 不做真实报价,P2 接 SCS API 后填
```

cost 在 P1 实施时把这两份 schema 写到 [08 §2.x](08-shared跨子技能协议.md#2-handoff-protocolsmd子技能串接) 跨技能 handoff 协议里登记。

### 8.5 行动项(从决议派生)

| # | 行动 | Owner | 截止 | 状态 |
|---|---|---|---|---|
| A1 | 委托 sysadmin `brew install --cask orcaslicer`,装完贴 `--version` 输出回 §8.1 | cost | 2026-06-03 EOD | 待委托(本节决议后立即触发) |
| A2 | 在 §8.3 把 `pip show ezdxf` / `pip show mcmaster` 实测结果回填,缺的写 sysadmin 申请单 | cost | 2026-06-04 | 待 P0-5 启动 |
| A3 | igus / Murata 抓取器初版(§8.2.2 白名单首批两个),其余厂商先返回人工提示 | cost | 2026-06-12(P1-1 截止同步) | 待 P0-5 完成 |
| A4 | `~/.cache/parts-catalog/` 加全局 `.gitignore`,确保抓到的 STEP 不被误 commit | cost | 2026-06-03(P0-5 实施前) | 待执行(实施期一并做) |
| A5 | §8.4 schema 同步到 [08 §2.x handoff](08-shared跨子技能协议.md#2-handoff-protocolsmd子技能串接),@tech_lead Review | cost | 2026-06-12 | 待 P1-1 实施 |

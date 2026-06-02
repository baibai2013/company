# 03 · viewer 多引擎子技能（网页预览容器）

- 负责人：fullstack @ 2026-06-02
- 协作人：mechanical（提供 STEP 样件）、hardware（P3 提供 PCB 样件）、algorithm（提供 URDF 样件）
- 优先级：P0（cad engine 实跑 + 路由）/ P1（headless 降级链）/ P3（pcb/sch/sim engine 落地）
- 状态：评审中(已细化:21 条目路由表 / cad engine 实跑规格 / stub API / P1-5 三档降级链 / §15 与 01 §8 对齐;待 Gate 1 锁定)
- 本机依赖:VS Code OCP CAD Viewer 扩展 `bernhard-42.ocp-cad-viewer` ✓ **已装**(2026-06-02 验证),Tier 2 OCP 后端可用
- 依赖：P0-1 骨架、[08-shared协议](08-shared跨子技能协议.md)

---

## 1. 目标与范围

把网页预览升级成**「多引擎容器」**：一个统一 node server，按文件后缀路由到不同前端引擎。
P0 先把 CAD 引擎实跑（复刻 earthtojake 的 cad-viewer），同时把 pcb / sch / sim 引擎的
**目录 + 路由 + 占位页**建好，P3 再填充实现 —— 让扩展是「填空」而非「重构」。

这是用户重点扩展项：**「目前预览是 CAD，后面还要预览 PCB / 电路原理图 / 模拟仿真」**。

设计目标（用户感知顺序）：
1. **一句话起服务**：`bash start.sh <file>` → 拿到 URL 直接打开。
2. **后缀决定一切**：扩支持新格式 = 在 `router.mjs` 加一行 + 在 `engines/<name>/` 放静态文件。
3. **端口不爆炸**：所有引擎共享一个 server，按 `?engine=` 参数选 SPA。
4. **复用而非重启**：同一 workspace 已有活 server 就复用，不再开新端口。
5. **CI/飞书友好**：`web_preview.py` 在没有浏览器的环境下能降级为 PNG 截图（P1）。

## 2. 现状

- 本机已有 earthtojake 的 `cad-viewer` skill（`/Users/liyijiang/.agents/skills/cad-viewer/`），含：
  - `scripts/viewer/backend/server.mjs`（26104 行 bundled，Three.js + Vite SSR runtime + STEP/STL/GLB loaders）
  - `scripts/viewer/dist/`（13M，前端打包产物，含 `assets/` + `index.html`）
  - 已实现 `/__cad/server` 健康检查 + `--shutdown-after 12h` + PID/git/workspaceRoot 复用协议
  - 默认端口 4178（`DEFAULT_VIEWER_PORT`），`serverApiVersion=2`，`dynamicRoot=true`
- 现 build123d-cad 的预览：PNG cache 后端 OCP 优先 → VTK 兜底，依赖 VS Code 插件，CI/机器人不友好
- **复刻策略**：直接 `cp -R cad-viewer/scripts/viewer/{backend,dist} → skills/viewer/scripts/engines/cad/`，
  外面新写一个**只做后缀路由**的极薄 `server.mjs`，把 `?engine=cad` 反代/静态映射到 cad engine 资源。

## 3. 目标目录

```
skills/viewer/
├── SKILL.md                    # ≤ 250 行，声明引擎路由表 + URL 协议 + start.sh 用法
├── README.md                   # 给开发者：怎么加新引擎
├── references/
│   ├── cad-engine.md           # P0：STEP/STL/GLB/3MF/URDF/SRDF/SDF/G-code/DXF
│   ├── pcb-engine.md           # P3：KiCad PCB / Gerber 三维+二维
│   ├── sch-engine.md           # P3：KiCad 原理图 / SVG
│   ├── sim-engine.md           # P3：仿真轨迹 / 波形 / 录屏
│   ├── routing.md              # 后缀 → 引擎 映射表（扩展核心）
│   ├── url-protocol.md         # ?engine= / ?dir= / ?file= 协议（高扇入接口）
│   ├── server-reuse.md         # 端口/pid/git/workspace 复用规则（复刻 cad-viewer）
│   ├── viewer-features.md      # 复刻 cad-viewer 细节（关节滑块 / 截面 / 测量）
│   ├── headless-fallback.md    # P1：Web → OCP → VTK 降级链
│   └── moveit2-server.md       # 复刻 cad-viewer 同名 ref（机器人规划集成）
├── scripts/
│   ├── backend/
│   │   ├── server.mjs          # 统一 HTTP server，按后缀分发 + /__cad/server 协议
│   │   └── router.mjs          # 后缀 → 引擎 SPA 路径（纯函数，可单测）
│   ├── engines/
│   │   ├── cad/                # P0：复刻 cad-viewer/{backend,dist}（Three.js）
│   │   │   ├── backend/server.mjs   # 来自 cad-viewer，作为 cad 子模块
│   │   │   └── dist/                # Three.js + 各 loader 打包产物
│   │   ├── pcb/                # P3 占位：index.html + assets/placeholder.png
│   │   ├── sch/                # P3 占位
│   │   └── sim/                # P3 占位
│   ├── package.json            # type:module，无 npm install（直跑 node）
│   ├── start.sh                # 父级 wrapper，与引擎无关
│   └── web_preview.py          # Python launcher（飞书/CI 调用入口）
└── tests/
    ├── conftest.py             # 共享 fixture：临时端口、样件路径
    ├── test_routing.py         # 后缀路由正确性（纯函数，不起 server）
    ├── test_url_assembly.py    # URL 拼装 ?engine=&dir=&file= 编码 + 安全
    ├── test_start.py           # 启动 server / 端口探测 / shutdown
    ├── test_server_reuse.py    # 同 workspace 复用、不同 git 不复用
    ├── test_cad_engine.py      # P0 CAD 端到端（headless Chrome 截图断言）
    ├── test_placeholders.py    # P3 占位页 200 + 含「待实现」字样
    └── test_{pcb,sch,sim}_engine.py  # P3 占位（pytest.skip 标记）
```

## 4. 引擎路由表（`scripts/backend/router.mjs`）

`router.mjs` 导出**纯函数** `routeByExtension(filePath)`，便于单测：

```js
// router.mjs（规格示意，非实现）
export const ENGINE_ROUTES = [
  // ========== cad engine（P0/P1：3D + 2D 几何 + URDF 家族 + 工艺文件 + 图片） ==========
  { ext: ['.step', '.stp'],                         engine: 'cad' },  // P0 OCC native
  { ext: ['.brep'],                                 engine: 'cad' },  // P0 OCCT 边界表示
  { ext: ['.iges', '.igs'],                         engine: 'cad' },  // P1 OCP 转 GLB
  { ext: ['.stl'],                                  engine: 'cad' },  // P0
  { ext: ['.glb', '.gltf'],                         engine: 'cad' },  // P0 GLTFLoader
  { ext: ['.obj'],                                  engine: 'cad' },  // P1 OBJLoader
  { ext: ['.3mf'],                                  engine: 'cad' },  // P0 3MFLoader
  { ext: ['.fcstd'],                                engine: 'cad' },  // P3 FreeCAD CLI 转 STEP
  { ext: ['.urdf', '.srdf'],                        engine: 'cad' },  // P0 urdf-loader-three
  { ext: ['.sdf'],                                  engine: 'cad' },  // P1 sdf2urdf 桥接
  { ext: ['.gcode', '.nc'],                         engine: 'cad' },  // P0 toolpath ribbon
  { ext: ['.dxf'],                                  engine: 'cad' },  // P0 dxf-parser + Canvas
  { ext: ['.png', '.jpg', '.jpeg', '.webp'],        engine: 'cad' },  // P0 raw image inline (?mode=image)
  // ========== pcb engine（P3 占位） ==========
  { ext: ['.kicad_pcb'],                            engine: 'pcb' },  // P3 tracespace + kicad-cli gltf 桥接
  { ext: ['.gbr', '.ger', '.drl', '.gtl', '.gbl'],  engine: 'pcb' },  // P3 tracespace 纯 web Gerber
  // ========== sch engine（P3 占位） ==========
  { ext: ['.kicad_sch', '.sch'],                    engine: 'sch' },  // P3 KiCanvas
  { ext: ['.svg'],                                  engine: 'sch' },  // P3 inline + 缩放（原理图导出场景）
  // ========== sim engine（P3 占位） ==========
  { ext: ['.csv'],                                  engine: 'sim' },  // P3 plotly.js
  { ext: ['.mp4', '.webm'],                         engine: 'sim' },  // P3 HTML5 video
  // .json 后缀冲突（urdf 轨迹回放 ↔ 通用配置）：router 不 sniff，
  // 由调用方显式 ?engine=sim 透传或 routeByExtension 返 'ambiguous' 让 server 报 409
  { ext: ['.json'],                                 engine: 'ambiguous' },
];

export function routeByExtension(filePath) {
  const lower = String(filePath).toLowerCase();
  for (const { ext, engine } of ENGINE_ROUTES) {
    if (ext.some(e => lower.endsWith(e))) return engine;
  }
  return null; // server.mjs 应回 400 + 列出所有支持后缀
}
```

### 4.1 完整后缀 → 引擎映射(单一权威源,共 21 条目 / 33 后缀)

> 任何子技能新增/调整产物后缀 **必须先改这张表 + 同步 ENGINE_ROUTES**,否则 server 回 400。本表是 [08-shared §2.A.2](08-shared跨子技能协议.md#2A2-viewer-url-schema已锁定) 的二级路由实现。

| # | 文件后缀 | 引擎 | 前端处理 | 优先级 | 备注 |
|---|---|---|---|---|---|
| 1 | `.step` `.stp` | cad | OCP STEP→GLB sidecar 转换 + Three.js 加载 | P0 | OCC native,默认毫米 |
| 2 | `.brep` | cad | OCP BREP→GLB sidecar(OCCT 原生格式) | P0 | mechanical 中间产物 |
| 3 | `.iges` `.igs` | cad | OCP IGES→GLB sidecar | P1 | 旧 CAD 格式,优先级低 |
| 4 | `.stl` | cad | Three.js STLLoader(直接加载,不转换) | P0 | mesh 兜底格式 |
| 5 | `.glb` `.gltf` | cad | Three.js GLTFLoader(原生最快) | P0 | viewer 内部首选交换格式 |
| 6 | `.obj` | cad | Three.js OBJLoader + MTLLoader | P1 | 老式 mesh 格式 |
| 7 | `.3mf` | cad | Three.js 3MFLoader | P0 | 3D 打印生态 |
| 8 | `.fcstd` | cad | FreeCAD CLI 转 STEP → 走 step 链 | P3 | 需系统装 FreeCAD,P3 启用 |
| 9 | `.urdf` | cad | urdf-loader-three + 关节滑块 | P0 | 高扇入,与 04 子技能联动 |
| 10 | `.srdf` | cad | URDF 渲染 + 自碰撞对高亮 | P1 | 依赖同目录 urdf 文件 |
| 11 | `.sdf` | cad | sdf2urdf 桥接(P1 由 algorithm 出脚本) | P1 | Gazebo 世界文件 |
| 12 | `.gcode` `.nc` | cad | Three.js toolpath ribbon(着色按速度/层) | P0 | 复刻 cad-viewer |
| 13 | `.dxf` | cad | dxf-parser + Canvas 2D | P0 | 钣金/激光切割预检 |
| 14 | `.png` `.jpg` `.jpeg` `.webp` | cad | server 直接返回 inline `<img>`(`?mode=image`,不走 Three.js) | P0 | viewer.snapshot 产物自显;不引入新 image engine |
| 15 | `.kicad_pcb` | **pcb** | tracespace 2D 渲染,3D 走 `kicad-cli pcb export gltf` 转 cad 链 | P3 | 需系统装 KiCad 9+ |
| 16 | `.gbr` `.ger` `.drl` `.gtl` `.gbl` | **pcb** | tracespace 纯 web Gerber(MIT) | P3 | 制造文件 |
| 17 | `.kicad_sch` `.sch` | **sch** | KiCanvas 内嵌(MIT,单 ES module) | P3 | KiCad 原理图 |
| 18 | `.svg` | **sch** | inline `<img>` + 平移缩放(假定为原理图导出) | P3 | mechanical 出图也用 svg,P3 落地时 UI 加切换 |
| 19 | `.csv` | **sim** | plotly.js 波形 | P3 | 仿真/采集数据 |
| 20 | `.mp4` `.webm` | **sim** | HTML5 `<video>` | P3 | 仿真录屏 |
| 21 | `.json` | **ambiguous** | router 不 sniff,要求调用方显式 `?engine=sim` 透传(轨迹回放) | P3 | 否则 server 回 409 + 提示「需显式指定 engine」 |

**冲突处理规则**:
- `.svg` 同时被原理图导出与 mechanical 工程图使用 → 默认 sch,调用方可 `?engine=cad` 透传强制走 cad。
- `.json` 顶层 schema sniff 留给 P3 sim 落地时再决定;P0/P1 直接 409 拒绝,避免误路由。
- 路由表未列后缀 → `routeByExtension()` 返 `null` → server 回 400 + body 列全部支持后缀(便于排查)。

**变更纪律**(对齐 [08 §8 变更登记](08-shared跨子技能协议.md#8-变更登记sharedchangelogmd)):
- 任何在 §4.1 加/删/改一行,**必须同步**:① ENGINE_ROUTES JS 块;② shared/CHANGELOG.md 一行;③ tests/test_routing.py 加用例;④ 父 SKILL.md 路由关键词若新增类型(如「图片预览」)需补关键词 → cad 映射。

**统一启动协议**：

```bash
bash skills/viewer/scripts/start.sh <file_path> [workspace_root]
# 输出（stdout 严格机器可读，唯一一行）：
#   http://127.0.0.1:<port>/?engine=<cad|pcb|sch|sim>&dir=<abs>&file=<rel>
# 退出码：0 成功 / 2 后缀不支持 / 3 文件不存在 / 4 端口分配失败
```

## 5. server.mjs 详细规格（P0 必交付）

### 5.1 启动行为

- 默认端口 `DEFAULT_VIEWER_PORT=4178`（沿用 cad-viewer，避免和外部 cad-viewer 冲突时改 `4188`，环境变量 `VIEWER_BASE_PORT` 覆盖）。
- 启动入参：`node server.mjs --workspace-root <abs> [--shutdown-after 12h] [--host 127.0.0.1] [--port 4188]`。
- 端口分配顺序：requested → 4178 → 4179 → ... → 4197（共 20 候选），逐个 `GET /__cad/server` 探活复用，全占则报错退出码 4。
- 启动后向 stdout 输出**唯一一行 JSON**：`{"url":"http://...","port":4188,"reused":false}`，便于父级 `start.sh` 解析。

### 5.2 HTTP 路由

| Path | Method | 行为 |
|---|---|---|
| `/` | GET | 解析 `?engine=&file=` → 静态返回 `engines/<engine>/index.html`（不存在则 404） |
| `/assets/*` | GET | 按 `?engine` 反代到 `engines/<engine>/assets/*`（无 `?engine` 时回 400） |
| `/files/*` | GET | 文件代理：从 `?dir=` 根读 `<rel>`，强制路径在 dir 内（防 `../`），仅放过白名单后缀 |
| `/__cad/server` | GET | 健康检查，schema 复用 cad-viewer 协议 v2 + 加一字段 `engines` |
| `/__cad/shutdown` | POST | 优雅关停（仅 127.0.0.1） |

`/__cad/server` 响应 schema（在 cad-viewer v2 基础上扩展）：

```json
{
  "schemaVersion": 1,
  "serverApiVersion": 2,
  "app": "build123d-cad/viewer",
  "engines": ["cad", "pcb", "sch", "sim"],
  "engineImpl": { "cad": "ready", "pcb": "stub", "sch": "stub", "sim": "stub" },
  "viewerVersion": "<git short sha>",
  "git": "<worktree-gitdir>:<branch>",
  "workspaceRoot": "/abs/path",
  "port": 4178,
  "pid": 12345,
  "dynamicRoot": true,
  "url": "http://127.0.0.1:4178"
}
```

复用判定（沿用 cad-viewer）：
- 探活 `/__cad/server` 返回 `app` 以 `build123d-cad/viewer` 起头 + `serverApiVersion >= 2`
- `workspaceRoot` 完全匹配
- `git` 字段双方都有时必须相等（若任一为空跳过此条）

### 5.3 安全约束

- `?dir=` 必须是绝对路径，且必须是 `--workspace-root` 的子路径或就是 workspace-root 本身。否则 403。
- `?file=` 必须是相对路径，`path.resolve(dir, file)` 后必须仍在 `dir` 内，否则 403（防目录穿越）。
- 文件代理白名单：路由表中所有后缀 + `.json .yaml .yml`（urdf 配套），其它 415。
- 仅监听 127.0.0.1，不绑公网；CI 容器内通过隧道访问。

### 5.4 shutdown 策略

- 默认 `--shutdown-after 12h`（沿用 cad-viewer）。
- 每收到一次请求重置定时器（活跃续命）。
- `SIGINT/SIGTERM` 优雅关停，等待在飞请求完成上限 5s。

## 5.5 cad engine 实跑规格(P0 必交付,复刻 earthtojake/cad-viewer)

cad 是 viewer 唯一在 P0 真实跑的引擎,这一节锁死它的物理参数,T2 复刻、T3 父 server 嵌入、T7 测试都按这套来。

### 5.5.1 静态资产清单(来自 `~/.agents/skills/cad-viewer/scripts/viewer/`)

| 路径 | 实测大小 | 用途 | 复刻动作 |
|---|---|---|---|
| `viewer/backend/server.mjs` | 26104 行 / ≈ 1.4 MB(esbuild bundled) | Three.js + Vite SSR runtime + STEP/STL/GLB/URDF/G-code/DXF loader 链 | `cp` → `engines/cad/backend/server.mjs`,**移除原顶层 HTTP listen + 端口分配**,改导出 `mountRoutes(parentApp, opts)` 给父 server 注册 |
| `viewer/dist/` | **13 MB**(2 个 hash 化 JS + WASM + 字体) | 前端 SPA(index.html + assets/) | `cp -R` → `engines/cad/dist/`,直接 git 提交(§11 红线 80M) |
| `viewer/dist/assets/*.wasm` | ≈ 5.8 MB | OCCT WASM(STEP 解析) + draco 解压 | 不动,父 server 只做静态文件服务 |
| `viewer/dist/assets/*.js` | ≈ 6.5 MB | Three.js + URDFLoader + dxf-parser + plotly chunk | 不动 |

**总占用**:13M(单引擎)→ 4 引擎全占满预计 ≤ 35M(pcb tracespace ≈ 4M、sch KiCanvas ≈ 3M、sim plotly+chart ≈ 6M),仍在 §11 红线 80M 内。

### 5.5.2 启动 cmd / 端口约定

cad engine **不独占端口**,作为父 `scripts/backend/server.mjs` 的子模块 import:

```js
// scripts/backend/server.mjs(父 server,T3 落地)
import { mountRoutes as mountCad } from '../engines/cad/backend/server.mjs';
import { mountRoutes as mountStub } from './stub-engine.mjs';

const app = createApp({ workspaceRoot, port });
mountCad(app,  { distDir: '../engines/cad/dist',  prefix: '/__engines/cad'  });
mountStub(app, { distDir: '../engines/pcb',       prefix: '/__engines/pcb', name: 'pcb' });
mountStub(app, { distDir: '../engines/sch',       prefix: '/__engines/sch', name: 'sch' });
mountStub(app, { distDir: '../engines/sim',       prefix: '/__engines/sim', name: 'sim' });
app.listen(port);
```

| 项 | 值 | 说明 |
|---|---|---|
| 父 server 端口 | 4178(默认),冲突顺移到 4179..4197 | 见 §5.1,沿用 cad-viewer 协议 |
| cad engine 端口 | **无独立端口** | 作为父 server 路由的一部分 |
| cad 子路径前缀 | `/__engines/cad/*`(资产)+ `/?engine=cad` (SPA) | 静态文件由 `dist/` 目录托管 |
| STEP→GLB 转换 | 进程内同步调用 cad/backend `convertStepToGlb()` | 不另起进程,WASM 直接在 server.mjs 跑 |
| 启动 cmd | `node scripts/backend/server.mjs --workspace-root <abs>` | 与父 server 同条命令,cad 是它的子模块 |
| 关停 | 父 server 关 → cad 子模块自然销毁 | 沿用 §5.4 `--shutdown-after 12h` |

### 5.5.3 HEADLESS 模式(P1-5 的 Tier-1 触发点)

cad engine 必须支持 `HEADLESS=1` 环境变量,行为差异:

| 环境变量 | 行为 | 用途 |
|---|---|---|
| (未设) | 完整模式:STEP→GLB + Three.js 渲染 + 关节滑块 + 截面 + 测量 | 用户开浏览器交互 |
| `HEADLESS=1` | 仅启用「STEP→GLB sidecar + 静态 GLB 服务」,不嵌入交互 UI(节省 dist load) | playwright 挂 chromium 自动截图(P1-5 Tier 1)|
| `HEADLESS=1 PROBE_ONLY=1` | 只解析 STEP 元数据(bbox / 体积 / 拓扑数),不做 GLB 转换 | P1-5 Tier 3 命令行只输出 JSON |

**实现要点**:
- HEADLESS 模式下父 server 也起,但 `?engine=cad` 时返回的 HTML 把 `index.html` 替换为「最小 GLB 显示页」(<300 行 inline HTML + Three.js GLTFLoader,不引 URDF/DXF/GCODE 模块,首屏 ≤ 1MB)。
- PROBE_ONLY 直接走 `engines/cad/backend/probe.mjs` 输出 JSON 到 stdout,跳过 HTTP 流程。

### 5.5.4 STEP→GLB sidecar 缓存

复刻 cad-viewer 的 content-hash 缓存:

```
~/.cache/build123d-cad/viewer/glb-cache/
  └── <sha256(step bytes)>.glb     # 命中直接返,不再走 OCCT
```

- 命中率目标:重复打开同一 `output/<task>/parts/<part>.step` 应 100% 命中;
- 失效策略:LRU 上限 2 GB,超过淘汰最旧;手动清理 `rm -rf` 该目录即可。
- 缓存文件**不进 git**(在 super skill 根 `.gitignore` 加 `.cache/`)。

### 5.5.5 验收

```bash
# (1) cad engine 静态资产就位
test -f skills/viewer/scripts/engines/cad/backend/server.mjs
test -d skills/viewer/scripts/engines/cad/dist/assets
du -sh skills/viewer/scripts/engines/cad/dist  # ≤ 16 MB

# (2) STEP→GLB 转换可单测(不起 server)
node -e "
import('./skills/viewer/scripts/engines/cad/backend/server.mjs').then(m =>
  m.convertStepToGlb('/tmp/hip_bracket.step').then(r =>
    console.log('triangles:', r.triangleCount)))
"

# (3) HEADLESS 模式起 server 后用 curl 验
HEADLESS=1 node skills/viewer/scripts/backend/server.mjs --workspace-root /tmp &
curl -s 'http://127.0.0.1:4178/?engine=cad&dir=/tmp&file=hip_bracket.step' | grep -q 'GLTFLoader'

# (4) PROBE_ONLY 输出尺寸 JSON(P1-5 Tier 3)
HEADLESS=1 PROBE_ONLY=1 node engines/cad/backend/probe.mjs /tmp/hip_bracket.step | jq .bbox
```

## 6. URL 协议（`references/url-protocol.md`）

```
http://127.0.0.1:<port>/?engine=<cad|pcb|sch|sim>&dir=<abs-dir>&file=<rel-file>
```

字段约束：
- `engine` 必填，枚举 4 选 1。
- `dir` 必填，绝对路径，URL-encoded。前端不直接 fetch `dir`，只把 `dir+file` 拼成 `/files/<rel>` 走后端代理。
- `file` 必填，相对 `dir`，URL-encoded，可包含子目录（`meshes/foo.stl`）。
- 可选：`engine=cad` 透传给前端的 hash 参数（如 `#frame=base_link&joint=hip:0.5`）由 cad engine 自管，不在 server 协议层定义。

**这是 viewer 对外的稳定接口**：所有上游子技能（mechanical/urdf/gcode/sendcutsend）只产文件路径,
URL 拼装由 `start.sh` / `web_preview.py` 统一生成，上游不直接拼 URL。

## 7. 任务拆解（按依赖排序）

- [ ] **T1 router.mjs（纯函数，无依赖，最先做）**
  - 写 `ENGINE_ROUTES` 表 + `routeByExtension()` + 单测（不需任何真实文件）。
  - 验收：`pytest tests/test_routing.py` 全过。
- [ ] **T2 cad engine 复刻**
  - `cp -R ~/.agents/skills/cad-viewer/scripts/viewer/{backend,dist} skills/viewer/scripts/engines/cad/`
  - 删除 cad/backend/server.mjs 中的 `--port` 参数（端口由父 server 持有），保留 loader/SSR 逻辑作为 helper 库。
  - 验证：拿一个 hip_bracket.step 直跑老 cad-viewer 能渲染，复刻后入父 server 也能渲染。
- [ ] **T3 父 server.mjs**
  - 实现 §5 的 5 个 endpoint + 端口分配 + `/__cad/server` 协议。
  - 引入 cad/backend 作为子模块（动态 import），不重复实现 STEP→GLB 转换。
- [ ] **T4 占位引擎（P3 的 P0 占位）**
  - `engines/{pcb,sch,sim}/index.html`：单文件，暗色系，居中显示「engine=<name> 待实现 · P3 落地」+ 当前文件名 + 一个 `<a href="https://github.com/.../03-viewer">规格文档</a>`。
  - 不引入 JS 框架，纯 HTML + 内嵌 CSS（≤ 50 行）。
- [ ] **T5 启动器**
  - `start.sh`：参数校验 → 调 `node backend/server.mjs` → 抓 stdout JSON → 拼 URL 输出。
  - `web_preview.py`：Python 包装 `start.sh`，提供 `start(file, workspace=None) -> str(url)` API + CLI；飞书机器人调用入口。
- [ ] **T6（P1）headless 降级链** —— 见 §10。
- [ ] **T7 测试**：详见 §3 测试清单，CI 必跑 `test_routing` + `test_url_assembly` + `test_placeholders`，本地必跑 `test_cad_engine`。

## 8. 占位 engine 规格(P0 交付占位 + P3 升级真实现)

四个引擎(cad / pcb / sch / sim)在 P0 都要存在,但只有 cad 真跑。pcb/sch/sim 三个 stub 必须实现「最小可用 API + 占位 HTML」,P3 真上时只替换 `dist/` 与 `api.mjs`,父 server 无需改动 —— 这是「填空式扩展」的关键。

### 8.1 stub engine 统一 API(`engines/<name>/api.mjs`)

每个 stub 引擎暴露三个导出,父 server 通过 dynamic import 调用:

```js
// engines/<name>/api.mjs(stub 与 ready 实现共用此签名)
export const meta = {
  name: 'pcb',                        // 引擎名,与目录同名
  version: '0.0.1-stub',              // 实现替换为真版本时升 0.x → 1.x
  status: 'stub',                     // 'stub' | 'ready';/__cad/server 据此回 engineImpl
  supportedExtensions: ['.kicad_pcb', '.gbr', '.ger', '.drl', '.gtl', '.gbl'],
};

// 静态判断文件是否可被本引擎处理(被 router fallback 调用,纯函数,无 IO)
export function probe(filePath) {
  const lower = String(filePath).toLowerCase();
  return {
    supported: meta.supportedExtensions.some(e => lower.endsWith(e)),
    hint: meta.status === 'stub' ? 'P3 落地后真实渲染' : null,
  };
}

// 返回 SPA 入口 HTML(stub 是占位,ready 是真实 SPA 的 index.html);
// query 含 dir/file/engine 等已校验参数,stub 用于显示文件名,ready 用于注入 bootstrap 配置
export function indexHtml(query) {
  // stub:返回内嵌占位页(<150 行单文件 HTML,含「待实现」+ 文件名 + 规格链接)
  // ready:读 dist/index.html 模板 + 注入 window.__VIEWER_CONFIG = {...}
}
```

### 8.2 stub 占位页规格(P0 必交)

`engines/{pcb,sch,sim}/index.html` 单文件,**约束**:

- ≤ 150 行(含 inline CSS),0 外部依赖,纯 HTML5 + ES module。
- 暗色背景(`#1e1e1e`),与 cad engine 的 Three.js 黑底视觉统一。
- 居中显示 4 行内容:
  1. 大字 `engine=<name> 占位页`
  2. 中字 `当前文件:<file_name>`(从 query string 读)
  3. 小字 `优先级: P3 · 状态: stub · 预计实现: <版本号>`
  4. 链接 `查看规格 →` 指向本文 `#8-占位-engine-规格`(GitHub 地址 P0-1 骨架时落 README 里)
- **无可交互元素**(避免被误以为「可以用」)。
- HTTP 状态 200,正文必须含字面量 `engine=<name> 占位` 字样,被 `tests/test_placeholders.py` grep。

### 8.3 stub → ready 升级动作(P3 各引擎落地时)

P3 任一引擎(以 pcb 为例)从 stub 升 ready 的 9 步,作为参考流程:

```bash
ENGINE=pcb

# 1. 选型敲定(在本文 §8.4 表里更新行,理由 + License)
# 2. 拉真实现 dist 到 engines/$ENGINE/dist/
git submodule add https://github.com/tracespace/tracespace skills/viewer/scripts/engines/$ENGINE/_upstream
(cd skills/viewer/scripts/engines/$ENGINE && npm run build && cp -R _upstream/dist ./dist)

# 3. 实现 api.mjs:meta.status='ready' + 真 indexHtml() 注入 dist/index.html + bootstrap

# 4. 父 server 不改(已通过 mountStub 路径加载 dist/)

# 5. 路由表 §4.1 该后缀「优先级」从 P3 改成 ready 实际值

# 6. shared/CHANGELOG.md 加一行(viewer §4 路由表的引擎升 ready)

# 7. 加 tests/test_${ENGINE}_engine.py:dist 加载、关键 selector 渲染

# 8. P1-5 Tier 1 链路在 mode=auto 下应能截图本引擎,在 web_preview.py 加用例

# 9. 在 06(pcb)/04(sim)/03 文档对应章节加「升级日志」
```

### 8.4 P3 真实现选型(待 Gate 3 复核)

| 引擎 | 选型 | License | 依赖项 | dist 预估 | 集成方式 |
|---|---|---|---|---|---|
| pcb(3D) | KiCad CLI 转 GLB → cad engine | GPL-3(CLI 仅命令行调用,不污染) | 系统装 KiCad 9+ | 0(借用 cad/dist) | `kicad-cli pcb export gltf <f>.kicad_pcb -o /tmp/<sha>.glb` 落到 cache,URL 改走 cad |
| pcb(2D) | tracespace | MIT | 纯 web | ≈ 4 MB | `engines/pcb/dist/` 静态托管 |
| sch | KiCanvas | MIT | 纯 web,单 ES module | ≈ 3 MB | `<kicanvas-embed src="/files/...">` |
| sim(轨迹) | URDF + JSON 时间序列 | 自研 | 复用 cad engine + timeline 控制条 | 0(借用 cad/dist) | 共用 cad/dist + 加 `?mode=playback` |
| sim(波形) | plotly.js | MIT | 本地 dist(不走 CDN,符合 [00 §6 不 npm install]) | ≈ 6 MB | csv → plotly traces |
| sim(录屏) | HTML5 video | 0 deps | 浏览器内置 | 0 | `<video src="/files/...">` |

> P3 任何一个引擎落地前先回到本文档加章节,更新 §4 路由表 + §3 目录,再起 PR。

## 9. 验收标准

```bash
# 路由单测（不依赖文件存在，T1 完成即可跑）
pytest skills/viewer/tests/test_routing.py
# 期望: .step→cad / .kicad_pcb→pcb / .kicad_sch→sch / .csv→sim / .xyz→null 全过

# CAD 端到端（T2+T3+T5 完成）
bash skills/viewer/scripts/start.sh /tmp/test.step /tmp
# 期望 stdout 唯一一行: http://127.0.0.1:<port>/?engine=cad&dir=/tmp&file=test.step
curl -s http://127.0.0.1:<port>/__cad/server | jq .app
# 期望: "build123d-cad/viewer"

# 占位页（T4 完成）
bash skills/viewer/scripts/start.sh /tmp/board.kicad_pcb /tmp
# 浏览器打开应看到 "engine=pcb 待实现"
curl -s "http://127.0.0.1:<port>/?engine=pcb&dir=/tmp&file=board.kicad_pcb" | grep -q "待实现"

# Server 复用（T3 完成）
bash skills/viewer/scripts/start.sh /tmp/a.step /home/me/proj   # 起 server 1
bash skills/viewer/scripts/start.sh /tmp/b.step /home/me/proj   # 复用 server 1，端口不变
bash skills/viewer/scripts/start.sh /tmp/c.step /home/me/other  # 不同 workspace，新起 server 2

# 安全断言
curl -s "http://127.0.0.1:<port>/files/../../etc/passwd?dir=/tmp"
# 期望 403
```

CI 门槛：`test_routing` + `test_url_assembly` + `test_placeholders` 必须在 GitHub Actions ubuntu-latest 上 < 10s 全过（无浏览器依赖）。

## 10. P1-5:headless 三档降级链(`references/headless-fallback.md`)

> **任务**: P1-5(`fullstack` Owner @ 2026-06-15)。`web_preview.py` 在没有浏览器/GPU/桌面环境时仍能给出**某种**预览输出,让飞书机器人 / CI / 远程 ssh 三个场景都能用。

### 10.1 入口 API

`web_preview.py` 提供两种顶层调用,由参数 `--mode` 决定:

```python
# Tier 1 直返 URL,用户开浏览器交互;chromium 不可用则升级到 snapshot
web_preview.start(file, workspace=None, mode="web") -> str  # URL
web_preview.snapshot(file, workspace=None, mode="auto", out=None) -> dict
# 返回值:
#   {"tier": 1|2|3, "kind": "url"|"png"|"json",
#    "path": <abs file path>, "fallback_reason": <str|None>,
#    "duration_ms": <int>}
```

`mode="auto"` 按 Tier 1 → 2 → 3 依次尝试,每级 import / 系统调用失败回落下一级,**不直接报错**(除非 Tier 3 也挂)。
`mode="web"` 强制只用 Tier 1,失败抛 `HeadlessUnavailable`。
`mode="snapshot"` 强制 Tier 2,跳过 Tier 1。
`mode="probe"` 强制 Tier 3,只解析尺寸不渲染。

### 10.2 三档详细规格

| Tier | 名称 | 触发条件 | 输出文件命名 | 说明 |
|---|---|---|---|---|
| **1** | Full OCP 交互 web viewer | ① `which playwright` 成功 ② `playwright install chromium` 已装 ③ 父 server 起得来 ④ DISPLAY 或 XDG 不强制要求 | `output/<task>/_viewer/preview.url`(单行 URL 文本)+ 可选 `output/<task>/_viewer/snapshot.png`(playwright 截图) | 真完整体验,与用户浏览器看到 1:1 |
| **2** | VTK / chromium 静态 PNG | ① Tier 1 不满足 ② 但有 OCP-Python(`from OCP.STEPControl import ...`)或 VTK(`import vtk`)其一可 import | `output/<task>/parts/<part>.preview.png`(默认 1024×768,可 `--size 512x384` 参数化) | 飞书机器人主用此档(CI 容器 + chromium 装包慢) |
| **3** | 命令行尺寸 JSON(probe-only) | ① Tier 1 + 2 均不满足 ② 但 `pip install steputils` 或 OCP 任一可 import | `output/<task>/parts/<part>.dimensions.json` | 终极兜底,纯 ASCII 输出,任何环境(裸 docker / 没显卡)都能跑 |

### 10.3 触发条件检测细则(`web_preview.py:detect_tier()`)

```python
def detect_tier() -> int:
    """每个 tier 需要满足的依赖一次性检查;返回当前可用最高档"""
    # Tier 1
    try:
        import playwright.sync_api  # noqa
        # 必须 chromium 已装;subprocess 跑 `playwright install --dry-run chromium` exit 0
        if subprocess.run(['playwright', 'install', '--dry-run', 'chromium'],
                          capture_output=True).returncode == 0:
            return 1
    except ImportError:
        pass

    # Tier 2
    has_ocp = importlib.util.find_spec('OCP') is not None
    has_vtk = importlib.util.find_spec('vtk') is not None
    if has_ocp or has_vtk:
        return 2

    # Tier 3
    has_step_parser = importlib.util.find_spec('steputils') is not None or has_ocp
    if has_step_parser:
        return 3

    raise HeadlessUnavailable('all tiers unavailable')
```

### 10.4 输出文件契约(对接 [08 §2.0 标准 output 约定](08-shared跨子技能协议.md#20-标准-output-约定))

为三档产物在 `output/<task>/` 下增加约定路径(同步到 08 §2.0,需要 tech_lead 在 P0-6 落盘前同步登记):

```
<project_root>/domains/<domain>/output/<task>/
├── _viewer/
│   ├── preview.url                # Tier 1 主产物:单行 URL 文本(便于飞书直接贴链接)
│   ├── snapshot.png               # Tier 1 可选:playwright 渲染截图(默认开启)
│   └── tier_meta.json             # 任一 tier 跑完都写:{"tier": 2, "fallback_reason": "no chromium", ...}
├── parts/<part>.preview.png       # Tier 2 主产物(键名同 mechanical 出件 sibling)
└── parts/<part>.dimensions.json   # Tier 3 主产物
```

**键名规则**:
- Tier 1 用 `_viewer/`(下划线前缀避免和 mechanical 真产物 `parts/` 同级混淆)。
- Tier 2 / 3 直接挂 `parts/<part>.<ext>`,与 STEP/STL 同级 sibling,这样 `viewer.snapshot()` 也能用同一个产物 → 不重复产文件。
- `_viewer/tier_meta.json` 是降级链审计字段,内容 schema:

```json
{
  "tier": 2,
  "kind": "png",
  "path": "/abs/.../parts/hip_bracket.preview.png",
  "fallback_reason": "playwright not installed",
  "tier_attempted": [1, 2],
  "duration_ms": 3421,
  "tool": "ocp",
  "ts": "2026-06-15T10:00:00+08:00"
}
```

### 10.5 dimensions.json schema(Tier 3 输出)

```json
{
  "schema_version": 1,
  "source_file": "parts/hip_bracket.step",
  "bbox_mm": {"min": [0, 0, 0], "max": [120.5, 80.0, 32.4]},
  "size_mm": {"x": 120.5, "y": 80.0, "z": 32.4},
  "volume_mm3": 142500.5,
  "surface_area_mm2": 18432.1,
  "mass_kg": null,                  
  "topology": {"solids": 1, "shells": 1, "faces": 28, "edges": 72, "vertices": 48},
  "centroid_mm": [60.25, 40.0, 16.2],
  "tool": "ocp",                    
  "ts": "2026-06-15T10:00:00+08:00"
}
```

字段保证:
- `bbox_mm` / `size_mm` 必给(STEP 元数据可解,不需渲染)。
- `volume_mm3` / `surface_area_mm2` / `topology` 在 OCP 后端可给,steputils 后端可能 null(写 null 不要瞎填 0)。
- `mass_kg` 默认 null;若 mechanical 提供材料 → tech_lead 在 02 落 `compute_mass_from_step.py` 后再填。

### 10.6 各场景默认 mode(对接 01 §1)

| 调用方 | 默认 mode | 理由 |
|---|---|---|
| 用户终端 `bash start.sh <file>` | `web` | 直接浏览器看 |
| 飞书机器人 employee_bot | `auto`(优先 Tier 2) | 飞书贴 PNG 体验最好;chromium 装包重,默认不上 Tier 1 除非 BOT 显式装 |
| CI(GitHub Actions ubuntu) | `auto`(优先 Tier 2) | playwright 装 + 跑 ≥ 90s,影响 CI 时长;Tier 2 OCP < 5s |
| `python -m skills.viewer.scripts.web_preview --probe` | `probe` | 纯尺寸校验场景,1s 内出 |

### 10.7 验收

```bash
# Tier 1(本机有 chromium 时)
python skills/viewer/scripts/web_preview.py --mode=web /tmp/hip_bracket.step
test -f output/<task>/_viewer/preview.url

# Tier 2(强制 snapshot,假装无 chromium)
PLAYWRIGHT_DISABLED=1 python skills/viewer/scripts/web_preview.py \
    --mode=snapshot /tmp/hip_bracket.step
test -f output/<task>/parts/hip_bracket.preview.png
file output/<task>/parts/hip_bracket.preview.png | grep -q PNG

# Tier 3(强制 probe)
python skills/viewer/scripts/web_preview.py --mode=probe /tmp/hip_bracket.step
jq .bbox_mm.max output/<task>/parts/hip_bracket.dimensions.json

# 降级审计
jq .tier_attempted output/<task>/_viewer/tier_meta.json
# 期望:[1, 2] 表示 Tier 1 试过失败,落到 Tier 2
```

CI 默认 `mode=auto`;飞书机器人若已装 chromium 则用 `mode=web` 拿到完整 URL(用户点开看实时),降级 PNG 仅在 chromium 缺失时启用。

## 11. dist 提交策略（结论）

- **决定：直接 commit `engines/cad/dist/` 进 git，不上 LFS。**
- 理由：
  - cad-viewer dist 实测 13M，远低于 GitHub 单仓 1G 软上限。
  - LFS 引入操作复杂度（员工拉取要装 git-lfs，CI 要配 token），收益不抵成本。
  - 升级频率约一季度一次（Three.js 大版本节奏），单次增量 < 5M，可接受。
- **触发改用 LFS 的红线**：当 `engines/*/dist/` 总和 > 80M，或 git 仓库 `.git/` 体积 > 200M，重启评审。
- 不需 npm install：dist 是已 bundle 的 vanilla JS + WASM，server.mjs 也是 esbuild bundled，
  仓库内开箱即用，符合 [00-总览](00-总览与目标架构.md) 第 6 条「不 npm install」。

## 12. P3 split-view（结论）

- **决定：P3 落地时再做，且做成「双 iframe + 同一 server」**，不在 P0 引入。
- 理由：
  - P0 阶段没有真实需求触发（用户首个 PCB 项目还没立项）。
  - 实现成本不大：父页面两个 `<iframe>` 各拿一个 URL，不需要改 server 路由。
  - 提前做有过度设计风险（不知道用户实际想看哪两路并排）。
- 触发条件：用户提出「想同时看 PCB + 外壳」或「想同时看 URDF + 仿真轨迹」时,
  在 06 / 04 文档加 split-view section,引用本节。

## 13. 不集成（防 scope 蔓延）

- ❌ 实时 ROS 数据流 viewer（rosbridge + Foxglove 已成熟，不重造轮子）
- ❌ 实时 PCB 编辑（我们只做预览，不做编辑器）
- ❌ FEA 应力云图（P4+，需 CAE 后端）
- ❌ 公网部署（仅 127.0.0.1；远程访问走 SSH 隧道或 ngrok，由调用方自理）
- ❌ 用户/会话/权限（单机单租户，按需在父 super skill 层加，不在 viewer）

## 14. 与其他文档的接口

- 上游 **mechanical(02)** / **urdf(04)** / **gcode(05)** 出的文件路径 → viewer 起 server。接口见 [08](08-shared跨子技能协议.md) §2 表格。
- viewer 是 [08](08-shared跨子技能协议.md) §4 的高扇入节点，本文 §6 URL 协议改动必须在 08 dependencies 里同步。
- P3 pcb 引擎依赖 [06](06-电子域扩展-pcb-eda-drc.md) 提供样件（`board.kicad_pcb`）。
- P3 sim 引擎依赖 [04](04-机器人描述子技能-urdf-srdf-sdf.md) 提供 URDF + joint timeline JSON 样例。
- 测试基建依赖 [07](07-测试与验证基建.md)：跨子技能 e2e 用 mechanical → viewer 出图作为 M2 demo。

## 15. 已定稿决议(对齐 [01 §8](01-分工与排期.md#8-跨文档待讨论汇总项目经理建议结论))

> 本节所有项已与 01 §8 项目经理建议结论比对一致,Gate 1 评审一次性锁定,后续改动经 [08 §8 变更登记](08-shared跨子技能协议.md#8-变更登记sharedchangelogmd)。

| # | 议题 | 结论 | 与 01 §8 对齐 | 章节 |
|---|---|---|---|---|
| D1 | viewer dist 13M 直接 commit 还是 LFS | **直接 commit**,红线 80M(红线触发后 fullstack 主导转 LFS) | ✅ 一致(01 §8 row 2) | §11 |
| D2 | split-view(左 PCB 右 3D 外壳)做不做 | **P0/P1 不做,P3 落地时双 iframe**;触发条件:用户首次提出「想同时看 PCB + 外壳」或「URDF + 仿真轨迹」 | ✅ 一致(01 §8 row 3) | §12 |
| D3 | `.json` 后缀冲突(urdf 轨迹 vs 配置) | router 不 sniff,返 `'ambiguous'`;调用方显式 `?engine=sim` 透传,否则 server 回 409 | 一致(03 自定,无 01 §8 对应行) | §4.1 |
| D4 | 默认端口 4178 与外部 cad-viewer 冲突 | 优先 4178,存在外部活 server 时启动器自动跳 4188+;`VIEWER_BASE_PORT` 环境变量可覆盖 | 一致(03 自定) | §5.1 |
| D5 | headless 工具选型(chromium vs OCP vs VTK) | **三档降级链** Tier 1 chromium(交互)→ Tier 2 OCP/VTK PNG → Tier 3 dimensions.json;CI / 飞书默认 `mode=auto` 优先 Tier 2,本机用户终端默认 `web`(Tier 1) | ✅ 一致(01 §8 row 4 含原则,本文 §10 给出三档实现) | §10 |
| D6 | 是否要 npm install | **不要**,dist 已 bundle,server.mjs 也 esbuild bundled,直跑 node | ✅ 一致([00 §6](00-总览与目标架构.md) 强约束) | §11 |
| D7 | OCP CAD Viewer 本机依赖(用于 Tier 2) | **已装**(`bernhard-42.ocp-cad-viewer`,2026-06-02 验证);Tier 2 OCP 后端 `from OCP.STEPControl import ...` 可用,无需额外 P0 安装动作 | 新增决议 | §10 + 顶部 |

## 16. 后续待协作（不在本文细化）

- joints.yaml schema：viewer 不直接消费，但 cad engine 需要它把 URDF 关节滑块和 joint 名对上 —— 由 [08](08-shared跨子技能协议.md) §2 待办 + [04](04-机器人描述子技能-urdf-srdf-sdf.md) 协同定，本文只承诺消费它。
- `output/<task>/` 物理位置（项目 vs skill 内）—— 由 [08](08-shared跨子技能协议.md) §7 决定；本文 §6 URL 协议对两者透明（只看 `?dir=` 绝对路径）。

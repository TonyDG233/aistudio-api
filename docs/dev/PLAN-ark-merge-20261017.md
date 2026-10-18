# 计划书：Ark 能力合并入 aistudio-api（Build 通道 + ATG + 账号刷新 + 开关/统计）

> 日期：2026-10-17（含当日决策修订 v3）· 状态：**P0–P2 已完成；P3 进行中（WP4 服务内验收已通过、代码待提交；WP5 未开工）** · 作者：Ark_AIStudioToAPI 侧
> 目标仓库：`TonyDG233/aistudio-api`（fork of `DeconstructedCube/aistudio-api`，谱系见 §7）
> 定位：本文是可行性方案与实施蓝图；P0 门禁通过后按里程碑推进。
>
> **⚠️ 强制纪律（2026-10-18 起，用户指令）：任何"移植既有实现"动手前，必须先翻阅对方项目源码与移交参考文档，并先产出"文档对照"再实施；出现重复造轮子（文档已写明答案却重新试错）按事故追责。**
> 必读位置：
> - 对方源码（只读）：`D:\LLM\Ark_AIStudioToAPI`（`src/core/DirectSignerService.js`、`src/core/MakerSuiteCodec.js`、`src/atg/*`、`scripts/client/build.js`）
> - 技术总参考：`docs/ark-reference/ARK-TECHNICAL-REFERENCE.md`（§6 Build 通道全程与坑位）
> - Build 通道深读：`docs/ark-reference/build-channel/`（`direct-signer-pipeline-20261017.md`、`transport-decision-and-400-triage.md`、`makersuite-generate-content-bridge-20261016.md`、`archive-2026-10-16-build-channel-closure.md`、`evidence-20261017/`）
> - app 侧客户端协议第一手：`docs/ark-reference/scripts/client/`（`build.js`）
> - 本仓底层验证链：`docs/BOTGUARD_VERIFICATION_CHAIN.md`（403/快照语义）、`docs/WIRE_SPECIFICATION.md`、`docs/ARCHITECTURE.md`
> - 反例对照表：`docs/dev/AUDIT-P2P3-20261018.md` 附二（"多少答案早已写好"，动手前先看）

---

## 0. 背景与动机

1. **模型可达性互补（核心动机）**
   - 正常 Web 会话面（本仓库现架构，`/prompts/new_chat`）：**Pro/Ultra 档账号可调用 Pro 及 argon（gemini-4-argon）系列模型**。
   - Build 通道（Ark 侧 `MAKERSUITE_DIRECT` 页面直连，宿主 `aistudio.google.com/apps/<id>`）:对免费档可用，但 **argon / Pro 系模型在该通道不可用**（额度与模型范围受 Build 网关限制，证据见 Ark `docs/zh/build-channel/archive-2026-10-16-build-channel-closure.md:57-63`）。
   - 结论路线：**pro/ultra 账号走本仓库现有聊天面；free 账号走移植进来的 Build 面**。两个面在同一服务内按账号档位（tier）路由。
2. **内存动机**：本仓库单浏览器进程 + 标签页热切，实测文档值 250–380MB（Linux/Docker），单标签页增量（P0 实测校准：首页 +123MB、同浏览器增量页 **+66–71MB**，见 `SPIKE-RESULTS-20261017.md` §14，原 20–30MB 估计偏乐观）；Ark 现方案每账号约 700MB（Firefox 上下文池，本机实测 2 context ≈ 758–771MB）。小鸡（1GB）环境只有合并到本架构才能承载多账号。
3. **策略**：**不重写、只增量移植**。以本仓库为基座，把 Ark 侧四项能力翻译为 Python/CDP 实现：① Build 页面直连面 ② ATG 防截断 ③ 账号信息刷新（email/tier/过期）④ 功能开关与统计融合。
4. **非目标（首期不做）**：UI 完整复刻；MakerSuite 以外的上游通道扩展（第三面见 WP9，另行立项）。
5. **多格式兼容层升级为必须项（WP8，2026-10-17 拍板）**：大量目标客户端（OpenAI SDK / Claude 生态）无 Gemini 协议兼容，属强需求；对方仓库当前仅 Gemini v1beta 原生协议、无跨厂商适配层（其 AGENT.md 架构原则），须移植 Ark 的格式转换层。
6. **决策记录（2026-10-17 用户拍板）**
   - **A1 回落**：默认零自动回落；以显式报错标注具体问题为优先；回落仅经显式开关开启（默认 off）。
   - **A2 编排**：不设"pro 不开 build"的硬规则；改为**账号按 tier 分组 + 组级面策略（两者/单一/彻底禁用）+ 页面实例配额 + 实例-面绑定 + 管理面板**（UI 提供丰富调用选项）。
   - **A3 多格式**：OpenAI/Claude 兼容层升级为必须项（WP8），纳入计划。
   - **远期**：合并 GeminiLocalAPI（gemini.google.com 消费者面）为第三面，统一纳入编排策略（WP9）。

---

## 1. 双方架构与能力对照

| 维度 | Ark_AIStudioToAPI（Node） | aistudio-api（Python，本仓库） |
|---|---|---|
| 浏览器 | Camoufox（Firefox 135 fork）+ Playwright | CloakBrowser（Chromium 146 stealth）+ 纯 Python CDP |
| 宿主页面 | AI Studio Build App（host apps 页 + ais-pre 预览 iframe） | AI Studio 普通聊天页（`/prompts/new_chat`，temporary 模式） |
| 传输 | WebSocket(9998) + 页内脚本；直连面经 `ProxyStreamedCall` 页内 fetch | CDP Binding 推流（`__aistudio_stream_push__`）+ 页内 fetch 重放 |
| 上下文模型 | 多 context 池（MAX_CONTEXTS，~700MB/账号） | 单进程多标签页，蓝绿热切，空闲回收（900/1800s） |
| 上游 RPC | 同款 `alkalimakersuite-pa` MakerSuiteService | 同款（`ProxyStreamedCall` 桥 / `GenerateContent`） |
| 对外协议 | OpenAI / Gemini / Claude / Responses（转换层完备） | 仅 Gemini v1beta 原生（无跨厂商层，需 WP8） |
| 账号凭证 | storageState（含 run.app 域 cookie） | Cookie 扁平导入（域忽略、重绑 `.google.com`） |
| 多账号调度 | 用量/失败/定时切换 + 即时状态码 | 黏性 + 按模型 429 隔离 + 403 冷却 + 美西午夜重置 + drip 标记（更完备） |
| 统计 | UsageStatsService（JSONL，格式/账号/耗时维度） | stats.json（模型 token）+ rotator_state.json（账号配额）+ Dashboard |
| 邮箱抓取 | 有（页面正则 + WIZ_global_data） | 有（`detect-email` 端点 + `extract_email.js`） |
| tier（free/pro/ultra/plus） | **有**（`GetAiStudioBenefitTier` RPC） | **无** |
| ATG 防截断合成工具 | **有**（完整模块 + 6+2 注入/解包点） | **无** |
| Build 页面直连 | **有**（零编译 `ProxyStreamedCall`、真流式实证） | **无** |

---

## 2. 工作包（WP）总览

| WP | 内容 | 复杂度 | 预估人日 | 依赖 |
|---|---|---|---|---|
| WP0 | 门禁 Spike（cookie 保真 / 签名捕获 / 直连 200 / 内存测绘） | M | 1–2 | 无 |
| WP1 | 功能开关框架（env + config.yaml + 热重载） | S | 0.5–1 | 无 |
| WP2 | ATG 移植（synthetic tool 模块全套） | L | 3–5 | WP1 |
| WP3 | 账号信息刷新（tier/email/过期 + 批量并行 + 徽章） | M | 1–2 | WP1 |
| WP4 | Build 页面直连面（cookie 保真 + 注入脚本 + warmup + 传输 + 双面会话） | L | 5–10 | WP0 通过 |
| WP5 | 分组与面编排（组策略/实例配额/实例-面绑定/管理面板） | L | 4–6 | WP3, WP4 |
| WP6 | 统计整合与 UI 增量 | M | 2–4 | WP2, WP3, WP5 |
| WP7 | 部署/文档/质量门/致谢 | S | 1–2 | 全部 |
| WP8 | 多格式兼容层（OpenAI Chat Completions / Claude Messages；Responses 可选） | L | 5–8 | WP1, WP2 |
| WP9 | （远期）第三面：Gemini App（合并 GeminiLocalAPI） | L | 待立 | WP5 |

---

## 3. WP0 门禁 Spike（go / no-go）

在动手前，用一次性脚本在本仓库技术上验证四个前提。全部通过 → 继续；任一失败 → 回到方案层修设计。

### Spike A：凭证保真与 Build 宿主可达
- 扩展 cookie 导入（保留 `ais-pre-*.run.app` 域 cookie：`__SECURE-aistudio_auth_token`、`GAESA`）后，在 CloakBrowser 中导航 `https://aistudio.google.com/apps/<appId>?showAssistant=true&showPreview=true`。
- **通过标准**：宿主页登录态正常（能读到用户邮箱）；ais-pre 预览 iframe 加载存活。
- 已知改动点：`cookie_parser.py:238-250`（域重绑逻辑需保真模式）、`session_auth.py` cookie 注入。

### Spike B：签名器捕获（tb）
- 以 CDP `Page.addScriptToEvaluateOnNewDocument` 注入 Ark `installDirectSigner`（`src/core/DirectSignerService.js` 内 ~788 行浏览器侧逻辑）的 Python/Chromium 等价物：hook `M.Cn` / `M.GB` / `XMLHttpRequest.prototype.send`，捕获带 key 的 `tb` 实例与白名单头，吞没 `__warmup__` 请求（零额度）。
- **通过标准**：`signer_status: ready`，`hasToken: true`。
- 风险点：Angular `NG0203`（禁止 `new M.TB()`）；API key 属性名漂移（须 `mb.nK || mb.jK`）。

### Spike C：直连真流式
- 页内执行：`token = M.Cn(tb, await M.GB(path + " " + jsonText))` → `fetch(ProxyStreamedCall, [path, jsonText, token])` → `getReader()` 逐帧经 CDP Binding 回推 → Python 端 Holder 解码（移植 `MakerSuiteCodec.scanArrayElements`/`convChunk`）→ 喂现有 `wire_parser.py`。
- **通过标准**：HTTP 200；≥2 帧渐进到达（真流式）；chunk JSON 可被现有 `wire_parser.parse_response_chunk` 解析。
- 注意：请求体为**原始 Gemini REST JSON 字符串**，逐字节一致，键序不可变（否则签名 403）。

### Spike D：内存测绘
- 在 `--renderer-process-limit=1 --js-flags=--max-old-space-size=128` 下同时承载宿主 apps 页 + ais-pre 预览页 + 并发流，量 PSS。
- **通过标准**：双页并发下总内存 < 500MB，或找到需放宽的具体 flag（记入 WP4 结论）。
- 同时确认：现有资源拦截清单（`cdp_client.py:24-65`）**不得**屏蔽 Build 应用主包（boq-makersuite 等），需白名单。

---

## 4. 各工作包详细设计

### WP1 功能开关框架（S）
- **落点**：`config.py` 的 `Settings` dataclass（`_load_bool_env`/`_load_env` 模式，`AISTUDIO_*` 前缀）+ `config.yaml` 顶层新增 `features:` 段（`model_defaults.py:_load_yaml_config` 已整读 YAML，天然兼容）+ 热重载沿用 `PUT /config/yaml` → `invalidate_config_cache()`。
- **开关清单（对应 Ark 的 .env 语义，改名为本仓库风格）**：
  - `AISTUDIO_BUILD_SURFACE`（on/off，默认 off）：Build 页面直连面总开关
  - `AISTUDIO_ATG_MODE`（off/prefer/required，默认 off）及 `features.atg.*`（prefix、control_position、strict、empty_retries、recovery、side_buffer_bytes、client_synthetic_guard、text/non-text 模型正则）
  - `AISTUDIO_ACCOUNT_REFRESH_CONCURRENCY`（默认 3）
  - `AISTUDIO_ROUTE_BY_TIER`（on/off）+ 路由规则表（见 WP5）
  - `AISTUDIO_AUTO_SWITCH=true|false` + `AISTUDIO_AUTO_SWITCH_INTERVAL_HOURS`（Ark `ENABLE_AUTO_SWITCH` 语义，落在 rotator）
- **验收**：`/config` 可见 features 段；改 yaml 后不重启生效；README/AGENT.md 补开关说明（遵循"开关文案自解释"约束：开=什么/关=什么/何时开/失败怎么办）。

### WP2 ATG 移植（L）
- **新包**：`src/aistudio_api/application/atg/`（Python），逐模块对照移植：
  | Ark 源（JS） | Python 目标 | 说明 |
  |---|---|---|
  | `config.js` (77) | `atg/config.py` | 模式/预算/正则 |
  | `injector.js` (178) | `atg/injector.py` | 合成声明 + 控制消息 + toolConfig AUTO；尊重客户端 ANY pin |
  | `extractor.js` (107) | `atg/extractor.py` | 非流式解包 |
  | `streamExtractor.js` (185) | `atg/stream_extractor.py` | SSE 逐块解包（状态机） |
  | `streamDecoder.js` (377) | `atg/stream_decoder.py` | 增量 JSON 字符串解码 |
  | `repair.js` (246) | `atg/repair.py` | 确定性修复 |
  | `historyPurge.js` (44) | `atg/history_purge.py` | 历史清理（同前缀合成调用剔除） |
  | `clientPatterns.js` (191) | `atg/client_patterns.py` | 客户端自带合成机制检测（5 信号） |
  | `modelFilter.js` (47) | `atg/model_filter.py` | 文本/非文本模型分类 |
  | `index.js` (73) | `atg/__init__.py` | facade |
- **注入点（已定位到行）**：
  - 请求 wrap：`application/api_service_gemini.py:314-321`（非流式）与 `:526-528`（流式），在 `normalize_gemini_request` 之后、执行之前。
  - 响应 unwrap：`:337-346`（非流式）、`:544-568`（流式 `event_type == "tool_calls"` 分支）。
- **wire 层配合（关键坑）**：`infrastructure/gateway/wire_codec.py:329-343` 在 `allowedFunctionNames` 存在时会把声明按白名单过滤——移植时必须把本次请求的合成工具名**加入白名单**，否则合成工具被剔除。**（P0 前置实测已复现并验证修复：白名单并入合成工具名即可，PRE-P1 E5）**
- **协议面**：ATG 的 per-format 结构（gemini/openai/claude）在 WP8 落地后直接启用；首期可先以单开关运行，后续切 per-format。
- **功能开关**：`features.atg.*`；`client_synthetic_guard` 默认 on（防止与客户端（如酒馆类扩展）双合成通道叠加）。
- **统计产出**（并入 WP6）：`syntheticHit / syntheticCount / realToolCount / repaired / conflict`。
- **验收**：单元测试覆盖 injector/extractor/decoder/repair 纯函数；真机两项——`prefer` 下合成命中日志；`required` 下无合成调用时显式失败（不静默）。

### WP3 账号信息刷新（M）
- **移植 Ark `AccountInfoRefresher`**（`src/core/AccountInfoRefresher.js`，513 行；纯 Node，无浏览器）：
  - 探测 RPC：`POST alkalimakersuite-pa...MakerSuiteService/GetAiStudioBenefitTier`，body `"[]"`，`content-type: application/json+protobuf` + `x-goog-api-key`（公网 web key，可覆盖）+ `x-goog-authuser` + `Authorization: SAPISIDHASH ...`（**本仓库已有现成 `calculate_sapisid_hash`，直接复用**，`cookie_parser.py:171-189`）。**（P0 前置实测：纯 Python 直连 200，free/pro 映射正确，PRE-P1 E3）**
  - tier 映射：`0 或 []` → free；`1` → pro；`2` → ultra；`3` → plus；其他 `unknown-N`。
  - 401 → 标记凭据过期（tier 徽章与过期徽章**必须并存显示**，遵循 Ark 约束）；非权威失败（5xx/代理故障）→ 不改状态。
  - 403 且疑为 key 被拒 → 从 apps 页 HTML 正则回收 key 一次重试。
  - 邮箱回填：apps 页 HTML `WIZ_global_data` 的 `"oPEP7c":"..."` 正则 + 噪音域排除回退。
- **落点**：
  - `account_store.py`：`AccountMeta` 增 `email/tier/tier_checked_at/expired`（`to_dict/from_dict` 同步）。
  - 批量并行：`asyncio.Semaphore(3)` 工作池（对齐 Ark `REFRESH_CONCURRENCY=3`），端点 `POST /accounts/refresh`（body `{account_ids|all, refresh_email}`；重复触发 409）。
  - Web UI：`AccountRow.vue` 增加 tier/expired 徽章与管理页刷新按钮（`AccountsView.vue`）——**不做完整 UI 复刻，仅核心徽章与按钮**。
- **验收**：真账号刷新出正确 tier；401 账号显示过期徽章且被排除出调度；批量刷新期间 409 互斥。

### WP4 Build 页面直连面（L，核心）
> 本质：把 Ark 的"Build 宿主页直连"翻译为本仓库的"第二个 surface"。本仓库传输层（`transport.py` binding 推送 + `Runtime.evaluate`）经评估**与宿主面无关**，可直接复用。

4.1 **凭证保真**：cookie 导入增加 `preserve_domains` 模式（或 `_DOMAIN_OVERRIDES` 扩展），保留 run.app 源 cookie；`session_auth.py` 注入支持 origin-bound（`url` 目标）cookie。
4.2 **页面脚本移植**（`infrastructure/browser/js/build_signer.js`，由 `Page.addScriptToEvaluateOnNewDocument` 注入）：
   - 移植对象：Ark `DirectSignerService.js` 浏览器侧（~788 行）+ `MakerSuiteCodec.js` 的 `scanArrayElements`（Holder 流扫描）。
   - 职责：hook `M.Cn`/`M.GB`/XHR → 捕获 `tb`+请求头 → warmup 吞没 → 暴露 `window.__executeDirectRpc(taskId, model, jsonBody)` → 逐帧 `window.__aistudio_stream_push__(...)`（复用本仓库既有 binding）。
4.3 **请求执行协议**（Python 侧 `infrastructure/gateway/build_surface.py`）：
   - warmup 捕获（零额度）→ `session_state.py:SessionContext` 扩展 `build_context`（page+ready 态）→ 任务分发 `Runtime.evaluate` → 帧回流。
   - **Holder 解码**：响应为 2–3 层嵌套数组、叶子是转义 Gemini chunk JSON（区别于聊天页 envelope）。需先迁 `convChunk` 预处理，再喂现有 `wire_parser.py`（其字段索引兼容 `GenerateContent/ProxyStreamedCall` 信封，仅需 Holder 拆壳层）。
4.4 **双面会话管理**：`session.py:transition_to_account` 支持双页（宿主 apps 页 + ais-pre 预览页）；蓝绿热切、drain、空闲回收覆盖两页；`client.py` 增加 `surface` 路由参数。**（P0 前置 E4 结论：多账号"瞬时切换"备用须由 `Target.createBrowserContext` 承载（独立 cookie jar，实测 +180MB/账号 context）；现有"cookie 交换式"蓝绿是单 context 内换号，需扩展为 context 池形态。）**
4.5 **失败语义（已决）**：**默认零自动回落**——面内失败与跨面（build↔chat）失败均不改道，一律显式报错并标注具体问题（哪个面、哪类失败、处置建议；直连类失败不计账号故障的规则沿用）。预留 `features.fallback.*` 开关组（默认 off；显式开启才允许指定回退），任何改道行为必须在日志与响应中可 grep。
- **验收**：free 账号经 build 面真流式 200（≥2 帧渐进）；pro 账号不受影响；崩溃后显式报错含原因与处置建议。

### WP5 分组与面编排（Surface Orchestration，L）
> 决策更新（2026-10-17）：不做"pro 不开 build"的硬规则；改为**账号分组 + 组级面策略 + 页面实例配额 + 实例-面绑定**，由 UI 提供丰富调用选项。

- **数据模型**（落 `config.yaml features.groups`，热重载）：
  ```yaml
  features:
    groups:
      - name: pro
        match_tier: [pro, ultra, plus]        # 亦支持 match_emails_regex
        allowed_surfaces: [chat]              # 两者=[chat, build]；单一=[chat]/[build]；彻底禁用=[]
        max_page_instances: 2                 # 该组允许并存的页面实例上限
      - name: free
        match_tier: [free]
        allowed_surfaces: [build]
        max_page_instances: 3
  ```
  未显式分组的账号按 tier 落入自动默认组（free→`[build]`、pro/ultra/plus→`[chat]`），默认组策略可改。
- **页面实例编排**：统一"页面实例"抽象——每实例承载一个面（`chat` 或 `build`），按需惰性创建；空闲回收沿用 900/1800s；组配额（`max_page_instances`）限定并存实例数，超限对请求**显式拒绝**（含配额与处置说明），不排队、不静默换面。内存参考基线 **~70MB/实例**（P0 已校准：`SPIKE-RESULTS-20261017.md` §14 实测增量页 +66–71MB、首页 +123MB；瞬时切换需的备用页同价）。**跨账号备用须 `Target.createBrowserContext` 隔离**（独立 cookie jar + 独立渲染进程，实测 **+180MB/账号 context**；PRE-P1 E4）。
- **模型守卫**：argon/Pro 系仅在 `chat` 面可用；目标组 `allowed_surfaces` 未含 chat 时，对 argon 请求显式 4xx（写明组策略与处置建议），绝不静默换面或换账号。
- **rotator 扩展**：冷却/429 隔离键 `(account, model)` → `(account, surface, model)`；`/rotation` API 增 `surface` 维度；`allowed_surfaces=[]` 的组在调度器层面直接排除。
- **管理面板（最小但完整，无 UI 完整复刻）**：分组管理（策略编辑、账号移入移出）、实例面板（各面实例数/配额/内存估算、手动开关实例）、组策略启停；文案遵循"开关自解释"。
- **可选并入**：Ark 的定时自动切换开关（`ENABLE_AUTO_SWITCH`/间隔）。
- **验收**：策略改 yaml/面板即时生效；配额超限显式拒绝；chat-disabled 组上 argon 请求返回具体 4xx；日志可见 surface 决策链（可 grep）。

### WP6 统计整合（M）
**以本仓库现有统计为底座增量扩维**（不引入第二套存储）：
- `api/state.py`：`ModelStatsItem` 增 `surface` 维度拆分或并列字段；新增 `atg`（syntheticHit 等）与 `refresh`（成功/过期/upstream_error 计数）统计组；
- `AccountRotator.get_all_stats`（`account_rotator.py:433-481`）已含模型级配额视图——增补 `tier`、`surface_cooldowns`；
- 端点：`/stats`、`/rotation` 增字段；账号行增 tier/expired。
- Web UI（最小增量）：`DashboardView.vue`（surface 拆分与 ATG 命中率）、`AccountRow.vue`（tier 徽章）、`ModelQuotaGrid.vue`（surface 维度）。
- 可选吸收 Ark UsageStats 的记录级 JSONL 明细（序列号、耗时、尝试链）作为高级面板数据源——**列为二期**，首期只做聚合字段。
- **验收**：一次 free 请求在 Dashboard 可见 surface=build 与 ATG 命中；一次 pro 请求可见 surface=chat。

### WP7 部署 / 文档 / 致谢（S）
- Dockerfile 与 `setup-browser` 不需变更（同一 CloakBrowser）；仅新增 features 文档与 `.env` 说明对应物。
- AGENT.md 增补：双面约定、开关命名规范、ATG 模块边界。
- **致谢（强制）**：合并/发布提交文本写明上游出处与授权——`chrysoljq/aistudio-api`（原仓，MIT）与 `DeconstructedCube/aistudio-api`（重构 fork）；README 谱系一节；遵循 Ark `merge_commit_attribution_required` 约束。
- 质量门（对齐本仓库 AGENT.md）：`uv run ruff check .`、`uv run ruff format --check .`、`bun x pyright src tests`、`uv run pytest`、页面 JS 语法校验。

### WP8 多格式兼容层（必须，L）
> 决策更新（2026-10-17）：对方仓库仅 Gemini 原生协议；大量外部客户端（OpenAI SDK / Claude 生态）无 Gemini 兼容，属强需求，本项从"可选"升级为**必须交付**。

- **范围**：新增三个对外适配面，复用同一上游管线（`chat_service` → MakerSuite）：
  - OpenAI Chat Completions：`POST /v1/chat/completions`（流式 SSE delta + 非流式；工具调用双向；`usage` 映射）
  - Anthropic Messages：`POST /v1/messages`（含 system 字段、tool_use/tool_result、流式事件序列）
  - OpenAI Responses API：可选（Ark 已有完整实现可随迁）
- **架构**：新增 `api/routes_openai.py`、`api/routes_claude.py` + `application/format_converters/`（Python 平移 Ark `FormatConverter` 模块族：ChatCompletionsConverter、ClaudeRequest/ResponseConverter、Responses 三件套、CommonConverter）。内部一律归一到 `NormalizedGeminiRequest`，响应在**格式边界**做逆变换；原生 `/v1beta` 路由完全不动。**（P0 前置 E6 结论：chat 面转换到内部归一模型；build 面直连本就是 REST 语义，可保持 REST dict 边界，避免无谓二次映射——两边界互不污染。）**
- **移植基准（Ark 源）**：`src/core/FormatConverter/*`（Chat/Claude/Responses/Common 各模块）；行为验收以 Ark 现状为准（流式增量形状、工具调用参数分片、思维链/思考块的丢弃或映射策略）。
- **与 ATG 的啮合（关键）**：ATG 包装/解包按**客户端格式**分别在 6+2 个边界生效——OpenAI 流/非流、Claude 流/非流、Responses 流/非流、Gemini 原生两处；`atgModeByFormat`（gemini/openai/claude）在此真正启用（见 WP2）。
- **开关**：`features.formats.openai` / `features.formats.claude` / `features.formats.responses`（默认按需开启）；API Key 复用现有 `api_keys` 机制，可为各协议面独立配置。
- **统计**：请求记录增 `api_format` 维度（并入 WP6 `formatBreakdown`）。
- **约束**：错误消息具体化（字段路径/收到值/处置建议）；零静默回落；不引入第二套上游传输。
- **验收**：OpenAI 与 Claude 两协议面各过：非流式 + 真流式 + 工具调用（含多轮）+ usage 正确；ATG `prefer/required` 在两协议面各真机验证；原生 Gemini 面回归无变化。

### WP9（远期）第三面：Gemini App 消费者端（合并 GeminiLocalAPI）
- **形态**：把 `D:\LLM\self_programming\GeminiLocalAPI\GeminiLocalAPI-source`（gemini-webapi 2.1.1.post2，gemini.google.com 消费者端）作为**第三个 surface 适配器**接入（认证/协议独立，不复用 MakerSuite 链路）。
- **能力错位互补**：文生图（Nano Banana）、视频/音频、Deep Research、Gems/扩展、思维链深度（参见 Ark `docs/zh/build-channel/archive-2026-10-16-build-channel-closure.md` §六.D 既有调研）。
- **集成口径**：**不做融合重构**；WP5 的 `allowed_surfaces` 枚举扩展为 `[chat, build, gemini_app]`；同一账号体系（`__Secure-1PSID/1PSIDTS` 通常已在 auth 内）、同一统计口径与配额规则。
- **触发条件**：P0–P4 稳定且 WP5 编排落地后单独立项评估。

---

## 5. 里程碑

| 阶段 | 内容 | 出口标准 |
|---|---|---|
| P0 | WP0 四 Spike | 全绿或设计修订；内存结论定 flag 基线 |
| P1 | WP1 + WP3 | ✅ 完成（2026-10-18）：开关框架可用；tier/email/expired 真实刷新 + 徽章。真机验收全绿（auth-1→free、auth-5→pro、401 样本→expired 落库，零模型额度） |
| P2 | WP2 | ✅ 完成（2026-10-18）：ATG 全套移植（10 模块 + 90 单测，pytest 352 全绿）；真机 prefer 非流式/流式成功、required 为 R8 500（无 200 落盘证据，见 `docs/dev/AUDIT-P2P3-20261018.md` §3）；E5 白名单补偿已入库。（2026-10-18 晨复验通过：off/prefer/required 3/3——prefer/required 日志直证 `syntheticHit=true`；pro 账号 argon 经 chat 路径首调 200；证据 `docs/dev/evidence/p2-verify/`） |
| P3 | WP4 + WP5 | free 走 build 真流式、pro 走 chat；分组编排（策略/配额/实例绑定/面板）与零回落错误语义闭环。✅ 完成（2026-10-18 晚）：WP4 服务内验收 2/2（`docs/dev/evidence/p3-build3/`）；WP5 实现完毕并经真机五相位验收全绿——组路由 build 200 / argon chat 200 / 守卫 403×2 / 配额 429，决策链可 grep（`docs/dev/evidence/wp5-20261018/`）；根因与结论见 `docs/dev/AUDIT-P2P3-20261018.md` 附与附三 |
| P4 | WP8 | OpenAI/Claude 双协议可用（流式/非流式/工具调用/用量）；ATG per-format 激活 |
| P5 | WP6 + WP7 | 统计/UI 增量（含 formatBreakdown）、文档、质量门全绿、致谢入库 |

---

## 6. 风险登记与开放决策

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | Build 页在 CloakBrowser/headless 下签名链不成立（NG0203/字段漂移/M.Cn 缺失） | WP4 阻塞 | P0 Spike B 门禁；健壮探测 + 报错具体化 |
| R2 | 单渲染进程 + 128MB 堆装不下双页并发 | 内存优势侵蚀 | P0 Spike D 定 flag；必要时放宽 heap 到 256–512MB（仍远低于 700MB/账号） |
| R3 | 上游（DeconstructedCube）周级重构造成 merge 痛 | 维护成本 | 变更集中于新目录（`atg/`、`build_surface` 等），定期 rebase；本 fork 保持 master 单线 |
| R4 | `ProxyStreamedCall` Holder 格式或索引再变更 | WP4 解析层 | Holder 解码独立文件 + fixture 快照测试 |
| R5 | functionCall 参数是否增量分片未知 | ATG 流式增量收益 | P2 实测；分片则启用增量解码器，整块则走整块路径（Ark 已有两态经验） |
| R6 | 资源拦截误伤 Build 主包 | 面不可用 | Spike D 同步产出白名单 |
| R7 | 多实例并存导致内存预算失控 | 小鸡环境 OOM | 组配额默认保守（Spike D 校准）；实例面板显示内存估算；超限显式拒绝 |
| R8 | 冷启动窗口（预热/蓝绿热切进行中）首个请求 500：`list index out of range` @ `wire_codec.py:146 decode`（capture 模板 body 未就绪即被使用） | 服务启动后数秒内偶发首请求失败 | 既有问题，与 ATG 无关（mode off 冷启动可复现，暖会话重试即 200）；缓解：待预热完成再发首请求；后续可在 capture 层对空模板显式等待/重试（小修，未入本轮回合） |

**决策记录（2026-10-17 拍板）**
- **A1（已决）**：默认零自动回落；显式报错优先、标注具体问题；回落仅经显式开关开启（默认 off、可 grep）。
- **A2（已决）**：不设"pro 不开 build"的硬规则；改为**分组编排**（见 WP5）：组级面策略（两者/单一/禁用）+ 页面实例配额 + 实例-面绑定 + 管理面板。
- **A3（已决）**：WP8 多格式层（OpenAI/Claude）升级为必须项、纳入计划（见 §0.5 与 WP8）。
- **A4（方向已定）**：远期合并 `GeminiLocalAPI`（gemini.google.com 消费者面）为第三面（WP9），统一纳入 WP5 编排策略。

---

## 7. 仓库谱系与 fork 操作（附录）

**谱系**：`chrysoljq/aistudio-api`（原仓，MIT，默认分支 master）→ `DeconstructedCube/aistudio-api`（活跃重构 fork，本次研究对象）→ `TonyDG233/aistudio-api`（本工作 fork）。

**remote 关联（2026-10-17 已执行并校验，位于 `D:\LLM\self_programming\aistudio-api`）**：
```bash
git remote rename origin upstream                                        # DeconstructedCube → upstream
git remote add origin https://github.com/TonyDG233/aistudio-api.git      # origin = 本工作 fork
git remote add source https://github.com/chrysoljq/aistudio-api.git      # 原仓参照
git fetch origin; git fetch upstream --tags
git branch --set-upstream-to=origin/master master                        # master 跟踪本 fork
```
现状：`origin/master` = `upstream/master` = `9bd5a20`（三端同步）。日常同步：`git fetch upstream && git merge upstream/master`（冲突隔离见 R3）。

**本文档位置**：`docs/dev/PLAN-ark-merge-20261017.md`（已随 P1 里程碑入库；2026-10-18 迁入 `docs/dev/`）。

**配套移交资料（2026-10-18）**：`docs/ark-reference/` —— Ark 技术总参考（`ARK-TECHNICAL-REFERENCE.md`）、
Build 通道/直连签名器原始研究与 bundle 考古证据（`build-channel/`）、VPS 部署硬约束
（`VPS-DEPLOYMENT-NOTES.md`，脱敏版）、Build App 创建教程（`misc/`）。开工前先读参考文档，避免重复逆向。

---

## 8. 我方近期提交 → 移植映射（覆盖检查）

| Ark 近期功能（提交） | 去向 |
|---|---|
| AccountInfoRefresher（tier/email/过期，并行 3 工作池；`d9f6a1a` 等） | WP3 |
| 账号刷新 API/UI 徽章与批量分片（5/批） | WP3（徽章与按钮为最小 UI） |
| `ENABLE_AUTO_SWITCH` / 间隔开关（`a0211e5`） | WP5（可选并入） |
| DirectSigner / `ProxyStreamedCall` 零编译直连（`dcdea40` 等） | WP4 |
| ATG 模块群 + 6+2 注入/解包点 + per-format 模式（`8484466` 等） | WP2 |
| UsageStatsService（JSONL 明细） | WP6（一期聚合；二期明细可选） |
| 过期/档位徽章显示约束、错误消息具体化、开关文案自解释 | WP2/WP3/WP6 验收标准 |
| 多格式（OpenAI/Claude/Responses）转换层（`8484466` 等） | WP8（必须） |
| `.env.example` 分块注释风格 | WP1 文档风格 |
| GeminiLocalAPI 消费者面（远期合并；文生图/Deep Research 等） | WP9 |
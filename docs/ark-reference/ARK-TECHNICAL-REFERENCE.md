# Ark_AIStudioToAPI 技术总参考（移交版 · 2026-10-18）

> 来源：`D:\LLM\Ark_AIStudioToAPI`（Node.js）。本文档目的是让 aistudio-api 侧**无需逆向**即可掌握
> Ark 的全部关键技术点与移植素材。文中路径均指 Ark 仓库内路径。已脱敏（无凭据值）。
> 配套深读：本目录 `build-channel/`（Build 通道一手中文研究）、`VPS-DEPLOYMENT-NOTES.md`（部署约束）。

---

## 1. 项目定位

- 把 Google **AI Studio Build App 配额**暴露为 API：对外提供 OpenAI / Gemini / Claude / Responses 四种协议。
- **不是**普通 AI Studio 网页反代：数据面是"专用 Build App 页面"内的 in-app 请求（宿主页
  `aistudio.google.com/apps/<appId>` + `ais-pre-*.run.app` 预览 iframe）。
- 技术栈：Node.js / Express / Playwright 1.59.1 + Camoufox（Firefox 135 分支，隐私增强，
  `camoufox-js` 自动下载或 `CAMOUFOX_EXECUTABLE_PATH`）/ Vue3 管理 UI / WebSocket 9998。
- 端口：API `7860`，浏览器 WS `9998`。

## 2. 系统组成

```
客户端(OpenAI/Gemini/Claude/Responses)
   │ Express 路由 + API 鉴权
   ▼
RequestHandler ──┬─ 格式 Handler（ChatCompletionsHandler / ClaudeHandler / ResponsesHandler / GeminiHandler）
                 │      └─ ATG 包装(请求) / 解包(响应) [AtgHandler + src/atg/*]
                 ├─ RetryHandler（重试、失败计数、切号触发、ATG wrap 调用点）
                 ▼
            TransportHandler（通道分流）
                 ├── 直连通道 DirectSignerService（页面签名器 → ProxyStreamedCall，真流式）
                 └── 原生通道 ConnectionRegistry → WS → build.js(预览 iframe) → Studio shim → 宿主 → ProxyStreamedCall
                 ▼
            响应流回传 → FormatConverter → 客户端 (SSE/JSON)
旁路服务：AuthSwitcher（切号）/ AuthSource（账号池）/ AccountInfoRefresher（/ UsageStatsService / Web UI
```

| 组件 | 文件 | 职责 |
|---|---|---|
| 主编排 | `src/core/ProxyServerSystem.js` | HTTP/WS 服务器、模块装配、WS 路由（区分 `role=signer` 直连客户端与常规 build.js 客户端，L769-772） |
| 浏览器管理 | `src/core/BrowserManager.js` | Camoufox 实例、多 context 池（`Map: authIndex -> {context,page,healthMonitor}`）、注入 build.js / 直连脚本（L2135-2142）、切号 rebalance、后台预热 |
| 连接注册 | `src/core/ConnectionRegistry.js` | WS 连接管理、消息队列（MessageQueue）、重连宽限（10s grace + 120s 轻量重连 = 最长 130s） |
| 直连服务 | `src/core/DirectSignerService.js`（1363 行） | 直连签名器生命周期、页面脚本注入、warmup 引导、任务下发、帧回传注入 SSE |
| 直连 codec | `src/core/MakerSuiteCodec.js`（403 行） | `scanArrayElements`（holder 流扫描状态机）、`convChunk`（JSPB 元素→Gemini 块）、`errorEnvelope`（上游数组错误→HTTP 状态） |
| 账号刷新 | `src/core/AccountInfoRefresher.js`（513 行） | 纯 Node 探活：tier/email/过期（详见 §5） |
| 账号池 | `src/auth/AuthSource.js` | `auth-(\d+).json` 扫描、邮箱去重、expired 过滤 |
| 切号 | `src/auth/AuthSwitcher.js` | 用量/失败/即时状态码/定时切号 |
| ATG | `src/atg/*`（10 模块）+ `src/core/RequestHandler/AtgHandler.js` | 合成工具注入/解包（详见 §7） |
| 格式层 | `src/core/FormatConverter/*` | Chat/Claude/Responses 与 Gemini 互转（详见 §8） |
| 统计 | `src/core/UsageStatsService.js` | 请求级 JSONL 明细（`data/usage-stats.jsonl`）+ `/api/usage-stats` |
| 页面客户端 | `scripts/client/build.js`（1035 行） | 预览 iframe 内执行体；原生通道的请求执行者；直连通道仅作 warmup 触媒 |

## 3. 请求生命周期（关键 12 步）

1. 客户端请求 → Express 路由（各格式入口）→ 鉴权（`API_KEYS`）。
2. 格式 Handler 解析请求；OpenAI/Claude/Responses 经 FormatConverter 归一为 Gemini 结构。
3. RetryHandler 选择账号（AuthSource/AuthSwitcher），必要时先切号（系统 busy 锁）。
4. **ATG wrap**（如开启）：注入合成工具 + 控制消息（`RetryHandler.js:45`、`GeminiHandler.js:56`）。
5. 通道分流（TransportHandler）：
   - `MAKERSUITE_DIRECT=on` 且 `streaming_mode=real` → **直连候选**（`TransportHandler.js:133` → `maybeHandleTask`）；
   - 其余 → 原生通道（发往该 authIndex 的 build.js WS 连接）。
6. 直连：任务下发 ws（`{type:"signer_task", task:{id, body, model}}`）→ 页面签名并直发上游。
7. 原生：WS 消息 → build.js 在预览 iframe 内 fetch → shim/宿主代签 → 上游。
8. 上游流回传：直连为逐帧 `signer_frame`（`data: {...}\n\n`）；原生为 build.js 侧 SSE 转发。
9. Node 端把帧注入当前请求的 MessageQueue（`connectionRegistry.handleExternalMessage`）。
10. **ATG 解包**（如开启）：SSE 逐块或整体解包（§7 解包点）。
11. FormatConverter 逆转换为客户端格式（SSE delta 或整包）。
12. UsageStatsService 记录；失败则按策略重试/切号；直连拒绝显式 502（不计账号故障）。

## 4. 浏览器与页面通信

- **多上下文池**：每账号一个 context+page；`MAX_CONTEXTS`（默认 1）决定同时在线数；
  文档值约 **~700MB/账号**（2 账号 ~950MB，3 账号 ~1100MB）。
- **WS 9998 两类客户端**：
  - 常规：预览 iframe 内 `build.js`（每 authIndex 一条）；
  - 直连：顶层 realm `installDirectSigner` 注入脚本，`ws://127.0.0.1:9998?authIndex=N&role=signer`（`DirectSignerService.js:754`）。
- **MessageQueue**：请求-响应关联；流块以 `{event_type:"chunk", data}` 注入；结束 `stream_close`。
- **健康监控**：每 context 周期体检；断连先 10s 宽限、再 120s 轻量重连。
- **build.js 与两条通道的关系**：原生通道全权执行；直连通道中仅用于**触发 warmup**（见 §6）。

## 5. 凭证与账号管理（WP3 移植素材）

### 5.1 auth-N.json（Playwright storageState + Ark 元数据）

```json
{
  "accountName": "user@example.com",
  "benefitTier": "pro",            // free | pro | ultra | plus
  "tierCheckedAt": "ISO time",
  "expired": false,
  "cookies": [ { "name": "...", "value": "...", "domain": ".google.com", "path": "/", "expires": 0,
                 "httpOnly": false, "secure": true, "sameSite": "None" } ],
  "origins": [ { "origin": "https://aistudio.google.com", "localStorage": [ ... ] } ]
}
```

- **cookie 域分布（实测）**：`.google.com`（SID/SAPISID/__Secure-1PAPISID 等核心）；
  `accounts.google.com`（LSID/ACCOUNT_CHOOSER/__Host-GAPS）；`.aistudio.google.com`；
  `.youtube.com` 与 `.google.com.bd`（**注意：同名 cookie 覆盖风险——实测 SAPISID 三域同值，
  但 SID 值不同**）；`ais-pre-*.run.app`（Build 应用用：`__SECURE-aistudio_auth_token`、`GAESA`）。
- 轮换所需最小集合：`SAPISID` / `__Secure-1PAPISID`（无则探活直接失败）。

### 5.2 账号池与轮换（AuthSource / AuthSwitcher）

- 扫描 `configs/auth/auth-(\d+).json`；**按邮箱去重保留最高索引**；`expired:true` 排除轮换；
  另有 canonicalIndexMap（重定向到存活副本）。
- 切号触发：`SWITCH_ON_USES`（默认 40）、`FAILURE_THRESHOLD`（默认 3）、
  `IMMEDIATE_SWITCH_STATUS_CODES`（默认 `429,503`；用户线上设为 `429,503,403`）、
  `ENABLE_AUTO_SWITCH` + `AUTO_SWITCH_INTERVAL_HOURS`（定时切号，Web UI 可调）。

### 5.3 AccountInfoRefresher（本次移交重点，可直接翻译为 Python）

- **探测 RPC**：`POST https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GetAiStudioBenefitTier`
  - body：字面量 `"[]"`；`content-type: application/json+protobuf`；
  - headers：`x-goog-api-key`（内置 AI Studio 公网 web key，形如 `AIzaSyDdP8…`，可经
    `MAKERSUITE_WEB_API_KEY` 覆盖）、`x-goog-authuser`、`x-user-agent: grpc-web-javascript/0.1`、
    `Authorization: SAPISIDHASH <ts>_<sha1> SAPISID1PHASH … SAPISID3PHASH …`
    （三哈希并列，各自按 `${ts} ${cookie值} https://aistudio.google.com` 计算；缺哪个 cookie 省哪段）。
- **判定**：HTTP 200 → tier 码映射 `0/[]=free，1=pro，2=ultra，3=plus`，其他 `unknown-N`；
  **401 → 凭据过期**（写 `expired:true`，且 UI 必须同时显示"过期"徽章与档位徽章）；
  403 且疑 key 被拒 → 走 apps 页回收 key 一次重试；5xx/代理故障 → 非权威失败，不改状态。
- **邮箱回填**：GET `https://aistudio.google.com/apps/<appId>`（带 cookie）→ 页面 HTML
  `WIZ_global_data` 的 `"oPEP7c":"<email>"` 正则；回退为全页扫邮箱 + 噪音域排除。
- **并发与互斥**：3 工作池（每账号 ~3.2s）；进行中再次触发返回 409；支持经代理（Node `http`+`tls.connect`
  CONNECT 隧道直连实现，方便平移到 `httpx`/`aiohttp`）。
- **端点**：`POST /api/accounts/refresh`（`{indices, refreshEmail}`）；`/api/status` 的
  `accountDetails[]` 字段：`{canonicalIndex, hasContext, index, isDuplicate, isExpired, isInvalid,
  isRotation, name, tier, tierCheckedAt}`。

## 6. Build 通道与直连签名器（WP4 核心，详见 build-channel/ 全套文档）

### 6.1 两条通道对比

| | 直连通道（DirectSigner） | 原生通道（build.js） |
|---|---|---|
| 执行位置 | **顶层主页面** realm（`aistudio.google.com/apps/<appId>`） | 预览 iframe（`ais-pre-*.run.app`）内 build.js |
| 传输 | 页面自持 WS（role=signer）+ 页内 `fetch(ProxyStreamedCall)` | 页面 fetch → Studio shim → 宿主 MessageChannel → 宿主代签 |
| 请求体 | `[path, 原始 Gemini JSON 字符串, 签名]` **零编译** | 同款（宿主观测一致） |
| 流式 | 真流式（逐帧 holder 解析） | 真流式（宿主回传，速率受宿主节流） |
| 开关 | `MAKERSUITE_DIRECT=on`（默认 off） | 默认通道 |
| 静态路由 | `real` 流 = 直连候选；非 real 恒原生；直连不可用时**显式 502 拒绝**（`skipAccountSwitch`），绝不静默改道 | — |

### 6.2 直连链路七步（含全部坑位）

1. **注入**：`BrowserManager` 在 context 初始化时 `page.addInitScript` 注入 `installDirectSigner`（~788 行浏览器侧逻辑）。
2. **Hook**：包装 `window.default_MakerSuite.Cn`（捕获会话服务对象 `tb`）与 `XMLHttpRequest.prototype.send`（收割请求头/吞 warmup）。white-list headers：`authorization`、`content-type`、
   `x-goog-api-key`、`x-goog-authuser`、`x-goog-ext-519733851-bin`、`x-user-agent`。
3. **warmup 引导（0 额度）**：Node 经原生通道发 `models/__warmup__:streamGenerateContent`,
   宿主构造请求触发 `Cn`，页面 hook **本地吞没**（直接 400 应答，**不出网**），随后回传
   `signer_status: ready`。
4. **任务下发**：`{type:"signer_task", task:{id:"ds_<uuid>", body:原始JSON, model}}`。
5. **页面签名+直发**：
   `token = await M.Cn(tb, await M.GB(path + " " + jsonText))`（`M.GB`=64 位十六进制摘要；
   `M.Cn` 输出 `!` 前缀 ~2.2KB token；与 warmup 出站体 element[2] 交叉验证一致）；
   `fetch(tb.mb.F + "/$rpc/.../MakerSuiteService/ProxyStreamedCall", {body: JSON.stringify([path, jsonText, token])})`。
   **签名对字节敏感**：`path + " " + jsonText` 必须与上行体逐字一致（键序不可变），否则 403。
6. **Holder 逐帧解析**：响应是 `[[["{\"candidates\":…}", "…"]]]`（REST chunk 为转义 JSON 字符串，
   套 2–3 层数组）；`scanArrayElements` 状态机逐元素扫描，双解析后即刻发 `signer_frame`。
7. **Node 注入**：`signer_started` → 注入 SSE 响应头；`signer_frame` → `{event_type:"chunk", data}`；
   `signer_done` → `stream_close`；诊断通道 `signer_note`（Cn/GB 输入输出、warmup 出站体）。

### 6.3 上游与 wire 格式速查

- 上游主机：`alkalimakersuite-pa.clients6.google.com`；已实证方法：
  `ProxyStreamedCall`（现役直连/原生传输）、`GenerateContent`（旧桥，已封存留档）、
  `GetAiStudioBenefitTier`（账号探活）、`ListModels`（探活）。
- 请求体（ProxyStreamedCall）：`[ "/v1beta/models/<model>:streamGenerateContent", "<原始 Gemini REST JSON 字符串>", "!<签名>" ]`。
- `toolConfig ANY/allowedFunctionNames` 等任意形态**原样透传**（"抓样本补编译槽位"已被证伪：
  原生链路不存在编译步骤——见 `build-channel/evidence-20261017/native-forced-tool-egress-capture-20261017.md`）。
- 旧 `GenerateContent` 位置数组（仅供考古）：`[0]model [1]contents [2]safetySettings [3]generationConfig
  [5]systemInstruction [6]tools [10]1 [13]timezone`——**已被 ProxyStreamedCall 取代，勿基于它做新移植**。

### 6.4 排版类坑位（照抄即可避开）

- **NG0203**：页内严禁 `new M.TB()`；`tb` 只能经 hook `M.Cn` 捕获（Angular 注入上下文限制）。
- **字段混淆名漂移**：API key 访问须写 `tb.mb.nK || tb.mb.jK`（新版为 `nK`）；authuser 为 `tb.mb.Ml`；
  基址为 `tb.mb.F`；`tb.getMetadata()` 提供当前 X-Goog-Api-Key 与现算 SAPISIDHASH。
- **holder 解包顺序**：先双解析（JSON 字符串套 JSON），再进 wire 解析；勿把 holder 层当业务层。
- **帧协议**：`signer_frame.data` 已是 `data: …\n\n` 形态，Node 端不再包 SSE 壳。

## 7. ATG（Anti-Truncation Gateway，WP2 移植素材）

- **目标**：把模型正文强制经由"合成工具调用"通道交付（绕过文本通道截断/空回），再在代理边界
  解包为纯文本。**与通道选择完全解耦**（只改请求体/响应体）。
- **模式**：`off`（默认，零管线改动）/ `prefer`（尽力，模型不理会则普通文本通道）/ `required`（缺失合成调用=显式失败）；
  可经 `ATG_MODE_GEMINI/OPENAI/CLAUDE` 按对外格式覆盖（`inherit` 为默认）。
- **模块**（`src/atg/`，行数为物理行）：

| 模块 | 行 | 职责 |
|---|---|---|
| `config.js` | 77 | 模式/预算/正则装载与边界 |
| `injector.js` | 178 | 注入合成声明（`atg_emit_*`，`content:STRING`）+ 控制消息 + toolConfig AUTO；尊重客户端 ANY pin；客户端绕行守卫 |
| `extractor.js` | 107 | 非流式解包（functionCall→text，重置 finishReason） |
| `streamExtractor.js` | 185 | 流式逐块解包（状态机 + stats + 可选侧缓冲抑制前导泄漏） |
| `streamDecoder.js` | 377 | 增量 JSON 字符串解码（部分转义/代理对/UTF-8 边界） |
| `repair.js` | 246 | 畸形参数确定性修复（快路径解析→代码围栏剥离→括号引号配平） |
| `historyPurge.js` | 44 | 历史清理（剔除同前缀 functionCall/functionResponse 环） |
| `clientPatterns.js` | 191 | 客户端自带合成机制检测（5 信号：前缀名/回答声明+content 参数/强制固定工具/历史调用环/指令文本） |
| `modelFilter.js` | 47 | 文本模型白名单分类（图/音/嵌入跳过） |
| `index.js` | 73 | facade（wrap/unwrap/createStreamUnwrapper/isSyntheticToolName） |

- **配置面**（env → 行为）：`ATG_MODE`、`ATG_MODE_{GEMINI,OPENAI,CLAUDE}`、
  `ATG_CLIENT_SYNTHETIC_GUARD`（默认 on）、`ATG_TOOL_PREFIX`（默认 `atg_emit_`）、
  `ATG_CONTROL_POSITION`（tail 默认）、`ATG_TOOL_STRICT`、`ATG_EMPTY_RETRIES`（默认 2）、
  `ATG_RECOVERY`（repair/fail）、`ATG_SIDE_BUFFER_BYTES`（0=真流式）、`ATG_MAX_RESPONSE_BYTES`（16MB）、
  `ATG_{TEXT,NON_TEXT}_MODEL_PATTERN`。
- **管线注入/解包点（共 8 处）**：
  - wrap：`RetryHandler.js:45`（openai/claude/responses 生成）、`GeminiHandler.js:56`（原生 Gemini）；
  - 流式解包：`ChatCompletionsHandler.js:307,369`、`ClaudeHandler.js:431,491`、
    `ResponsesHandler.js:540,607`、`GeminiHandler.js:469,500`（对应 `finish()` flush：315/440/548）；
  - 非流式解包：`ChatCompletionsHandler.js:430`、`ClaudeHandler.js:556`、`ResponsesHandler.js:685`、`GeminiHandler.js:597`。
- **stats 对象**（每个解包器实例持有）：`{ conflict, realToolCount, repaired, syntheticCount, syntheticHit }`，
  流结束 debug 输出 `[ATG] stream done (<format>): stats=...`。
- **Python 侧移植预警（重要）**：
  1. aistudio-api 的 `wire_codec.py:329-343` 会在 `allowedFunctionNames` 存在时按白名单过滤声明——
     移植 ATG 时必须把本次请求的合成工具名加入该白名单，否则合成工具被剔出上行请求。
  2. Build 通道实测 functionCall 参数**整块到达**（非 token 级分片）；酒馆侧样本存在未决矛盾
     （见 `build-channel/direct-signer-pipeline-20261017.md` §7），`streamDecoder` 的增量收益在实测前勿夸大。
  3. 客户端绕行守卫默认开启，避免与客户端（酒馆类扩展）自带合成机制叠加。

## 8. 多格式转换层（WP8 移植素材）

- 模块（`src/core/FormatConverter/`）：`CommonConverter.js`（共享：模型后缀、Gemini 工具/schema、
  媒体装载、usage 解析）、`ChatCompletionsConverter.js`（OpenAI）、`ClaudeRequestConverter.js` +
  `ClaudeResponseConverter.js`、`ResponsesRequestConverter.js` + `ResponsesStreamResponseConverter.js` +
  `ResponsesNonStreamResponseConverter.js`、`EmbeddingsConverter.js`。
- 特性：流式/非流式双向、工具调用（含多轮 tool_calls/tool_result 映射）、`usage` 归一、
  思维块策略、`STREAMING_MODE=real|fake`（fake=先缓冲后按 SSE 吐）。
- **与直连的关系**：只有 `real` 流请求是直连候选；fake 恒走原生。ATG 解包在**客户端格式边界**做。

## 9. 运行开关总表（精选）

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` / `WS_PORT` | 7860 / 9998 | API / 浏览器 WS |
| `API_KEYS` | — | 客户端鉴权 |
| `MAX_CONTEXTS` | 1 | 同时在线账号上下文（~700MB/个） |
| `STREAMING_MODE` | real | real/fake |
| `SWITCH_ON_USES` | 40 | 每 N 次请求切号 |
| `FAILURE_THRESHOLD` | 3 | 连续失败切号 |
| `IMMEDIATE_SWITCH_STATUS_CODES` | 429,503 | 即时切号状态码 |
| `ENABLE_AUTO_SWITCH` / `AUTO_SWITCH_INTERVAL_HOURS` | false / 3 | 定时切号 |
| `MAKERSUITE_DIRECT` | off | 直连通道总开关 |
| `MAKERSUITE_WEB_API_KEY` | 内置公网 web key | 账号探活用 key 覆盖 |
| `ATG_*` | 见 §7 | ATG 全量开关 |
| `STREAM_TIMEOUT_MS` / `FAKE_STREAM_TIMEOUT_MS` | 60000 / 300000 | 块间超时 / 缓冲超时 |
| `HTTP_PROXY` / `HTTPS_PROXY` | — | Google 出网代理（线上见 VPS 文档） |
| `CAMOUFOX_EXECUTABLE_PATH` | 自动 | 浏览器可执行文件 |
| `AI_STUDIO_APP_URL` | — | 专用 Build App 链接 |

## 10. 行为契约（红线，移植后应保持）

1. **零静默回落**：直连候选失败=显式 502（含原因与处置，`skipAccountSwitch`），绝不悄然改道；
2. **错误消息具体化**：客户端 4xx/5xx 必须写清字段路径、收到值、处置建议；
3. **开关文案自解释**：开=什么/关=什么/何时开/失败怎么办；
4. **过期凭证标签必须显示**：过期徽章与 free/pro 档位徽章并存，不可互相覆盖；
5. **可 grep 承诺**：自动行为仅两类且全部有日志指纹（ATE 检测跳过=info；静态路由=debug），
   通道决策日志见 `build-channel/transport-decision-and-400-triage.md` 的指纹表。

## 11. 移植对照速查（Ark 文件 → Python 落点 → 计划书 WP）

| Ark 素材 | 建议 Python 落点（aistudio-api） | WP |
|---|---|---|
| `AccountInfoRefresher.js` | `application/account_refresher.py`（复用本仓 `calculate_sapisid_hash`；`asyncio.Semaphore(3)`） | WP3 |
| `src/atg/*`（10 模块） | `application/atg/`（逐模块翻译；白名单放行合成名） | WP2 |
| `DirectSignerService.js` 浏览器侧 | `infrastructure/browser/js/build_signer.js`（CDP `addScriptToEvaluateOnNewDocument` + binding 推送） | WP4 |
| `MakerSuiteCodec.js` `scanArrayElements/convChunk` | `infrastructure/gateway/holder_decoder.py`（holder 拆壳 → 喂现有 `wire_parser.py`） | WP4 |
| `FormatConverter/*` | `application/format_converters/` + `api/routes_openai.py` / `routes_claude.py` | WP8 |
| `UsageStatsService.js` | 融入本仓 `api/state.py`（增 `api_format`/`surface`/`atg` 维） | WP6 |
| 切号参数语义 | 本仓 rotator 已更强（黏性+按模型 429 隔离），仅需对齐即时状态码与定时开关 | WP5 |

## 12. 来源与致谢

- 本文件由 Ark_AIStudioToAPI 侧整理（2026-10-18），随 `docs/dev/PLAN-ark-merge-20261017.md` 一同移交。
- 深度研究文档见 `build-channel/`：桥接史（`makersuite-generate-content-bridge-20261016.md`）、
  封档与复活（`archive-2026-10-16-build-channel-closure.md`）、直连方案（`direct-signer-pipeline-20261017.md`）、
  400 排查手册（`transport-decision-and-400-triage.md`）、bundle 考古（`evidence-20261017/`）。
- ATG 逻辑源自 [Xeltra233/Antigravity-gateway](https://github.com/Xeltra233/Antigravity-gateway)（MIT）；
  Build 通道修复的社区交叉引用：`yunx738/ais2api`。
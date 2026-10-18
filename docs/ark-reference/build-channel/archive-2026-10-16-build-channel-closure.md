# 封档：Build 反代路线终结记录

> **日期**：2026-10-16 · **状态**：封档（本方向终止，不再投入修复）
> 本文件替代并归档原诊断笔记 `issue-2026-10-16-preview-relay-400.md`（已删除），供此后任何会话/协作者直接引用。
> **自本文件生效起，禁止再尝试修复或复活预览中继通道。**

## 一、结论（TL;DR）

1. **2026-10-16 17:00（UTC+8）起，AI Studio Build 预览宿主关闭了"无凭据静默代签"通道**：本项目全部数据面（`_aistudio-iframe.js` → MessageChannel → 宿主）中的 generativelanguage 请求被宿主侧统一拒绝，返回**合成的 400 INVALID_ARGUMENT**。全社区同时中招，客户端侧无解。
2. **新 Build 模型要求自带凭据**：调用须位于应用服务端（`server.ts` + `@google/genai`），凭据为平台注入的 `process.env.GEMINI_API_KEY`；该 key 需用户在 UI 中为应用选择/绑定，且**绑定在 Google Cloud 项目上**。Build 通道不再是"零配置免费额度池"。
3. 谷歌自 2025-06 起持续收缩 API 能力；当前**最大上下文约 13 万 token**、**GCP 项目存在 403/封禁风险**、**Pro 与最新 argon 模型不可用**（用户线报，见 §四）。
4. 因此本方向**正式封档**：不修复、不迁移该通道；本仓库现有实现（预览通道中继）进入维护冻结，仅保留历史价值。
5. 出路参考：**AI Studio Web 会话路线**（cookie + BotGuard 页内重放），见 §六方向 C。

## 二、事件与证据链（实测）

### 2.1 症状

- 所有账号/模型生成请求返回 400，错误体仅：
  `{"error":{"code":400,"message":"Request contains an invalid argument.","status":"INVALID_ARGUMENT"}}`
- 约 0.6s 返回、无 details / fieldViolations —— 不是公网 REST 常规校验错误的形态。

### 2.2 判定：该 400 系宿主侧合成

- 页内钩子抓取出站消息（portOut）：headers 仅 `accept` / `content-type`，**无任何 key**（build.js 转发前会剥离 `x-goog-api-key` 防凭据外发），body 511B。
- 响应重建点（respCtor）：`status=400, statusText="INVALID_ARGUMENT"`。该 statusText 是 gRPC 状态名，按 fetch 规范 HTTP/2 与跨域响应的 statusText 恒为空 —— 只能由宿主经 MessageChannel 送回、由 shim 重建 `Response` 产生。
- Playwright 全程零网络事件：真实调用在**宿主/谷歌内侧**完成，浏览器网络层不可见。
- 差分矩阵（7 组，含仅 contents 的最小 body）、多账号、多模型**全部同签** —— 与请求内容无关，拒单先于内容校验。

### 2.3 旧通道（至 10-16 17:00）为何无需 key 即可用

- 架构：预览页 fetch → `_aistudio-iframe.js`（宿主注入 shim，内含字面占位符 `window.API_KEY='GEMINI_API_KEY'`）→ MessageChannel → 宿主侧代持凭据执行并回传。
- 凭据从未出现在请求中而请求一直可用 ⇒ 凭据由**宿主与会话/账号绑定**；本项目多账号轮换（`auth-N.json`）即摊薄该额度。
- 10-16 宿主取消静默兜底 ⇒ 拒单。客户端侧补救（补占位符头等）未经证实有效；官方新模型已明示走服务端，不复存在客户端直连通道。

### 2.4 新 Build 模型机制（社区实证 + 平台文案互证）

- 工作形态：全栈应用（Express + Vite 中间件），浏览器只调自家 `/api/*`；服务端用 `@google/genai`（`User-Agent: aistudio-build`）直连 generativelanguage（请求头带 `x-goog-api-key`），不经 shim/宿主 ⇒ 不报 400。
- key 来源：平台启动时注入，原文依据为模板 `.env.example`：_"AI Studio automatically injects this at runtime from user secrets"_；用户在 UI 的 Key 选择弹窗/Secrets 处配置；**绑定 GCP 项目**。
- 社区实锤：build 与 GCP 项目关联；被 403 账号（"项目是死的"）不可用；能正常对话者均为有可用 key/项目者。
- 我方实测（无 key 新应用）：`hasKey=false`（env 仅 `GOOGLE_RUNTIME`）→ SDK 回退 ADC → `403 ACCESS_TOKEN_SCOPE_INSUFFICIENT`；应用自产 400 "GEMINI_API_KEY 未配置"。

### 2.5 三类错误的区分（避免混淆）

| 错误                                                         | 来源                     | 触发条件                           |
| ------------------------------------------------------------ | ------------------------ | ---------------------------------- |
| 400 INVALID_ARGUMENT（裸体、无 details、gRPC 名 statusText） | 预览中继宿主网关（已死） | 客户端直连、无凭据                 |
| 400 "GEMINI_API_KEY 未配置…"                                 | 应用自身守卫代码         | 服务端未注入 key                   |
| 403 scope insufficient / PERMISSION_DENIED                   | 公网 API                 | 无 key 走 ADC，或项目无权限/被处置 |

## 三、为何不用"自带 GCP key"续命

1. key/额度绑定 GCP 项目，**多账号轮换不再放大额度**（额度跟着项目走）；
2. 需避免 GCP 项目 403/封禁风险（用户红线）；
3. 免费额度残缺，与"API 池"定位不符。

## 四、API 收缩背景（用户线报）

- 2025-06 起：谷歌开始削减 API：调用次数、最大上下文、可用模型范围；
- 现状：**最大上下文约 13 万 token**（与"路由到 GCP key"现象吻合）；
- **无法使用 Pro 模型 / 最新 argon 模型**；
- 红线：不得把 GCP 项目推入 403/封禁路径。

## 五、封档操作记录（本次）

- 删除本地诊断产物：`scripts/debug/`（差分矩阵 / 通道探针 / ClientCapture v2/v3）；
- 删除原诊断笔记 `docs/zh/issue-2026-10-16-preview-relay-400.md`（由本文替代）；
- 回滚 `src/core/BrowserManager.js` 中临时 [NetDiag] 补丁（工作区还原至 HEAD）；
- 删除临时 oneshot 脚本；关键证据快照（shim 源码 `_aistudio-iframe.js`、ais-pre 页头/根文档）保留于本机临时目录，**易失，勿依赖**；
- 本文档独立提交并推送至 fork（main）。

## 六、后续方向（先行探索结论，未实施）

> 结论先行：**以 C 为主体推进，A 作过渡，D 按需补位，B 放弃。**

1. **C（首选）微调 `aistudio-api`（AI Studio Web 会话路线）**：以其现成能力（cookie 多账号 + u/N 探活、BotGuard 页内签名、JSPB 编解码、按模型 429 隔离黏性轮换、账号热切、Web 控制台、Docker）为基座，增量实现：
   - **双开无空窗**：同一浏览器内使用**两个 browser context**（独立 cookie jar，等价于 Playwright context 概念）分别持号；切换时先备好备用 context 再放流，消除换号空窗（面向 24h 常驻 agent / QQ bot 场景）。内存增量非翻倍——增量仅为第二个页面/上下文自身开销（预计百 MB 量级以内，需实测；参考项目 `--renderer-process-limit=1` 下两页共享渲染进程与 JS 堆上限，需相应放宽）。
   - **ATG 移植**（Anti-Truncation Gateway：防截断/空回，含控制位注入、历史清理与响应解包，本仓库 `src/atg/` 已有完整实现可参照）。
   - **账号刷新**调度（cookie 重新导入 / 会话续期）。
   - **多格式 API 兼容层**（OpenAI / Claude / Responses）选择性对接。
   - 凭证兼容性：我方 `auth-N.json`（Playwright storageState 形态）**原生可直接导入**。
2. **A（过渡）原版直用 `aistudio-api`**：零开发立即恢复可用；缺无空窗切号 / ATG / 多格式 API 面。先顶住，再按 C 改造。
3. **D（补位）并行接入 `GeminiLocalAPI`（gemini-webapi 2.1.1.post2，gemini.google.com 消费者端）**：独立上游面（Gemini App），强项为文生图（Nano Banana）、视频/音频、Deep Research、Gems/扩展、思维链。**不做融合重构**；按能力缺口补位（同一 Google 账号体系，`__Secure-1PSID/1PSIDTS` 通常已在 `auth-N.json` 内），或以独立服务并行部署。
4. **B（否决）在 Node 侧重写 Web 会话路线**：等于重做 BotGuard/WAA 逆向 + JSPB 编解码 + 页内重放，维护成本最高且无增量收益。

## 七、参考索引

- `D:\LLM\self_programming\aistudio-api` —— AI Studio Web 反代（FastAPI + CloakBrowser CDP + BotGuard 页内签名 + 多账号调度）；入门看其 `README.md` 与 `docs/{ARCHITECTURE,BOTGUARD_VERIFICATION_CHAIN,WIRE_SPECIFICATION}.md`。
- `D:\LLM\self_programming\GeminiLocalAPI\GeminiLocalAPI-source` —— gemini-webapi（Gemini 消费者端异步库 + CLI + desktop）。

---

## 八、更正与状态变更（2026-10-17）

1. **因果表述更正（§2.2/§2.3）**：旧代理通道并非"宿主本地合成 400、未出网"。实测旧 RPC 为**真实网络请求**——抓取到完整请求头（`host=alkalimakersuite-pa.clients6.google.com`、`x-goog-api-key` 39 字符 AIza key、`authorization` 195 字符、cookie）。400 INVALID_ARGUMENT 系**上游拒绝该旧 RPC 通道的生成 POST 后透传**（与 yunx738/ais2api 修复结论一致）；此前"零网络事件"推断系观测范围所限。
2. **状态变更**：经用户指示，本方向**解除封档并已复活**——采用代理层桥接（拦截旧 RPC → 页面内直调 `MakerSuiteService/GenerateContent` → 旧信封回填；App 零改动），2026-10-17 真机验证 HTTP 200（非流式×5 + 流式 + ATG 回环）。实现与验收记录见 `docs/zh/build-channel/makersuite-generate-content-bridge-20261016.md`，分支 `fix/makersuite-generatecontent-bridge`。
3. §一.1 修订：全社区同时中招属实；"客户端侧无解"不成立——代理层桥接即为客户端侧解法，已实证。

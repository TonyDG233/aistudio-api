# 08-11 Build 400 离线溯源（辅助附录，非计划门）

> **状态：未请求的辅助附录，不属于 P4 计划门，不进入 05/10 必读清单，不要求 G1–G9 证据、reviewer 签字或 Q4 写回。** 当前没有进入 P4；Build 实现阶段到来时按 `08-10` §4 重新做前期探索即可。本文内部出现的 B0a/B0b 仅是附录中的历史分析标签，不构成当前任务或阻塞。

状态：**辅助附录（仅部分溯源）**。本文件只做代码/文本/证据溯源，**不判断已确认根因**、不宣告 Build 或 signer 可用、不替代 P4 方案、不含实现代码。
基线：撰写时分支 `rebuild`，HEAD `ba6c219`；当前 HEAD/工作区以 `git status` 为准（见附录 B）。
状态标签：本文内部 `B0a/B0b` 仅为历史分析标签，不构成当前计划门或阻断。
事故来源：`08-10-build-surface-incident-20261028.md:9-21`（用户提供的整理日志，未独立核对原始日志）。

---

## 0. 结论摘要

- 400 的错误文本与 `docs/ark-reference` 参考副本的模板一致（**同源模板；线上实际 bundle 未取证，G9 待补**）。按该模板，文本来自 Build 客户端 `fetch` 非 2xx 分支（`docs/ark-reference/scripts/client/build.js:267-276`、`:985-993`），经 9998 回传（`build_surface.py:157-206`、`:851-905`）。**本次提供的日志没有显示** 9998 断开事件，**不能据此断言 9998 断线，也不能断言 watchdog 被触发**。
- 我们的 Python 侧把该 `error` 事件当成“首帧前断开”，触发一次 native 重试（`build_surface.py:1616-1646`、`:1783-1797`）；**400 的 `status` 字段在 pump 层被丢弃**（`:894-897`），因此无法区分 400 与真实断线。
- 单次客户端请求内 native 重试 **有界（最多 1 次）**；但 400 被归为瞬态，**跨请求**会无界重复（`:1323-1359`、`:1577-1579`）。watchdog 只在 **9998 断开超过 10s** 时触发（`:1330-1359`）；本次日志未证明 9998 断开，所以不能把本次 400 说成“由 watchdog 重连触发”。
- 本次日志最终错误码是 `NATIVE_ERROR` 而非 `SIGNER_ERROR`（`08-10:20`）。**前提**：同一请求、首帧前、无 status 成功帧、且无回退 WARN。该事实只证明**这一个请求**没有发生 native→signer 回退（`build_surface.py:1664-1679`、`:1791`），**不证明 signer 整体不可用**。
- `PROXY_GENERATION_DISABLED: streamGenerateContent is not supported` 最可能是**端点/渠道级拒绝**（中高置信），但**未确认**；同路径在 2026-10-18 曾记录 **native** 200/2 frames/3.01s（`06-feature-ledger.md:613`，仅 native 侧索引；真实账号不可复跑）。**signer** 的 200/2 帧是另一条通道（`docs/dev/AUDIT-P2P3-20261018.md:91`），**不能作为 native 反证**；native 目前**无独立可复跑证据**。因此不能排除服务端策略变化，也不能排除 model/app 组合差异。
- 现有 signer 与 native **共用逻辑路径** `.../{model}:streamGenerateContent`（`build_surface.py:835`、`build_signer.js:905`），但 **RPC 主机/服务不同**（native → `generativelanguage.googleapis.com`；signer → `alkalimakersuite-pa.clients6.google.com/$rpc/.../ProxyStreamedCall`）。**不能假定 signer 绕过同一拒绝。**
- **历史附录**：本报告只完成部分溯源，不构成当前计划门、不要求补证据、不要求签字，也不给出“完成/解除”结论；`08-10` §4 是唯一的未来 P4 前期探索要求。

---

## 1. 事故复述

| 项 | 内容 | 证据 |
|---|---|---|
| 时间 | 2026-10-28 12:04:21 / 12:04:22（日志查看器时间，未独立核对） | `08-10:9-21` |
| 组件 | `aistudio.build_surface`（WARN）→ `aistudio.upstream.errors`（ERROR） | `08-10:12`、`:18` |
| 失败面 | `surface=build` 的 native 流请求 | `08-10:25` |
| 上游返回 | `400 INVALID_ARGUMENT`，`{"error":{"code":400,"message":"PROXY_GENERATION_DISABLED: streamGenerateContent is not supported","status":"INVALID_ARGUMENT"}}` | `08-10:14`、`:20` |
| 时机 | native 在**首帧前**断开（无 status/chunk 成功帧） | `08-10:29` |
| 当前处置 | 打印“等待 9998 重连后重试”，随后仍 `NATIVE_ERROR` | `08-10:14`、`:20` |
| 不是 | 网络超时、空 200、malformed SSE、普通 BotGuard 失败 | `08-10:31` |

补充事实（由代码而非日志推出）：

- 按 `docs/ark-reference` 参考副本模板（**同源模板；线上实际 bundle 未取证，G9 待补**），错误文本前缀 `Proxy browser error: ` 由页面侧 Build 客户端在 `fetch` 失败时加（`docs/ark-reference/scripts/client/build.js:989`）；`Google API returned error: <status> <statusText> <errorBody>` 来自同一模板的非 2xx 分支（`:269-275`）。因此可确认的是：该 400 响应体来自 Google，请求已真正出网并收到 HTTP 响应。
- 该错误以 `event_type: "error"` 回传，并**带有** `status: error.status || 504`（参考副本 `build.js:992`）；我们的接收链没有保留 `status`。

---

## 2. 调用链（`build_surface:stream` → 9998 → proxy browser/transport → Google API）

### 2.1 实际路径（native）

```text
客户端流式请求
  generation_service.stream_generate(...)                         application/generation_service.py:618-625
    surface == "build" → client.stream_generate_content(surface="build", rest_body=...)
      AIStudioClient.stream_generate_content                      infrastructure/gateway/client.py:581-600
        _build_lease_auth / _ensure_lease_build_surface           client.py:158-177
        BuildSurface.ensure_ready(...)                            build_surface.py:975-1188
          page.goto(https://aistudio.google.com/apps/<app_id>?showAssistant=true&showPreview=true)
                                                                  build_surface.py:72-74, :1111
          等待 app 内嵌客户端连上 9998 → native_ready=True        build_surface.py:1128-1166
        BuildSurface.stream_generate(model, json_body)            build_surface.py:1553-1842
          _dispatch_native(task_id, model, json_body)             build_surface.py:819-849
            websocket.send({method:"POST",
              path:"/v1beta/models/{model}:streamGenerateContent",
              query_params:{"alt":"sse"},
              request_attempt_id/request_id, streaming_mode:"real",
              is_generative:true, body})                          build_surface.py:833-844
              9998 sink 收消息 → 按 request_id 投递队列              build_surface.py:157-206
          _pump_native(...) → 归一化 status/chunk/done/error      build_surface.py:851-905
  按 docs/ark-reference 参考副本模板，页面内 Build 客户端（build.js）执行 spec（线上实际 bundle 未取证，G9 待补）：
    URL   = https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse
    method= POST，body = spec.body（Gemini REST JSON）
    fetch(...) → 非 2xx → Error("Google API returned error: 400 ... <Google errorBody>")
    → 9998 {event_type:"error", message:"Proxy browser error: ...", status:400}
                                                                  docs/ark-reference/scripts/client/build.js:202-203, :264-276, :423-437, :985-993
  回到 stream_generate 的 error 分支 → retry_native / fallback_to_signer / NATIVE_ERROR
                                                                  build_surface.py:1783-1797
  BuildSurfaceError(stage="stream", code="NATIVE_ERROR") →
    handle_attempt_exception 未识别该类型 → 通用分支 HTTPException(500)
                                                                  application/upstream_errors.py:378-392
```

关键点：按 `docs/ark-reference` 参考副本的同源模板（**线上实际 bundle 未取证，G9 待补**），400 在页面浏览器层产生（Google 对 `generativelanguage.googleapis.com` 的响应），并由页面侧 Build 客户端按该模板包装成 `Proxy browser error: ...`，再经 9998 回传。**不能把“页面实际行为”写成已证实**；可确认的是我们的 Python 不产生该文本，也不解析其 HTTP 状态。

### 2.2 为什么最终是 `NATIVE_ERROR`

- `error` 分支先尝试 `retry_native(...)`（`build_surface.py:1785`），再尝试 `fallback_to_signer(...)`（`:1788`），两者都失败后按当前 transport 决定 code（`:1791`）。
- 本次日志出现 `NATIVE_ERROR`，且没有 `native 传输失败（...），回退 signer 直连` 的 WARN（`:1668`）。**前提**：同一请求、首帧前、无成功 status 帧。由此只能推出**该请求**的 `fallback_to_signer` 返回 False；其唯一非 emitted 失败条件是 `state.signer_ready == False`（`:1666`）。**这只证明该请求未回退，不证明 signer 整体不可用。**
- `retry_native` 的日志文本正是事故第一条日志（`:1628-1630`），触发参数是 `message[:200]`（`:1785`），即 400 文本本身。说明 **400 被当作“首帧前断开”**。

### 2.3 与 chat 面传输的差别（为什么不能拿 chat 的经验推断）

- chat 面 `transport.py` 在页面内用 XHR 回放，并在 `_ensure_authorization_header` 里补 `Authorization`、`X-Goog-AuthUser`、`X-AIStudio-G1-Tier`（`transport.py:69-116`）。
- Build native **不经过** `transport.py`，其 spec **没有 headers 字段**（`build_surface.py:833-842`）；参考副本在 headers 为空时只做 `{...undefined}` 并剔除禁头（`docs/ark-reference/scripts/client/build.js:423-428`、`:634-656`）。**我们的 spec/参考副本不发送 tier 头；线上实际出站 headers 未验证，不能据此否定 tier 假设。**
- Build signer 也不走 `transport.py`，它自建 headers（`build_signer.js:793-812`）。

---

## 3. native 路径

### 3.1 端点 / 方法 / 体 / 头

| 项 | 值 | 证据 |
|---|---|---|
| 通道 | 9998 WebSocket（`ws://127.0.0.1:9998`，`?realm=` 分桶） | `build_surface.py:126-154`、`:209-225`、`:907-934` |
| spec 方法 | `POST` | `build_surface.py:834` |
| spec 路径 | `/v1beta/models/{model}:streamGenerateContent` | `build_surface.py:835` |
| query | `alt=sse` | `build_surface.py:836` |
| URL 模板（参考副本；线上实际 URL 待 G4 取证） | `https://generativelanguage.googleapis.com/{path}?{query}` | `docs/ark-reference/scripts/client/build.js:202-203`、`:338`、`:385` |
| 请求体 | `rest_body`：`effective_req.model_dump(mode="json", exclude_none=True)` 的 JSON 字符串 | `generation_service.py:553-558`；发送于 `build_surface.py:841` |
| 请求体形状 | Gemini `generateContent` REST 体（`contents`、`systemInstruction`、`tools`、`toolConfig`、`generationConfig`、`safetySettings`），**不含 model 字段**（model 在 URL） | 同上；`client.py:596-598` |
| 关键 headers（native） | **spec 未携带任何 header**；按参考副本，客户端仅剔除禁头、不补 key；**线上实际出站 headers 未取证，G5 待补** | `build_surface.py:833-842`；参考 `build.js:423-428`、`:634-656` |
| 认证 | native 路径在**我们可控的代码里没有显式凭据**；是否由页面宿主补凭据、页面实际带什么凭据，离线无法证明（见 §9） | — |

> 注意：`docs/ark-reference/scripts/client/build.js` 是 Ark 参考项目内的 Build 客户端副本，**不等于** Google 当前实际投放的 bundle。上述 URL/字段/错误模板只能作为“**与参考副本同源模板一致**”的证据；**线上实际 bundle 未取证**，不能写成已证实的页面行为。

### 3.2 `streamGenerateContent` 在代码中的使用点

| 位置 | 用途 | 性质 |
|---|---|---|
| `build_surface.py:835` | native dispatch spec 的 path | fork 新增 |
| `build_surface.py:1437` | warmup spec 的 path（`__warmup__:streamGenerateContent`，本地吞没，零上游流量） | fork 新增 |
| `build_signer.js:905` | signer 的 RPC payload[0]（逻辑路径） | fork 新增 |
| `routes_gemini.py:34`、`:40` | **客户端 API 路由**（我们对外提供的 Gemini 接口），不是 Build 上游调用 | 上游原生 |
| `docs/dev/evidence/p3-build3/spikeQ4.py:236` | 历史探针 warmup | 证据文件，不入库 |

### 3.3 是否存在别的 Build 端点 / 选择点

- **传输选择点**：`_preferred_transport`（`build_surface.py:366-377`）——默认 native；`AISTUDIO_BUILD_TRANSPORT=signer` 仅在 signer 健康时生效；`stream_generate` 在 `:1580` 调用。
- **native 重试点**：stream 开始时连接缺失的轻量恢复（`:1577-1579`、`:1684-1692`）；首帧前 error 的一次重试（`:1616-1646`）。
- **回退点**：`fallback_to_signer`（`:1664-1679`）与超时回退（`:1718-1723`）。
- **其他端点（非本仓实现，来自 Ark 参考证据）**：
  - `MakerSuiteService/ProxyStreamedCall`（signer 当前使用，见 §4）；
  - `MakerSuiteService/GenerateContent`（Ark 2026-10-16/17 桥接修复使用的“产品 UI 同款方法”）——`docs/ark-reference/build-channel/makersuite-generate-content-bridge-20261016.md:8-10`、`:18-24`；`docs/ark-reference/build-channel/direct-signer-pipeline-20261017.md:25-27`；
  - `MakerSuiteService/ProxyUnaryCall`（旧通道）。
- **上游对比（99c38ba）**：`build_surface.py`、`build_signer.js`、9998 sink 均**不存在于 upstream**（`git cat-file -e 99c38ba:...build_surface.py` 失败；`git ls-tree` 无 `js/build_signer.js`）。99c38ba 中唯一的 `streamGenerateContent` 是**对外 Gemini 路由**（`99c38ba:src/aistudio_api/api/routes_gemini.py:32`）。因此 **Build 面整条链路都是 fork 新增，不能宣称“上游原生已验证该端点”。**

---

## 4. signer 路径

### 4.1 实际发送内容

| 项 | 值 | 证据 |
|---|---|---|
| 触发 | `window.__buildSigner.execute(taskId, model, jsonBody)` | `build_surface.py:317-319`、`:1648-1662`；`build_signer.js:108-110`、`:846-887` |
| 前置 | `state === "ready"`：`tb` + 收割头 `x-goog-api-key` + 自检通过 | `build_signer.js:151`、`:93-95` |
| URL | `rpcBase() + "/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/ProxyStreamedCall"` | `build_signer.js:66-68`、`:815-824`、`:923` |
| 主机 | 优先 `tb.mb.F`，缺省 `https://alkalimakersuite-pa.clients6.google.com` | `build_signer.js:68`、`:815-824` |
| method | `POST`（`nativeFetch`） | `build_signer.js:927-932` |
| body | `JSON.stringify([ "/v1beta/models/{model}:streamGenerateContent", jsonText, token ])` | `build_signer.js:905-906`、`:922` |
| headers | `content-type: application/json+protobuf`、`x-user-agent: grpc-web-javascript/0.1`、`x-goog-authuser`（`tb.mb.Ml`）、`authorization`（`tb.getMetadata().Authorization`）、收割头白名单；缺 `x-goog-api-key` 时用**仓库内硬编码的默认 web key**（值不复制） | `build_signer.js:56-63`、`:71`、`:793-812` |
| 流读取 | `resp.body.getReader()` + 数组扫描，按原始叶子推送 | `build_signer.js:962-1010` |
| 错误 | 非 200 时 `normalizeError` → `[buildSigner] upstream (HTTP xxx) rejected the request: ...` | `build_signer.js:826-843`、`:944-959` |
| 自检 | **纯本地签名自检**（`sha256Hex("build-signer-selftest")`），不发出生请求 | `build_signer.js:234-270` |

### 4.2 与 native 的关系

- **相同**：逻辑方法/路径都是 `POST /v1beta/models/{model}:streamGenerateContent`；都使用原始 Gemini REST JSON。
- **不同**：按参考副本模板，native 经 Build App 的页面客户端 fetch 到 `generativelanguage.googleapis.com`（**线上实际链路未取证，G4/G5 待补**）；signer 经页面顶层脚本 fetch 到 MakerSuite RPC（`ProxyStreamedCall`），body 是 `[path, json, token]` 信封（signer 侧代码可直接复核）。
- **证据能证明**：signer 是独立实现、独立主机、独立鉴权（SAPISIDHASH + 收割 key + token），且其“就绪”只代表本地 token/自检成功。
- **证据不能证明**：
  - signer 在 2026-10-28 的同样账号/app/model 下能否 200；
  - `PROXY_GENERATION_DISABLED` 是否同样作用于 `ProxyStreamedCall`；
  - signer 的 `streamGenerateContent` 路径参数是否也被上游策略拒绝。
- **反面证据**：Ark 参考记录显示，2026-10-16 起 `ProxyUnaryCall`/`ProxyStreamedCall` 的生成 POST 曾被 Google 整体 400 拒绝，修复方向是改调 `MakerSuiteService/GenerateContent`（`docs/ark-reference/build-channel/makersuite-generate-content-bridge-20261016.md:8-10`）。本仓 signer 目前仍是 **ProxyStreamedCall + streamGenerateContent**，**存在与 native 同类拒绝的合理风险**，不得假定可用。
- **正面但不充分的证据**：本仓 signer 曾在 2026-10-18 报告“direct 200 / 2 帧”（`docs/dev/AUDIT-P2P3-20261018.md:91`；原始 `spikeQ4-evidence.json` 不在本仓库），但该证据不可复跑、且早于本次事故 10 天，不能证明现在可用。`06-feature-ledger.md:613` 只是索引行，不作为 signer headers 的证据。

---

## 5. 9998 行为

### 5.1 为什么首帧前断开后会进入“9998 重连”

1. `stream_generate` 的 error 分支调用 `retry_native(message[:200])`（`build_surface.py:1783-1787`）。
2. `retry_native` 的前置条件只有：`transport == "native"`、`emitted == False`、`native_retries < 1`、`_realm_id` 非空、页面未关闭（`:1619-1626`）。**它不检查错误类型、HTTP status，也不检查 9998 连接是否真的断开。**
3. 满足条件即打印“native 在首帧前断开，等待 9998 重连后重试”（`:1628-1630`），取消 pump、清队列、调用 `_recover_warmup(page, timeout_s=15.0)`（`:1639`），再 `_dispatch_native` 重发（`:1640`）。
4. `_recover_warmup` 在 9998 **仍然连接**时会直接跳过等待循环、重发 warmup 并返回 True（`build_surface.py:1272-1301`）。因此日志中的“等待 9998 重连”在本次场景下**名不副实**：连接未断，重发只导致同一条 400 再来一次。
5. 第二次 error 到达时 `native_retries >= 1`，`retry_native` 返回 False（`:1621-1626`），进入 `fallback_to_signer`；本次 signer 未就绪 → `NATIVE_ERROR`（`:1791-1797`）。

### 5.2 重试是否有界

| 范围 | 行为 | 证据 |
|---|---|---|
| 单次客户端请求（native） | **有界**：最多 1 次 `retry_native` + 首次 dispatch = 2 次；另外 dispatch 前可能有一次 `_recover_warmup`（不是重发） | `:1616-1646`、`:1682-1704` |
| 单次客户端请求（signer） | 单向切换，不来回 | `:1664-1679` |
| 单次客户端请求（超时） | 只在 native 下允许一次 signer 续期；已是 signer 则直接 TIMEOUT | `:1718-1723`、`:1808-1814` |
| 跨请求 | **无界**：每次新请求都会再次 dispatch → 再次 400 → 再一次重试 | `:1682-1704` |
| 后台 watchdog | **仅在 9998 断开超过 10s 时触发** `_recover_warmup`；watchdog 循环本身无界（页面关闭才 return）。**本次提供的日志未证明 9998 断开** | `:1323-1359` |
| 上层生成重试 | **不追加**：`BuildSurfaceError` 不是 `AistudioError`/`RequestError`，落入通用分支直接 `HTTPException(500)`，不消耗 `MAX_RETRIES` | `upstream_errors.py:378-392`；`domain/errors.py:8-27`；`account_orchestrator.py:13` |

> **区分**：本次事故可确认的是“同一请求内 1 次有界重试 + 跨请求会重复”；**不能**把 400 直接归因于 watchdog。watchdog 的触发条件是 9998 断开 >10s（`:1330-1359`），而**本次提供的日志没有显示任何 9998 close/disconnect 证据**。B0b-10 的测试应分别覆盖“连接保持 + 400”和“真实断开 + 恢复”两种场景。

结论：**“无限重试”不是单请求内的死循环**；风险在于 400 被当作瞬态，导致每次请求都重复“重发 + 400”；若同时发生真实 9998 断开（本次未证明），watchdog 才会在断开 >10s 后重连。B0b 必须让 400 走 fail-fast。

### 5.3 400 是否被错误当作瞬态

是。判定唯一依据是“首帧前 + 未 emitted + 未重试过”，没有 status/code 判定（`:1616-1626`）。同时：

- `_app_ws_sink_handler` 原样保留 message 字段（`:176-183`），但 `_pump_native` 只把 `message` 转成 `{"type":"error"}`（`:894-897`），**丢弃了 `status`**（参考客户端会带 `status: 400`，`build.js:992`）。
- 因此即使上游 400 与 WebSocket 关闭在语义上完全不同，下游得到的都是同一个 `error` 事件。

### 5.4 就绪判定与生成能力脱节

`ensure_ready` 只要求“app 内嵌客户端连上 9998”就置 `native_ready=True`、`ready=True`（`build_surface.py:1128-1166`）。**9998 连接只证明通道握手成功，不证明上游允许 `streamGenerateContent`。** 本次事故正是该缺口：就绪 → dispatch → 400。

---

## 6. 失败分类假设表（均为候选，未确认）

置信度为离线证据下的主观区间；排序仅表示当前证据强弱。

| # | 假设 | 支持证据 | 反证 / 缺口 | 离线区分方法 | 置信度 |
|---|---|---|---|---|---|
| H1 | **端点/渠道级拒绝：`streamGenerateContent` 在该代理通道不被支持** | 错误文本直接点名方法（`08-10:14`）；400 `INVALID_ARGUMENT`；无首帧；约 1s 返回；Ark 记录显示 2026-10-16 起同类生成 POST 被整体拒，需改走 `GenerateContent`（`makersuite-generate-content-bridge-20261016.md:8-10`）；`ProxyStreamedCall`/`ProxyUnaryCall` 均属旧生成通道 | 2026-10-18 曾记录 **native** 200/2 frames/3.01s（`06-feature-ledger.md:613`；仅 native 侧、不可复跑），说明并非永久“不支持”；也可能是 model/app 组合差异。**signer** 的 200/2 帧（`AUDIT-P2P3-20261018.md:91`）不能作为 native 反证；native 无独立可复跑证据 | 用脱敏错误体做指纹：`400 + PROXY_GENERATION_DISABLED + "streamGenerateContent is not supported"` 与 401/403/429/连接关闭分别建 fixture；对同一 body 换 `:generateContent` 只能在实机做，离线只能验证“错误码→分类”映射 | **中高 0.6** |
| H2 | **模型/端点组合不支持**（该 model 不支持流式，或该 model 在 Build 代理被禁用） | 文本提到具体方法；未记录 model（`08-10` 无 model 字段） | 无 model 证据；若是模型级，通常会返回 `model not found / not supported for method` 而非 `PROXY_GENERATION_DISABLED` | 离线只能检查代码是否把 `build` 的 model 规范化成异常值（`generation_service.py:519,602`）；真正区分需同一账号换 model（实机授权） | 中 0.3 |
| H3 | **app / Build 应用级禁用**（app 未绑定可用 key、代理生成被关闭） | 错误前缀与参考副本的 Build 客户端模板一致；Ark 档案称新 Build 模型要求应用自带 `GEMINI_API_KEY`、绑定 GCP 项目（`archive-2026-10-16-build-channel-closure.md:10`、`:36-41`） | 若 app 未配 key，常见是应用自身 `400 GEMINI_API_KEY 未配置` 或 `403`，与本次文本不同；且 9998 握手成功、请求已出网 | 离线检查 native spec **不带任何 key/header**（§3.1），说明“app key 未注入”是可能的上游视角；区分需换 app_id（实机） | 中低 0.25 |
| H4 | **账号 / tier 不允许** | 账号可能有地区/额度/产品开关差异 | **我们的 spec/参考副本不发送** `X-AIStudio-G1-Tier`（对比 `transport.py:94-98`），但**线上实际出站 headers 未验证**，不能据此否定 tier 假设；错误是 400 方法级，不是 403 `PERMISSION_DENIED`；无账号级字样 | 离线只能确认“我们的代码没有发 tier 头”，不能证明页面/宿主没发；区分需换账号（实机） | 低 0.2 |
| H5 | **代理 / 出口 / 区域策略** | 请求确实经代理出网；可能是出口 IP 被策略化 | 客户端收到了 Google 形态的 400 JSON，说明网络可达、非本地代理阻断；无 TLS/超时/连接错误 | 离线检查错误是否来自 `fetch` 非 2xx（是，§2.1）；区分需直连/换出口（实机） | 低 0.15 |
| H6 | **签名 / 鉴权失败** | native 路径在我们的 spec 中没有显式凭据；页面是否补凭据未知 | 鉴权类通常 401/403 + `API_KEY_INVALID`/`UNAUTHENTICATED`；本例是 400 且文本谈“方法不支持”；signer 的 `authorization`/key 逻辑与之无关（未参与本次请求） | 离线可对比错误码族；可对 signer 用 stub 复现其错误包装（只能证明包装，不证明上游） | 低 0.1 |
| H7 | **真实瞬时断线** | 日志措辞“首帧前断开”；代码按断线重试 | 9998 消息能送达并回传错误；无 `stream_close`/socket error；同一请求重发后仍是同一 400（确定性）；`retry_native` 没有看到任何“断开”事件 | **最强离线反证**：错误事件携带 400 body；若是断线，不会有 Google JSON。可加 fixture：`error{status:400,message:...}` ≠ `WS close` | 很低 0.05 |

> `PROXY_GENERATION_DISABLED` 只是 H1 的文本证据，**不是已确认根因**。上述任何单一假设都不能单独解释“10-18 可用、10-28 不可用”的时间差；最可能是 H1（服务端通道策略变化）叠加未知的 model/app 变量。

---

## 7. Q4 重审建议（初步；新行为建议，待用户确认/重审——reviewer 不可代签）

> 本节**不是**既有 Q4 决议，也不是 reviewer 可签字事项；Q4-C′ 是**新行为建议**，需用户确认/重审。未获用户确认前**不得**写回 `06-P4-questions.md` / `06-decision-log.md`，也**不得作为 B0a 的解除条件**。

1. **建议暂停**把“native 主 + signer 首帧前无条件回退”作为实现依据。依据：本次 400 是确定性上游拒绝，不是瞬态；该请求未发生回退（§2.2）；signer 与 native 共用 `streamGenerateContent` 逻辑路径，且 signer 仍走 Ark 已记录为“生成 POST 被拒”的 `ProxyStreamedCall`（§4.2）。
2. **新行为建议（Q4-C′，尚未成立，待用户确认/重审）**：显式传输能力矩阵 + 默认 fail-fast。
   - 每种传输（native / signer）独立维护 `可用 / 已禁用 / 未验证` 三态；
   - 只有**该传输自己有成功样本**才允许作为主路径；
   - 400 `PROXY_GENERATION_DISABLED` 立即终止该传输（可标记为“端点禁用”并冷却），**不重连、不重发**；
   - 回退只在“配置显式允许 + 目标传输有成功证据”时发生；禁止把 400 当瞬态自动改道。
   - 该条不是既有 Q4，reviewer 不能代签；是否采纳由用户决定。
3. **signer 需要独立验证，不能由 native 的失败/成功推导。** 现有 signer 的“ready”只代表本地 token/自检（`build_signer.js:234-270`），不代表上游可用。若要做 signer-only，应优先评估 Ark 已记录的 `MakerSuiteService/GenerateContent`（非流式/缓冲流）是否仍可用，而不是继续假设 `ProxyStreamedCall + streamGenerateContent` 可用。
4. **保留 Q6（单 9998 + realm）与 Q7（页面并发）不变**；本次事故不构成对它们的反证。
5. §9 历史缺口只用于当时附录分析；**即使补齐，也不自动构成对 Q4-C′ 的用户确认**。

---

## 8. 历史附录：当时的测试计划（不执行，不作为当前计划）

> 以下 §8–§10 是撰写时的历史分析，不是当前 P4 门、不执行、不要求补证据/签字；当前唯一要求见 `08-10` §4。

### 8.1 历史测试项（仅供未来参考）

> 目标不是“让 Build 成功”，而是证明：**400 被准确分类、快速失败、错误透传、native/signer 互不冒充**。

### 8.1 fixture 与单元测试

| ID | 测试项 | 输入 | 预期 | 禁止项 |
|---|---|---|---|---|
| B0b-01 | native 400 分类 | 9998 事件 `{event_type:"error", message:"Proxy browser error: Google API returned error: 400 INVALID_ARGUMENT {…PROXY_GENERATION_DISABLED…}", status:400}` | 只 dispatch 1 次；**不调用** `_recover_warmup`/`retry_native`；抛 `BuildSurfaceError(code="PROXY_GENERATION_DISABLED" 或 "UPSTREAM_REJECTED")`，保留 `upstream_status=400` 与原始 message | 不得触发 9998 重连；不得重发同一 request |
| B0b-02 | 400 不透传为“断线” | 同上 + 9998 连接保持 | 日志中不出现“native 在首帧前断开，等待 9998 重连” | 不得把 400 计入 `native_retries` |
| B0b-03 | 真实断线仍可重试 | 关闭 9998 / 无 `response_headers` / 无 error status，连接关闭事件 | 允许最多 1 次有界恢复；恢复失败 → 明确 `NATIVE_WS_MISSING`/断线错误 | 不得与 400 混淆；不得无限恢复 |
| B0b-04 | 401/403/429 分类 | fixture 分别给 401/403/429 body | 分类为鉴权/权限/限流；429 不进入 Build 端点禁用；401/403 不与 400 合并 | 不得所有非 200 都映射成同一 code |
| B0b-05 | 错误透传 | 400 fixture 经 `generation_service` → `handle_attempt_exception` → HTTP | 客户端收到包含 `build`、`PROXY_GENERATION_DISABLED`、`upstream_status=400`、`app_id/model`（脱敏）的错误体；**不是** `未知严重异常` 500 | 不得伪装 200 SSE/空流；不得只返回 “server_error” |
| B0b-06 | 500 兜底不吞分类 | 任意 BuildSurfaceError | **先阻止** `upstream_errors.py:378-392` 通用 500 分支捕获，**再走** `app.py:311-334` Build 专用映射，保留 code/detail/advice；若两处都改，必须有回归断言 | 不得落入通用分支只返回 `server_error`；不得只改 `app.py` 而仍被上游通用分支先吞 |
| B0b-07 | signer 独立成功 | signer fixture：200 + 2 个 Holder 叶子 | 只走 signer；`transport=signer`；产出 body/finish/usage | 不得先发 native 再切 signer；不得因 native 400 才进入 |
| B0b-08 | signer 独立 400 | signer fixture：非 200 + `[buildSigner] upstream (HTTP 400) rejected…` | 抛 `SIGNER_ERROR`（或专门 code），**不回退 native**、不重连 9998 | 不得把 signer 失败算作账号故障并自动换号 |
| B0b-09 | 无成功基线声明 | 当前无 native/signer 成功 fixture | 测试报告必须写“**无基线**”，并新增成功 fixture 后才能宣称回归 | 不得用历史 2026-10-18 的 200 当作当前基线 |
| B0b-10 | watchdog 不因 400 触发 | 9998 保持连接 + 一次 400 请求 | watchdog 不打印“断线超过 10s”，不触发 `_recover_warmup` | 不得把应用层 400 写成通道断线 |

### 8.2 集成 / 页面级（离线，不访问 Google）

- 用本地 WS 模拟 9998 客户端，按 `event_type` 注入 `response_headers`/`chunk`/`stream_close`/`error`，验证 `_pump_native` 是否保留 `status`、是否区分 `status>=400` 与连接关闭。
- 用 `FakePage`（现有 `tests/unit/test_build_surface.py` 模式）验证 retry 次数、`_recover_warmup` 调用次数、`fallback_to_signer` 调用条件。
- 用 `handle_attempt_exception` 单测验证 BuildSurfaceError 不再落入“未知严重异常”。

### 8.3 禁止项（全局）

- 禁止用真实账号/IP/Google 做未授权探测；禁止把 400 当瞬态无限重试；禁止在 native 400 后无条件切 signer；禁止把 signer 本地自检通过当作上游可用；禁止用历史成功样本冒充当前基线。

---

## 9. 历史附录：当时识别的证据缺口（不构成当前要求）

以下是撰写时当时识别的复核缺口，**不构成当前要求、不要求补齐、不影响计划**。全部只要求字段名/占位符/布尔值，禁止真实 key/cookie/header 值。

| # | 历史缺口字段 | 需要的最小形式 | 用途 |
|---|---|---|---|
| G1 | **app_id** | Build 应用 ID（可脱敏为前缀+长度） | 区分 app 级禁用（H3） |
| G2 | **model** | 规范化后的模型名（可保留族名） | 区分模型/端点组合（H2） |
| G3 | **账号 / tier** | 账号编号 + 已知 tier（无需 cookie） | 区分账号/tier（H4） |
| G4 | **实际 URL** | host + path + query **键名**（不含 key 值） | 证明 native 真实目标与 `alt` 参数 |
| G5 | **实际出站 headers** | 仅**键名** + 是否存在（布尔） | 证明页面/宿主是否补凭据、是否发 tier 头 |
| G6 | **代理 / 出口** | 是否启用代理、出口类型/区域（无 IP） | 区分代理/出口策略（H5） |
| G7 | **完整 error details/reason** | 除 `code/message/status` 外的 `details`/`reason`/`fieldViolations` | 把 H1 从文本猜测升级为归类证据 |
| G8 | **9998 事件序列** | 是否有 `response_headers`、`error.status`、`stream_close`、WS 是否保持 | 证明 400 ≠ 断线；判断 watchdog 是否可能触发 |
| G9 | **线上 bundle 版本/时间** | Build 页面 bundle 的版本/抓取时间 | 证明错误模板来自当前投放，不是旧参考副本 |
| G10 | **请求体形状** | 字段名 + 大小（无值） | 排除请求体被拒（本报告倾向认为不是，但需证据） |
| G11 | **signer 状态快照** | `state`、`tbPresent`、`harvest.headers` **键名**、`lastError`；一次授权 signer-only 结果 | 回答 signer 是否可用、能否兜底 |
| G12 | **最小 A/B（需授权）** | 同账号换 model ×1；同 model 换 app_id ×1；换账号 ×1 | 区分 H1/H2/H3/H4；**未授权不得执行** |

**历史判据（不适用当前计划）**：G1–G9 齐全且可复核；G10–G12 若无法取得，当时应明确写“未取得”。当前不执行该判据。

> 可选项：一次脱敏出站抓包（URL/method/header 名/body 大小），用于回答“native 请求到底带没带凭据”（§3.1 的离线空白）。

---

## 10. 历史附录：当时结论（当前不适用）

**历史结论：撰写时仅完成部分离线溯源。** 当前 `08-11` 只是辅助附录，不构成阻断、签字或 Q4 要求；唯一未来要求见 `08-10` §4。

- **已完成的溯源**：调用链、错误产生层、错误包装层、重试/回退语义、native/signer 差异、400 被误判为瞬态的代码位置、Q4 必须重审的事实依据，均已有 file:line 证据；§8 的测试计划可作为后续输入。
- **仍未证明**：
  - 根因是 H1 还是 H2/H3/H4（缺 G1–G7、G12）；
  - signer 是否可用（缺 G11；其“ready”只是本地自检，且仍走旧 `ProxyStreamedCall` 通道）；
  - native 是否有当前成功基线（无成功样本；历史 2026-10-18 记录不可复跑）；
  - 线上页面实际行为（缺 G9；参考副本只是同源模板）；
  - 400 与 9998 断线/watchdog 的关系（缺 G8；本次日志未证明断开）。
- **不能做的事**：不能把 `PROXY_GENERATION_DISABLED` 写成已确认根因；不能给出任何解除结论；不能把 §7 的 Q4-C′ 当作 reviewer 已签或既有 Q4；不能在 §9 补齐前称 B0a 完成。
- **历史建议（当前不执行）**：当时建议补 §9 的 G1–G9；G10/G11 若未取得则标记；如用户授权再做 G12 最小 A/B。当前不设 B0a 状态、不要求补证据、不要求签字；P4 实现阶段按 `08-10` §4 重新探索。

---

## 附录 A：上游 vs fork（99c38ba）

| 项 | 99c38ba（upstream） | 本仓（fork） | 证据 |
|---|---|---|---|
| `build_surface.py` | **不存在** | 存在（1869 行） | `git cat-file -e 99c38ba:src/aistudio_api/infrastructure/gateway/build_surface.py` → fatal |
| `js/build_signer.js` | **不存在** | 存在（1296 行） | `git ls-tree -r 99c38ba -- src/aistudio_api/infrastructure/browser` |
| 9998 sink | 无 | `build_surface.py:157-225` | 同上 |
| `streamGenerateContent` | 仅对外路由 `routes_gemini.py:32` | Build 上游调用 + 对外路由 | `git grep` |
| `transport.py` | **360 行**（chat XHR 回放） | 311 行（chat 用，Build 不用） | `git show 99c38ba:...transport.py \| Measure-Object -Line` → 360 |
| 引入提交 | — | `1cc634e`（signer/协议）、`8dd6782`、`1cf3994`、`8e5e0cb` | `git log -- build_surface.py` / `build_signer.js` |

结论：**Build surface 是 fork 新增能力；“上游原生”只能指 chat/协议/wire 部分，不能为 Build 端点背书。**

## 附录 B：实际执行的只读命令（摘要）

```powershell
git status --short
git log --oneline -3
git branch --show-current
git show 99c38ba:src/aistudio_api/infrastructure/gateway/transport.py   # 行数对比
git cat-file -e 99c38ba:src/aistudio_api/infrastructure/gateway/build_surface.py
git ls-tree -r --name-only 99c38ba -- src/aistudio_api/infrastructure/gateway
git ls-tree -r --name-only 99c38ba -- src/aistudio_api/infrastructure/browser
git grep -n "streamGenerateContent" 99c38ba -- src
git grep -n "Proxy browser error" -- .
git grep -n "9998" -- src
git grep -n "MAX_RETRIES" -- src/aistudio_api
git log --oneline --max-count=5 -- src/aistudio_api/infrastructure/gateway/build_surface.py
git log --oneline --max-count=5 -- src/aistudio_api/infrastructure/browser/js/build_signer.js
Get-ChildItem -Recurse -File src\aistudio_api | Select-Object -ExpandProperty FullName
Select-String -Path src\aistudio_api\infrastructure\gateway\build_surface.py -Pattern '^(class |def |    async def |    def |_[A-Z_]+ = )'
Select-String -Path src\aistudio_api\infrastructure\gateway\client.py -Pattern 'build' -CaseSensitive:$false
Select-String -Path src\aistudio_api\application\generation_service.py -Pattern 'build|surface' -CaseSensitive:$false
Select-String -Path docs\ark-reference\scripts\client\build.js -Pattern 'Proxy browser error|Google API returned error|fetch\(|9998|targetDomain'
Get-ChildItem -Recurse -File docs\dev\evidence -Include *.log,*.sse,*.json,*.txt |
  Select-String -Pattern "PROXY_GENERATION_DISABLED|Proxy browser error|not supported" -Encoding UTF8
```

**实际只读校验输出（2026-10-28 复核）：**

```text
$ git status --short
?? docs/dev/rebuild/08-11-build-400-trace.md

$ git cat-file -e 99c38ba:src/aistudio_api/infrastructure/gateway/build_surface.py
fatal: path 'src/aistudio_api/infrastructure/gateway/build_surface.py' exists on disk,
but not in '99c38ba'        # 退出码 128

$ git show 99c38ba:...transport.py | Measure-Object -Line
Lines
-----
  360

$ (Get-Content src\aistudio_api\infrastructure\gateway\build_surface.py).Count
1869
$ (Get-Content src\aistudio_api\infrastructure\browser\js\build_signer.js).Count
1296

$ Select-String ...build_surface.py -Pattern 'page.goto\('
1111: await page.goto(url, timeout_s=180.0, wait_until="domcontentloaded")

$ git ls-tree -r --name-only 99c38ba -- src/aistudio_api/infrastructure/browser
# 无 build_signer.js（仅 browser_engine.py / cdp_client.py / js/{check_identity,dialog_cleanup,
#   dom_gc_cleanup,extract_email,hooked_request,install_hooks,snapshot_generate,
#   stop_generation,stream_cleanup,streaming_init}.js / scripts.py）
```

未执行：任何网络请求、真实 Build 调用、浏览器启动、账号/IP 访问、Agent Manager、子 agent、代码/测试/config/现有文档修改。
（注：本机无 `rg`，内容检索统一使用 `Select-String`/`grep` 工具。）

## 附录 C：脱敏字段清单（本报告只写字段名）

- 请求：`surface`、`app_id`、`model`、`account_id`（编号）、`request_id`、`request_attempt_id`、`streaming_mode`、`is_generative`、`body`（字段名：`contents`/`systemInstruction`/`tools`/`toolConfig`/`generationConfig`/`safetySettings`）。
- 响应：`status`、`event_type`（`response_headers`/`chunk`/`stream_close`/`error`）、`message`、`code`、`statusText`、`details`（若有）。
- 头字段名（仅名称）：`authorization`、`x-goog-api-key`、`x-goog-authuser`、`x-user-agent`、`content-type`。
- 未写入任何真实 key/cookie/header 值；`build_signer.js` 内的默认 key 值也未复制。

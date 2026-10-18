# MakerSuite GenerateContent 桥接修复实施记录（2026-10-16/17）

分支：`fix/makersuite-generatecontent-bridge`。状态：代码与离线测试已完成；真机验收与结果回填见 §6.3。

## 1. 背景与根因

- 2026-10-16 起，全部模型统一返回 `400 INVALID_ARGUMENT`（裸错误、无 `reason`、约 0.6s），旧协议链路全面失效。
- 根因（来自参考实现与本次调研）：AI Studio 页面旧通用代理 RPC `MakerSuiteService/ProxyUnaryCall` / `ProxyStreamedCall` 转发的生成 POST 被 Google 拒绝并返回 400。Build App 侧 `scripts/client/build.js` 的 fetch 经页面宿主机制落到该通道，App 侧无法自行修复。
- 佐证（本地诊断报告 `.local_logs/400-INVALID_ARGUMENT-诊断报告.md`）：不走该通道的直连请求会得到 Google 原始错误（含 `reason`），五种凭证形式全部被拒——"寻找新占位符/新注入点"为死路；官方文档所述"占位符替换"机制即寄生在旧代理 RPC 通道内，已被整体关闭。
- 结论：修复必须绕过旧 RPC，改调专用 `MakerSuiteService/GenerateContent`（使用页面自身会话凭证），即本桥接方案。旧 App 信封保持不变，已部署的 Build App 无需任何改动。

## 2. 出处与许可

- 原始实现：`yunx738/ais2api` 提交 `79807728179482509cf1f8a4f5e3e175d9dd6702`（提交说明：Google 已拒绝经 ProxyUnaryCall/ProxyStreamedCall 的生成 POST；改为直调 MakerSuite GenerateContent；real/fake 流式与反截断已验证）。许可 CC BY-NC 4.0，已获原作者授权移植。
- 移植参照：`Naoya666/AIStudioToAPI` 提交 `8a137ca`（同源代码基座，含配套离线测试）。
- 本仓库 LICENSE 为 CC BY-NC 4.0，兼容；两个新文件头部保留署名与提交号。

## 3. 机制

1. **拦截**：`context.route(/MakerSuiteService\/Proxy(Unary|Streamed)Call(?:\?|$)/)` 截住旧 RPC；仅当报文 `[0]` 为 `models/*:(stream)generateContent` 时桥接，其余（模型列表/文件等）原样放行，并剥除本地关联标记。
2. **编码**：把 Gemini JSON 编码为 MakerSuite 位置数组（`MakerSuiteCodec.buildBody`），含 contents / safety / generationConfig / tools / systemInstruction 的已核实位置。
3. **直调**：`page.addInitScript` 预注入页面钩子，包装 `window.default_MakerSuite.Cn` 捕获会话服务对象 `tb`；请求时经 `await M.Cn(tb, await M.GB(ht))` 生成令牌，以页面凭证（`authorization` / `x-goog-api-key: mb.jK` / `x-goog-authuser` / `x-user-agent`）在页面内直调 `GenerateContent`。
4. **回填**：响应解码为标准 Gemini JSON，另按旧信封（unary `[json]`、streamed `[[chunk...], tail]`）`route.fulfill` 交还页面宿主机制——App 侧无感知。
5. **取消与清理**：fetch/XHR 仅给被拦截 URL 附加关联 query（不新增请求头，避免改变 CORS 预检）；AbortController 贯通；导航 epoch 失效丢弃迟到结果；实例绑定具体 context/page，随 context 关闭/初始化失败自动与显式 dispose。

## 4. 变更清单

- 新增 `src/core/MakerSuiteCodec.js`：请求编码、响应解码/合并、旧信封包装、错误规范化（纯函数）。
- 新增 `src/core/MakerSuiteBridge.js`：每账号桥实例（拦截、页面钩子、执行、取消、资源清理）。
- `src/core/BrowserManager.js`：
  - require 桥模块；
  - `_initializeContext` 中 `context.newPage()` 后、首次导航前创建并 `install()`；
  - `contexts.set` 条目挂 `makerSuiteBridge` 引用；
  - 初始化失败 catch 与 `closeContext` 中 `dispose()`；
  - 超时：`streamTimeoutMs = min(config.streamTimeoutMs, 300s) - 5s`；`timeoutMs = min(config.fakeStreamTimeoutMs, 300s) - 5s`。
- 新增测试：`test/makersuite/codec.test.js`、`test/makersuite/bridge.test.js`、`test/makersuite/browser.e2e.js`（可选真机 Firefox fixture，默认套件不包含）。
- `package.json`：新增 `test`（ATG + MakerSuite 全量）、`test:protocol:browser` 脚本。

## 5. 能力边界（不支持项显式 400，不静默）

- 已支持（离线样本验证）：文本、systemInstruction、inlineData、函数声明（嵌套参数/返回）、thought / thoughtSignature、thinkingBudget、LOW/HIGH thinkingLevel、safetySettings、AUTO toolConfig；`candidateCount=1`（视为默认丢弃；>1 显式 400）。
- 结构化输出（`generationConfig.responseFormat`，即 OpenAI/Claude/Responses 的 json_schema / json_object 模式）：位置未映射，显式 400，待探针补位。
- 明确拒绝（400）：非 AUTO toolConfig（含 `allowedFunctionNames`）；googleSearch / urlContext / codeExecution；fileData；responseSchema / responseJsonSchema；responseModalities（TTS/图片输出）；thinkingLevel MEDIUM / MINIMAL；未知枚举不猜测。
- 本仓库特有受影响点：OpenAI/Claude `tool_choice` 的 none/required/function 映射、`allowed_tools` 扩展、Claude web_search、模型后缀 `-search`/`-code`、`FORCE_WEB_SEARCH`/`FORCE_CODE_EXECUTION`、思考级别 MEDIUM/MINIMAL、TTS/图片模型。
- 流式：streamed 响应当前为缓冲返回（`real` 模式退化为缓冲；`fake` 模式本身即缓冲，不受影响）。
- 非生成入口（models 列表 / embedding / countTokens 等）保持旧路径。

## 6. 验证记录

### 6.1 离线（2026-10-17 +08，已完成）

- `npm test`（`test/atg` + `test/makersuite`）：107 项通过，0 失败；其中桥接 21 项（含"ATG 合成声明 → 真转换器 → 桥接编解码"链路）。
- ESLint、Prettier 检查全部通过。

### 6.2 可选真机浏览器 fixture

- `npm run test:protocol:browser`（需 `npx playwright install firefox`；全部为本地回环流量，不接触 Google，不耗额度）。

### 6.3 真机验收（2026-10-17 +08，执行中·结果回填）

| 场景                             | 结果                                                                   | 时间/备注   |
| -------------------------------- | ---------------------------------------------------------------------- | ----------- |
| 服务启动、账号初始化、桥安装日志 | ✅ 账号 #7/#8/#11 桥安装成功                                           | 16:48-17:04 |
| 非流式对话（OpenAI 协议）        | ✅ 连续 5 次 HTTP 200；内容与提示一致；单次 2.7-5s；usage 含 reasoning | 16:50-16:54 |
| 流式（real 缓冲）                | ✅ SSE 正常：单块 delta + `finish_reason:stop` + usage + `[DONE]`      | 16:57       |
| ATG（AUTO 工具）回环             | ✅ 非流式 unwrap `syntheticHit:true`，thoughtSignature 透传            | 16:50       |
| 页面重载存活（轻量 reconnect）   | ⏳ 离线用例覆盖；实机待自然 reconnect/切号场景                         |             |
| 双账号隔离                       | ⏳ 待定时切号后复测                                                    |             |

**关键取证（修复依据，旧 RPC 请求头画像）**：页面自构造的旧 RPC 为真实网络请求（非宿主本地合成）：

    host           = alkalimakersuite-pa.clients6.google.com
    x-goog-api-key = masked:39:AIza   （39 字符、AIza 前缀 = 合法 key）
    authorization  = masked:195:SAPI  （SAPISIDHASH 体系）
    x-goog-authuser= 0 ；cookie 在列；content-type=application/json+protobuf

⇒ 页面服务对象仍持有有效 key，字段名由参考版的 `jK` 变为 **`nK`**（`mb.jK` 已不存在；直发 `undefined` 会被上游以 `API_KEY_INVALID` 拒单）。

**修复提交**：`2801051`（凭证适配 `jK||nK` + 上游错误透传 `[,[3,…]]` + 凭证/头画像诊断）。

## 7. 风险与红线

- 页面内部名（`Cn` / `GB` / `mb.F` / `mb.jK` / `mb.Ml`）为混淆实现细节，页面改版可能失效；失效特征为 503（token 未就绪）或 502（传输失败），需日志观测。
- 令牌捕获竞态：页面未自然调用 `Cn` 时，桥以有界等待（默认 10s）返回 503。
- **错误分类红线**：桥接协议错误（503 / 504 / 502 及编码 400）不得按账号故障结算。本仓库 `IMMEDIATE_SWITCH_STATUS_CODES=429,503,403`、`FAILURE_THRESHOLD=3`——真机阶段须核对桥接错误是否触发无效切换，必要时加护栏（待办）。

## 8. 回退

- 回退方式：revert 本分支提交即可；不影响 Build App 侧（无 App 改动）。桥接安装失败时会走初始化失败路径（桥 dispose 后按原有逻辑处理）。
- 备注：封档文 `docs/zh/build-channel/archive-2026-10-16-build-channel-closure.md` 中关于 400"宿主合成"的表述，已于 2026-10-17 在其 §八（更正与状态变更）中更正。

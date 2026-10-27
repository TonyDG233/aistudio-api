# PG 渠道工具调用链路调查（只读）

- 分支/基线：`rebuild` @ `be6eda6`（工作区另有未跟踪的 `08-execution-readiness-audit.md`，本文件不改动它）
- 调查方式：只读源码 + 既有单测/fixture/证据文档 + 离线构造 wire 数据；未访问真实账号、未走网络、未重跑真实请求、未提交
- 目标：判断“pg 渠道 + 酒馆预设（把发送文本塞进工具调用输出框）”从请求到返回的哪里断了，并列出可验证的根因假设
- 术语：本文的“预设工具”指酒馆 anti-truncation 扩展声明的 `emit_complete_response_<nonce>`（见 `tests/unit/test_gemini_request_normalization.py:448-502`），不是 ATG 的 `atg_emit_*`

---

## 0. 结论（按责任面与置信度）

| 责任面 | 置信度 | 结论 |
|---|---|---|
| **反代侧（主因）** | **0.65** | 工具调用的**请求归一化**与**流终止/错误暴露**存在多处不对称：Gemini 原生面缺 thoughtSignature 兜底、`functionResponse` 未强制 Struct、上游 4xx 被包成 HTTP 200 SSE、tool-call 流的终止只看 finishReason。模型本身没有“输出错”的必要条件；即使模型正确产出 functionCall，也可能在请求阶段被 400 拒绝或在流阶段不终结。 |
| 上游侧（次因/放大器） | 0.20 | 上游会强制校验 functionCall 历史的 thought signature，并可能在“工具调用后等待后续”时保持连接，触发 120s 空闲超时；这不是“模型输出错”，而是协议约束。 |
| 格式/preset 侧（触发条件） | 0.15 | 预设把用户文本放进 `functionResponse`、把模型回答放进 `functionCall.args`，并可能每轮重建工具环；这恰好绕开了反代目前只在文本 part 上做的处理（签名、hash、严格校验），所以看起来像“只有这个预设坏”。 |

一句话判定：**最可能不是模型输出格式错，而是“预设把内容搬进工具通道后，反代仍按文本聊天的方式处理”**。最先要排除的是：历史 `functionCall` 缺 `thoughtSignature`（400）→ Gemini SSE 把它伪装成 200 的 error 事件 → 酒馆既不显示错误也不显示正文；以及 tool-call 流没有 finishReason/不关闭导致的 120s 超时。

---

## 1. pg 在代码中的定义（不是“另一个模型格式”）

- `pg` 是 **chat 渠道的别名**，不是独立协议：`src/aistudio_api/application/channel_registry.py:20-27`（`"pg": "chat"`），展示前缀也是 `pg`（`:31-46`）。
- 显式前缀优先且被钉死：`select_channel()` 在 `channel_registry.py:344-368` 解析 `pg/<model>` → `channel=chat`、`surface=chat`、`clean_model=<model>`；不会因为 build 可用而改道。
- 路由资格：`application/routing_eligibility.py:253-312` 先用 `select_channel` 钉住渠道，再用 `eligible_account()`（`:164-207`）检查 tier/组/模型门槛。
- 模型名归一：`application/chat_service.py:545-560` 把 `requested_model` 统一成 `models/<clean_model>`；正常路径 `effective_model_path` 来自 `ctx.clean_model`（`generation_service.py:264`、`:519`）。
- 只有**回退旧路径**时才会保留 `pg/` 前缀并可能误路由/误写模型：见 §3 的 F-09。
- `model_defaults` 不按 `pg` 分支；它只提供模型级 generation defaults / `default_tools`，与本问题无直接关系（`infrastructure/gateway/model_defaults.py:571` 只是重复别名映射）。

结论：正常路径下 `pg` 不会被 build/chat 误路由；问题发生在 chat 面内部的工具请求/流处理，而不是“pg 被当成 build”。

---

## 2. pg 工具调用完整链路（file:line）

### 2.1 入站与选路

1. Gemini 入口：`api/routes_gemini.py:34-41` → `application/api_service_gemini.py:186-196`；流式走 `_build_gemini_streaming_response()`（`:228-259`）。
2. OpenAI/Claude 入口（若酒馆用自定义 OpenAI/Claude 面）：`api/routes_openai.py` / `api/routes_claude.py`，先做格式转换，再进同一个 `generation_service`。
3. 选路：`application/generation_service.py:160-177`（`_begin_route`）→ `application/routing_pipeline.py` 的 `begin/acquire`；`ctx.clean_model`/`ctx.surface` 由 `channel_registry` 决定（见 §1）。

### 2.2 请求归一化（工具部分）

4. `application/chat_service.py:545-560`：模型名 → `models/<clean_model>`。
5. `chat_service.py:570-587`：`text` / `inlineData` 进入 `AistudioPart`。
6. `chat_service.py:588-599`：`functionCall` → `AistudioPart(function_call=(name,args,id))`；**只透传** `part.thoughtSignature`，缺失时没有兜底。
7. `chat_service.py:600-609`：`functionResponse` → `AistudioPart(function_response=(name,response,id))`；`response` 原样传入，**不做 Struct 强制**；`fr.parts` 的文本/图片被拆成同级的普通 part（`:610-625`）。
8. wire 编码：`infrastructure/gateway/wire_types.py:259-327`：
   - `functionCall` 写 `PartIndex.FUNCTION_CALL_ALT`（下标 10），`:270-278`；
   - `functionResponse` 写 `PartIndex.FUNCTION_RESPONSE_ALT`（下标 11），`:280-290`；
   - `thought_signature` 写下标 14，`:277-278`/`:289-290`；
   - `_encode_wire_args()` 对 `dict` 递归编码为 Struct，**对字符串/其他类型原样返回**（`:330-333`）。
9. 工具声明编码：`application/chat_service.py` 中 `encode_function_declaration_to_wire`（被 `normalize_gemini_request` 调用；测试见 `tests/unit/test_gemini_request_normalization.py:448-502`）。
10. 模板重写：`infrastructure/gateway/wire_codec.py:244-365`：
    - `request.tools` 覆盖（`:319`）；
    - `tool_config` 只用于 **NONE→清空 tools** 和 **allowedFunctionNames→过滤声明**（`:320-364`），**没有任何顶层 toolConfig 字段被写入 wire**（`encode()` 见 `:197-242`；WIRE_SPEC `docs/WIRE_SPECIFICATION.md:214-223` 明确 MakerSuite 无独立顶层字段）。
11. 捕获与签名：`infrastructure/gateway/capture.py:40-110` → `session.py:352-383`（模板）、`:385-419`（snapshot）：
    - `hash_parts` 只收集 `text`/`inline_data`/`file_id`/`executable_code`/`code_execution_result`；
    - `functionCall`/`functionResponse` 一律 append `""`（`:388-403`）。

### 2.3 上游流

12. `infrastructure/gateway/client.py:557-628`：chat 面 → `capture_request()` → `stream_gateway.stream_chat()`；build 面走 `build_surface.stream_generate()`。
13. `infrastructure/gateway/transport.py:69-116`：页内 fetch 流、SSE/JSPB 帧、`timeout_stream`（默认 120s，`config.py:301-302`）。
14. `infrastructure/gateway/streaming.py:69-155`：
    - `parser.feed()` 解析 chunk（`:105`）；
    - 逐 chunk 产出 `thinking/function_calls/images/body/thought_signature`（`:120-131`）；
    - **只有 `candidate.finish_reason is not None` 才 break**（`:132-135`），末尾补 `finish_reason`/`usage`/`done`（`:152-155`）；
    - 无 `finish_reason` 时完全依赖上游关闭连接或 120s 超时。
15. `infrastructure/gateway/wire_parser.py:406-509`：
    - `candidates_container` 为空时 **在读取 finish_reason 之前 return**（`:416-417`）；finish_reason 的读取在 `:491-496`；
    - part 解析在 `:128-224`，函数调用支持下标 3 与 10（`:164-173`），签名读下标 14（`:174-179`）；
    - 函数调用参数解码在 `:227-260`（`args`/`arguments` 两种落点）。

### 2.4 事件 → 输出

16. `application/generation_service.py:540-560`：ATG 注入（可选）→ `effective_req`；`tool_name` 只有本地 ATG 包装时才非空。
17. `generation_service.py:602-644`：每次 attempt 重新 `normalize_gemini_request`，传给 `client.stream_generate_content()`。
18. `generation_service.py:645-693`：事件循环：
    - `usage`/`finish_reason`/`response_id`/`thought_signature` 专用分支（`:646-663`）；
    - ATG unwrapper 分支（`:664-682`）；
    - 普通事件直通（`:683-693`）；
    - **tool_calls 不做跨 chunk 聚合**。
19. `generation_service.py:730-770`：ATG 空响应重发、`finalize()` flush；`:801-807` 发终块（finish/usage）。
20. Gemini SSE：`application/api_service_gemini.py:54-111`（`_render_stream_event`）、`:114-144`（终块）、`:166-183`（`format_sse_event(tool_calls)`）。
21. 非流式：`api_service_gemini.py:198-225`（`to_gemini_parts` + `finishReason`），`api/responses.py:92-109`。
22. OpenAI：`format_converters/openai_response.py:104-220`（`tool_calls` 增量形状、finish_reason）。
23. Claude：`format_converters/claude_response.py:138-352`（`tool_use` 块、`input_json_delta`、`stop_reason`）。
24. 错误：`application/upstream_errors.py:42-78`（finish 映射）、`:177-343`（分类/切换）；Gemini SSE 的 error 只在 body 里（`api_service_gemini.py:245-257`）。

### 2.5 ATG（若开启）

25. wrap：`application/atg/injector.py:104-217`（注入 `atg_emit_*` + 控制 user turn + 改写 `toolConfig`）。
26. guard：`application/atg/client_patterns.py:72-190`（5 个信号；只读 `parameters`，见 F-07）。
27. 流解包：`application/atg/stream_extractor.py:70-115`（只对自己的合成名/无名续块累积；真实调用透传）。
28. 非流解包：`application/atg/extractor.py:141-200`（合成调用→正文、finish 重写）。
29. 集成点：`generation_service.py:531-551`、`:605-606`、`:664-682`、`:755-770`。

---

## 3. 失败点排序（症状 / 证据 / 触发 / 如何离线验证）

> 标注约定：`[代码]` = 只读代码即可确认；`[离线]` = 用本机离线构造的数据可复现；`[未验证]` = 需要真实请求/日志/页面，不能当已证实。

### F-01　Gemini 原生面历史 `functionCall` 缺 thoughtSignature 兜底（最可能）

- **症状**：pg + 预设第一/第二轮都可能在生成前失败；酒馆计时器启动后无正文，最终超时或无输出。上游错误通常是 `Function call is missing a thought signature`。
- **证据**：
  - `chat_service.py:588-599` 只把客户端的 `part.thoughtSignature` 透传；缺失时不注入占位签名。
  - 占位签名常量只被 OpenAI/Claude 转换器使用：`format_converters/common.py:27`，使用点 `openai_request.py:201`、`claude_request.py:203`；Gemini 原生路径没有引用。
  - `docs/dev/evidence/wp8-20261019/README.md` 的 “thoughtSignature 修复” 记录了真实上游 400 与修复方案，但验收只覆盖 OpenAI/Claude；Gemini 原生面只做了文本回归。
  - WP8 的 OpenAI 多轮工具请求（`docs/dev/evidence/wp8-20261019/openai-tool-multiturn-request.json`）同样经过 `normalize_gemini_request` → wire 下标 10，并靠 dummy 签名成功；这说明 **wire 下标 10 本身可用，缺的只是签名兜底**，进一步支持 F-01 是本路径的高概率缺口。
  - `tests/unit/test_gemini_request_normalization.py:448-502` 只验证“声明”编码，未覆盖 `functionCall/functionResponse` 历史与签名。
- **触发**：预设重建上一轮模型 `functionCall` 时没有回传 `thoughtSignature`（常见：扩展只保存 `args.content` 的可见文本、或上游本轮没有把签名附在 functionCall part 上）。`pg` chat 面使用 Gemini 3.x 时，上游对历史 functionCall 强制要求签名。
- **离线验证**：
  - 用 `normalize_gemini_request` 构造 `role=model` 的 `functionCall`，无 `thoughtSignature`；检查 `part.to_wire()[10]` 有值而 `[14]` 为空（本次 probe 已复现：`[null,...,["emit_complete_response_x", [[["content", ...]]]]]`，无下标 14）。
  - 再跑 OpenAI 转换器的同一历史，检查其 wire 下标 14 = `context_engineering_is_the_way_to_go`，形成对照。
  - 追加单测：`test_normalize_gemini_request_injects_dummy_signature_for_historical_function_call`。
- **置信度**：中高（代码缺口确定；“该预设一定丢签名”需一次脱敏请求样本确认）。

### F-02　上游 4xx 被包成 HTTP 200 + SSE error，客户端“看不到错误也等不到正文”

- **症状**：计时器有反应（HTTP 头/`: resolving-instance` 注释已到），但既没有正文，也没有正常的 4xx；最终客户端自己超时。
- **证据**：
  - Gemini 流式入口直接返回 `StreamingResponse`，**不预读首事件**（`routes_gemini.py:34-41`、`api_service_gemini.py:228-259`）；异常在生成器里被 `except Exception` 捕获，转成 `data: {"error": ...}`（`:256-257`）。
  - 对比：OpenAI/Claude/Responses 都预读首事件以保留真实 HTTP 状态（`routes_openai.py:102-122`、`routes_claude.py:121-144`、`routes_responses.py:138-152`；台账 `docs/dev/rebuild/06-feature-ledger.md:299`）。
  - 架构审计已列此风险：`docs/dev/rebuild/02-architecture-audit.md:34`（“SDK 看到 200，错误码在 data 里”）；台账 `06-feature-ledger.md:297` 标 `partial`。
  - **离线复现**：用 TestClient 让 `stream_generation` 抛 `RequestError(400, ...)`，实际响应为 `HTTP 200`、`content-type: text/event-stream`、body 仅 `data: {"error": {"code": 400, ...}}`（本次 probe 已复现）。
- **触发**：F-01/F-04/F-08/F-12 任一在**首个事件之前**失败；或首事件之后上游 4xx。首事件之前失败本可返回 4xx，现在是 200。
- **离线验证**：对 `handle_gemini_generate_content(stream=True)` 注入会抛 400 的 fake stream，断言 status=200 且 body 只有 error；对比 OpenAI 路由的同类测试（`test_format_openai.py:701-726` 期望 500 JSON）。
- **置信度**：高（代码/离线已证实）；“酒馆是否忽略 200 SSE error 后一直等”需客户端侧确认，但这是最符合“计时器有反应→最终超时”的放大器。

### F-03　流终止只认 finishReason；工具调用流可能不终结/丢 finish → 120s 超时

- **症状**：计时器跑满约 120s（或酒馆自己的超时），没有任何内容；代理侧可能只记录超时。
- **证据**：
  - `streaming.py:118-135` 只有 `candidate.finish_reason is not None` 才 break；否则一直等上游关闭；空闲计时在 `transport.py`/`streaming_init.js`，默认 `timeout_stream=120`（`config.py:301-302`）。
  - `wire_parser.py:416-417`：`candidates_container` 为空就提前 return；`finish_reason` 在 `:491-496` 才读。**离线复现**：`parse_response_chunk([[], None, [5,3,8], ...])` 的 `finish_reason` 为 `None`（即使这种 chunk 携带 finish 逻辑）。
  - 历史修复 `c1d8f28` 的提交说明明确提到“lingering upstream connections”；该修复只加了“看到 finish_reason 就终止”，没有为“工具调用已完整但没有 finishReason”的情况兜底。
  - 台账承认“chat 流式网关聚合”无单测（`06-feature-ledger.md:714`）、“消息超时无 E2E”（`:589`）。
- **触发**：上游对 functionCall 流不发送 `candidate.finishReason`，或先投递 functionCall 后保持连接等待后续（AI Studio 的工具循环语义）；或 finish 落在空 candidates 容器里。
- **离线验证**：
  - 构造 `1)` finish-only chunk（空 candidates）→ 断言 finish 丢失；
  - `2)` fake session 先给一个完整 functionCall chunk，然后 sleep 超过 `timeout_stream` → 断言代理直到超时才产出错误；
  - `3)` fake session 给 functionCall 后直接 `done`（无 finish）→ 当前依赖默认 `STOP`，能结束；与 (2) 对比可确定“是否上游不关连接”。
- **置信度**：中（终止逻辑代码确定；“上游确实不关/不发 finish”需实机/抓包）。

### F-04　工具载荷未强制 Struct：`functionResponse.response`/`functionCall.args` 是字符串时直接写 wire

- **症状**：预设把“发送文本”放进工具输出；若该字段是裸字符串，上游 400（invalid Struct）或忽略该 tool output；所有轮次都可能无回复。
- **证据**：
  - `chat_service.py:600-609` 原样传 `fr.response`；`wire_types.py:280-288` 调用 `_encode_wire_args()`，而 `_encode_wire_args()` 对 `str` **原样返回**（`:330-333`）。
  - **离线复现**：`functionResponse.response="user text"` → wire 下标 11 = `["emit_complete_response_x", "user text"]`；不是 `[[["content", [null,null,"user text"]]]]`。同请求改成 dict 才是合法 Struct。
  - 对照：OpenAI 转换器明确把非 JSON 文本包成 `{"result": <text>}`（`openai_request.py:25-64`，测试 `test_format_openai.py:153-157`）；Claude 转换器同样包 `{"result": ...}`（`claude_request.py:218-225`）。Gemini 原生面**没有**这层。
  - WIRE_SPEC 明确 functionCall 参数必须是 `google.protobuf.Struct`（`docs/WIRE_SPECIFICATION.md:266-278`）。
- **触发**：酒馆的 `functionResponse.response` 是字符串（或 `functionCall.args` 是裸字符串/数组以外的标量）。预设把整段文本塞进“工具调用输出框”时，这种序列化很常见。另一种同类触发：**没有前置 functionCall 的孤立 functionResponse**（客户端只保留“输出框”历史），Gemini 原生面同样不校验/不降级，可能被上游 400。
- **离线验证**：构造 string 与 dict 两种 response，检查 wire 下标 11；再 mock 上游校验（或看上游 400 日志）确认拒绝。再构造一个只有 functionResponse 的 contents，检查 normalize 后的 wire 仍把它原样放在 user 轮。
- **置信度**：中高（wire 形状确定；预设实际发送类型需一次脱敏请求样本）。

### F-05　流式 tool_calls 不做跨 chunk 聚合；无名续块被当作独立调用

- **症状**：客户端收到多个部分 tool_call（有的 name 为空/`unknown`、args 只含半截），酒馆执行失败或等待完整 JSON，最终无输出。
- **证据**：
  - `streaming.py:124-125` 对每个 chunk 的 `candidate.function_calls` 直接 `yield ("tool_calls", ...)`；`generation_service.py:683-693` 原样透传；没有任何按 `call_id` 合并的状态。
  - **离线复现**：两个 chunk 都带 `call_id=call_1`、args 分别为 `"hel"`/`"lo"` → 事件序列产生两个 `tool_calls`；第二个 name 为空。ATG 的 `stream_extractor.py:101-108` 只有在 `synthetic_hit` 时才把无名续块并入合成调用，否则仍作为 `remaining` 透传（`:108`）。
  - 渲染后果：Gemini `to_gemini_parts` 把空 name 渲染成 `"unknown"`（`api/responses.py:92-104`）；OpenAI 的 `_tool_call` 在**无 call_id** 时分配新 id，并且每个事件都会拿到新的 stream index（`openai_response.py:38-64`）；Claude 的 `_tool_call_id` 同理（`claude_response.py:44-76`）。也就是说：有 call_id 的续块会以“同 id 的第二个调用/新 index”出现，无 call_id 的续块会以“新 id + 空 name”出现，两种都不是合法的增量语义。
  - 证据矛盾：Ark 文档说 Build 通道 functionCall 参数**整块到达**（`docs/ark-reference/ARK-TECHNICAL-REFERENCE.md:215-216`），WP8 也说“工具参数为上游完整 JSON，不做伪分片”；但 `stream_extractor.py:101-108` 的“无名续块”分支说明代码作者预期存在增量形态，且酒馆侧样本有未决矛盾（`direct-signer-pipeline-20261017.md:61`）。
- **触发**：上游把同一个 functionCall 分成“name/首段 + 无名 args 续块”，或多次发送同名/同 id 的部分调用。大参数（预设的整段正文）更容易触发。
- **离线验证**：已有 probe 的事件序列；补 `test_streaming_merges_partial_tool_calls`，输入两个 chunk，断言只产生一个带完整 name/args 的调用。
- **置信度**：中（聚合缺口确定；上游是否分片在 chat/该预设上未证实）。

### F-06　`thought=True` 下标 10 被误判成 functionCall（潜在“幽灵工具调用”）

- **症状**：每个 thinking chunk 都额外产出一个 name 为空、args 为 `True`/`{}` 的 functionCall；客户端可能把它当工具调用执行/报错，或把预设的正文覆盖。
- **证据**：
  - `wire_parser.py:140-153` 把下标 10 的 bool 识别为 thought；但同函数 `:170-173` 在下标 3 为空时无条件 `_coerce_wire_payload(raw_part[10], "functionCall")`。`_coerce_wire_payload` 对 bool/int 返回 `{"type":"functionCall","raw": value}`（`:236-260`）。
  - **离线复现**：`parse_response_part([None,"thinking text",...,True])` 返回 `thought=True` **且** `function_call={'type':'functionCall','raw':True}`；`parse_response_chunk` 把它放进 `candidate.function_calls`。
  - 现有单测 `test_wire_parser.py:81-100` 只断言 text/thought，**没有断言 function_calls 为空**，所以回归不会红。
  - 真实 fixture 的 thinking 用下标 12（`tests/fixtures/test_output.json`、`test_image_output.json`），所以当前样本不触发；但 parser 明确声称支持下标 10，属于潜在版本漂移风险。
- **触发**：上游/模型版本改用下标 10 表示 thought，或某个 thinking part 同时带 index 10 bool。
- **离线验证**：上面的 probe；补 `assert not parsed.function_calls`。
- **置信度**：代码确定、当前模型触发未证实（低-中）；但一旦触发，表现就是“模型在思考，客户端却收到工具调用”。

### F-07　ATG 客户端合成守卫漏看 `parametersJsonSchema`；required 模式下会在出数据后硬失败

- **症状**：ATG 开着时出现“双通道叠加”或 500/无终块：模型被 ATG 控制消息引到 `atg_emit_*`（正文被转成纯文本），或只调用了预设工具但 ATG 要求合成调用。
- **证据**：
  - `client_patterns.py:91-111` 只读 `declaration.get("parameters")`；不读 `parametersJsonSchema`/`parameters_json_schema`/`input_schema`。
  - **离线复现**：同一 `emit_complete_response_*` 声明，`parameters` → `hit=True`；改放 `parametersJsonSchema` → `hit=False, reason='declared-tools-present'`。
  - OpenAI/Claude 转换器产出的是 `parametersJsonSchema`（`openai_request.py` 的 `_function_declaration`），所以 **OpenAI 面的 ATG guard 会漏判**；Gemini 面的 `parameters` 能命中（这一点与测试 `test_gemini_request_normalization.py:448-502` 一致）。
  - ATG `required` 在缺合成调用时显式失败（`atg/extractor.py:188-200`；`stream_extractor` 的 `finalize`），而 `generation_service.py:755-770` 的 finalize 发生在已 yield 数据之后；客户端可能只看到半截 + 错误。
- **触发**：`features.atg.mode` 非 off + 预设走 OpenAI 面（或客户端自身 schema 用 `parametersJsonSchema`）+ 模型选择预设工具而非 ATG 工具。
- **离线验证**：probe 2；再用 `atg.wrap()` 喂 OpenAI 转换后的 body，断言 `client_synthetic` 是否命中；模拟 `required` 且只给真实工具调用，断言 finalize 抛错。
- **置信度**：代码/离线确定；是否命中取决于用户 ATG 配置（默认 `off`，`defaults/config.yaml:170`）。

### F-08　OpenAI/Claude 面的工具历史校验过严（预设形态直接 400）

- **症状**：请求在转换阶段失败，没有进入上游；酒馆可能显示错误或被当作空回复。
- **证据**：
  - OpenAI：`openai_request.py:218-223` 要求 `tool_call_id` 必须匹配请求内更早的 assistant tool_call；`:184-187` 要求 `arguments` 是合法 JSON 对象（`common.py:145-168`）。
  - Claude：`claude_request.py:212-217` 要求 `tool_result.tool_use_id` 必须匹配更早的 `tool_use`。
  - **离线复现**：
    - `{"role":"tool","tool_call_id":"call_fake","content":"user text"}` → `ConversionError: tool_call_id 'call_fake' does not match any prior assistant tool call`
    - assistant `arguments` 含未转义换行 → `ConversionError: tool call arguments are not valid JSON: Invalid control character`
    - Claude 孤立 `tool_result` → `ConversionError: tool_result references unknown tool_use_id`
- **触发**：预设只保存“工具输出”而丢掉 assistant tool_call；或把整段 RP 文本直接塞进 `arguments`（未 JSON 转义）。
- **离线验证**：probe 5；补兼容性用例。
- **置信度**：代码/离线确定；是否为该酒馆配置的实际形状需请求样本。

### F-09　路由管线回退时，`pg/` 前缀会泄漏上游或把请求误送到 build

- **症状**：偶发“模型不存在”的 404，或 pg 请求被 build 面执行（模型/能力不匹配），在工具请求上表现为空响应。
- **证据**：
  - 非流式回退：`generation_service.py:263-265` 在 `ctx is None` 时 `effective_model_path=model_path`（保留 `pg/`）、surface 硬编码 `chat`；随后 `normalize_gemini_request` 得到 `models/pg/<model>`，上游 404。
  - 流式回退：`generation_service.py:519-520` 在 `ctx is None` 时对**带前缀的** `effective_model_path` 调 `_resolve_surface`；paid 账号 + `build_surface=true` 会返回 `build`（`surface_orchestrator.py:437+`；`tests/unit/test_surface_policy_routing.py:90-92`），于是 pg 请求被 build 面执行。
  - 触发条件：`get_routing_pipeline()` 抛错/返回 None，或 `pipe.begin()` 非策略异常（`generation_service.py:164-177` 静默回退）。
- **离线验证**：monkeypatch `get_routing_pipeline` 为抛错/None，调用 `generate`/`stream_generation` 的路径，检查 `normalized.model` 与 `surface`。
- **置信度**：代码确定；触发频率低（正常路由管线可用时不发生）。

### F-10　Free 账号 + pg/chat 被默认组策略直接拒绝（所有工具问题之前的 403）

- **症状**：pg 请求很快被拒（或无正文），与工具无关。
- **证据**：`surface_orchestrator.py:35-43`（free → `["build"]`）、`:301-346`（默认组按 tier）、`routing_eligibility.py:160-191`（`channel not in group.allowed_surfaces` 即不合格）；`docs/dev/rebuild/02-architecture-audit.md:30`、`03-context-and-summary.md:67` 已记录“Free 被硬编码为 build-only；build 关闭时全部 403”。
- **触发**：账号 tier=free，`features.groups=[]`，`build_surface=false`（默认）。
- **离线验证**：`eligible_account(free, "chat", ...)` 返回 None；`_resolve_surface` 对 free 抛 `surface_group_disabled`（`test_surface_policy_routing.py:166-183`）。
- **置信度**：代码确定；是否适用于该用户取决于账号 tier（先查 `[surface]`/账号 tier 日志）。

### F-11　`toolConfig` 不写入上游：ANY/required 被静默降级为 AUTO

- **症状**：预设若依赖强制工具调用（`tool_choice: required`/Gemini `mode: ANY`），模型可以自由输出纯文本；只消费工具输出的预设于是“没有回复”。
- **证据**：
  - `wire_codec.py:319-364` 只用 `tool_config` 过滤声明/清空 tools；`encode()`（`:197-242`）没有写任何 toolConfig 索引。
  - **离线复现**：`modify_body(..., tool_config={ANY, allowedFunctionNames})` 的 body 中不含 `functionCallingConfig`/`ANY`（probe 6）。
  - WIRE_SPEC 明确 MakerSuite 无独立顶层字段，ANY 只能靠过滤声明近似（`docs/WIRE_SPECIFICATION.md:214-223`）。
  - 酒馆 anti-truncation 的实测样本使用 `mode: AUTO`（`test_gemini_request_normalization.py:474-477`），因此**该预设通常不受此项影响**；但它是“工具调用不按预期发生”的一个通用解释。
  - 另一个相关风险：`allowedFunctionNames` 的**声明过滤**仍会执行（`wire_codec.py:348-364`）。如果预设每轮生成新的 `emit_complete_response_<nonce>`，而白名单里还是上一轮的名字，新的声明会被过滤掉（甚至 `filtered_tools or None` 变成无工具），模型只能输出文本或调用未声明工具。
- **触发**：任何客户端显式请求 required/ANY；或 allowedFunctionNames 与声明名不一致（nonce 每轮变化）。
- **离线验证**：probe 6；或看 `modify_body` 结果。
- **置信度**：代码确定；对本 preset 的优先级低（AUTO）。

### F-12　snapshot 内容 hash 对 functionCall/functionResponse 视而不见（可能触发 403 重放/校验失败）

- **症状**：工具内容占比越高，越容易出现 403；代理把 403 当鉴权错误切号，耗时后仍失败/超时。
- **证据**：
  - `session.py:385-403`：`hash_parts` 只收 text/inline/file/code；function parts → `""`。
  - 预设把用户文本放 functionResponse、把回答放 functionCall.args，两者都不进 hash；多轮工具会话的 hash 可能近似常量。
  - `c1d8f28` 的提交说明：“Remove SnapshotCache to ensure every request computes a fresh one-time signature, eliminating 403 token replay rejections”；`session.py:385-419` 的 docstring 也称 snapshot 是“根据请求内容计算”的一次性票据。
  - 现有测试只断言并发锁和“有 token”，未断言 tool part 参与 hash（`tests/unit/test_browser_session.py:389-457`）。
- **触发**：连续工具轮次的 hash 相同/与正文不匹配；上游按内容绑定校验时拒绝。**上游是否真的绑定校验未验证**（正常文本聊天不受影响，WP8 的工具多轮也成功，说明 hash 可能只是种子而非严格校验）。
- **离线验证**：按 `session.py:387-403` 的同一逻辑，对两段不同的 tool-only 历史计算 hash，断言相等；再对“把同样文本放 text part”的版本计算，断言不同。**只有拿到真实 403 日志/抓包后才能升级为根因。**
- **置信度**：代码缺陷确定；上游后果未验证（低-中）。修复前应先加日志，不要盲目改 hash。

### F-13（补充）错误 finish code 原样透传：MALFORMED_FUNCTION_CALL / UNEXPECTED_TOOL_CALL / TOO_MANY_TOOL_CALLS

- **症状**：客户端收到“functionCall + 非 STOP finishReason”（10/12/13），SDK 可能丢弃该调用或报错，表现为无输出。
- **证据**：映射表 `upstream_errors.py:42-61` 有 10/12/13，但 `api_service_gemini.py:218`/`:173` 原样输出；没有“有合法 functionCall 时把 10/12/13 视作可交付调用”的判断；ATG 的 `_is_tool_calls_finish` 只认字符串 `"TOOL_CALLS"`（`extractor.py:36-40,131-132`），而真实 finish 是 wire int，因此 **TOOL_CALLS→STOP 的重写在真实管线中永不触发**（`generation_service.py:654-659`）。
- **触发**：模型生成了非法/未声明/过多的工具调用（常见于历史工具名/声明不匹配、参数截断）。
- **离线验证**：构造 `Candidate(function_calls=[...], finish_reason=10/12/13)`，走非流式/流式渲染，检查 finishReason；检查 `to_gemini_finish_reason` 的所有值都不等于 `"TOOL_CALLS"`（本次 probe 已验证）。
- **置信度**：代码确定；实际是否出现取决于上游。

---

## 4. 需要补的 fixture / 日志 / 测试

### 4.1 日志（脱敏后）

1. **`[toolshape]` 入站摘要**（在 `normalize_gemini_request` 前/后各一次）：
   - `api_format`, `channel`, `surface`, `model`；
   - 每个 content：`role`，part 计数；
   - functionCall：`name` 的 **hash 前 8 位**、`args` 的 Python 类型（dict/str/list/None）、`args` 键数、`has_signature`、`call_id` 是否存在；
   - functionResponse：`name` hash、`response` 类型、`has_id`、`parts` 子类型计数；
   - 工具声明：name hash、参数 schema 的键名（`parameters` vs `parametersJsonSchema`）、mode。
   - **不记录**正文、完整签名、cookie/header。
2. **`[snapshot]`**：`content_hash[:8]`、是否包含 tool parts、连续两次请求的 hash 是否相同、token 长度/前缀（不记录完整 token）。
3. **`[stream]` 终止摘要**：是否见到 finishReason、最后 chunk 的 `candidates` 长度、工具调用数、是否等到 `done`/超时；用于区分 F-03 的两种路径。
4. **`[surface]`/`[route]`**：账号 tier、channel、surface、`clean_model`；用于排除 F-09/F-10。
5. **上游错误**：保留 `status` + 脱敏后的 `error.message`（签名/Struct/function name 的报错文本），不要记录请求体全文。

### 4.2 Fixture / 单测

1. **Gemini 历史 functionCall 无签名**：断言 `to_wire()[14]` 有占位签名（修复前应红）。
2. **Gemini functionResponse 裸字符串**：断言 wire Struct 形状（或明确 400 的契约）。
3. **finish-only chunk（空 candidates）**：断言 finish 保留，且流能立刻结束。
4. **tool-call 流无 finish**：fake session 先 functionCall、后 sleep；断言在 N 秒内产生终块/错误，而不是等到 120s。
5. **分片 tool_call**：两个 chunk 同 `call_id`/无名续块；断言输出只有一个完整调用（Gemini/OpenAI/Claude 三面）。
6. **thought 下标 10**：断言 `parse_response_part(...True)` 不产生 `function_calls`；同时保留下标 12 用例。
7. **OpenAI/Claude 预设形态历史**：tool 无前置 call、arguments 含换行、孤立 tool_result；断言兼容或明确 400 文案。
8. **ATG guard**：`parametersJsonSchema` 版本必须 `hit=True`；`required` + 只有真实工具调用时必须在首个数据事件前失败（或明确终块），不能“半截 + 500”。
9. **toolConfig**：`ANY`/allowed 的 body 不应假装已下发；当前应断言“被过滤声明、不写 mode”，并在文档中标注限制。
10. **nonce 变化**：同一段对话里让 `emit_complete_response_<nonce>` 每轮变化，分别测试末尾（无 allowed）与 pinned allowedFunctionNames 两种；断言声明不会被白名单误删。
11. **预设回归 harness**：用一份**脱敏**的酒馆请求（只保留结构：role/part 类型/name hash/args 形状）跑 `convert → normalize → wire → fake stream → render`，对三面各断言一次。

---

## 5. 最小修复建议（方案，不改代码）

按优先级；每项都尽量局部、可回滚、可测试。

1. **签名兜底（最高优先）**
   在 `chat_service.normalize_gemini_request` 处理 model-role `functionCall` 时：
   - 若 `part.thoughtSignature` 为空，先查 `signature_cache.get(fc.id)`；
   - 仍为空则用 `DUMMY_THOUGHT_SIGNATURE`（`context_engineering_is_the_way_to_go`）；
   - **只对 functionCall**，不要给 functionResponse/纯文本乱加。
   这样与 OpenAI/Claude 现有策略对齐，且不改上游行为。

2. **工具载荷类型归一（Gemini 原生面）**
   - `functionResponse.response`：非 dict/list → 包成 `{"result": <str>}`（与 OpenAI/Claude 一致）；空 → `{}`。
   - `functionCall.args`：字符串先尝试 `json.loads`（对象/数组）；失败时按 `{"content": <str>}` 包（或按严格开关显式 400，并给出字段路径）。
   - 在 `_encode_wire_args` 前做，避免字符串直接落到 Struct 位置。

3. **Gemini 流式预读首事件**
   复制 `routes_openai.py:102-122` 的模式：在 `StreamingResponse` 之前 `__anext__()` 一次；首事件前的 `SurfacePolicyError`/`RequestError` 直接返回真实 HTTP 4xx/5xx。这样 F-01/F-04 会以 400 显示，而不是“200 空流”。
   若必须保留 200 SSE，至少把 error 事件升级为“带 finishReason/终止标记”的终块，并在客户端侧验证。

4. **流终止兜底**
   - 在 `parse_response_chunk` 里先读 `finish_reason`，再判断 candidates 是否为空（把 `:416` 的 early return 移到 finish 读取之后）。
   - 对 tool-call 流：若已产出完整调用（name 非空、args 可解析）且上游在短窗口内无新 chunk，或收到 `done`，合成一次 finish（STOP）；窗口值需可配置，默认不要大于客户端超时。
   - 把 `timeout_stream` 的语义写成“工具流空闲超时”，并加 E2E。

5. **tool_call 聚合器**
   在 `streaming.py` 或 `generation_service` 增加 per-stream accumulator：
   - key = `call_id`（无 id 时用“最近一个未完成调用”）；
   - 合并 name/args；无名续块并入最近调用；
   - 完成条件 = finish / 参数 JSON 可解析 / 下一调用出现；
   - 渲染层只发完整调用；无法合并时显式报错，不要发 `name=""`/`"unknown"` 的调用。

6. **修正 thought/functionCall 下标歧义**
   在 `parse_response_part` 先判定 thought（含下标 10 bool/int），若该位置是标量则**不要**再尝试 functionCall；只有 list/dict 才走 `_coerce_wire_payload`。补 `assert not parsed.function_calls`。

7. **ATG guard 补齐**
   `_declaration_shape` 依次读 `parameters` / `parametersJsonSchema` / `parameters_json_schema` / `input_schema`；保持 5 信号不变。`required` 的失败应尽量在首个事件前发生（与第 3 项联动），并输出可 grep 的 stats。

8. **回退路径前缀清理**
   `_begin_route` 回退时用 `parse_model_target(model_path)` 取 `clean_model`；流式回退不要对带前缀的 model 调 `_resolve_surface`，而是钉死 `surface="chat"`。normalize 只用 clean model。

9. **Free/chat 策略**
   若确定允许 free 用 pg：把 `DEFAULT_ALLOWED_BY_TIER["free"]` 改为 `["chat","build"]` 或在 `build` 关闭时回退 `["chat"]`；否则在错误文案/README 明确“pg 需要 paid”，并让 `[surface]` 日志可 grep。不要在工具层修这个。

10. **snapshot（先观测再改）**
   先加 `[snapshot]` 日志确认连续工具轮次是否同 hash/token；若确认，方案是：
   - 在 `hash_parts` 中加入 functionCall/functionResponse 的**确定性序列化**（name + args/response 的 canonical JSON + id），或
   - 在 hash 输入中 append 每请求 nonce（更安全，避免改变内容绑定语义）。
   注意：现有 hash 可能刻意模仿上游；未验证前不要直接改语义。

11. **OpenAI/Claude 兼容开关**
   加 `features.compat.lenient_tool_history`（默认 off）：
   - 未匹配的 tool_call_id/tool_use_id → 退化为普通 user 文本 part，而不是 400；
   - `arguments` 非法 JSON → 包成 `{"content": raw}`；
   - 日志标注 `[toolshape] degraded=...`。
   这会让“只保留工具输出框”的预设可用，同时保留严格模式给普通客户端。

---

## 6. 已验证 / 未验证边界

**已验证（只读代码或离线数据）**
- pg=chat 的别名与显式选路（§1）。
- 工具请求的 wire 形状：functionCall 下标 10、functionResponse 下标 11、签名 14；裸字符串 response 不成为 Struct（F-04）。
- 上游 4xx 在 Gemini 流式入口变成 HTTP 200 + SSE error（F-02）。
- finish-only chunk 的 finish 丢失（F-03）。
- 流式 tool_calls 无聚合、无名续块产生空 name（F-05）。
- thought 下标 10 被误判为 functionCall（F-06）。
- ATG guard 对 `parametersJsonSchema` 漏判（F-07）。
- OpenAI/Claude 严格工具历史的三类 400（F-08）。
- toolConfig 不写入 wire（F-11）。
- snapshot hash 不含 function parts（F-12）。

**未验证（需要一次脱敏请求/日志/真实上游，不能当结论）**
- 该酒馆预设实际发送的 `functionResponse.response` 是字符串还是 dict；历史 functionCall 是否带签名；工具名是否每轮变化。
- 上游在该 chat 模型上是否对 functionCall 流保持连接/不发 finish（F-03 的触发端）。
- 上游是否对 functionCall 参数做增量分片（F-05 的触发端；现有证据倾向“整块”，但有未决矛盾）。
- snapshot 403 是否真的由 tool-blind hash 触发（F-12）。
- 酒馆客户端是否忽略 200 SSE error 并继续等待（F-02 的客户端侧）。

**明确排除**
- 不是“pg 被 build surface 误路由”（正常路径 `select_channel` 钉死 chat；只有回退路径 F-09）。
- 不是“模型 4a 不会输出 functionCall”；即使输出正确，F-01/F-02/F-03/F-04 任一都能让它在酒馆侧表现为无输出。
- 不是 ATG 的合成工具在**默认 off** 配置下导致的；ATG 只在显式开启时才是 F-07 的触发条件。

---

## 7. 离线探针索引（可复跑，均不触网）

以下为本轮只读调查用的临时脚本（位于 `%TEMP%\kilo`，未入仓库；不是项目测试）：
- `probe_pg_toolcall.py`：functionResponse 字符串 vs dict 的 wire 形状、ATG guard 的 `parameters`/`parametersJsonSchema`、finish-only chunk、finish 映射、ATG finish remap、模板解码丢 call_id。
- `probe_pg_toolcall2.py` / `probe_pg_toolcall3.py`：真实 chunk 形状、分片/无名续块、StreamingGateway 事件序列。
- `probe_pg_toolcall4.py`：Gemini 流式入口把上游 400 变成 HTTP 200 + SSE error。
- `probe_pg_toolcall5.py`：OpenAI/Claude 严格工具历史的三类 ConversionError。
- `probe_pg_toolcall6.py`：`modify_body` 不写 toolConfig。
- `probe thought flag`：`parse_response_part` 在下标 10 bool 时同时返回 thought 与幽灵 functionCall。

复跑建议：在项目根用 `.venv\Scripts\python.exe` 执行；所有输入均为手写 wire 数据，不包含账号、cookie、API key 或正文。

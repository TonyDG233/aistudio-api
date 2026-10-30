# REV-B5a-toolcall — independent read-only review

- 目标 worktree：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`
- 基线：`05a9df7`；B5a 改动全部未提交（12 个 tracked 修改 + 2 个 untracked：`src/aistudio_api/application/signature_cache.py`、`tests/unit/test_pg_toolcall_harness.py`）
- 审查日期：2026-10-30（Q-01/N-12、Q-03 复审见 §13）
- 审查方式：只读源码/测试/fixture/WIRE spec；独立运行门禁；未修改源码/测试/配置，未 stage/commit，未启动 B5b，未访问真实账号/IP/Google。
- 权威：`06-decision-log.md` D7/P5、`domains/P5-generation/index.md`、`domains/P5-generation/09-pg-toolcall-analysis.md`、`docs/WIRE_SPECIFICATION.md`、目标 diff。

## 0. 结论

**Verdict: PASS（Q-01/N-12 与 Q-03 已在 2026-10-30 复审中闭合，见 §13）。**

未发现 PG-01..PG-04 的阻断性代码缺陷；PG-05..PG-13 的 triage 与目标树一致。两个 pytest 失败均为未改动文件中的 Windows 环境问题，与 B5a 无关。已知非阻断缺口全部列入 §10 N 项；需要用户裁决的项列入 §11 Q 项。

Q-01/N-12（端到端 preset harness）与 Q-03（tool-stream idle fallback）已验证为 **RESOLVED**；当前无 blocker。剩余 Q-02、Q-04..Q-10 与 N-01..N-11、N-13..N-18、N-19..N-25 均为非阻断项。

## 1. 独立门禁

在目标 worktree 执行：

```powershell
$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q
uv run ruff check src tests
uv run pyright src tests
git diff --check
```

| 门禁 | 结果 | 说明 |
|---|---|---|
| `uv run pytest -q` | **2 failed, 549 passed, 1 skipped in 8.46s** | 本次复审（2026-10-30）计数；新增 harness + idle 测试各 1 个。失败仍为 `tests/unit/test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser`、`tests/unit/test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly` |
| `uv run ruff check src tests` | **All checks passed!** | — |
| `uv run pyright src tests` | **0 errors, 0 warnings, 0 informations** | — |
| `git diff --check` | **exit 0** | 仅 LF→CRLF 警告，无 whitespace error |
| `git diff --cached --check` | **exit 0** | 无 staged 改动 |
| 定向 B5a 测试 | **18 passed（上一轮）+ 3 passed（新增 harness/idle）** | 上一轮 `-k "pg01 or pg02 or pg03 or pg04 or metadata or tool_stream or finish_only or continuing or prestream or streaming_success"`；本轮 `test_pg_toolcall_harness.py` + idle 用例 |

两个失败的原因均在未改动文件及其环境依赖：

- `test_find_chromium_executable_strictly_requires_cloakbrowser`：Windows 上 `Path("/opt/cloakbrowser")` 的 `str()` 为 `\opt\cloakbrowser`，测试的 `fake_opt_is_dir` 只匹配 `/opt/cloakbrowser`，属于 Windows 路径语义问题（`tests/unit/test_browser_engine.py:99-137`）。
- `test_evaluate_wrapper_expressions_parse_cleanly`：`bun.CMD -e` 经 `cmd.exe` 包装后报 `\n was unexpected at this time`，属于 Windows 命令 shim 问题（`tests/unit/test_browser_scripts_syntax.py:199`）。

这两个文件及其目标代码都不在 B5a diff 中；未发现 B5a 改动导致失败。

## 2. 范围与仓库状态

`git status --short`（目标 worktree）恰好为：

```
 M src/aistudio_api/application/api_service_gemini.py
 M src/aistudio_api/application/chat_service.py
 M src/aistudio_api/infrastructure/gateway/streaming.py
 M src/aistudio_api/infrastructure/gateway/wire_spec.py
 M src/aistudio_api/infrastructure/gateway/wire_types.py
 M tests/fixtures/pg/f03_finish_only.bin
 M tests/unit/test_api_responses.py
 M tests/unit/test_api_service_gemini.py
 M tests/unit/test_gemini_request_normalization.py
 M tests/unit/test_routes_gemini.py
 M tests/unit/test_stream_transport.py
 M tests/unit/test_wire_parser.py
?? docs/dev/rebuild/domains/P5-generation/reviews/REV-B5a-toolcall.md
?? src/aistudio_api/application/signature_cache.py
?? tests/unit/test_pg_toolcall_harness.py
```

`git diff --cached --name-only` 为空；HEAD 仍为 `05a9df7`，未 commit/stage。两份 review 报告是本次复审允许新增/修改的唯一文档；`signature_cache.py` 与 `test_pg_toolcall_harness.py` 仍是 untracked。

没有 UI/frontend/build/static 改动；没有 OpenAI/Claude/Responses 适配器（`src/aistudio_api/api/app.py` 只注册 Gemini/models/accounts 等既有路由）；没有 `generation_service.py`/ATG 模块；没有 D10 改动；没有真实网络/账号/IP。

根配置检查：

- `D:\LLM\self_programming\aistudio-api\config.yaml` 存在；
- `git -C D:\LLM\self_programming\aistudio-api ls-files config.yaml` 为空；
- `git check-ignore -v config.yaml` → `.gitignore:62:/config.yaml`；
- `defaults/config.yaml` 仍为 tracked；
- 目标 worktree 的 `config.yaml` 同样存在但未跟踪/已忽略，未出现在 diff。

## 3. PG-01 — 历史 model-role functionCall 签名兜底

代码：

- `src/aistudio_api/application/chat_service.py:707-721`：`_historical_function_call_signature(role, part)`。
  - 非 `model` 或非 `functionCall`：返回原 `part.thoughtSignature`（不注入）；
  - 显式签名非空：直接返回显式签名；
  - 否则按 `functionCall.id` 查 `signature_cache.get(call_id)`；
  - 仍为空则返回 `DUMMY_THOUGHT_SIGNATURE`。
- `src/aistudio_api/application/chat_service.py:772-784`：只在 `part.functionCall` 分支调用该函数。
- `src/aistudio_api/infrastructure/gateway/wire_types.py:271-276`：`AistudioPart.to_wire()` 在 `thought_signature` 非空时写下标 14；`:284-291` 将 functionCall 写下标 10。
- `src/aistudio_api/application/signature_cache.py:27`：`DUMMY_THOUGHT_SIGNATURE = "context_engineering_is_the_way_to_go"`；`:56-83` `put/get`，非消费式读取。

测试证据：

- `tests/unit/test_gemini_request_normalization.py:866-883`：加载 `f01_missing_signature.json`，断言 `wire[10][0] == "get_weather"` 且 `wire[14] == DUMMY_THOUGHT_SIGNATURE`。
- `tests/unit/test_gemini_request_normalization.py:885-913`：`signature_cache.put("call_cached", ...)` 后断言 `wire[14] == "real_signature_from_upstream"`，证明缓存优先于 dummy。
- `tests/unit/test_gemini_request_normalization.py:916-937`：纯文本与 `functionResponse` 的 `thought_signature` 均为 `None`，证明未误注入。

结论：PG-01 代码与 f01 回归成立；显式签名优先级由 `:713-714` 保证。缓存生产者缺失见 N-01；显式签名无独立测试见 N-05。

## 4. PG-02 — 首事件前真实 HTTP 状态

代码：

- `src/aistudio_api/application/api_service_gemini.py:552-598`：`_build_gemini_streaming_response` 在构造 `StreamingResponse` 之前执行 `first_chunk = await events.__anext__()`（`:560`）。
- `:574-577`：首事件前 `BrowserTransportError` 显式转为 `RequestError(502)`；其他异常原样抛出。
- `:580-587`：首事件之后，`body()` 捕获异常并 `yield format_sse_error(exc)`，仍为 SSE。
- `src/aistudio_api/application/api_service_gemini.py:95-137`：错误分类；`src/aistudio_api/api/app.py:236-247` 将 `AistudioError`/`RoutingError` 映射为 JSON HTTP 状态。

测试证据：

- `tests/unit/test_routes_gemini.py:157-193`：真实 ASGI（`httpx.ASGITransport(app=app)`）注入首事件前 `RequestError(400)`，断言 HTTP 400、`application/json`、`text/event-stream` 不在 content-type、错误 JSON 字段，且 handle 已释放。
- `tests/unit/test_api_service_gemini.py:166-207`：首个 attempt 只产出 metadata（usage）后抛 `RequestError(503)`，断言仍能 switch 到第二个实例并产出正文/`STOP`，两个 handle 均释放。
- `tests/unit/test_api_responses.py:241-295`：metadata-only 首 attempt 抛 `RequestError(204)`，断言 `retry_same` 仍在同一实例完成（原有测试，未被削弱）。

状态路径核对：

- 400/503：`RequestError` → 真实 400/503。
- 401：`SessionExpiredError` → 401（切换耗尽后）。
- 403：`AuthError`/`TierRequiredError` → 403。
- 502：`BrowserTransportError` → 显式 `RequestError(502)`。
- 429：`UsageLimitExceeded` 若直接抛出为 429；按 D4，同账号重试/切换耗尽后由 `routing_pipeline.py:625-645,714-719` 转为 `SurfaceSwitchExhaustedError` → 503（真实 5xx，不是 200 SSE）。见 Q-02。

结论：PG-02 的“首事件前不伪装成 200 SSE”成立。ASGI 测试只直接覆盖 400；401/403/502/503 为通用路径未逐一断言（N-03）；首事件后 SSE error 无直接测试（N-04）；真实 status+error-body 的组合未被测试（N-17）。

## 5. PG-03 — finish-only / 工具流终止

fixture 更正：

- 旧 `f03_finish_only.bin`（HEAD，22 字节）：`)]}'\n[[],null,[5,3,8]]` —— candidates 为空，没有任何 candidate，也没有 finish 字段。
- 新 fixture（34 字节）：`)]}'\n[[[[[null,1]],null,[5,3,8]]]]` —— candidate 为 `[null, 1]`，finish 位于 WIRE spec 规定的 `candidate[1]`（`docs/WIRE_SPECIFICATION.md:260-263`），usage 仍为 `[5,3,8]`。
- 旧 fixture 的不成立原因：空 candidates 容器里不存在 candidate，因而不存在可“丢失”的 finish；`parse_response_chunk` 的提前 return 在 `wire_parser.py:446-448`，finish 读取在 `:523-528`。更正后的 fixture 才符合 WIRE spec。

测试证据：

- `tests/unit/test_wire_parser.py:382-402`：解析更正后的 f03，断言 `finish_reason == 1`、`text == ""`、`function_calls == []`、usage 5/3/8，且 `parse_text_output` 保留 finish/usage。
- `tests/unit/test_wire_parser.py:404-412`：`finish_reason == 22`（CONTINUING）被保留，正文仍解析。
- `tests/unit/test_stream_transport.py:357-380`：fake session 只给一个 functionCall chunk、无 finish，断言事件含 `tool_calls`、`("finish_reason", 1)`、`done`，usage 只有一条且 `total_tokens == 11`。
- `tests/unit/test_pg_toolcall_harness.py:86-204`：端到端 preset harness（f01+f04 → normalize → modify_body → fake stream → `StreamingGateway` → `format_sse_event`/`format_sse_usage`）；断言 wire 14 dummy、wire 11 Struct、`tool_calls`/`finish_reason=1`/usage 7/4/11、`done`、SSE `functionCall.args` 与最终 `STOP`/usage；同时断言 `[toolshape]` 不含 prompt/arg/response 明文。
- `tests/unit/test_stream_transport.py:403-462`：idle fallback；`monkeypatch` 把 `TOOL_STREAM_IDLE_FINISH_S` 设为 0.05，fake upstream 在 functionCall 后无限等待；断言恰好一个 `finish_reason=1`、一个 usage（total 11）、`done` 最后、`elapsed < 2.0`、生成器 `finally` 已执行（`closed is True`）。

代码：

- `src/aistudio_api/infrastructure/gateway/streaming.py:37`：`TOOL_STREAM_IDLE_FINISH_S = 2.0`；`:118` 仅在 `has_tool_calls and latest_finish_reason is None` 时启用；`:121-122` 用 `asyncio.wait_for(stream.__anext__(), ...)`；`:129-130` 非工具流超时原样抛出；`:164-167` 过滤 CONTINUING=22（不终止）；`:172-174` 记录 `has_tool_calls`；`:181-188` 在 `finally` 中 `aclose()`；`:215-216` 流结束/空闲后合成 STOP；`:218-220` 恰好一个 finish/usage/done。
- `src/aistudio_api/infrastructure/gateway/wire_spec.py:127`：`WIRE_FINISH_REASON_STOP = 1`。
- 未重新引入全局/120s deadline：`transport.py:350` 的 1.0s 只是队列轮询间隔；`session.py:206`/`pooled_session.py:136` 的 120s 是 `ensure_ready` 就绪超时，不是流式请求 deadline；2.0s 只在工具流且无 finish 时生效。

结论：PG-03 成立；无 120s 全局超时。Q-03 的 idle fallback 已由代码 + 0.05s monkeypatch 测试闭合（§13）。CONTINUING=22 的“不终止”仍是 parser 级断言（N-06）；空 candidates 的旧形状不再被测试（N-07）；空闲窗口是逐事件间隔而非总时长（N-19）；上游自身抛出的 `TimeoutError` 与 `wait_for` 超时不可区分（N-20）；CONTINUING=22 后 2s 空闲仍会合成 STOP（N-25）。

## 6. PG-04 — Struct 强制与脱敏 [toolshape]

代码：

- `src/aistudio_api/infrastructure/gateway/wire_types.py:325-333`：`_decode_json_object` 只解析以 `{` 开头的 JSON 对象字符串。
- `:336-353`：`coerce_function_call_args`：`None -> {}`；dict 原样；JSON-object 字符串解码为对象；其他（纯字符串/list/标量）包成 `{"content": value}`。
- `:356-374`：`coerce_function_response`：`None -> {}`；dict 原样；JSON-object 字符串解码为对象；其他包成 `{"result": value}`。
- `src/aistudio_api/application/chat_service.py:772-795`：在写 wire 前调用 `coerce_function_call_args`/`coerce_function_response`。
- 参数 schema 别名：`chat_service.py:37-42` 的 `_TOOL_SCHEMA_KEYS` 覆盖 `parameters`/`parametersJsonSchema`/`parameters_json_schema`/`input_schema`/`parameters_schema`；既有编码在 `chat_service.py:313-323` 保留别名。

测试证据：

- `tests/unit/test_gemini_request_normalization.py:940-953`：f04 的字符串 `functionResponse.response = "user text"` → wire 下标 11 为 `["emit_complete_response_x", [[["result", [null, null, "user text"]]]]]`。
- `:955-991`：JSON-object 字符串、list、标量、`None` 四种 shape；分别验证对象解码、`result` 包装、`result` 标量包装、空 Struct `[[]]`。
- `:993-1031`：`functionCall.args` 的 JSON-object 字符串、纯字符串、标量分别解码/包成 `content`。
- `:1033-1091`：注入 `TOP_SECRET_PROMPT_AND_ARGUMENT_VALUE`，断言 `[toolshape]` 日志不含该值，且包含 `args_type`/`has_signature`/`schema_key`/`parameters`。

`[toolshape]` 内容核对（`chat_service.py:619-704`）：

- 只记录 `api_format`/`channel`/`surface`/`model`、每个 content 的 role/part 计数、functionCall 的 `name_hash`/`args_type`/args 键名/`has_signature`/`has_id`、functionResponse 的 `name_hash`/`response_type`/`has_id`/子 parts 计数、工具声明的 `name_hash`/`schema_key`/`mode`。
- 不含正文、参数值、完整签名、header、cookie。
- 日志级别为 INFO，每次 `normalize_gemini_request`（含每次 retry attempt）都会输出（`chat_service.py:1019`）；体积风险见 N-08。键名本身可能含用户文本见 N-09。

结论：PG-04 成立；f04 wire 下标 11 与别名保留均有证据。list args 未单独测试见 N-16；dict 原样返回未复制见 N-15。

## 7. fixture 说明

| fixture | 形状 | 用途 |
|---|---|---|
| `tests/fixtures/pg/f01_missing_signature.json` | user text → model `functionCall`（args dict，无 signature） → user `functionResponse`（response dict） | PG-01：历史 model functionCall 缺签名 → 下标 14 dummy；response/text 不注入 |
| `tests/fixtures/pg/f02_prestream_400.json` | upstream `status=400` + `error{code,message,status}`；client stream | PG-02：首事件前失败必须是真实 400 JSON，不是 200 SSE |
| `tests/fixtures/pg/f03_finish_only.bin` | `)]}'` + `[[[[[null,1]],null,[5,3,8]]]]` | PG-03：candidate finish 在真实下标 1；finish/usage 保留 |
| `tests/fixtures/pg/f04_string_response.json` | user `functionResponse.response = "user text"` | PG-04：裸字符串 → `{"result": ...}` Struct；下标 11 shape |

f03 的字节更正不是“改测试迁就实现”，而是旧 fixture 本身不符合 WIRE spec；更正后测试才有可断言的 candidate finish。

## 8. PG-05..PG-13 triage

| PG | 结论 | 目标树证据（file:line） | 准确性/备注 |
|---|---|---|---|
| PG-05 分片聚合 | **defer** | `src/aistudio_api/infrastructure/gateway/streaming.py:172-174` 逐 chunk `yield ("tool_calls", ...)`，无 accumulator；`generation_service.py` 不存在 | 准确；属 P5 后续，不阻塞 B5a |
| PG-06 下标 10 幽灵 functionCall | **solved（基线已解）** | `src/aistudio_api/infrastructure/gateway/wire_parser.py:273-283` `_coerce_structural_payload` 只接受 list/dict；`tests/unit/test_wire_parser.py:101-104` 断言 `function_call is None` | 准确；但这是 HEAD 基线行为，不是本次 B5a diff 新增，triage 应写“基线已解” |
| PG-07 ATG guard | **defer** | `src/aistudio_api/application/atg/` 不存在；`chat_service.py:1-35` 无 ATG import | 准确；目标树无 ATG，需 P5-Q4 port 后再验 |
| PG-08 OpenAI/Claude 严格工具历史 | **C3** | 无 `format_converters/`、无 `routes_openai.py`/`routes_claude.py`；仅 `src/aistudio_api/api/routes_gemini.py:19-43` | 准确；C3 为适配器阶段，不属 B5a/B5b 无工具闭环 |
| PG-09 回退前缀泄漏 | **N/A（目标架构无该路径）** | `api_service_gemini.py:197-200` 直接 `pipeline.begin`，异常传播；`routing_pipeline.py:297-310` 显式拒绝/抛出；`generation_service.py` 不存在 | 准确；应写 N/A，而不是“B5a 修复” |
| PG-10 Free/chat 默认组拒绝 | **N/A（目标架构无 group policy）** | `routing_eligibility.py:29-30,68-86` 移除并拒绝 `pg/playground/official` 前缀；无 channel registry/group allowed_surfaces 路径 | 准确；N/A |
| PG-11 toolConfig 不写上游 | **upstream limitation** | `wire_codec.py:330-364` 只用 tool_config 过滤/清空声明；`docs/WIRE_SPECIFICATION.md:215-224` 明确 MakerSuite 无独立顶层字段 | 准确；B5a 未改语义 |
| PG-12 snapshot hash 忽略 tool parts | **defer** | `session.py:59-85` hash 只收 text/inline/file/code，function parts append `""`；`:410-412` 用该 parts 计算 | 准确；未改语义 |
| PG-13 非 STOP finish 原样透传 | **defer** | `api_service_gemini.py:48-74` 映射 10/12/13；`:487-491` 流式使用同一映射；无 TOOL_CALLS→STOP 重写 | 准确；未改语义 |

## 9. 测试真实性与是否削弱

- 没有删除/弱化既有断言。`tests/unit/test_api_responses.py` 仅把两处调用改为 `await _build_gemini_streaming_response(...)`（`:227`、`:287`），断言仍保留。
- 其余测试文件均为新增；f03 是 fixture 更正（旧字节无效）。
- 新增测试断言的是外部行为：ASGI HTTP status/content-type/JSON、wire 下标 10/11/14、Struct shape、`finish_reason`/usage、日志脱敏，以及 preset 端到端链路与 idle fallback 的 STOP/usage/done/cleanup。没有只断言私有实现细节的“假绿”。
- 定向 18 个旧测试 + 3 个新增测试全部执行且通过；pytest 计数 549 与 worker 声明一致。
- 覆盖缺口已在 N-03..N-07、N-16、N-17、N-19..N-25 列出；不影响已断言行为的有效性。

## 10. 非阻断 N 项

- **N-01** `signature_cache.put()` 无生产调用者：`signature_cache.py:56-67` 仅在测试 `test_pg01_cached_signature_precedes_dummy` 被调用；`chat_service.py:719-721` 只读缓存。真实签名恢复路径当前不会命中，PG-01 实际总走 dummy。需在 B5b/适配器或响应侧补生产者。
- **N-02** 新 `ThoughtSignatureCache` 无专属测试：TTL、4096 上限淘汰、磁盘持久化/原子替换、线程安全均未被覆盖；ledger 提到的 `test_signature_cache.py` 不在本树。仅 put/get 一条路径被 PG-01 测试覆盖。
- **N-03** PG-02 的 ASGI 测试只直接覆盖 400；401/403/502/503 走同一异常传播路径但未逐一断言。429 在 D4 耗尽时映射为 503（`routing_pipeline.py:625-645,714-719`），不是 429。
- **N-04** 首事件之后的错误仍为 SSE error 只有代码证据（`api_service_gemini.py:580-587`），没有测试断言 `data: {"error": ...}`。
- **N-05** 没有测试证明“显式 model-functionCall thoughtSignature 优先于 cache/dummy”；只有 cache 优先于 dummy 与 text/functionResponse 不注入的测试。代码 `chat_service.py:713-714` 明确优先显式值。
- **N-06** CONTINUING=22 的测试是 parser 级（`test_wire_parser.py:404-412`），没有 streaming 级测试证明 `StreamingGateway` 收到 22 后继续消费；代码 `streaming.py:164-167` 过滤 22 且循环无 break，行为可由代码确认。
- **N-07** f03 测试覆盖的是“candidate content 为 null、finish 在 candidate[1]”，不是“空 candidates 容器”。`parse_response_chunk` 对空 candidates 仍提前 return（`wire_parser.py:446-448`），这符合 WIRE spec（无 candidate 即无 finish），但旧“finish-only”叙事需按此更正。
- **N-08** `[toolshape]` 在 INFO 级别每次 normalize（含 retry）输出完整 per-content/per-declaration 摘要（`chat_service.py:686-704,1019`）。长历史/多次重试时日志体积线性增长，无采样/级别门槛。
- **N-09** `[toolshape]` 的 `keys` 是参数键名（`chat_service.py:635-640`）。若客户端把用户文本用作键名，该文本会进入日志；09 §4.1 允许键名，但仍有残余隐私风险。
- **N-10** `name_hash` 为 8 位十六进制（`chat_service.py:566-569`），低熵工具名可被暴力反推；这是 09 §4.1 的设计，不含正文/签名。
- **N-11** 测试 teardown `signature_cache.configure_path(None); clear()` 会把进程级缓存置为 `_path=None,_loaded=True`，后续同进程测试若依赖默认文件持久化不会加载；仅影响测试隔离。
- **N-12（RESOLVED 2026-10-30）** 端到端“预设形态 harness”已由 `tests/unit/test_pg_toolcall_harness.py:86-204` 提供：f01+f04 → normalize → modify_body/wire → fake stream → `StreamingGateway` → `format_sse_event`/`format_sse_usage`，断言 wire 14 dummy、wire 11 Struct、tool_calls/finish/usage/done、`[toolshape]` 明文脱敏。确定性、离线；无网络/浏览器/账号。残余缺口见 N-21（未走 ASGI `_build_gemini_streaming_response`）与 N-24（未断言签名/header/cookie 哨兵）。
- **N-13** `_build_gemini_streaming_response` 的 `StopAsyncIteration` 分支返回空 200 `text/event-stream`，并用 `if False: yield ""` 占位（`api_service_gemini.py:560-571`）。成功路径总会 yield `format_sse_usage`，失败会抛异常，该分支实际不可达；属代码异味。
- **N-14** P5 index 仍写“B 未开始”/基线 `99c38ba+9b5cdea`（`domains/P5-generation/index.md:3-4`），09 仍写旧 fork 基线；`docs/dev/evidence/B5a/` 无本次证据产物。文档/流程滞后。
- **N-15** `coerce_function_call_args`/`coerce_function_response` 对 dict 原样返回（`wire_types.py:344-374`），未复制；若后续代码原地修改会反向修改请求模型。当前无原地修改。
- **N-16** PG-04 未单独测试 `functionCall.args` 为 list 的情况（代码会包成 `{"content": [...]}`），也未测试嵌套 dict response；既有 struct 编码测试覆盖了嵌套，但新 coercion 分支未覆盖。
- **N-17** PG-02 测试用“直接 raise `RequestError`”模拟首事件前失败，没有模拟“status 400 + error body 帧”。`streaming.py:117-147` 会先把 body 喂给 parser，`:156-167` 才检查 status；若 error body 恰好被解析成带 text 的 chunk，理论上可能在首事件前 yield 数据。已知 Google 错误体为 JSPB/非 JSON 或 dict 形状，当前未观察到触发；属残余风险/测试缺口。
- **N-18** `_toolshape_summary` 对非 dict 工具声明直接跳过（`chat_service.py:668-673`），畸形声明在摘要中不可见；仅影响诊断日志。
- **N-19** 空闲窗口是**逐事件间隔**（每次 `__anext__()` 2.0s），不是工具流总空闲预算。上游若在 tool_calls 后每 <2s 发一个非 finish 事件（如 `response_id`/`thought_signature`），仍可能长期不触发 STOP。当前实现符合“idle”语义，但残余挂起风险。
- **N-20** `except TimeoutError` 不区分 `asyncio.wait_for` 的超时与上游/`send_streaming_request` 自身抛出的 `TimeoutError`；在 `idle_bounded` 为真时，上游 `TimeoutError` 会被当作空闲并合成 STOP，而不是传播。当前 `XHRStreamTransport` 不抛 `TimeoutError`，属理论风险。
- **N-21** harness 走 `StreamingGateway` + `format_sse_event`/`format_sse_usage`，但不走 `_build_gemini_streaming_response`/ASGI 路由，因此不覆盖首事件预读、真实 HTTP 状态和 SSE body 的完整组装；PG-02 的 ASGI 测试另行覆盖 400。
- **N-22** `tests/unit/test_pg_toolcall_harness.py` 仍为 untracked；提交时若只用 `git add -u` 会漏掉（与 `signature_cache.py` 同类）。
- **N-23** 没有测试证明“非工具流不会被 2.0s idle 窗口截断”。代码 `streaming.py:118-130` 明确只在 `has_tool_calls and latest_finish_reason is None` 时 `wait_for`，但缺少文本流 idle > 窗口仍继续的网关级回归。
- **N-24** harness 的 `[toolshape]` 脱敏断言只覆盖 prompt/arg/response 明文，未加入 `DUMMY_THOUGHT_SIGNATURE`、header、cookie 哨兵；摘要结构本身不含这些字段，但回归未显式锁定。
- **N-25** CONTINUING=22 被有意排除在 `latest_finish_reason` 之外（`streaming.py:164-167`），因此工具流收到 22 后若 2.0s 内没有新事件，`idle_bounded` 仍为真并合成 STOP。对合法的慢续传工具流可能提前截断；这是“22 不终止”与“工具流 idle 窗口”的交互副作用，需确认 2.0s 与 CONTINUING 语义可接受。

## 11. 决策/条件 Q 项

- **Q-01（RESOLVED 2026-10-30）** P5 index 第 10 行的预设形态 harness 已由 `tests/unit/test_pg_toolcall_harness.py:86-204` 满足（见 §13）；无须再按“单元切片”豁免。残余 ASGI 链路缺口见 N-21。
- **Q-02** PG-02 的“429”口径：D4 规定 429 同账号重试/切换耗尽后返回 503（`routing_pipeline.py:625-645,714-719`）。确认按“真实非 200（耗尽为 503）”验收，而不是必须逐字 429。
- **Q-03（RESOLVED 2026-10-30）** 工具流空闲 fallback 已实现：`TOOL_STREAM_IDLE_FINISH_S = 2.0` 仅在 `has_tool_calls and latest_finish_reason is None` 时启用，非工具流保持无限；`test_stream_transport.py:403-462` 用 0.05s monkeypatch 证明 functionCall 后挂起会在窗口内合成一个 STOP + usage + done，并触发 generator 清理（见 §13）。残余逐事件/TimeoutError/CONTINUING 风险见 N-19、N-20、N-25。
- **Q-04** `signature_cache` 的生产者何时/在哪接入（B5b OpenAI 适配器？Gemini 响应侧？），以及 `data/signature_cache.json` 路径、30 天 TTL、4096 上限是否为最终口径。
- **Q-05** `[toolshape]` 的级别/采样策略：当前每次 normalize（含 retry）INFO 输出；是否改为 DEBUG、按请求采样，或仅在检测到 tool parts 时输出？
- **Q-06** f03 fixture 从 22 字节改为 34 字节，是否作为新契约（旧字节无 candidate，无法证明 finish 丢失）；PKG-B5a-01 §2 原写“fixture 字节不变”，需同步口径。
- **Q-07** PG-08 的 C3 归属：确认 C3 是 OpenAI/Claude 适配器阶段，且 B5b 仍为无 tools 的最小闭环。
- **Q-08** PG-12/PG-13 defer 是否接受（D7 未将其纳入 P5 门）；是否现在补 `[snapshot]` 观测日志。
- **Q-09** PG-06 的“solved”应标注为“HEAD 基线已解”，不是 B5a diff 修复；PG-09/10 同理应标 N/A（架构无该路径），避免把基线行为记成 B5a 成果。
- **Q-10** `src/aistudio_api/application/signature_cache.py` 与 `tests/unit/test_pg_toolcall_harness.py` 目前均 untracked；提交时若只用 `git add -u` 会漏掉。确认 B5a 提交前显式 `git add` 这两个文件及两份 review 报告（报告是否入库由用户决定）。

## 12. 只读声明

本次审查只读；除本报告的两个相同副本外，未修改任何源码、测试、配置或文档，未 stage/commit，未启动 B5b，未访问真实账号/IP/Google。

## 13. B5a Q-01/Q-03 复审（2026-10-30）

- 复审范围：仅 Q-01/N-12（preset 端到端 harness）与 Q-03（tool-stream idle fallback）；其余 Q/N 保留。
- 代码/测试：`tests/unit/test_pg_toolcall_harness.py:86-204`；`src/aistudio_api/infrastructure/gateway/streaming.py:37,118-136,164-167,172-174,181-188,215-220`；`tests/unit/test_stream_transport.py:403-462`。
- Harness 链路：f01+f04 fixtures → `normalize_gemini_request` → `modify_body` wire body → fake `send_streaming_request` → `StreamingGateway.stream_chat` → `format_sse_event`/`format_sse_usage`。断言：normalize 与 wire body 的 index 14 = `DUMMY_THOUGHT_SIGNATURE`；index 11 = `["emit_complete_response_x", [[["result", [null,null,"user text"]]]]]`；事件含一个 `tool_calls`、`finish_reason=1`、usage 7/4/11、最后 `done`；SSE `functionCall.args={"city":"Paris"}`、最终 `finishReason=STOP`、`totalTokenCount=11`；`[toolshape]` 不含 prompt/arg/response 明文。离线、确定性、无浏览器/账号/网络。
- Idle fallback：`TOOL_STREAM_IDLE_FINISH_S=2.0` 仅在 `has_tool_calls and latest_finish_reason is None` 时用于 `asyncio.wait_for(stream.__anext__(), ...)`；非工具流无 `wait_for`；超时 `break` 后在 `finally` 执行 `aclose()`；随后恰好 yield `finish_reason=1`、一条 `usage`、`done`。测试 monkeypatch 为 0.05s，fake upstream 在 functionCall 后 `await asyncio.Event().wait()`；断言 `finish_events == [1]`、usage 条数 1/total 11、`events[-1]==("done",None)`、`elapsed < 2.0`、`closed is True`。
- 定向验证：`uv run pytest -q tests/unit/test_pg_toolcall_harness.py tests/unit/test_stream_transport.py -k "preset_shape or idle_after_open or tool_stream_without_finish" -v` → **3 passed, 7 deselected in 0.40s**（本轮实际执行输出）。
- 全量门禁：`uv run pytest -q` → **2 failed, 549 passed, 1 skipped in 8.46s**；`uv run ruff check src tests` → **All checks passed!**；`uv run pyright src tests` → **0 errors, 0 warnings, 0 informations**；`git diff --check` / `git diff --cached --check` → **exit 0**（仅 CRLF 警告）。
- 状态：HEAD `05a9df7`，`git diff --cached --name-only` 为空；未 commit/stage；无 UI/frontend/build、无 OpenAI/Claude/Responses、无 D10、无真实网络；根 `config.yaml` 仍 untracked/ignored。
- 结论：Q-01/N-12 **RESOLVED**；Q-03 **RESOLVED**；无 blocker，verdict 维持 **PASS**。新 N 项 N-19..N-25 均为非阻断。
- 报告 SHA256（不含本最后一行）: - 报告 SHA256（不含本行）: d3f824d0fd16df2fccacd5202fb35c36682f3bd072e0387503e2fffe8d8676c6

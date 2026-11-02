# 协议适配与工具调用技术规格

- 状态：依据当前 `rebuild-upstream` 工作树源码整理，只读核对。
- 范围：`GenerationService` 的协议中立生成链、OpenAI Chat Completions 与原生 Gemini 的请求/响应转换、ATG（Anti-Truncation Gateway）五信号检测与状态机、PG-01..PG-04 门、PG-08 酒馆伪造工具历史兼容、`countTokens` 策略、`[toolshape]` 脱敏规则。
- 非范围：实例池/路由调度、保活、日志归档。这些见 `INSTANCE_POOL_AND_ROUTING_SPEC.md` 与 `KEEPALIVE_AND_LOGGING_SPEC.md`。
- 文中 `file:line` 均指本仓库路径。

---

## 目录

- [1. 分层与总链路](#1-分层与总链路)
- [2. 协议中立生成链](#2-协议中立生成链)
  - [2.1 `generate`（非流式）](#21-generate非流式)
  - [2.2 `stream_generation`（流式）](#22-stream_generation流式)
  - [2.3 空响应判定](#23-空响应判定)
  - [2.4 释放与取消](#24-释放与取消)
- [3. OpenAI Chat Completions ↔ Gemini 转换](#3-openai-chat-completions--gemini-转换)
  - [3.1 入口](#31-入口)
  - [3.2 请求转换总表](#32-请求转换总表)
  - [3.3 工具调用与工具结果（含 PG-08）](#33-工具调用与工具结果含-pg-08)
  - [3.4 工具声明与 tool_choice](#34-工具声明与-tool_choice)
  - [3.5 生成参数](#35-生成参数)
  - [3.6 图片](#36-图片)
  - [3.7 响应渲染（非流式）](#37-响应渲染非流式)
  - [3.8 响应渲染（流式）](#38-响应渲染流式)
  - [3.9 数据流示例](#39-数据流示例)
- [4. 原生 Gemini 请求归一化与渲染](#4-原生-gemini-请求归一化与渲染)
  - [4.1 `normalize_gemini_request`](#41-normalize_gemini_request)
  - [4.2 PG-01 签名兜底](#42-pg-01-签名兜底)
  - [4.3 PG-04 Struct 强制](#43-pg-04-struct-强制)
  - [4.4 PG-02 首事件前真实 HTTP 状态](#44-pg-02-首事件前真实-http-状态)
  - [4.5 PG-03 finish-only 与工具流终止](#45-pg-03-finish-only-与工具流终止)
  - [4.6 Gemini 非流式渲染](#46-gemini-非流式渲染)
  - [4.7 Gemini 流式渲染](#47-gemini-流式渲染)
- [5. ATG 五信号检测与状态机](#5-atg-五信号检测与状态机)
  - [5.1 组件与配置](#51-组件与配置)
  - [5.2 wrap 流程](#52-wrap-流程)
  - [5.3 `inject_gemini`](#53-inject_gemini)
  - [5.4 五信号检测](#54-五信号检测)
  - [5.5 ATG 状态机](#55-atg-状态机)
  - [5.6 非流式还原](#56-非流式还原)
  - [5.7 流式还原](#57-流式还原)
  - [5.8 参数修复](#58-参数修复)
- [6. PG-01..PG-04 门](#6-pg-01pg-04-门)
  - [6.1 PG-01：历史 model functionCall 缺签名](#61-pg-01历史-model-functioncall-缺签名)
  - [6.2 PG-02：首事件前不得伪装成 200 SSE](#62-pg-02首事件前不得伪装成-200-sse)
  - [6.3 PG-03：finish-only 与工具流终止](#63-pg-03finish-only-与工具流终止)
  - [6.4 PG-04：工具载荷必须 Struct](#64-pg-04工具载荷必须-struct)
- [7. PG-08：酒馆伪造工具历史兼容](#7-pg-08酒馆伪造工具历史兼容)
  - [7.1 典型酒馆形态](#71-典型酒馆形态)
  - [7.2 兼容策略](#72-兼容策略)
  - [7.3 与原生 Gemini 的差异](#73-与原生-gemini-的差异)
  - [7.4 残余限制](#74-残余限制)
- [8. `countTokens` 策略](#8-counttokens-策略)
  - [8.1 入口](#81-入口)
  - [8.2 打包策略](#82-打包策略)
  - [8.3 上游调用](#83-上游调用)
  - [8.4 不触碰的路径](#84-不触碰的路径)
  - [8.5 数据流示例](#85-数据流示例)
  - [8.6 残余限制](#86-残余限制)
- [9. `[toolshape]` 脱敏规则](#9-toolshape-脱敏规则)
  - [9.1 触发](#91-触发)
  - [9.2 摘要内容](#92-摘要内容)
  - [9.3 明确不记录](#93-明确不记录)
  - [9.4 残余风险](#94-残余风险)
- [10. 残余限制汇总](#10-残余限制汇总)
- [11. 证据索引](#11-证据索引)

---

## 1. 分层与总链路

协议面只负责“把某种客户端协议翻译成 Gemini 请求，再把中立输出渲染回该协议”；路由、重试、ATG、签名、空响应重试都在协议中立的 `generation_service` 中完成。

```text
Gemini route  ─┐
OpenAI route  ─┼─> GenerationService ─> RoutingPipeline ─> InstanceRequestClient ─> PooledSession/上游
Claude/Responses（未启用）─┘        │
                                     └─ ATG wrap/unwrap、空响应重试、签名记录
```

证据：

- `src/aistudio_api/application/generation_service.py:1-11`：模块职责与“无 lease/reservation”说明。
- `src/aistudio_api/api/gemini_service.py:1-7`：Gemini 只做 wire 渲染与错误翻译。
- `src/aistudio_api/api/routes_gemini.py:21-61`：Gemini 入口。
- `src/aistudio_api/api/routes_openai.py:79-158`：OpenAI 入口。
- `defaults/config.yaml:57-64`：`formats.openai=true`，`claude/responses=false`。

---

## 2. 协议中立生成链

### 2.1 `generate`（非流式）

`generation_service.generate`（`generation_service.py:151-285`）的步骤：

1. `pipeline.begin(model_path=...)` 得到 `RouteContext`，取 `route.model` 与 `route.surface`（`:160-162`）。
2. 根据 `api_format` 解析 ATG 模式并构造 `AtgProcessor`（`:164-167`, `:82-90`）。
3. `_wrap_atg_request` 可能注入合成工具并返回 `tool_name`（`:165-167`, `:93-130`）。
4. 循环 `acquire` -> `normalize_gemini_request` -> `client.generate_content`（`:174-217`）。
5. 空响应重试：`max_empty = atg.cfg.empty_retries if tool_name else 1`，最多 `max_empty + 1` 次（`:187-232`）。
6. 若 ATG 启用且有 `tool_name`，调用 `atg.unwrap_response` 把合成调用还原为正文/真实工具调用（`:235-242`）。
7. `record_function_call_signatures` 记录真实签名（`:244`）。
8. `_finish_reason` 映射为 Gemini finish 字符串（`:245`, `:133-139`）。
9. `pipeline.record_success`，返回 `GenerationOutcome`（`:246-264`）。
10. 异常统一交给 `pipeline.handle_error`；`retry_same` 继续同一 attempt，`switch` 释放并置空 attempt，其他上抛（`:265-276`）。
11. `finally` 释放 attempt（`:277-279`）。

`GenerationOutcome` 字段：`output/model/finish_reason/response_id/surface`（`:59-66`）。

### 2.2 `stream_generation`（流式）

`generation_service.stream_generation`（`:298-506`）的步骤：

1. `pipeline.begin`、ATG 构造与 wrap 同非流式（`:310-317`）。
2. 每次新 attempt 发送一个 `routing_wait` 事件（`:331-339`），OpenAI 渲染为 SSE 注释，Gemini 渲染为空（见 4.3/4.4）。
3. `normalize_gemini_request` 每次 attempt 调用一次；`emit_toolshape` 只在第一次 normalize 时为 True（`:322-323`, `:346-353`）。
4. 创建 per-stream ATG unwrapper（`:354-358`）。
5. 迭代 `client.stream_generate_content`（`:366-384`）：
   - `usage` -> `final_usage`
   - `finish_reason` -> `final_finish_reason`，若 ATG 合成调用且无真实工具调用则把 `TOOL_CALLS` 改为 `STOP`
   - `response_id` -> `latest_response_id`
   - `thought_signature` -> `buffered_thought_sig`
   - 其他事件：`tool_calls` 记录签名；ATG unwrapper 处理；yield `StreamEvent`；非空 payload 置 `has_yielded_data=True`（`:385-423`）。
6. 空流重试：`max_empty` 同非流式；`empty_attempt` 计数；仍空且 required -> `SyntheticRequiredError`（`:440-457`）。
7. `unwrapper.finalize()` flush 缓冲/尾块（`:459-475`）。
8. `record_success`，break（`:476-488`）。
9. `finally` 释放 attempt（`:489-491`）。
10. 循环结束后 yield 终止 `usage` 事件，携带 `finish_reason`/`response_id`（`:500-506`）。

`StreamEvent` 字段：`kind/payload/model/response_id/thought_signature/finish_reason`（`:47-57`）。

### 2.3 空响应判定

`_output_is_empty`（`:68-79`）：没有 candidates，或第一个 candidate 的 `text/thinking/function_calls/images/reasoning_images` 全为空，即视为空。

注意：非流式在 `tool_name` 为空时 `max_empty=1`，最多尝试 2 次；若仍为空，代码会继续走 unwrap/成功路径并返回空输出（`:217-233`）。流式在同样情况下若 `produced_content=False` 且 required 未触发，会 flush/记录成功（`:440-457`）。这是“空响应不总是错误”的当前行为。

### 2.4 释放与取消

- 非流式 `finally` 释放 attempt（`:277-279`）。
- 流式 `finally` 释放 attempt（`:489-491`）。
- `pipeline.release` 只调用 `handle.release()`（`routing_pipeline.py:586-587`），即 `ActiveRequest.release()`（`instance_pool.py:131-135`），保证 `active_requests` 归零。

---

## 3. OpenAI Chat Completions ↔ Gemini 转换

### 3.1 入口

`routes_openai.chat_completions`（`routes_openai.py:79-158`）：

1. 解析 JSON -> `OpenAIChatRequest`（`:84-94`）。
2. `MediaFetcher` + `convert_openai_request` -> Gemini body（`:96-99`）。
3. `GeminiGenerateContentRequest.model_validate(body)`（`:99`）。
4. `model_path = strip_models_prefix(payload.model)`（`:100`）。
5. 非流式 -> `generate(api_format="openai")` -> `openai_response`（`:102-109`）。
6. 流式 -> `stream_generation(api_format="openai")`，预读首个非 `routing_wait` 事件，失败返回真实 JSON 错误；成功则 `OpenAIStreamRenderer` 逐事件渲染（`:111-156`）。

### 3.2 请求转换总表

`convert_openai_request`（`format_converters/openai_request.py:441-532`）：

| OpenAI 输入 | Gemini 输出 | 证据 |
|---|---|---|
| `n != 1` | `ConversionError` | `:445-446` |
| `system` / `developer` | 聚合到 `systemInstruction.parts[0].text`，多条以 `\n` 连接 | `:467-471`, `:497-499` |
| `user` 文本/图片 | `contents[].role="user"`，文本 -> `{"text": ...}`，图片 -> `inlineData` | `:480-485`, `:176-214` |
| `assistant` 文本 | `contents[].role="model"` | `:487-490` |
| `assistant.tool_calls` | `functionCall` parts + `thoughtSignature` | `:217-269` |
| `tool` 消息 | `contents[].role="user"` 的 `functionResponse` | `:473-478`, `:290-323` |
| 连续同 role | 合并到上一个 content 的 parts | `:452-459` |
| `tools[].function` | `tools[0].functionDeclarations[]` | `:501-522` |
| 顶层 `functions` | 追加到同一 declarations 列表 | `:516-519` |
| `tool_choice` / `function_call` | `toolConfig.functionCallingConfig` | `:351-378`, `:524-526` |
| `generationConfig` | `temperature/topP/maxOutputTokens/stopSequences/thinkingConfig/responseMimeType/responseSchema` 等 | `:381-438`, `:528-530` |

### 3.3 工具调用与工具结果（含 PG-08）

`_ToolHistory`（`openai_request.py:27-49`）维护：

- `names`：call_id -> 工具名。
- `pending`：尚未匹配结果的调用 `(call_id, name)`。
- `used_ids`：已使用的 call_id。
- `synthetic_seq`：孤立工具输出的合成序号。

`_tool_call_parts`（`:217-269`）：

- `tool_calls` 不是 list -> warning 并忽略（`:226-228`）。
- 单个 call 不是 dict -> warning 并跳过（`:233-235`）。
- `function` 不是 dict -> warning 并跳过（`:236-239`）。
- `function.name` 缺失/空 -> 使用 `tool_output`（`:240-243`, `:24`）。
- `arguments` 由 `_lenient_arguments` 解码（`:244-247`, `:64-86`）：
  - dict 原样；
  - None -> `{}`；
  - 空字符串 -> `{}`；
  - JSON object -> dict；
  - JSON 标量/数组 -> `{"content": parsed}`；
  - 非 JSON 文本 -> `{"content": raw}`。
- `id` 缺失/重复 -> `call_auto_{message_index}_{call_index}`，重复时追加 `_1`、`_2`（`:249-258`, `:42-49`）。
- 签名：`signature_cache.get(call_id)`，缺失用 `DUMMY_THOUGHT_SIGNATURE`（`:259-267`）。

`_tool_result_parts`（`:290-323`）：

- `tool_call_id` 优先精确匹配 `history.names`。
- 没有 id 但有 `name` -> 匹配 `pending` 中同名调用。
- 仍没有 -> 取 `pending` 最后一个。
- 仍没有 -> 视为孤立工具输出：名称用声明名或 `tool_output`，id 用 `call_output_{index}_{seq}`（`:307-316`）。
- `content` 由 `_tool_response` 处理（`:89-128`）：
  - None -> `{}`；
  - 字符串/文本 parts -> 拼接；
  - JSON object/array -> 原样；
  - 其他 -> `{"result": value}`。

这些行为就是 PG-08 的“宽松工具历史”兼容：不因缺失 id、重复 id、孤立工具输出、非法 JSON 参数而 400，而是合成合法 Gemini 工具历史。日志前缀为 `[pg08]`（`:52-61`）。

### 3.4 工具声明与 tool_choice

`_function_declaration`（`:326-348`）：

- `name` 必填。
- `description` 可选。
- `parameters` 或 `parametersJsonSchema` -> `parametersJsonSchema`。

`_function_calling_config`（`:351-378`）：

- 字符串：`auto -> AUTO`、`none -> NONE`、`required -> ANY`。
- 对象：`type=auto/none` -> AUTO/NONE；`type=required` -> ANY。
- `function.name` 或对象 `name` -> `{"mode":"ANY","allowedFunctionNames":[name]}`。
- 其他 -> `ConversionError`。

### 3.5 生成参数

`_generation_config`（`:381-438`）：

- `max_completion_tokens` 优先于 `max_tokens` -> `maxOutputTokens`。
- `temperature/top_p/top_k` -> `temperature/topP/topK`。
- `stop` 字符串/数组 -> `stopSequences`。
- `reasoning_effort` 只接受 `MINIMAL/LOW/MEDIUM/HIGH` -> `thinkingConfig.thinkingLevel`。
- `response_format.type=json_schema` -> `responseMimeType=application/json` + `responseSchema`。
- `json_object` -> 空 object schema。
- `text` -> 不设置。
- 其他 -> `ConversionError`。

### 3.6 图片

`_image_part`（`:155-173`）：

- 支持 `image_url` 字符串或 `image_url.url` 对象。
- `MediaFetcher.to_data_url` 拉取/转换 URL。
- `decode_data_url` 拆出 mime/base64。
- 输出 `{"inlineData":{"mimeType":..., "data":...}}`。

`features.formats.media.allow_private_urls` 控制私有地址是否允许（`defaults/config.yaml:59-64`），由 `format_media_allow_private()` 读取（`routes_openai.py:97`）。

### 3.7 响应渲染（非流式）

`openai_response`（`format_converters/openai_response.py:64-99`）：

- `text = output.text`，`images = _image_text(output.images)`，两者都有时用换行连接（`:66-68`）。
- `message.role="assistant"`。
- `output.thinking` -> `message.reasoning_content`。
- `output.function_calls` -> `message.tool_calls`，每项 `{index,id,type:"function",function:{name,arguments}}`（`:74-79`, `:45-61`）。
- `finish_reason`：有工具调用 -> `tool_calls`；否则 `map_finish_reason_openai(outcome.finish_reason)`（`:81-85`）。
- `id`：`outcome.response_id` 或新 `chatcmpl-`。
- `usage`：`to_openai_usage(output.usage)`。

`_tool_call`（`:45-61`）：

- `id` 缺失 -> `new_id("call_")`。
- `thought_signature` 存在 -> 写入 `signature_cache`。
- `arguments` 为字符串则原样，否则 `json.dumps`。

### 3.8 响应渲染（流式）

`OpenAIStreamRenderer`（`:102-221`）：

| StreamEvent.kind | 输出 | 证据 |
|---|---|---|
| `routing_wait` | SSE 注释 `: resolving-instance\n\n` | `:163-164` |
| `body` | `delta.content` | `:179-181` |
| `thinking` | `delta.reasoning_content` | `:183-185` |
| `images` | `delta.content`（Markdown 图片） | `:187-188`, `:25-33` |
| `reasoning_images` | `delta.reasoning_content`（Markdown 图片） | `:190-191` |
| `tool_calls` | `delta.tool_calls[]`，递增 index | `:193-213` |
| `usage` | 终止 chunk + `data: [DONE]` | `:166-177`, `:147-154` |
| 其他 | 空 | `:215` |

首个内容 delta 会带 `role:"assistant"`（`:138-145`, `:209-211`）。`close()` 在未收到 usage 时补一个终止 chunk（`:217-221`）。

### 3.9 数据流示例

**OpenAI 文本请求**

```text
POST /v1/chat/completions
{"model":"gemini-3.7-flash","messages":[{"role":"system","content":"S"},{"role":"user","content":"U"}]}
  -> convert_openai_request
     contents=[{role:"user",parts:[{text:"U"}]}]
     systemInstruction.parts=[{text:"S"}]
  -> generation_service.generate(api_format="openai")
  -> upstream
  -> openai_response: choices[0].message.content = 上游文本
```

**OpenAI 工具请求**

```text
messages:
  user: "weather?"
  assistant: tool_calls=[{id:"call_1",function:{name:"get_weather",arguments:"{\"city\":\"Paris\"}"}}]
  tool: {tool_call_id:"call_1", content:"{\"temp\":20}"}

convert:
  contents=[
    {role:"user",parts:[{text:"weather?"}]},
    {role:"model",parts:[
      {functionCall:{name:"get_weather",args:{city:"Paris"},id:"call_1"},
       thoughtSignature:<cache 或 dummy>}
    ]},
    {role:"user",parts:[
      {functionResponse:{name:"get_weather",response:{temp:20},id:"call_1"}}
    ]}
  ]
```

**原生 Gemini 工具请求**

```text
contents:
  user text
  model functionCall (args 可能是 dict/string/scalar)
  user functionResponse (response 可能是 dict/string/list/scalar)

normalize_gemini_request:
  functionCall.args -> coerce_function_call_args -> Struct
  functionResponse.response -> coerce_function_response -> Struct
  model-role functionCall 缺签名 -> signature_cache 或 DUMMY_THOUGHT_SIGNATURE
```

---

## 4. 原生 Gemini 请求归一化与渲染

### 4.1 `normalize_gemini_request`

`chat_service.normalize_gemini_request`（`application/chat_service.py:737-1061`）：

- 必须有 `contents`（`:746-747`）。
- 模型名统一为 `models/<clean>`（`:749-753`）。
- 逐个 content 处理 parts（`:758-820`）：
  - `text` -> `AistudioPart(text, thought, thought_signature)`（`:765-775`）。
  - `inlineData` -> `AistudioPart(inline_data, thought_signature)`（`:776-785`）。
  - `functionCall` -> `coerce_function_call_args(fc.args)`；签名走 `_historical_function_call_signature(role, part)`（`:786-799`）。
  - `functionResponse` -> `coerce_function_response(fr.response)`；`fr.parts` 的文本/图片拆成同级 part（`:800-820`）。
- 工具声明通过 `_TOOL_SCHEMA_KEYS` 识别 schema 别名（`chat_service.py:37-43`），编码为 wire 声明。
- `toolConfig`、safety、generationConfig 归一化（`:900-1032` 附近）。
- 若 `emit_toolshape` 且请求有工具活动，输出 `[toolshape]`（`:1033-1040`）。

### 4.2 PG-01 签名兜底

`_historical_function_call_signature`（`chat_service.py:720-734`）：

```text
if role != "model" or part.functionCall is None:
    return part.thoughtSignature          # 不注入
if part.thoughtSignature:
    return part.thoughtSignature          # 显式优先
call_id = part.functionCall.id
cached = signature_cache.get(call_id) if call_id else None
return cached or DUMMY_THOUGHT_SIGNATURE
```

- 只对 `model` role 的 `functionCall` 注入；`functionResponse` 与纯文本不注入（`:728-734`）。
- `DUMMY_THOUGHT_SIGNATURE = "context_engineering_is_the_way_to_go"`（`signature_cache.py:27`）。
- 缓存读写：`get` 不消费，TTL 30 天，最多 4096 条（`signature_cache.py:29-83`）。
- 真实签名生产者：
  - 非流式：`generation_service.generate` 调 `record_function_call_signatures(output.function_calls)`（`generation_service.py:244`）。
  - 流式：`stream_generation` 在 `tool_calls` 事件上调 `record_function_call_signatures(text)`（`:400-402`）。
  - OpenAI 响应：`_tool_call` 把 `thought_signature` 写入缓存（`openai_response.py:49-51`）。

### 4.3 PG-04 Struct 强制

`wire_types.py:325-372`：

| 输入 | `coerce_function_call_args` | `coerce_function_response` |
|---|---|---|
| `None` | `{}` | `{}` |
| `dict` | 原样 | 原样 |
| JSON object 字符串 | 解码为 dict | 解码为 dict |
| 普通字符串 | `{"content": str}` | `{"result": str}` |
| list/标量 | `{"content": value}` | `{"result": value}` |

调用点在 `normalize_gemini_request` 的 functionCall/functionResponse 分支（`chat_service.py:786-809`），因此在写 wire 之前已经保证 Struct 形状。

wire 写入位置：

- `FUNCTION_CALL_ALT = 10`（`wire_spec.py:40`）。
- `FUNCTION_RESPONSE_ALT = 11`（`wire_spec.py:41`）。
- `THOUGHT_SIGNATURE = 14`（`wire_spec.py:43`）。
- `AistudioPart.to_wire` 在 `wire_types.py:271-301` 写入这些下标。

### 4.4 PG-02 首事件前真实 HTTP 状态

`_build_gemini_streaming_response`（`api/gemini_service.py::_build_gemini_streaming_response`）：

1. 创建 `stream_generation` 异步迭代器。
2. 在构造 `StreamingResponse` 之前循环 `__anext__()`，直到渲染出非空首个 chunk。
3. 若迭代器在首 chunk 前结束 -> `RequestError(502, "generation produced no stream events")`。
4. `BrowserTransportError` -> `RequestError(502)`；`AtgError` -> `RequestError(502)`。
5. 其他异常原样抛出。
6. 首个 chunk 已确定后才返回 `StreamingResponse`。
7. 首 chunk 之后的异常在 `body()` 中 yield `format_sse_error(exc)`。

因此 PG-01/PG-04 等“首事件前失败”会走真实 HTTP 4xx/5xx，而不是 200 + SSE error。

OpenAI 路由采用同样的预读模式（`routes_openai.py:111-135`），窗内空流同样返回 502 `generation produced no stream events`；`routing_wait` 只渲染为进度注释，不计入首个模型内容帧。

**首窗与保活环境变量**（`api/sse_keepalive.py`）：

- `AISTUDIO_SSE_KEEPALIVE_SEC`：正文等待上游事件时发送 `: keepalive\n\n` 注释帧的间隔，默认 15 秒；非数字、`NaN`/`inf`、0 或负值回退 15 秒。它与“是否返回响应头”无关，只控制 200 之后的保活节奏。
- `AISTUDIO_SSE_PREREAD_WINDOW_SEC`：返回 `StreamingResponse` 前的首事件预读窗口。未设置或非法时跟随 `AISTUDIO_SSE_KEEPALIVE_SEC`（默认 15 秒）；独立设置时以自身为准，且必须为有限正数。
- 本实现**没有整体生成/空闲总超时**：上游若永久挂起，200 后的 body 会持续发送 keepalive 而不是主动终止。连接释放依赖客户端断开/CDP WebSocket 断开，浏览器命令超时由命令层控制。不要把首窗或 keepalive 间隔理解成服务端总超时。

### 4.5 PG-03 finish-only 与工具流终止

`wire_parser.parse_response_chunk`（`wire_parser.py:437-541`）：

- `candidates_container` 为空 -> 返回空 Candidate（`:446-448`）。
- 否则读取 candidate 的 finish/message（`:523-534`）。
- usage 由 `parse_chunk_usage` 从 chunk 的 usage 下标读取（`:429-434`）。

`StreamingGateway.stream_chat`（`streaming.py:84-249`）：

- `TOOL_STREAM_IDLE_FINISH_S = 2.0`（`:34-37`）。
- `idle_bounded = has_tool_calls and latest_finish_reason is None`（`:118`）。
- 只有 `idle_bounded` 时对 `stream.__anext__()` 使用 `asyncio.wait_for(..., 2.0)`（`:119-125`）。
- 超时且 `idle_bounded` -> 记录日志并 break；非工具流超时原样抛出（`:128-136`）。
- finish reason 为 0 或 `CONTINUING=22` 时不记录终止（`:162-168`），继续消费。
- 工具调用事件置 `has_tool_calls=True` 并 yield（`:173-175`）。
- 流结束/空闲后，若仍无 finish 且有工具调用 -> 合成 `STOP`（`:239-243`）。
- 最终恰好 yield 一个 `finish_reason`、一个 `usage`、一个 `done`（`:244-249`）。
- 没有任何 chunk -> `BrowserTransportError`（`:235-238`）。
- `status_code != 200` -> `classify_error` 或 `RequestError`（`:223-234`）。

### 4.6 Gemini 非流式渲染

`handle_gemini_generate_content`（`api/gemini_service.py::handle_gemini_generate_content`）：

- `AtgError` -> `RequestError(502)`。
- 输出 parts 由 `to_gemini_parts(text, function_calls, function_responses, thinking, images, reasoning_images)` 生成。
- `finishReason=outcome.finish_reason`。
- `usageMetadata=to_gemini_usage_metadata(output.usage)`。
- `modelVersion=outcome.model`，`responseId=outcome.response_id`。

finish 映射 `WIRE_TO_GEMINI_FINISH_REASON`（`application/finish_reasons.py::WIRE_TO_GEMINI_FINISH_REASON`）。

### 4.7 Gemini 流式渲染

`format_sse_event`（`api/gemini_service.py::format_sse_event`）：

| 事件 | parts |
|---|---|
| `body` | `{text}`，可选 `thoughtSignature` |
| `thinking` | `{text, thought:true}`，可选 `thoughtSignature` |
| `tool_calls` | `functionCall` parts，可选首个 part 的 `thoughtSignature` |
| `images` | inlineData parts |
| `reasoning_images` | inlineData parts（thought 图） |

`format_sse_usage`（`:239-269`）输出终止 chunk + `usageMetadata`。`format_sse_error`（`:272-286`）输出 `data: {"error": ...}`，`TierRequiredError`/`RoutingError` 会合并 `as_fields()`。

---

## 5. ATG 五信号检测与状态机

### 5.1 组件与配置

`AtgProcessor`（`atg/__init__.py:38-90`）：

- `enabled = cfg.mode != "off"`。
- `wrap`：先 `purge_synthetic_history`，再 `inject_gemini`。
- `unwrap_response`：非流式还原。
- `create_stream_unwrapper`：流式还原。
- `create_atg_processor` 每次构造都重新读取 features，支持热重载。

`AtgConfig`（`atg/config.py:92-110`）：

| 字段 | 默认 | 作用 |
|---|---|---|
| `mode` | `off` | `off/prefer/required` |
| `tool_prefix` | `atg_emit_` | 合成工具名前缀 |
| `control_position` | `tail` | 控制消息插入位置 |
| `strict` | `false` | 声明加 `additionalProperties:false` |
| `empty_retries` | `2` | 空响应重试次数（0-5） |
| `recovery` | `repair` | 参数解码失败时修复或失败 |
| `side_buffer_bytes` | `0` | 流式正文缓冲阈值 |
| `client_synthetic_guard` | `true` | 客户端已有合成通道时跳过本地 wrap |
| `max_response_bytes` | 16 MiB | 参数/响应上限 |
| `model_filter` | 默认文本过滤器 | 非文本模型直通 |

`resolve_atg_mode(api_format)`（`config.py:43-58`）：`features.atg.mode_by_format.<format>` 显式指定 `off/prefer/required` 时优先，否则继承 `features.atg.mode`。默认配置 `defaults/config.yaml:69-85` 为 `mode: off`，四个 format 均为 `inherit`。

### 5.2 wrap 流程

`AtgProcessor.wrap`（`atg/__init__.py:52-73`）：

```text
if not enabled or body 不是 dict: 原样返回
purge_synthetic_history(contents, tool_prefix)
inject_gemini(body, model_name)
```

`purge_synthetic_history`（`atg/history_purge.py:15-51`）：

- 删除 `functionCall.name` 或 `functionResponse.name` 以 `tool_prefix` 开头的 part。
- 如果某个 content 删除后没有 parts，则整个 content 丢弃（Gemini 不接受空 parts）。

### 5.3 `inject_gemini`

`atg/injector.py:104-216`：

1. `mode=="off"` -> 原样返回（`:109-110`）。
2. 模型名：`body["model"]` 优先，否则 `model_hint`（`:112-119`）。
3. 非文本模型 -> 直通（`:121-123`）。
4. 客户端合成守卫：
   - `client_synthetic_guard` 不为 False 时调用 `detect_client_synthetic_transport`。
   - `detected.hit` -> 直接返回，不注入本地合成工具（`:129-140`）。
   - 未命中但有声明工具 -> 记录 `client_tools`（`:140`）。
5. 生成 nonce 工具名 `prefix + token_hex(12)`（`:142-143`, `:46-51`）。
6. 追加合成声明：
   - `name` = nonce。
   - `parameters` = `{type:OBJECT, properties:{content:{type:STRING}}, required:["content"]}`。
   - `strict` 时加 `additionalProperties:false`（`:54-71`, `:145-163`）。
7. `toolConfig` 处理（`:165-199`）：
   - 默认 `AUTO`。
   - 客户端 `mode=NONE` -> `AUTO`。
   - 客户端 `mode=ANY` 且无 `allowedFunctionNames` -> `ANY`。
   - 客户端 `mode=ANY` 且有 `allowedFunctionNames` -> 把合成工具名合并进白名单（E5 修正），因为 wire 层会按白名单过滤声明。
   - 其他显式 mode 保留。
   - 若最终没有白名单，写 `toolConfig.functionCallingConfig.mode`。
8. 控制消息：Gemini 无 system/developer role，统一用 `user`；`control_position=head` 插到最前，否则追加到末尾（`:100-102`, `:201-209`）。
   - 这同时修复“以 model turn 结尾”的 400（`:5-8`）。

控制消息模板（`:29-34`）要求模型只通过合成工具返回完整答案。

### 5.4 五信号检测

`client_patterns.py` 的文件头列出五个信号（`:9-18`），实现如下：

| # | 信号 | 实现 | 证据 |
|---|---|---|---|
| 1 | 客户端声明了本地合成前缀 `atg_emit_*` | 遍历 declarations，`name.startswith(prefix)` -> hit `prefixed-name` | `:135-139` |
| 2 | 声明描述含 answer/response 意图 + content-like STRING 参数 | `_declaration_shape` + `DECLARATION_DESC_PATTERN` -> hit `declaration-intent` | `:150-157`, `:91-117` |
| 3 | 被 `allowedFunctionNames` 强制钉住且带 content-like 形状 | `content_param and name_hit and forced_hit` -> hit `forced-content-tool` | `:141-159` |
| 4 | 历史证据：同名工具已被调用并在 contents 中有 functionResponse | 收集 `functionCall.name` / `functionResponse.name`，同名声明的 content 参数 + name 意图 -> hit `history-loop` | `:161-190` |
| 5 | 控制指令措辞（systemInstruction 或最近文本 parts） | `INSTRUCTION_PATTERN` 扫描最多 60 parts / 4000 字符 -> hit `instruction-pattern` | `:192-221` |

其他：

- `CONTENT_PARAM_NAMES` 包含 `answer/body/content/final/message/output/reply/response/text`（`:26-38`）。
- `_declaration_shape` 依次读取 `parameters`、`parametersJsonSchema`、`parameters_json_schema`、`input_schema`、`parameters_schema`（`:91-111`），因此 OpenAI 转换器产出的 `parametersJsonSchema` 也能被识别。
- 没有任何声明 -> 返回 `None`；有声明但未命中 -> `Detection(hit=False, reason="declared-tools-present", tools=[...])`（`:223-227`）。

### 5.5 ATG 状态机

```text
mode = off
  └─ wrap 原样返回；unwrap 原样返回；required 不生效

mode = prefer
  ├─ wrap: 注入合成工具 + 控制消息
  ├─ 上游返回合成调用 -> 还原为正文；真实工具调用保留
  ├─ 上游返回纯文本 -> 正常返回（不强制合成）
  └─ 空响应 -> 按 empty_retries 重试；仍空则允许空输出

mode = required
  ├─ wrap 同 prefer
  ├─ 非流式：无合成调用且无真实工具调用 -> SyntheticRequiredError（502）
  └─ 流式：finalize 时同样检查 -> SyntheticRequiredError（502）

guard 命中（客户端已有合成通道）
  └─ 跳过本地 wrap；tool_name=""；不参与 unwrap/required 判定

模型非文本（image/audio/embedding...）
  └─ 跳过 wrap；tool_name=""
```

证据：

- `AtgConfig.enabled`（`config.py:107-110`）。
- `_wrap_atg_request` 的跳过分支（`generation_service.py:100-130`）。
- 非流式 required 检查（`extractor.py:188-195`）。
- 流式 required 检查（`stream_extractor.py:158-165`）。

### 5.6 非流式还原

`Extractor.normalize_non_streaming`（`extractor.py:141-197`）：

- 遍历 candidates。
- 若存在合成调用且同时有普通文本 -> `stats.conflict=True`（`:152-159`）。
- 合成调用：
  - 计数 `synthetic_count`。
  - `extract_content_from_args` 取 `content`（`:95-128`）。
  - 从 `function_calls` 移除。
- 真实工具调用保留并计数。
- 若有合成调用 -> `candidate.text` 替换为合成内容（`:176-180`）。
- 若 `finish_reason == "TOOL_CALLS"` 且无真实工具调用 -> 改为 `STOP_WIRE_CODE=1`（`:181-186`）。
  - 注意：当前 wire finish 是 int，`_is_tool_calls_finish` 只比较字符串 `"TOOL_CALLS"`（`:131-132`），因此该重写在真实 int finish 上不会触发。这是残余限制。
- required 且无合成、无真实工具调用 -> `SyntheticRequiredError`（`:188-195`）。

`extract_content_from_args`：

- dict：必须有字符串 `content`，否则 `SyntheticArgumentsError`（`:102-113`）。
- 字符串：`recovery=="fail"` -> 抛错；否则 `repair_synthetic_arguments`，成功则 `stats.repaired=True`（`:115-126`）。
- 其他 -> `SyntheticArgumentsError`（`:128`）。

### 5.7 流式还原

`StreamUnwrapper`（`stream_extractor.py:38-167`）：

- `side_buffer_bytes == 0`：`body` 事件立即透传；若之后出现合成调用，`_flushed_plain_text` 置 conflict（`:121-126`, `:89-90`）。
- `side_buffer_bytes > 0`：`body` 事件缓存，直到 `finalize`；若无合成命中则 flush；若合成命中则丢弃缓存（`:121-124`, `:152-156`）。
- `tool_calls`：
  - 合成名 -> `synthetic_hit=True`，累加 content，丢弃调用。
  - 真实名 -> `real_tool_count += 1`，保留。
  - 无名续块且已命中合成 -> 作为增量 content 继续累加。
  - 否则保留（`:70-115`）。
- 合成参数的字符串形式使用 `IncrementalJSONStringDecoder` 跨 chunk 解码（`:58-68`, `stream_decoder.py:136-375`）。
- `finalize`：
  - 先 flush 增量解码器尾块。
  - 再 flush 侧缓冲（仅无合成命中时）。
  - required 且无合成/真实调用 -> 抛错（`:133-167`）。
- `should_remap_finish_reason`：合成命中且无真实工具调用（`:54-56`）；`generation_service` 用它把 `TOOL_CALLS` 改为 `STOP`（`generation_service.py:389-394`）。

### 5.8 参数修复

`repair_synthetic_arguments`（`atg/repair.py:189-226`）：

1. 超过 `max_bytes` -> 错误。
2. 空 -> 错误。
3. 标准 JSON 解析 `{"content":"..."}` -> 成功。
4. 去 Markdown code fence 后重试。
5. 结构修复：未闭合字符串、尾逗号、不平衡括号（`:49-117`）。
6. 有界扫描 content-ish 键（`:120-186`）。
7. 仍失败 -> 错误。

`ARGUMENT_KEYS = content/text/answer/response/result/output/message`（`:17`）。

---

## 6. PG-01..PG-04 门

这里的“门”指 B5a 验收中要求代码/测试证明的四类保护。当前实现如下。

### 6.1 PG-01：历史 model functionCall 缺签名

**保护**：`model` role 的 `functionCall` 若无 `thoughtSignature`，先查 `signature_cache`，再回退 `DUMMY_THOUGHT_SIGNATURE`；纯文本与 `functionResponse` 不注入。

**代码**：`chat_service.py:720-734`, `:786-799`；`signature_cache.py:27`, `:56-83`。

**wire 证据**：签名写下标 14，functionCall 写下标 10（`wire_types.py:271-301`, `wire_spec.py:40-43`）。

**边界**：

- 只有 `role=="model"` 且 `part.functionCall` 非空才注入（`:728-734`）。
- 缓存未命中时使用固定占位串，不是真实签名；上游是否接受由上游决定。
- 真实签名的生产者依赖上游返回 `thought_signature` 并经过 `record_function_call_signatures`（`generation_service.py:244`, `:400-402`）。

### 6.2 PG-02：首事件前不得伪装成 200 SSE

**保护**：Gemini 流式入口在返回 `StreamingResponse` 之前预读首个可渲染事件；首事件前失败 -> 真实 HTTP 状态；首事件后失败 -> SSE error。

**代码**：`api/gemini_service.py::_build_gemini_streaming_response`；OpenAI 对应 `api/routes_openai.py::chat_completions`。

**状态映射**：

- `BrowserTransportError` -> 502（`api/gemini_service.py::_build_gemini_streaming_response`）。
- `AtgError` -> 502（同上）。
- 空流 -> 502（同上）。
- 其他异常由 FastAPI/`error_mapping` 映射（`api/error_mapping.py:56-136`）。

**边界**：

- 首事件之后的错误仍然是 `data: {"error": ...}`，HTTP 已是 200（`:344-354`）。这是 SSE 协议的固有限制。
- 预读循环会跳过渲染为 `None` 的事件（例如 `routing_wait`），因此“首事件”指首个可渲染 chunk，而不是第一个内部事件。

### 6.3 PG-03：finish-only 与工具流终止

**保护**：

- parser 对带 candidate finish 的 finish-only 帧保留 finish/usage（`wire_parser.py:437-541`, `:429-434`）。
- 工具流在已产生 `tool_calls` 且未见 finish 时，使用 2 秒逐事件空闲窗口；超时合成 `STOP`（`streaming.py:34-37`, `:113-136`, `:239-243`）。
- 无论正常结束还是空闲结束，最终都输出 `finish_reason` + `usage` + `done`（`:244-249`）。

**边界**：

- 空闲窗口是逐事件间隔，不是总时长；上游若每 <2s 发一个非 finish 事件，仍可能长期不结束（`streaming.py:118-125`）。
- `CONTINUING=22` 不终止（`:162-168`），但若之后 2 秒无事件，仍会合成 `STOP`。
- 非工具流没有 2 秒窗口，保持无限等待（`:119-125`）。
- `candidates_container` 为空时 parser 返回空 Candidate（`wire_parser.py:446-448`）；WIRE 语义里空 candidates 即无 finish。

### 6.4 PG-04：工具载荷必须 Struct

**保护**：

- `functionCall.args` -> `coerce_function_call_args`。
- `functionResponse.response` -> `coerce_function_response`。
- `None -> {}`；JSON object 字符串解码；其他包 `content` / `result`。
- 在写 wire 之前完成（`chat_service.py:786-809`）。

**代码**：`wire_types.py:325-372`。

**边界**：

- `dict` 原样返回，不复制；调用方不应原地修改（当前没有原地修改）。
- list 作为 `functionCall.args` 会被包成 `{"content": list}`；list 作为 `functionResponse.response` 会被包成 `{"result": list}`。
- 工具名缺失/为空的 `functionCall` 仍然会写 wire；PG-04 只保证参数形状，不保证语义合法。

---

## 7. PG-08：酒馆伪造工具历史兼容

### 7.1 典型酒馆形态

- assistant 只保留 `tool_calls`，丢失或重复 `id`。
- `arguments` 是未转义换行/非 JSON 文本，或直接是整段 RP 文本。
- `tool` 消息只有输出，没有前置 assistant 调用（孤立工具输出）。
- 工具名每轮变化（nonce）。

### 7.2 兼容策略

| 异常形态 | 当前处理 | 证据 |
|---|---|---|
| `tool_calls` 不是数组 | warning + 忽略该消息的工具调用 | `openai_request.py:226-228` |
| 单个 call 不是对象 | warning + 跳过 | `:233-235` |
| `function` 不是对象 | warning + 跳过 | `:236-239` |
| 工具名缺失/空 | 使用 `tool_output` | `:240-243` |
| `arguments` 非 JSON 文本 | `{"content": raw}` | `:64-86` |
| `arguments` 是 JSON 标量/数组 | `{"content": parsed}` | `:79-86` |
| `id` 缺失 | `call_auto_{msg}_{idx}` | `:249-258`, `:42-49` |
| `id` 重复 | 合成唯一 id | `:42-49`, `:249-258` |
| `tool_call_id` 匹配不到 | 按声明名/最近 pending/孤立输出降级 | `:272-287`, `:290-323` |
| 孤立工具输出 | 合成 `call_output_{idx}_{seq}` + 名称 | `:307-316` |
| `content` 非 JSON | `{"result": text}` | `:89-128` |

### 7.3 与原生 Gemini 的差异

- OpenAI 面的宽松处理发生在转换阶段，不会进入 `normalize_gemini_request` 的严格签名路径；它在 `_tool_call_parts` 里直接填入 `DUMMY_THOUGHT_SIGNATURE` 或缓存签名（`:259-267`）。
- 原生 Gemini 面先经过 PG-04 的 Struct 强制，再走 PG-01 的签名兜底；两条面最终都生成合法的 Gemini 工具历史。

### 7.4 残余限制

- 宽松处理只覆盖 OpenAI 面；Claude/Responses 适配器当前未启用（`defaults/config.yaml:59-62`）。
- 日志只记录原因/消息索引/字段，不记录参数值（`:52-61`）。
- 工具名 hash 只用于 `[toolshape]`，不参与 PG-08 匹配。

---

## 8. `countTokens` 策略

### 8.1 入口

`routes_gemini.count_tokens_route`（`routes_gemini.py:48-61`）调用 `token_counter.count_tokens(api_format="gemini")`，返回 `{"totalTokens": outcome.total_tokens}`。

### 8.2 打包策略

`_packed_body`（`application/token_counter.py:76-131`）：

1. 有 `systemInstruction` -> `[system]\n<文本>`。
2. 有 `tools` -> `[tools]\n<JSON>`（去掉 None 字段）。
3. `contents` -> `[conversation]\n`，每个 part：
   - 文本 -> `[role] text`，thought 用 `[role/thought]`。
   - `inlineData` -> 单独的 `AistudioPart(inline_data=...)`。
   - 其他 -> 压缩 JSON。
4. 所有文本 section 用 `\n\n` 连接成一个 text part，再拼接 inlineData parts。
5. 最终 body：`[f"models/{model}", [content.to_wire()]]`（`:128-130`）。

`build_count_body` 是对外暴露的包装（`:133-135`）。

### 8.3 上游调用

`count_tokens`（`:138-206`）：

1. `pipeline.begin` -> `route.model/surface`。
2. `build_count_body`。
3. `pipeline.acquire` -> `InstanceRequestClient.count_tokens(packed_body=body)`。
4. 成功 -> `pipeline.record_success`，返回 `TokenCountOutcome(model,total_tokens,surface)`。
5. 异常 -> `pipeline.handle_error`，支持 `retry_same` / `switch`。
6. `finally` 释放 attempt。

`InstanceRequestClient.count_tokens`（`instance_client.py:173-220`）：

- 在 `session.request_scope()` 内执行，占用 `active_requests`。
- 取 `SessionContext.base_template.url`；若含 `/GenerateContent`，替换为 `/CountTokens`；否则使用默认 CountTokens RPC URL（`:184-192`）。
- 复制模板 headers，设置 `Content-Type: application/json+protobuf`（`:193-199`）。
- `send_hooked_request`，`status>=400` -> `classify_error`（`:203-208`）。
- 解析 JSON，取 `data[0]`，必须是 >=0 的 int（`:209-220`）。

### 8.4 不触碰的路径

模块说明明确：计数复用路由与 `active_requests`，但不修改生成模板、不捕获 snapshot、不应用 ATG（`token_counter.py:1-8`）。`instance_client.count_tokens` 也只使用 live page，不调用 `capture`（`:173-220`）。

### 8.5 数据流示例

```text
Gemini countTokens request:
{
  "systemInstruction": {"parts":[{"text":"S"}]},
  "tools": [{"functionDeclarations":[{"name":"f"}]}],
  "contents": [
    {"role":"user","parts":[{"text":"U"}]},
    {"role":"user","parts":[{"inlineData":{"mimeType":"image/png","data":"..."}}]}
  ]
}

build_count_body:
[
  "models/gemini-3.7-flash",
  [[
    {"text": "[system]\nS\n\n[tools]\n[...]\n\n[conversation]\n[user] U"},
    {"inlineData": {"mimeType":"image/png","data":"..."}}
  ]]
]

upstream response [200, 1234] -> totalTokens=1234
```

### 8.6 残余限制

- 上游 CountTokens 只接受 `[model, contents]`；system/tools 被序列化进文本，不是原生结构，计数可能与真实生成时的 token 数有偏差（`instance_client.py:174-179`）。
- `data[0]` 的格式是上游约定；非该形状直接 502（`:215-220`）。
- 不应用 ATG 合成包装，因此开启 ATG 时 countTokens 不计入合成工具/控制消息。
- 分包只处理文本与 inlineData；fileData/executableCode 等由 JSON 化进入文本（`token_counter.py:96-124`）。

---

## 9. `[toolshape]` 脱敏规则

### 9.1 触发

`normalize_gemini_request` 的 `emit_toolshape` 为 True 且请求有工具活动时输出（`chat_service.py:1033-1040`）：

- `req.tools` 非空，或
- `req.toolConfig` 非空，或
- 任一 part 是 `functionCall`/`functionResponse`（`:686-696`）。

`generation_service` 每个请求只在第一次 normalize 时传 True（`generation_service.py:171-186`, `:322-353`），同一 attempt 的 `retry_same` 不会重复打印。

### 9.2 摘要内容

`_toolshape_summary`（`chat_service.py:619-683`）：

```json
{
  "api_format": "openai",
  "channel": "chat",
  "surface": "chat",
  "model": "models/...",
  "contents": [
    {
      "role": "model",
      "parts": {"text": 1, "functionCall": 1},
      "function_calls": [
        {
          "name_hash": "8hex",
          "args_type": "dict",
          "keys": ["city"],
          "has_signature": true,
          "has_id": true
        }
      ],
      "function_responses": [
        {
          "name_hash": "8hex",
          "response_type": "dict",
          "has_id": true,
          "parts": {"text": 0}
        }
      ]
    }
  ],
  "tool_declarations": [
    {
      "name_hash": "8hex",
      "schema_key": "parametersJsonSchema",
      "mode": "AUTO"
    }
  ]
}
```

字段来源：

- `name_hash`：`sha256(name)[:8]`（`chat_service.py:567-570`）。
- `args_type`：Python 类型名（`dict/str/list/int/NoneType`）。
- `keys`：args 为 dict 时的键名排序（`sorted(args)`）。
- `has_signature`：`bool(part.thoughtSignature)`。
- `has_id`：`bool(fc.id)` / `bool(fr.id)`。
- `parts`：`_count_part_kinds` 的 part 类型计数（`:611-616`）。
- `schema_key`：`parameters/parametersJsonSchema/parameters_json_schema/input_schema/parameters_schema` 中命中的键（`:591-595`）。
- `mode`：`toolConfig.functionCallingConfig.mode`（`:598-608`）。

### 9.3 明确不记录

- 正文文本。
- 参数值、response 值。
- 完整签名。
- header、cookie、API key。
- 工具名原文（只记 hash）。
- 模型完整请求体。

### 9.4 残余风险

1. **键名可能含用户文本**：`keys` 是参数键名；若客户端把用户文本当键名，文本会进入日志（`chat_service.py:635-640`）。这是当前设计保留的例外。
2. **name_hash 低熵**：8 位十六进制可被暴力反推短工具名（`:567-570`）。
3. **体积**：INFO 级别，完整 per-content/per-declaration 摘要；长历史/多次请求时日志体积线性增长。当前只在首次 normalize 输出，缓解了 retry 重复。
4. **非 dict 声明不可见**：`_toolshape_summary` 跳过非 dict 声明（`:665-673`），畸形声明不会出现在摘要里。
5. **不记录 `functionResponse.parts` 的具体类型内容**，只记录计数；排障时可能需要额外证据。

---

## 10. 残余限制汇总

1. **PG-05 分片工具调用未聚合**：`StreamingGateway` 逐 chunk yield `tool_calls`（`streaming.py:173-175`），没有按 call_id 合并；`generation_service` 也没有聚合器。分片工具流可能产生多个部分调用。
2. **PG-13 非 STOP finish 原样透传**：`application/finish_reasons.py` 有 10/12/13 的映射（`application/finish_reasons.py::WIRE_TO_GEMINI_FINISH_REASON`），但没有“有合法工具调用时把 MALFORMED/UNEXPECTED/TOO_MANY 视为可交付”的重写。ATG 的 `TOOL_CALLS -> STOP` 只比较字符串，真实 int finish 不触发（`extractor.py:131-132`, `:181-186`）。
3. **PG-11 toolConfig 不写入上游**：`wire_codec.py:330-375` 只用 `tool_config` 做 NONE 清空和 allowedFunctionNames 声明过滤，没有写顶层 toolConfig；上游 ANY/required 可能被降级为 AUTO。这是上游 wire 限制。
4. **PG-12 snapshot hash 对工具 part 视而不见**：`build_snapshot_hash_parts` 只收集 text/inline/file/code，functionCall/functionResponse 追加空串（`session.py:60-86`）。工具占比高的历史可能得到近似常量 hash，是否触发上游 403 未在本规格中验证。
5. **ATG required 可能在已 yield 数据后失败**：流式 `finalize` 在部分内容已发送后才检查 required（`generation_service.py:459-475`, `stream_extractor.py:158-165`）。客户端可能看到半截内容 + 错误。
6. **客户端合成守卫是启发式**：五个信号只扫描有限 parts/字符（`client_patterns.py:58-59`, `:201-221`），超长系统指令或非常规命名可能漏检；漏检会导致双重合成通道。
7. **模型过滤器是 permissive default**：未知模型默认按文本处理（`model_filter.py:38-46`），可能对非文本模型做不必要的 wrap；可通过 `non_text_model_pattern` 覆盖（`:49-84`）。
8. **OpenAI `n=1` 限制**：`n != 1` 直接 400（`openai_request.py:445-446`）。
9. **OpenAI `stop`/`reasoning_effort`/`response_format` 的未知值会 400**（`openai_request.py:398-437`），不是静默忽略。
10. **`countTokens` 的 system/tools 文本化**：与真实生成请求的结构不同，计数是近似值（`token_counter.py:76-131`）。
11. **`[toolshape]` 每个请求只输出一次**：后续 attempt 不重复；如果第一次 attempt 的请求形状与后续不同（例如 ATG 注入后重试），日志只反映第一次 normalize 的形状。
12. **`_output_is_empty` 只检查第一个 candidate**：多 candidate 场景下，第二个 candidate 有内容也可能被判空（`generation_service.py:68-79`）。当前上游通常单 candidate。

---

## 11. 证据索引

| 主题 | 主要证据 |
|---|---|
| 协议中立链 | `src/aistudio_api/application/generation_service.py:1-11`, `:47-79`, `:151-285`, `:298-506` |
| OpenAI 请求转换 | `src/aistudio_api/application/format_converters/openai_request.py:27-49`, `:64-128`, `:217-323`, `:441-532` |
| OpenAI 响应渲染 | `src/aistudio_api/application/format_converters/openai_response.py:45-99`, `:102-221` |
| 原生归一化 | `src/aistudio_api/application/chat_service.py:37-43`, `:567-704`, `:720-809`, `:1033-1040` |
| Struct 强制 | `src/aistudio_api/infrastructure/gateway/wire_types.py:325-372` |
| wire 下标 | `src/aistudio_api/infrastructure/gateway/wire_spec.py:28-43` |
| 首事件预读 | `src/aistudio_api/api/gemini_service.py::_build_gemini_streaming_response`; `src/aistudio_api/api/routes_openai.py::chat_completions` |
| 工具流终止 | `src/aistudio_api/infrastructure/gateway/streaming.py:34-37`, `:113-136`, `:239-249`; `wire_parser.py:437-541` |
| ATG 配置/门面 | `src/aistudio_api/application/atg/__init__.py:38-90`; `atg/config.py:43-110` |
| ATG 注入 | `src/aistudio_api/application/atg/injector.py:104-216` |
| 五信号 | `src/aistudio_api/application/atg/client_patterns.py:9-18`, `:91-227` |
| ATG 非流式还原 | `src/aistudio_api/application/atg/extractor.py:95-197` |
| ATG 流式还原 | `src/aistudio_api/application/atg/stream_extractor.py:38-167`; `stream_decoder.py:136-375` |
| ATG 参数修复 | `src/aistudio_api/application/atg/repair.py:17`, `:189-226` |
| 签名缓存 | `src/aistudio_api/application/signature_cache.py:27-83`, `:161-180` |
| countTokens | `src/aistudio_api/application/token_counter.py:1-8`, `:76-206`; `instance_client.py:173-220` |
| 默认 ATG/格式配置 | `defaults/config.yaml:57-85` |
| `[toolshape]` | `src/aistudio_api/application/chat_service.py:567-704`, `:1033-1040` |

# Google AI Studio Wire 协议逆向技术规范 (WIRE_SPECIFICATION.md)

本文档记录 Google AI Studio (MakerSuite / boq-makersuite) 底层 Protobuf-over-JSON Wire 数组协议的完整逆向映射结构、字段索引定义、类型系统及编解码实现细节。

---

## 目录

- [1. 协议概览与序列化机制](#1-协议概览与序列化机制)
- [2. 请求顶层 Wire 数组映射 (Request Array)](#2-请求顶层-wire-数组映射-request-array)
- [3. 对话内容与多模态数据结构 (Contents & Parts)](#3-对话内容与多模态数据结构-contents--parts)
  - [3.1 Content 结构](#31-content-结构)
  - [3.2 Part 字段定义与多态表示](#32-part-字段定义与多态表示)
- [4. 生成控制参数 (GenerationConfig)](#4-生成控制参数-generationconfig)
- [5. 安全策略 (SafetySettings)](#5-安全策略-safetysettings)
- [6. 工具与函数声明 (Tools & Function Calling)](#6-工具与函数声明-tools--function-calling)
  - [6.1 内置工具模板](#61-内置工具模板)
  - [6.2 自定义函数声明 (Function Declarations)](#62-自定义函数声明-function-declarations)
  - [6.3 结构化 Schema 编码与类型字典](#63-结构化-schema-编码与类型字典)
  - [6.4 工具控制模式 (ToolConfig)](#64-工具控制模式-toolconfig)
- [7. 响应报文解析与 Token 使用量 (Response & Usage)](#7-响应报文解析与-token-使用量-response--usage)
  - [7.1 响应 Chunk 容器](#71-响应-chunk-容器)
  - [7.2 Candidate 与 Part 字段解析](#72-candidate-与-part-字段解析)
  - [7.3 Token 使用量 (UsageMetadata)](#73-token-使用量-usagemetadata)
  - [7.4 FinishReason 完整状态码映射表](#74-finishreason-完整状态码映射表)
- [8. 前端逆向新特性与高价值发现](#8-前端逆向新特性与高价值发现)
- [9. 四协议共用 Wire 与 C1d 快照加固](#9-四协议共用-wire-与-c1d-快照加固)
---

## 1. 协议概览与序列化机制

Google AI Studio 在 Web 端（`alkalimakersuite-pa.clients6.google.com`）与后端通信时，采用 Google 内部的 **Protobuf-over-JSON 紧凑数组** 格式（即 JSPB / Protobuf 数组序列化规范）。

- **字段定位规则**：Protobuf 字段序号为 `N` 的属性，在 JSON 数组中对应的索引为 `N - 1`（0-indexed）；
- **稀疏表示**：空字段或默认未赋值字段序列化为 `null`；
- **结尾截断**：尾部连续的 `null` 元素在 JSON 编码时可省略截断。

---

## 2. 请求顶层 Wire 数组映射 (Request Array)

发往 `MakerSuiteService/GenerateContent` 的 JSON 顶层数组索引映射如下：

| 数组索引 | Proto 字段号 | 字段名称 | 类型 | 说明 |
|---|---|---|---|---|
| **`0`** | Field 1 | `model` | `string` | 目标模型路径，如 `"models/gemini-3.8-flash"`、`"models/gemini-3.7-flash"` |
| **`1`** | Field 2 | `contents` | `list` | 结构化对话轮次数组（`AistudioContent` 列表） |
| **`2`** | Field 3 | `safety_settings` | `list \| null` | 安全拦截阈值数组 |
| **`3`** | Field 4 | `generation_config` | `list` | 生成控制参数数组（见第 4 节） |
| **`4`** | Field 5 | `snapshot` | `string` | **BotGuard WAA 快照签名 Token**（以 `!` 开头） |
| **`5`** | Field 6 | `system_instruction` | `list \| null` | 系统提示词内容（格式同 Content） |
| **`6`** | Field 7 | `tools` | `list \| null` | 工具声明列表（内置工具与自定义函数） |
| **`7`** | Field 8 | `evergreen_model_uri` | `string \| null` | 动态常青模型 URI（如 `"models/gemini-..."`） |
| **`10`** | Field 11 | `request_flag` | `int` | 请求行为标记，固定值 `1` |
| **`11`** | Field 12 | `session_token` | `string \| null` | 会话续接令牌（如 `"v1_..."`） |
| **`13`** | Field 14 | `request_context` | `list \| null` | 请求上下文与时区声明，如 `[[null, null, "Asia/Tokyo"], null, 1]` |

---

## 3. 对话内容与多模态数据结构 (Contents & Parts)

### 3.1 Content 结构

单个对话轮次表示为二元数组 `[parts, role]`：

```json
[
  [
    [null, "用户输入的提示词内容"]
  ],
  "user"
]
```

- `role` 可选值：`"user"`、`"model"`。

### 3.2 Part 字段定义与多态表示

每个 Part 是一个稀疏数组，根据其数据类型的不同占用不同的下标位置（对应 Protobuf 字段号 `N` 位于下标 `N - 1`）：

| Part 类型 | 数组结构映射 | 说明 |
|---|---|---|
| **文本 (Text)** | `[null, "文本内容"]` | 下标 `1` (Field 2) 为普通文本块 |
| **思考文本 (Thinking)** | `[null, "思考链内容", ...]` | 下标 `1` 为思维文本；下标 `10` (Field 11) 为 `true`（主流）；或下标 `0` 为 `true`；部分变体下标 `12` 为 `1` |
| **内联多模态 (InlineData)** | `[null, null, ["image/jpeg", "base64_data"]]` | 下标 `2` (Field 3) 为 `[mimeType, base64]` |
| **工具调用 (FunctionCall)** | `[..., [name, args_struct, call_id]]` | 请求中位于下标 `10` (Field 11)；响应中主路径位于下标 `3` (Field 4)，变体回退兼容下标 `10` |
| **工具结果 (FunctionResponse)** | `[..., [name, response_struct, call_id]]` | 请求中位于下标 `11` (Field 12)；响应中主路径位于下标 `4` (Field 5)，变体回退兼容下标 `11` |
| **资源引用 (FileData)** | `[null, null, null, null, null, ["file_id"]]` | 下标 `5` (Field 6) 为 `[file_id]` |
| **可执行代码 (ExecutableCode)** | `[..., "print('hello')", ...]` | 下标 `8` (Field 9) 为模型生成的待执行代码脚本源码 |
| **代码执行结果 (CodeResult)** | `[..., "output: hello", ...]` | 下标 `9` (Field 10) 为代码执行沙箱返回的输出结果 |
| **思维签名 (ThoughtSignature)** | 位于任意 Part 的下标 `14` (`part[14] = "signature"`) | Google 官方校验思维链真实性的签名字符串 |

#### 参数与返回值的 Protobuf 结构体编码 (Struct & Value)
在 `FunctionCall.args` 与 `FunctionResponse.response` 中，键值数据遵循 `google.protobuf.Struct` 紧凑数组格式：
- **Struct 容器**：`[[key, value_wire], ...]`
- **Value 节点**（按值类型映射）：
  - `null`：`[0]`
  - `number` (int/float)：`[null, num]`
  - `string`：`[null, null, str]`
  - `boolean`：`[null, null, null, bool]`
  - `object` (嵌套 Struct)：`[null, null, null, null, [struct_fields]]`
  - `array` (ListValue)：`[null, null, null, null, null, [[item_values]]]`

## 4. 生成控制参数 (GenerationConfig)

`generation_config` 位于顶层数组下标 `3`，其内部数组下标定义如下：

| 数组索引 | 字段名称 | 类型 | 说明 |
|---|---|---|---|
| **`1`** | `stop_sequences` | `list[str] \| null` | 停止词列表 |
| **`3`** | `max_tokens` | `int \| null` | 最大生成 Token 数（如 `65536`） |
| **`4`** | `temperature` | `float \| null` | 采样温度（如 `1.0`） |
| **`5`** | `top_p` | `float \| null` | 核采样概率（如 `0.95`） |
| **`6`** | `top_k` | `int \| null` | Top-K 采样候选数（如 `64`） |
| **`7`** | `response_mime_type` | `string \| null` | 输出 MIME 类型（如 `"application/json"` 或 `"text/plain"`） |
| **`8`** | `response_schema` | `list \| null` | 结构化输出 JSON Schema（编码见第 6 节） |
| **`9`** | `presence_penalty` | `float \| null` | 存在惩罚系数 |
| **`10`** | `frequency_penalty` | `float \| null` | 频率惩罚系数 |
| **`11`** | `response_logprobs` | `bool \| null` | 是否返回 Logprobs |
| **`12`** | `logprobs` | `int \| null` | Logprobs 候选数 |
| **`14`** | `image_output_mode` | `list[int] \| null` | 生图输出模式：`[2]` (仅图片) 或 `[2, 1]` (图文混合) |
| **`16`** | `thinking_config` | `list \| null` | 思考模式：`[mode, null, null, level]`（`1`=LOW, `2`=MEDIUM, `3`=HIGH, `4`=MINIMAL） |
| **`17`** | `media_resolution` | `int \| null` | 多模态输入分辨率：`1`=LOW, `2`=MEDIUM, `3`=HIGH |
| **`18`** | `seed` | `int \| null` | 随机种子（Field 19） |
| **`26`** | `output_resolution` | `list[str] \| null` | 生图分辨率配置，如 `["1:1", "1K"]` |

> [!NOTE]
> **Seed 常量与写入路径**：代码以 `GenerationConfigIndex.SEED = 18` 作为唯一索引定义（`src/aistudio_api/infrastructure/gateway/wire_spec.py:63`）。请求侧 `generation_config.seed` 若存在，会写入 `generation_config_overrides["seed"]`（`src/aistudio_api/application/chat_service.py:1009-1011`），再由 `WireCodec.rewrite` 通过 `AistudioGenerationConfig.seed` setter 写入 generation config 数组下标 18（`src/aistudio_api/infrastructure/gateway/wire_types.py:224-232`、`wire_codec.py:305-310`）。缺失时不写该下标，不生成随机默认值。

---

## 5. 安全策略 (SafetySettings)

位于顶层数组下标 `2`，格式为二元条目列表 `[[null, null, category_code, threshold_code], ...]`：

### 类别码 (Category Code)
- `7`：`HARM_CATEGORY_HARASSMENT` (骚扰内容)
- `8`：`HARM_CATEGORY_HATE_SPEECH` (仇恨言论)
- `9`：`HARM_CATEGORY_SEXUALLY_EXPLICIT` (色情露骨内容)
- `10`：`HARM_CATEGORY_DANGEROUS_CONTENT` (危险内容)

### 阈值码 (Threshold Code)
- `1`：`BLOCK_LOW_AND_ABOVE` (低阈值及以上拦截，最严格)
- `2`：`BLOCK_MEDIUM_AND_ABOVE` (中阈值及以上拦截)
- `3`：`BLOCK_ONLY_HIGH` (仅高风险拦截)
- `4`：`BLOCK_NONE` (不拦截)
- `5`：`OFF` (完全关闭安全过滤器)

---

## 6. 工具与函数声明 (Tools & Function Calling)

位于顶层数组下标 `6` (Proto Field 7)。`tools` 是一个工具容器列表，支持内置工具与自定义函数声明。

### 6.1 内置工具模板

| 工具名称 | Wire 数组表示 |
|---|---|
| **代码执行 (Code Execution)** | `[[]]` |
| **网页搜索 (Google Search)** | `[null, null, null, [null, [[]]]]` |
| **图片搜索 (Google Image Search)** | `[null, null, null, [null, [null, []]]]` |
| **网页 + 图片联合搜索** | `[null, null, null, [null, [[], []]]]` |
| **谷歌地图 (Google Maps)** | `[null, null, null, null, null, null, null, null, null, null, []]` |
| **URL 上下文提取 (URL Context)** | `[null, null, null, null, null, null, null, []]` |

### 6.2 自定义函数声明 (Function Declarations)

在 `tools` 列表中，自定义函数集合序列化为 Tool 消息的 Field 2（前置 `null` 占位）：

```json
[
  null,
  [
    [func_name, func_description, parameters_schema],
    ...
  ]
]
```

单个函数声明三元数组结构如下：
- **下标 `0` (Field 1)**：`name` (`string`)，函数唯一名称；
- **下标 `1` (Field 2)**：`description` (`string | null`)，函数功能说明；
- **下标 `2` (Field 3)**：`parameters` (`list | null`)，参数 Schema 定义（遵循下文 6.3 节 Schema 编码规范）。

### 6.3 结构化 Schema 完整逆向编码表

针对自定义函数声明（Function Declarations）与结构化输出（Response Schema），根据 AI Studio 前端 JS 核心反编译（`_.nm`、`dwa` 与 AST 导出的字段全集 `_.mhb`）提取的完整 Protobuf-over-JSON 字段索引如下：

| 数组索引 | Proto 字段号 | 字段名 (Property) | 类型 | 说明与编码规范 |
|---|---|---|---|---|
| **`0`** | Field 1 | `type` | `int` | 取值范围 `1..6`：`1`=string, `2`=number, `3`=integer, `4`=boolean, `5`=array, `6`=object。不可为 `0` (`TYPE_UNSPECIFIED`)，否则上游返回 `HTTP 400` |
| **`1`** | Field 2 | `format` | `string` | 格式修饰符（如 `"date-time"`, `"int64"`） |
| **`2`** | Field 3 | `description` | `string` | 字段语义描述 |
| **`3`** | Field 4 | `nullable` | `bool` | 是否允许为 null |
| **`4`** | Field 5 | `enum` | `list[str]` | 枚举合法取值数组 |
| **`5`** | Field 6 | `items` | `list` | 数组元素 Schema 递归定义（针对 array 类型） |
| **`6`** | Field 7 | `properties` | `list` | 对象属性键值对列表：`[[prop_name, prop_schema], ...]` |
| **`7`** | Field 8 | `required` | `list[str]` | 必填属性名称数组 |
| **`8`** | Field 9 | `minProperties` | `int` | 最小属性数量约束 |
| **`9`** | Field 10 | `maxProperties` | `int` | 最大属性数量约束 |
| **`10`** | Field 11 | `minimum` | `float/int` | 数值下界 |
| **`11`** | Field 12 | `maximum` | `float/int` | 数值上界 |
| **`12`** | Field 13 | `minLength` | `int` | 字符串最小长度 |
| **`13`** | Field 14 | `maxLength` | `int` | 字符串最大长度 |
| **`14`** | Field 15 | `pattern` | `string` | 正则表达式匹配规则 |
| **`15`** | Field 16 | `example` | `list` | 示例值（google.protobuf.Value 编码） |
| **`16`** | Field 17 | `oneOf` | `list` | 独占联合类型定义数组 |
| **`17`** | Field 18 | `anyOf` | `list` | 多态联合类型定义数组 |
| **`18`** | Field 19 | `allOf` | `list` | 交叉继承类型定义数组 |
| **`19`** | Field 20 | `not` | `list` | 取反类型定义 |
| **`20`** | Field 21 | `maxItems` | `int` | 数组最大元素数 |
| **`21`** | Field 22 | `minItems` | `int` | 数组最小元素数 |
| **`22`** | Field 23 | `propertyOrdering` | `list[str]` | 属性在 UI / Prompt 中呈现的确定性声明顺序 |

> [!NOTE]
> **Schema 字段边界说明**：
> 根据 AI Studio 前端 Bundle 逆向提取的 Schema Proto 字段集（`_.mhb = new Set(...)`），定义范围截止于 Field 23（`propertyOrdering`，数组下标 22）。早期推测的 `title` (Field 24) 与 `default` (Field 25) 并不存在于 Google Schema Proto 中；向数组注入下标 23 或 24 会被服务端识别为未知字段并返回 `HTTP 400: Request contains an invalid argument`。

### 6.4 工具控制模式 (ToolConfig)

MakerSuite 协议无独立顶层工具控制字段，Gemini API 的 `toolConfig` 在网关层按语义映射：

| 模式 (Mode) | 语义说明 | 网关处理策略 |
|---|---|---|
| **`AUTO`** | 模型自主决定文本或工具调用 | 保持 `tools` 原样下发 |
| **`NONE`** | 禁用工具调用，强制纯文本输出 | 置空 `tools` 为 `null` |
| **`ANY`** | 强制执行工具调用 | 若配置 `allowedFunctionNames`，在网关层过滤候选工具列表 |
| **`VALIDATED`** | 受限解码验证 | 保持 `tools` 原样下发 |

## 7. 响应报文解析与 Token 使用量 (Response & Usage)

### 7.1 响应 Chunk 容器

Google AI Studio 响应为 Protobuf JSON 数组，每个流式分块或单次响应顶层结构为：

```json
[
  [
    [
      [
        [
          [null, "模型生成的文本内容"]
        ],
        "model"
      ],
      1,
      null,
      "STOP",
      []
    ]
  ],
  null,
  [12, 45, 57, 0, null, null, null, null, null, 18],
  null,
  null,
  null,
  null,
  "response_id_string"
]
```

### 7.2 Candidate 与 Part 字段解析

- `chunk[0][0]`：候选回答对象（Candidate）；
- `candidate[0]`：结构化内容 `[parts_list, role]`；
- `candidate[1]`：FinishReason 代码（`1` 对应 STOP）；
- `candidate[3]`：FinishMessage 说明；
- `candidate[4]`：安全评估结果数组（SafetyRatings）；
- `chunk[7]`：响应唯一标识（Response ID）。

### 7.2.1 函数调用参数 JSPB 映射规范

当候选 Part 为函数调用 (`FunctionCall`) 时，位于 Part 下标 `3` (Field 4) 或下标 `10` (Field 11)。其内部三元组格式为 `[name, args_struct, call_id]`。

`args_struct` 遵循 `google.protobuf.Struct` 映射：
1. **外层容器**：键值对二维列表 `[[key, value_wire], ...]`。当被上游多层容器包裹时（例如 `[[ [...pairs] ]]`），需解包外层单元素容器，但**绝不可破坏键值对本身**；
2. **多态值节点 (`google.protobuf.Value`)**：
   - **NullValue (Field 1, 下标 0)**：`[0]` 解码为 `None`；
   - **NumberValue (Field 2, 下标 1)**：`[null, number]` 解码为浮点数或整数；
   - **StringValue (Field 3, 下标 2)**：`[null, null, "text"]` 解码为字符串；
   - **BoolValue (Field 4, 下标 3)**：`[null, null, null, bool]`，JSPB 常压缩为 `0` (`False`) 与 `1` (`True`)，必须解码为标准布尔值；
   - **StructValue (Field 5, 下标 4)**：`[null, null, null, null, [struct_fields]]` 递归解码为字典；
   - **ListValue (Field 6, 下标 5)**：`[null, null, null, null, null, [items]]` 必须解码为列表。空列表 `[]` 必须严格保持为 `[]`，不可误转为字典；单元素列表必须完整保留单个元素，不可解包其内部字段导致被 null 填充。

### 7.3 Token 使用量 (UsageMetadata)

位于 `chunk[2]` 数组：

| 数组索引 | 统计指标 | 说明 |
|---|---|---|
| **`0`** | `prompt_tokens` | 提示词 Token 消耗量 |
| **`1`** | `visible_completion_tokens` | 可见模型输出 Token 消耗量 |
| **`2`** | `total_tokens` | 请求总 Token 数 |
| **`3`** | `cached_tokens` | 命中的上下文缓存 Token 数 |
| **`4`** | `prompt_tokens_details` | 提示词结构明细 |
| **`9`** | `reasoning_tokens` | **思维链（Thinking）思考 Token 消耗量** |

> [!NOTE]
> 最终对齐 Gemini API 标准时：
> `completion_tokens = visible_completion_tokens + (reasoning_tokens or 0)`

### 7.4 FinishReason 完整状态码映射表

上游 Protobuf 在 Candidate 的 Field 2 (下标 `1`) 返回整数代码，对应 Google API 标准字符串枚举：

| 代码 | 标准枚举值 | 说明 |
|---|---|---|
| `0` | `FINISH_REASON_UNSPECIFIED` | 未指定 |
| `1` | `STOP` | 自然停止或遇到停止序列 |
| `2` | `MAX_TOKENS` | 达到最大输出 Token 上限 |
| `3` | `SAFETY` | 触发安全过滤策略拦截 |
| `4` | `RECITATION` | 触发版权/原文背诵拦截 |
| `5` | `OTHER` | 其他终止原因 |
| `6` | `LANGUAGE` | 不支持的语言策略拦截 |
| `7` | `BLOCKLIST` | 触发禁用词黑名单 |
| `8` | `PROHIBITED_CONTENT` | 触发违规有害内容拦截 |
| `9` | `SPII` | 敏感个人身份信息拦截 |
| `10` | `MALFORMED_FUNCTION_CALL` | 模型生成了非法的函数调用参数 |
| `11` | `IMAGE_SAFETY` | 生成图片触发安全策略 |
| `12` | `UNEXPECTED_TOOL_CALL` | 意外的工具调用 |
| `13` | `TOO_MANY_TOOL_CALLS` | 工具链连续调用超限退出 |
| `14` | `IMAGE_PROHIBITED_CONTENT` | 图片包含违规内容 |
| `15` | `IMAGE_OTHER` | 生图其他未知原因终止 |
| `16` | `NO_IMAGE` | 预期生图但未生成 |
| `17` | `IMAGE_RECITATION` | 生图版权来源相似度拦截 |
| `18` | `MISSING_THOUGHT_SIGNATURE` | 缺少有效的思维签名 |
| `22` | `CONTINUING` (非终止) | 流式长输出续页中，网关忽略该中间码继续接收后续分块 |
AI Studio Web 核心过滤数组：
`hCb = [0, 3, 4, 5, 7, 8, 9, 11, 14, 15, 17]`
前端逻辑中 `1` (`STOP`) 与 `2` (`MAX_TOKENS`) 视为常规完成，其余在 `hCb` 中的代码均触发错误或警告弹窗处理。

> [!IMPORTANT]
> **`CONTINUING=22` 的网关实现**：`WIRE_FINISH_REASON_CONTINUING = 22` 与 `WIRE_FINISH_REASON_STOP = 1` 统一定义在 `src/aistudio_api/infrastructure/gateway/wire_spec.py:127-128`。流式解析只把 `finish_reason not in (0, 22)` 视为终止码；`0` 与 `22` 都继续接收后续分块（`streaming.py:164-168`、`198-202`）。如果工具流已经产出 `tool_calls` 但上游没有给出终止码，网关在流结束时合成 `STOP`，避免客户端无限等待（`streaming.py:242-248`）。文本流不设全局 wall-clock deadline。

### 7.5 PG-04：functionCall / functionResponse 的 Struct 强制

Gemini 上游要求 `functionCall.args` 与 `functionResponse.response` 是合法的 `google.protobuf.Struct`。网关在进入 Wire 编码前统一收口（`src/aistudio_api/infrastructure/gateway/wire_types.py:336-372`）：

| 输入形状 | `functionCall.args` | `functionResponse.response` |
|---|---|---|
| `dict` | 原样透传 | 原样透传 |
| 可解析为 JSON object 的字符串 | 使用解析后的 object | 使用解析后的 object |
| 普通字符串 / 数组 / 标量 | 包成 `{"content": value}` | 包成 `{"result": value}` |
| `None` | `{}` | `{}` |

编码侧 `_encode_wire_args` 把 dict 转成 Struct 字段列表 `[[key, value_wire], ...]`，并在外层再包一层容器；`_encode_wire_value` 按类型生成 Value 数组：null `[0]`、number `[null, num]`、string `[null, null, str]`、bool `[null, null, null, bool]`、object `[null,null,null,null,[fields]]`、array `[null,null,null,null,null,[[items]]]`（`wire_types.py:375-405`）。

响应侧 `_decode_wire_struct` / `_decode_wire_list` / `_decode_wire_value` 负责反向解码，且对 Repeated 容器只剥离一层外层包装，避免把单元素列表错误展开（`wire_parser.py:292-362`）。OpenAI 适配层对非法 JSON arguments 不做硬拒绝，而是保留原文到 `content`，让 PG-04 仍能生成合法 Struct（`application/format_converters/openai_request.py:64-93`）。

`[toolshape]` 日志只输出工具名、参数键集合/类型等形状摘要，不输出参数值或工具内容；`emit_toolshape` 每次请求只发一次（`application/chat_service.py:619-715`、`1033-1035`）。

### 7.6 PG-01：thoughtSignature 缓存与 DUMMY 兜底

Gemini 要求历史模型 `functionCall` Part 携带 `thoughtSignature`。客户端（OpenAI/Claude 适配器、会重建 tool channel 的预设）可能丢失该字段，网关按 `call_id` 从持久缓存恢复（`src/aistudio_api/application/signature_cache.py:1-11`）：

- 写入：响应中的 `function_calls[*].thought_signature` / `thoughtSignature` 由 `record_function_call_signatures` 按 `call_id` 写入；空 id/空签名忽略（`signature_cache.py:161-180`）。
- 读取：`_historical_function_call_signature` 先看 Part 自带签名，再看缓存；缓存未命中才回退占位符 `DUMMY_THOUGHT_SIGNATURE = "context_engineering_is_the_way_to_go"`（`chat_service.py:730-734`、`signature_cache.py:25-27`）。
- 缓存特性：读取不消费，可被后续轮次重复使用；TTL 默认 30 天，最多 4096 条；原子写入 `data/signature_cache.json`，代理重启后仍可用（`signature_cache.py:29-146`、`api/app.py:391`）。
- 真实缓存签名优先于 DUMMY；DUMMY 只是上游可接受的占位符，不替代真实签名缓存。

B6 实机验收记录：原生工具调用后 `signature_cache_entries_before=0`、`after=1`（`docs/dev/evidence/B6/results.json`）。

### 7.7 流式截断修复与终态兜底

AI Studio 流式响应以 `)]}'` XSSI 前缀开头，随后是嵌套数组。`IncrementalJSONStreamParser` 用字符串/转义状态机按括号深度切出 chunk（`src/aistudio_api/infrastructure/gateway/stream_parser.py:151-230`）。上游连接被截断时，最后一个 chunk 可能是不完整 JSON：

1. `_close_json_fragment` 补全未闭合字符串/数组/对象，转义裸控制符，裁掉被切断的 `\uXXXX`（`stream_parser.py:29-100`）。
2. `_cut_at_last_top_level_comma` 丢弃最深未闭合容器里的半个元素，保留已收到的兄弟元素（`stream_parser.py:103-129`）。
3. `_repair_truncated_json` 最多尝试 8 轮“补全 → 解析 → 回退到上一个顶层逗号”，救不回来返回 `None`（`stream_parser.py:132-148`）。
4. `finish()` 在流结束时修复并交付最后一个分块；只有包含 usage、response_id 或有效 candidate 内容时才交付，避免空片段污染终态（`stream_parser.py:232-274`）。
5. `streaming.py` 在流结束后调用 `parser.finish()`，再合成缺失的 finish/usage/done；若一个 chunk 都没收到，抛 `BrowserTransportError("stream ended before any data arrived (connection truncated)")`（`streaming.py:190-232`）。

---

## 8. 前端逆向未公开特性参考

通过对 AI Studio 前端代码包（`m=_b.js`）的 AST 逆向分析，整理以下协议特性与未公开接口参考：
1. **原生 MCP (Model Context Protocol) 支持**：
   前端 `toolType` 解析中内置 `mcp_server_tool_call`（枚举代码 `6`），表明 Google 正在或已在 AI Studio 底层协议中预留了连接本地/远程 MCP Server 的标准工具通道。
2. **原生文件检索工具 (`file_search`)**：
   工具分支代码中存在 `case 8: return "file_search"` 与 `file_search_call`，区别于传统的代码执行与普通检索，属于针对多文档的大规模知识库检索能力。
3. **语音自定义词汇表 (`customVocabulary`)**：
   GenerationConfig 字段 32（`_.cu` / Field 32）下支持向语音端点下发专属专业术语、专有名词与人名词典，用于提升音频转录与识别精度。
4. **音频高级控制标记**：
   - `wordTimestamps`：字级别（Word-level）输出时间戳；
   - `speakerDiarization`：多说话人角色分离与标签标记；
   - `smartTranscription`：去除口语赘字（Filler words）、自动语法重构与口误纠正；
   - `fillerWords`：允许自然停顿与语气词（"hmm", "ahh"）。
5. **视频帧流式抽取服务 (`StreamExtractVideoFrames`)**：
   RPC 服务 `/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/StreamExtractVideoFrames`，用于在后端按时间戳或指定采样率无损切片视频流并回传图片帧。

### 8.1 前端 JS (JSPB) 工具调用参数压缩机制与解包原则

根据对 AI Studio 前端生产包动态逆向分析，Google 前端在处理 `FunctionCall.args` 与 JSON Schema 时存在特定的 JSPB 优化机制：
1. **布尔压缩 (Boolean 0/1 Compression)**：
   在 `dwa` 反序列化器中，`BoolValue` 字段（Field 4）常以整数 `0` 和 `1` 传递以减少传输体积，Python 网关端必须通过 `value[3] in (0, 1)` 强制还原为 `bool`。
2. **Repeated 容器单层扁平化边界**：
   在 `_decode_wire_list` 中，Repeated 字段只允许剥离一层外层数组包装（`len == 1 and not _is_wire_value`），一旦内层为 `_is_wire_value`（以 `None` 或 `0` 开头的 JSPB 数组），必须停止拆包，否则单元素列表（如包含一个问题对象的 `ask` 工具）会被错误展开成 5 元素数组，导致首部填充 4 个 `None`。
3. **Schema 元数据注入与字段边界**：
   下发给 MakerSuite 的 `tools` 需保留字段的 `description` (下标 2) 与 `enum` (下标 4)，以保障模型获取参数语义与合法枚举选项。同时参数 Schema 数组下标上限为 `22` (`propertyOrdering`)，不包含 `title` 与 `default` 字段。
4. **客户端 Schema 字段多态解包 (`parametersJsonSchema`)**：
   官方 SDK（如 `@google/genai`）在生成 Gemini 工具规范时，默认将参数挂载在 `parametersJsonSchema` 字段下。网关层兼容该多态字段提取，保障参数结构完整转换。

### 8.2 ListModels 模型发现字段与动态 Paid

`ModelDiscoveryService._parse_raw_models` 解析 MakerSuite `ListModels` 响应 `raw_data[0]` 的每条模型数组（`src/aistudio_api/infrastructure/gateway/model_discovery.py:838-980`）。代码使用 **0-based 数组下标**：`access_tier` 读取 `item[77]`（protobuf Field 78），`accessModes` 读取 `item[82]`（protobuf Field 83）。历史任务口径中的“77/83”实际指这两个 0-based 数组下标，不是 1-based protobuf 字段号；本文统一写作 `item[77] (Field 78)` / `item[82] (Field 83)`，避免混淆：

| 数组下标 | protobuf 字段（1-based） | 字段 | 用途 |
|---|---|---|---|
| `0` | Field 1 | `id` | 模型 ID，如 `models/gemini-3.8-flash` |
| `2` | Field 3 | `version` | 版本，缺省 `"001"` |
| `3` | Field 4 | `displayName` | 展示名 |
| `4` | Field 5 | `description` | 描述 |
| `5`/`6` | Field 6/7 | `inputTokenLimit`/`outputTokenLimit` | Token 上限 |
| `7` | Field 8 | `supportedGenerationMethods` | 必须含 `generateContent`，否则仅图片/视频/音频模型保留 |
| `8`/`9`/`10` | Field 9/10/11 | `temperature`/`topP`/`topK` | 默认采样参数 |
| `56` | Field 57 | `aliases` | 模型别名 |
| `64` | Field 65 | `capabilityCodes` | 能力码（chat/code/function/google_search/thinking/image/video/audio 等） |
| `71` | Field 72 | `thinking` 配置 | 默认 thinking level 与可选 levels |
| **`item[77]`** | **Field 78** | **`access_tier`** | **`2` 表示 Paid（Pro-only）** |
| **`item[82]`** | **Field 83** | **`accessModes`** | **含 `3` 或 `4` 表示 Paid** |

Paid 判定（`model_discovery.py:972`）：

```text
paid = (access_tier == 2) or (3 in accessModes) or (4 in accessModes)
```

`accessModes` 的 tier 语义在路由侧进一步区分（`routing_eligibility.py:195-204`、`model_discovery.py:652-663`）：

- 仅含 `4` 且不含 `3` → `Ultra/Ultra_20X`；
- 含 `3` → `Pro/Ultra/Ultra_20X`；
- 仅显式 `paid=True` 或内置 Paid 集合命中 → `Pro/Ultra/Ultra_20X`。

### 8.3 Live Free 真值优先与 fallback 边界

模型记录带 `_discovery_source` 标记（`model_discovery.py:303-345`）：

| source | 是否 truth | 说明 |
|---|---|---|
| `live_page` | 是 | READY 池会话页面内 ListModels XHR |
| `live_http` | 是 | 账号 Cookie + SAPISIDHASH 直接 HTTP |
| `snapshot` | 是 | `data/models_cache.json` 持久化快照 |
| `fallback` | **否** | 仅冷启动兜底，绝不冒充 live |

真值优先级：

1. **真实记录显式 Free**（`paid is False` 且 `accessModes` 不含 3/4）→ authoritative Free；`paid_model_rules` 的静态子串规则（如 `image`）不得覆盖（`routing_eligibility.py:134-164`、`model_discovery.py:601-628`）。
2. **真实记录显式 Paid**（`paid is True` 或含 3/4）→ Paid lane；required tiers 由规则/`accessModes` 给出，最低 Pro（仅 `4` 时为 Ultra）（`routing_eligibility.py:241-250`）。
3. **fallback / unknown** → 保持历史顺序：`paid_model_rules` → `accessModes` → 内置 Paid 集合（`routing_eligibility.py:166-204`）。

快照写入 `data/models_cache.json` 时保留 `_discovery_source=snapshot`。`set_paid_models` 按传入记录逐条更新进程内 Paid 集合：记录中 `paid=True` 的模型加入，否则移除；live/snapshot 刷新时以真实记录覆盖，fallback 仅在无快照冷启动时作为初始种子（`model_access.py:265-283`、`model_discovery.py:584-600`）。不在传入列表中的模型保留旧状态，避免已知 Paid 模型因部分快照缺失而静默变 Free。路由的 live truth 判定只认 `_discovery_source` ∈ live/snapshot，不把 fallback 当真值。

B6 实机证据：`model_source=live_page`、31 个模型；`gemini-3.8-flash` 为 live Free（`access_tier=null`、`accessModes=[]`、`required_tiers=[]`），`antigravity-preview-09-2026` 等为 live Paid（`access_tier=2`）（`docs/dev/evidence/B6/model_discovery_fix.json`）。

---

## 9. 四协议共用 Wire 与 C1d 快照加固

### 9.1 四协议共用同一 Wire 编解码

Gemini、OpenAI Chat Completions、Claude Messages 与 OpenAI Responses 最终都转换为同一个 Gemini 请求形状，
再由 `AistudioWireCodec` 编码为上述 JSPB 数组：

- 请求契约：`contracts/gemini.py` / `openai.py` / `claude.py` / `responses.py`。
- 转换器：`format_converters/gemini`（原生归一在 `chat_service.normalize_gemini_request`）、`openai_request.py`、`claude_request.py`、`responses_request.py`。
- 编码入口：`wire_codec.py:198-377`（`encode`/`rewrite`），响应解析：`wire_parser.py:437-541`。
- Wire 下标常量集中在 `wire_spec.py:12-64`（`TopLevelIndex`/`PartIndex`/`GenerationConfigIndex`）与 `wire_spec.py:67-92`（`SchemaIndex`），四协议不得各自硬编码数组下标。
- 工具调用下标：请求侧 `FUNCTION_CALL_ALT=10` / `FUNCTION_RESPONSE_ALT=11` / `THOUGHT_SIGNATURE=14`（`wire_spec.py:40-43`）；响应侧主路径 `FUNCTION_CALL=3` / `FUNCTION_RESPONSE=4`，并兼容 10/11（`wire_parser.py:130-236`）。
- `toolConfig` 仍按 §6.4 语义映射：`NONE` 置空 tools，`ANY` 按 `allowedFunctionNames` 过滤候选（`wire_codec.py:331-377`）。

### 9.2 C1d 快照加固对 Wire 的影响

快照只写入顶层下标 `4`（`TopLevelIndex.SNAPSHOT`，Field 5），不改变其他 wire 字段：

- content hash 仍由 `build_snapshot_hash_parts` 生成（`session.py:84-124`），按 `text/inline/file/code` 顺序拼接并 SHA-256。
- 页面内 `snapshot_generate.js` 只把提示词末尾 256 个 Unicode 码点写入输入框，避免 DOM 物化与 emoji 代理项拆分（`snapshot_generate.js:20-27`）。
- 先以占位 hash 预热 `dms[snapKey]`，再生成真实快照；快照必须 `!` 开头且 `>= 1520` 字符，否则强制预热重试（`snapshot_generate.js:52-58`、`:73-101`）。
- Python 侧 5×250ms 外层重试仍在（`pooled_session.py:187-235`、`session.py:460-513`），但 `low_quality_snapshot` 会立即失败，不盲目重试。
- 403 鉴权刷新会强制下一次快照先预热（`pooled_session.py:237-248`、`session.py:515-527`）。

### 9.3 证据索引补充

| 主题 | 主要证据 |
|---|---|
| 四协议 Wire 共用 | `wire_spec.py:12-92`; `wire_codec.py:198-377`; `wire_parser.py:437-541` |
| C1d 快照 | `infrastructure/browser/js/snapshot_generate.js:20-113`; `pooled_session.py:187-248`; `session.py:84-124`, `:460-527` |
| 模型发现真值 | `model_discovery.py:303-345`, `:838-980`; `routing_eligibility.py:134-204` |
| 四协议入口 | `routes_openai.py:99-221`; `routes_claude.py:175-252`; `routes_responses.py:96-249` |

# D06 Generation / Protocol / Wire — 功能台账

基线：`upstream/master@4623109..rebuild`；仅 D06；证据缺失一律标 `未验证`；本批只读审计，不访问真实上游。

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 待用户决定 |
|---|---|---|---|---|---|---|---|
| D06 | 非流式生成主链（normalize→generate_content→parse→GenerationOutcome） | `6a6afbe` `src/aistudio_api/application/generation_service.py:254-467,470-503`；`src/aistudio_api/application/chat_service.py` | 新增 | working | `tests/unit/test_generation_routing.py:129-155`；`tests/unit/test_atg_integration.py:100-246` | port | 待定 |
| D06 | 流式生成主链（协议中立的 StreamEvent 事件流） | `6a6afbe` `generation_service.py:506-808,810-852` | 新增 | working | `test_generation_routing.py:336-355`；`test_atg_integration.py:252-271` | port | 待定 |
| D06 | 协议无关管线抽取（ATG／重试／换号／空响应归 service，渲染归 API） | `6a6afbe` `generation_service.py:1-7`；`api_service_gemini.py:1-6` | 新增 | working | 同上；`test_generation_routing.py:358-370` 覆盖渲染分离 | port | 待定 |
| D06 | Gemini 非流式入口 `:generateContent` | `4623109` `src/aistudio_api/api/routes_gemini.py:21-31`；`6a6afbe` `api_service_gemini.py:186-225` | 改造 | working | `test_atg_integration.py:100-246`；`test_api_responses.py:58-127` 覆盖 parts/usage | keep | 待定 |
| D06 | Gemini 流式入口 `:streamGenerateContent` | `4623109` `routes_gemini.py:34-41`；`6a6afbe` `api_service_gemini.py:228-263` | 改造 | working | `test_atg_integration.py:252-287`；`test_surface_policy_routing.py:186-215` | keep | 待定 |
| D06 | Gemini SSE 分块渲染（body/thinking/tool_calls/images/reasoning_images） | `4623109` `api_service_gemini.py:54-111`；`6a6afbe` `:166-183` | 改造 | working | `test_atg_integration.py:252-271`；`test_surface_policy_routing.py:204-215` | keep | 待定 |
| D06 | Gemini SSE 终块（usageMetadata＋finishReason＋responseId） | `4623109` `api_service_gemini.py:114-144`；`6a6afbe` `:169-176` | 改造 | partial | 无终块字段独立断言；仅 `test_atg_integration.py:271` 断言 STOP | port（补终块用例） | 待定 |
| D06 | Gemini SSE 错误块（HTTP 200 已出后只能塞 body） | `4623109` `api_service_gemini.py:147-163,245-257`；`6a6afbe` `api/format_errors.py:126-154` | 改造 | partial | `test_surface_policy_routing.py:186-215`：策略 403 仍为 200＋error body | port（见冲突 1） | 待定：是否改为预读首事件 |
| D06 | 路由等待注释块 `: resolving-instance`（不污染 SSE data） | `6a6afbe` `generation_service.py:589-593`；`api_service_gemini.py:166-168` | 新增 | working | `test_generation_routing.py:358-370`（三面均断言） | port | 待定 |
| D06 | 流式首事件预读以保留真实 HTTP 状态（Claude／OpenAI／Responses） | `1375646` `routes_claude.py:121-144`；`c0412c8` `routes_openai.py:102-122`；`2b39b53` `routes_responses.py:138-152` | 新增 | partial | `test_format_claude.py:793-817`；`test_format_openai.py:701-726`；`test_format_responses.py:269-287` | port（统一三面） | 待定：Gemini 是否同策略 |
| D06 | Claude Messages SSE 渲染与消息块序列 | `1375646` `src/aistudio_api/application/format_converters/claude_response.py:138-352`；`routes_claude.py:146-168` | 新增 | working | `test_format_claude.py:621-738,820-849` | port | 待定 |
| D06 | OpenAI Chat Completions SSE 渲染与 tool_calls 增量 | `c0412c8` `format_converters/openai_response.py:104-220`；`routes_openai.py:124-145` | 新增 | working | `test_format_openai.py:546-602` | port | 待定 |
| D06 | OpenAI Responses SSE 渲染与 response.failed 终态 | `2b39b53` `format_converters/responses_response.py:177-415`；`routes_responses.py:154-176` | 新增 | working | `test_format_responses.py:201-244` | port | 待定 |
| D06 | 流式错误边界总则（200 已出后错误只能进 body；已出正文禁切号） | `6a6afbe` `generation_service.py:694-726,836-849`；`upstream_errors.py:195-343`；`api_service_gemini.py:256-257`；`format_errors.py:211-220` | 新增 | partial | `test_api_responses.py:224-271`（仅元数据事件仍可切号）；无“200 后上游 429”用例 | port | 待定：错误响应一致性 |
| D06 | finish_reason 映射（wire int→Gemini 字符串；TOOL_CALLS→STOP） | `6a6afbe` `upstream_errors.py:74-78`；`generation_service.py:646-659`；`atg/extractor.py:36-40,131-132` | 新增 | working | `test_atg_extractor.py:129-149`；`test_atg_integration.py:252-271` | port | 待定 |
| D06 | usage 解析（visible＋reasoning 合并、cached、total 回退） | `4623109` `src/aistudio_api/infrastructure/gateway/wire_parser.py:342-403` | 原生 | working | `test_wire_parser.py::test_parse_usage_metadata`；`test_api_responses.py::test_to_gemini_usage_metadata_uses_visible_and_reasoning_tokens` | keep | 待定 |
| D06 | 流式 usage 聚合与终块事件 | `6a6afbe` `generation_service.py:572-576,646-648,801-807`；`4623109` `streaming.py:86-108,154` | 新增 | partial | `test_wire_parser.py:75-78` 仅 chunk 级；无 service 级聚合断言 | port（补聚合用例） | 待定 |
| D06 | response_id 解析与透出（chunk[7]→事件→各协议 id） | `4623109` `wire_parser.py:23,558-563`；`streaming.py:109-116`；`6a6afbe` `generation_service.py:660-661,801-807` | 新增 | partial | `test_format_openai.py:459` 提供 response_id 但未断言输出 id | port（补断言） | 待定 |
| D06 | thought_signature 流式缓冲并附着到下一事件 | `6a6afbe` `generation_service.py:662-693`；`api_service_gemini.py:69-70,83-84` | 新增 | working | `test_api_responses.py:224-271`；`test_format_claude.py:862-915`（签名缓存） | port | 待定 |
| D06 | 非流式 thought_signature 仅 functionCall／图片回传，纯文本签名丢失 | `4623109` `api/responses.py:58-127`；`6a6afbe` `api_service_gemini.py:205-225` | 原生 | partial | `test_function_call_parser.py:109-137` 仅 functionCall；纯文本无签名用例 | port（是否回传文本签名） | 待定：纯文本签名是否必须 |
| D06 | tool_calls 解析（wire part→functionCall dict，含 call_id／thought_signature） | `4623109` `wire_parser.py:29-30,164-199,227-260,406-509` | 原生 | working | `test_wire_parser.py:150-177,303-340`；`test_function_call_parser.py:6-108` | keep | 待定 |
| D06 | tool_calls 渲染（Gemini functionCall／OpenAI tool_calls／Claude tool_use／Responses function_call） | `6a6afbe` `api/responses.py:92-109`；`api_service_gemini.py:77-84`；`c0412c8` `openai_response.py:38-64`；`1375646` `claude_response.py:44-76`；`2b39b53` `responses_response.py:88-108` | 新增 | working | `test_format_openai.py:493-504,546-602`；`test_format_claude.py:218-299,696-738`；`test_format_responses.py:118-160` | port | 待定 |
| D06 | images／reasoning_images 解析（inlineData＋thought 分流） | `4623109` `wire_parser.py:155-166,443-466` | 原生 | working | `test_wire_parser.py:166-177`；`test_multimodal_pipeline.py:19-88` | keep | 待定 |
| D06 | images 渲染（Gemini inlineData／OpenAI＋Claude markdown data URL／Responses 文本化） | `6a6afbe` `api/responses.py:69-91`；`api_service_gemini.py:85-100`；`c0412c8` `openai_response.py:27-37`；`1375646` `claude_response.py:32-43`；`2b39b53` `responses_response.py:24-34` | 新增 | working | `test_format_openai.py::test_non_stream_images_become_markdown_data_urls`；`test_format_claude.py:597-607` | port | 待定 |
| D06 | thinking／thought 文本解析（多处 flag 兼容） | `4623109` `wire_parser.py:140-153,477-482` | 原生 | working | `test_wire_parser.py:81-149`；`test_multimodal_pipeline.py:89-126` | keep | 待定 |
| D06 | thinking 渲染（Gemini thought=true／OpenAI reasoning_content／Claude thinking block／Responses reasoning item） | `6a6afbe` `api_service_gemini.py:72-76`；`c0412c8` `openai_response.py:104-220`；`1375646` `claude_response.py:87-96,138-352`；`2b39b53` `responses_response.py:67-75,151-170` | 新增 | working | `test_format_openai.py:546-602`；`test_format_claude.py:621-738`；`test_format_responses.py:201-244` | port | 待定 |
| D06 | function_response／code execution／safety_ratings 解析 | `4623109` `wire_parser.py:187-210,467-474,497-508` | 原生 | working | `test_wire_parser.py::test_parse_response_part`；code execution 无独立用例 `未验证` | keep | 待定 |
| D06 | capture：hook-first 模板＋每次实时生成 snapshot | `4623109` `src/aistudio_api/infrastructure/gateway/capture.py:40-134`；`client.py:453-491` | 原生 | working | `test_browser_session.py:389-443`（单次全量 payload）；`test_multimodal_pipeline.py:19-88` | keep | 待定 |
| D06 | capture 模板缓存失效（账号切换／context 变化／强制刷新） | `4623109` `capture.py:35-38,104-133`；`client.py:205-217,622-623` | 原生 | working | `test_browser_session.py:389-443` 覆盖 context 切换清空；账号切换路径 `未验证` | keep | 待定 |
| D06 | replay：捕获模板原样回放（剥离 host／content-length） | `4623109` `replay.py:17-37`；`client.py:712-750` | 原生 | working | `test_browser_session.py:83-116`；`test_generation_routing.py:89-94` 仅 fake client | keep | 待定 |
| D06 | XHR 非流式回放＋鉴权头刷新（SAPISIDHASH／AuthUser／Tier） | `4623109` `transport.py:32-116,181-211` | 原生 | working | `test_browser_session.py:83-116`；鉴权头分支 `未验证` | keep | 待定 |
| D06 | XHR 流式队列（rid 分发、status／chunk／done／error、V8 清理） | `4623109` `transport.py:119-311` | 原生 | working | `test_stream_transport.py:28-84` | keep | 待定 |
| D06 | 流式超时／页面关闭／abort／close 中断语义 | `4623109` `transport.py:130-147,256-311` | 原生 | working | `test_stream_transport.py:87-190` | keep | 待定 |
| D06 | SSE 增量 JSON 解析（XSSI 前缀＋深度状态机切 chunk） | `4623109` `stream_parser.py:14-104` | 原生 | working | `test_response_parser.py:34-62,153-168` | keep | 待定 |
| D06 | wire spec 常量与 JspbArray 稀疏数组容器 | `4623109` `wire_spec.py:1-123` | 原生 | working | `test_wire_codec.py:339-377` 间接覆盖 | keep | 待定 |
| D06 | wire_types 请求编码（content/part/functionCall/schema 结构） | `4623109` `wire_types.py:247-400` | 原生 | working | `test_gemini_request_normalization.py:136,207,273,504`；`test_wire_codec.py:359-403` | keep | 待定 |
| D06 | wire_codec decode／rewrite／modify_body（模板改写与工具白名单过滤） | `4623109` `wire_codec.py:150-365,488-527` | 原生 | working | `test_wire_codec.py:21-45,305-450`；`test_gemini_request_normalization.py::test_normalize_gemini_request_encodes_function_declarations_to_wire_tools` | keep | 待定 |
| D06 | wire_parser 非流式解析与多 chunk 合并／response_id | `4623109` `wire_parser.py:406-565` | 原生 | working | `test_wire_parser.py:47-177,303-340`；`test_api_responses.py:224-271` | keep | 待定 |
| D06 | build REST body 分支（非流式，surface=build） | `ec35c8d` `generation_service.py:296-300,357-358`；`client.py:691-710` | 新增 | partial | 无 body 构造单测；`test_generation_routing.py:196-235` 仅 build busy 排队 | port（补 body 用例） | 待定：build body 是否复用 ATG 包装 |
| D06 | build REST body 分支（流式，capture 置空） | `ec35c8d` `generation_service.py:553-563,618-625`；`client.py:581-600` | 新增 | partial | 无流式 body 单测；`test_build_surface.py` 属 D07 | port | 待定 |
| D06 | build 流式事件聚合为 ModelOutput（非流式） | `ec35c8d` `client.py:630-666` | 新增 | working | `test_build_surface.py:303,689`（事件级，D07） | port | 待定 |
| D06 | ATG 配置与按格式 mode 解析（off／prefer／required） | `792f4e1` `src/aistudio_api/application/atg/config.py:21-58,92-170` | 新增 | working | `test_atg_config.py:1-220` | port | 待定：默认 off 是否保留 |
| D06 | ATG 文本／非文本模型过滤 | `792f4e1` `atg/model_filter.py:30-75` | 新增 | working | `test_atg_config.py::test_model_filter_defaults`；`test_atg_injector.py::test_non_text_model_passthrough` | port | 待定 |
| D06 | ATG 客户端自合成通道检测（五信号，防双包装） | `792f4e1` `atg/client_patterns.py:63-126`；`atg/injector.py:129-140` | 新增 | working | `test_atg_client_patterns.py:1-135`；`test_atg_injector.py:43-104` | port | 待定 |
| D06 | ATG 合成历史清理（防止控制轮／tool 声明累积） | `792f4e1` `atg/history_purge.py:15-42`；`atg/__init__.py:52-73` | 新增 | working | `test_atg_injector.py::test_request_dump_validate_roundtrip_is_idempotent` | port | 待定 |
| D06 | ATG 请求注入（合成 tool＋控制轮＋tools／toolConfig 合并） | `792f4e1` `atg/injector.py:104-216`；`atg/__init__.py:52-73` | 新增 | working | `test_atg_injector.py:43-160`；`test_atg_integration.py:100-180` | port | 待定：控制轮 head／tail 默认值 |
| D06 | ATG 非流式解包（synthetic→text＋真实 tool 保留＋冲突标记） | `792f4e1` `atg/extractor.py:86-197` | 新增 | working | `test_atg_extractor.py:40-129` | port | 待定 |
| D06 | ATG 流式解包（事件级过滤、side buffer、跨 chunk 参数累积） | `792f4e1` `atg/stream_extractor.py:38-174`；`atg/stream_decoder.py:136-326` | 新增 | working | `test_atg_stream.py:25-131` | port | 待定：side_buffer 默认 0 是否合适 |
| D06 | ATG 合成参数修复（markdown fence／截断 JSON／扫描回退） | `792f4e1` `atg/repair.py:21-192` | 新增 | working | `test_atg_repair.py:10-88` | port | 待定 |
| D06 | ATG 非流式空响应重试（同账号，不换号） | `6a6afbe` `generation_service.py:329-378` | 新增 | working | `test_atg_integration.py:118-150` | port | 待定：empty_retries 默认 2 |
| D06 | ATG 流式空响应重试（produced_content 判定，同账号） | `6a6afbe` `generation_service.py:607-609,728-749` | 新增 | working | `test_atg_integration.py:290-327` | port | 待定 |
| D06 | ATG required 模式校验（缺失显式抛错，不静默透传） | `792f4e1` `atg/extractor.py:188-195`；`atg/stream_extractor.py:158-165`；`generation_service.py:375-378,744-748` | 新增 | partial | `test_atg_extractor.py:110-129`；`test_atg_stream.py:91-103`；`test_atg_integration.py:275-287`（错误进 200 body） | port | 待定：required 是否允许 200 后报错 |
| D06 | ATG E5：ANY＋allowedFunctionNames 白名单合并合成 tool | `792f4e1` `atg/injector.py:165-199`；`wire_codec.py:320-364` | 新增 | working | `test_atg_injector.py:123-160`；`test_atg_integration.py:197-246,329-348` | port | 待定 |
| D06 | countTokens 打包 body（system／tools／conversation 合并进 contents） | `af3f55c` `src/aistudio_api/application/token_counter.py:49-148` | 新增 | working | `test_token_counter.py:36-142` | port | 待定：打包近似是否可接受 |
| D06 | countTokens 上游 RPC（GenerateContent→CountTokens URL 改写＋[0] 解析） | `af3f55c` `client.py:498-555` | 新增 | working | `test_token_counter.py:243-299`（mock session） | port | 待定：真实 RPC 形状 `未验证` |
| D06 | countTokens 路由（Gemini／Claude／Responses 三面） | `af3f55c` `routes_gemini.py:44-57`；`routes_claude.py:171-195`；`routes_responses.py:62-97` | 新增 | working | `test_token_count_routes.py:41-148` | port | 待定 |
| D06 | OpenAI Chat Completions 适配面（请求转换＋非流／流响应） | `c0412c8` `format_converters/openai_request.py:1-444`；`openai_response.py:66-220`；`routes_openai.py:70-147` | 新增 | working | `test_format_openai.py:464-602,780-873` | port | 待定 |
| D06 | Claude Messages 适配面（thinking／tool_use／tool_result／count_tokens） | `1375646` `format_converters/claude_request.py:1-446`；`claude_response.py:77-352`；`routes_claude.py:73-195` | 新增 | working | `test_format_claude.py:122-539,540-915` | port | 待定 |
| D06 | OpenAI Responses 适配面（instructions／function_call／reasoning item／usage） | `2b39b53` `format_converters/responses_request.py:1-385`；`responses_response.py:44-415`；`routes_responses.py:100-177` | 新增 | working | `test_format_responses.py:72-244` | port | 待定 |
| D06 | 格式共享层（媒体下载白名单＋thought signature 缓存） | `dcb7592` `format_converters/common.py:1-195`；`media.py:1-180`；`signature_cache.py:1-153` | 新增 | working | `test_format_claude.py:174-217,862-915`；`test_format_openai.py:174-217,816-873`；`test_signature_cache.py:10-27` | port | 待定 |
| D06 | 格式面 feature gate（features.formats.*） | `dcb7592` `api/format_errors.py:69-114`；三路由 `require_*` | 新增 | working | `test_format_error_routing.py:61-85`；`test_format_claude.py:740-751` | port | 待定 |
| D06 | 协议错误映射（OpenAI／Claude／Gemini 状态码与 body 形状） | `6a6afbe` `format_errors.py:18-66,126-220` | 新增 | working | `test_format_error_routing.py:28-106`；`test_format_responses.py:269-287` | port | 待定 |
| D06 | 上游错误分类与切号／同号重试决策 | `6a6afbe` `upstream_errors.py:98-392`；`generation_service.py:424-467,694-726` | 新增 | working | `test_generation_routing.py:238-306`；`test_api_responses.py:104-127` | port | 待定 |
| D06 | 测试钩子 forced 429（仅 AISTUDIO_TEST_HOOKS=1 生效） | `b59d6f3` `application/testing_hooks.py:35-62`；`generation_service.py:335-340,612-617`；`token_counter.py:191-196` | 新增 | working | `test_testing_hooks.py:9-25` | keep | 待定：是否保留到生产 |
| D06 | 非流式 204 空响应同号重试 | `6a6afbe` `upstream_errors.py` retry 分支；`generation_service.py:303-315` | 新增 | working | `test_generation_routing.py:238-254` | port | 待定 |
| D06 | 取消／断连时 lease 释放（finally＋holder） | `6a6afbe` `generation_service.py:79-91,453-457,819-852` | 新增 | working | `test_generation_routing.py:292-306,336-355` | port | 待定 |
| D06 | 原始请求／响应落盘（dump_raw_response） | `4623109` `client.py:426-451`；`streaming.py:25-50` | 原生 | unverified | 未验证（仅 settings 开关，无测试） | keep | 待定：重建是否保留 |
| D06 | chat 流式网关聚合（status→chunk→finish／usage，非 200 分类） | `4623109` `streaming.py:69-155` | 原生 | unverified | `test_stream_transport.py` 仅底层；网关无单测 | keep | 待定 |
| D06 | 生图旧路径（size 映射＋search tool＋replay＋保存） | `4623109` `client.py:752-840`；`wire_codec.py:36-149` | 原生 | working | `test_multimodal_pipeline.py:43-88`；`test_wire_codec.py:46-133` | keep | 待定：是否随新管线保留 |

## 冲突/缺口

| # | 冲突/缺口 | 证据 | 影响 |
|---|---|---|---|
| 1 | Gemini 流式错误恒为 HTTP 200，错误只进 SSE body；Claude／OpenAI／Responses 仅“首事件前”可返回真实状态 | `api_service_gemini.py:228-263`（StreamingResponse 先建）；`routes_claude.py:130-144`；`generation_service.py:589-593` 首事件是 routing_wait | 同一上游 429 在不同协议面状态码不一致；Gemini 侧客户端无法靠状态码重试 |
| 2 | HTTP 200 已出后错误只能塞 body；已出正文后 `has_yielded_data` 禁止切号，但 thought_signature 等元数据不计入 | `upstream_errors.py:195-343`；`generation_service.py:694-726,836-849`；`test_api_responses.py:224-271` | 正文中途 429／断流不可换号；仅元数据阶段仍可换号 |
| 3 | countTokens 把 system／tools／conversation 拼成单条 user 文本再计数，非原生结构化计数 | `token_counter.py:83-148`；`test_token_counter.py:36-142` | 与上游真实 structured count 存在偏差，客户端计费/配额预估可能不准 |
| 4 | build 面走 REST body 分支，chat 面走 capture／replay JSPB 模板；两套请求形态并存 | `generation_service.py:296-300,553-563`；`client.py:581-600,691-710` | 同一 ATG／normalize 结果未必在两面等价；build body 无单测 |
| 5 | build 流式首帧后错误只能进事件流；D06 只覆盖分支，不覆盖 BuildSurface 内部重试 | `generation_service.py:618-625`；`client.py:596-600`；`test_build_surface.py` 属 D07 | 200 已出后 build 错误无法用状态码表达，需 D07 共同定边界 |
| 6 | ATG side_buffer 默认 0，late synthetic call 到达时已 flush 的正文无法收回，只标 conflict | `atg/stream_extractor.py:12-18,86-94,121-126`；`atg/config.py:100` | prefer 模式下可能同时输出前言与合成答案 |
| 7 | ATG 空响应判定只看 text／thinking／function_calls，忽略 images／code_output | `generation_service.py:103-108` | 纯图片／纯代码结果会被误判为空并触发重发；ATG 仅文本模型时影响有限 |
| 8 | 非流式 Gemini 纯文本 part 不回传 candidate.thought_signature；仅 functionCall／图片带签名 | `api/responses.py:80-91,108-109`；`api_service_gemini.py:205-225` | 文本多轮 thinking 签名可能丢失，影响后续轮次一致性 |
| 9 | Gemini SSE 终块 usage／finish／responseId 无独立断言；response_id 在各协议渲染处也未断言 | `api_service_gemini.py:114-144`；`test_format_openai.py:459` 仅构造 | 终块字段回归不可检测 |
| 10 | 未验证项：capture 鉴权头分支、raw dump、chat 流式网关聚合、真实 CountTokens RPC、build REST body | `transport.py:69-116`；`client.py:426-451,498-555`；`streaming.py:69-155` | 重建时需补集成证据，不能仅依赖单测 |

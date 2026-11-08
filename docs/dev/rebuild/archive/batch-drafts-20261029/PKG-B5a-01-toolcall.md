# PKG-B5a-01 toolcall

- 步骤：B5a
- 状态：draft（中断时保留本文件并改为 partial/blocked）
- 基线：`.kilo/worktrees/rebuild-upstream`（`99c38ba` + `9b5cdea`，以目标 worktree HEAD 为准）
- 来源：D7 / D06-066 / 09 PG-01..PG-13

## 1. 范围
只做 pg/chat + 酒馆 preset 的离线工具调用链路：PG-01..PG-04 必须复现、修复、回归；PG-05..PG-13 逐项 triage，结论只能是 `block` 或 `defer`。预设形态 harness 必须通过。

基线声明：
- 534 行 feature ledger 只是 inventory，不是任务清单；目标树是唯一实现基线。
- 09 的 `file:line` 来自旧 fork；只迁移根因、fixture 形状、probe 思路、期望行为，不把旧路径当目标树实现文件。

PG 处理：
- PG-01 缺 thoughtSignature、PG-02 首事件前 4xx 被包成 200 SSE、PG-03 finish-only/工具流不终止、PG-04 未强制 Struct：离线复现、修复、回归。
- PG-05 分片聚合、PG-06 下标 10 幽灵调用、PG-07 ATG guard：triage；当前预期 `defer`（目标树无分片证据 / 已有结构载荷守卫 / 无 ATG 模块，均需复验）。
- PG-08 严格工具历史 → `defer` 到 B5b；PG-09 回退前缀 → B2；PG-10 Free/chat 策略 → B1/B2。
- PG-11 toolConfig、PG-12 snapshot hash、PG-13 非 STOP finish：`defer`，先记录/观测，不改语义。

范围外：B5b 的 OpenAI Chat 适配器、真实账号/IP/Google、ATG/Build/UI、534 全量、创建 `B5a.md`。本包不修“模型不会输出工具调用”。

顺序：只读复现 → 最小修复 → 回归 → triage + 预设 harness；不通过不得进 B5b。任一 triage 项若离线 fixture 证明阻塞 B5b，升级 `block` 并停。

## 2. 文件域
- 修改 `src/aistudio_api/application/chat_service.py`：PG-01 仅对 model-role `functionCall` 缺失签名时补占位签名；PG-04 写 wire 前归一 `functionResponse.response` / `functionCall.args`；输出脱敏 `[toolshape]` 摘要。
- 修改 `src/aistudio_api/infrastructure/gateway/wire_types.py`：占位签名写下标 14；`_encode_wire_args` 对非 dict/list 的 Struct 位置兜底，避免裸字符串直落。
- 修改 `src/aistudio_api/infrastructure/gateway/wire_parser.py`：PG-03 先读 finish 再处理空 candidates（当前 `:446-448` 提前 return）；保留 `:523-528` 语义。
- 修改 `src/aistudio_api/infrastructure/gateway/streaming.py`：完整工具调用无 finish 时按有界窗口合成终块；不得把默认 600s 超时当验收窗口。
- 修改 `src/aistudio_api/application/api_service_gemini.py` 与 `src/aistudio_api/api/routes_gemini.py`：PG-02 预读首个流事件；首事件前失败返回真实 HTTP 4xx/5xx，不再 200 + SSE error。
- 新增 `tests/unit/test_pg_toolcall_offline.py`：f01-f04、预设形态 harness、triage probe。
- 修改 `tests/unit/test_gemini_request_normalization.py`、`test_wire_parser.py`、`test_wire_codec.py`、`test_api_responses.py`：补 PG-01..04 断言。
- 只读 `tests/fixtures/pg/f01..f04`（目标基线已有，勿重造；`f03_finish_only.bin` 字节不变）。
- 共享冲突：`chat_service.py`、`api_service_gemini.py` 也是 B5b 只读依赖；本包须保持 Gemini 原生路径向后兼容，B5b 不得在本包合入前改同文件。

## 3. 依赖
- 目标 worktree 可导入 `aistudio_api`，`.venv\Scripts\python.exe` 可用；不依赖真实浏览器/账号。
- `tests/fixtures/pg/f01..f04` 与 `9b5cdea` 的 `.gitattributes` 已就位；fixture 是契约，不是测试产物。
- 占位签名固定为 `context_engineering_is_the_way_to_go`；只用于 `functionCall`，不得给 `functionResponse`/纯文本注入。
- 接口冻结：`normalize_gemini_request` 输入/输出、wire 下标 10/11/14、Gemini SSE 终止事件形状不改名。
- 本包是 D7 的 B5b 前置门；B5a 未全绿不得开始 B5b。

## 4. 测试
- PG-01 `f01_missing_signature.json`：model-role `functionCall` 缺签名 → wire 下标 14 有占位签名；`functionResponse`/text 不得有签名。
- PG-02 `f02_prestream_400.json`：首个事件前上游 400 → HTTP 400 + `application/json`，不得 200 SSE；流内错误仍按既有契约。
- PG-03 `f03_finish_only.bin`：finish-only chunk 不丢 finish；完整 functionCall 后无 finish 时在短有界窗口产出终块/错误，不等 600s。
- PG-04 `f04_string_response.json`：字符串 response → `{"result": ...}` Struct；空 response → `{}`；字符串 args 先试 JSON，失败按约定包装或明确 400。
- 预设形态 harness：用脱敏结构（role/part 类型/name hash/args 形状）跑 `convert → normalize → wire → fake stream → render`，断言“发送文本进 functionResponse、回答进 functionCall.args”。
- triage probe：PG-05..13 各至少一个离线用例或代码证据；结果写 `block`/`defer` 及触发条件，不得直接复用 09 旧结论。
- 全部离线；不得读取真实 cookie/API key、访问 Google、启动真实浏览器账号。

## 5. 命令与证据
在目标 worktree 执行（逐条，失败即停）：
```powershell
New-Item -ItemType Directory -Force docs/dev/evidence/B5a/20261028 | Out-Null
.venv\Scripts\python.exe -m pytest tests/unit/test_pg_toolcall_offline.py -q
.venv\Scripts\python.exe -m pytest tests/unit/test_gemini_request_normalization.py tests/unit/test_wire_parser.py tests/unit/test_wire_codec.py tests/unit/test_api_responses.py -q
.venv\Scripts\python.exe -m pytest tests/unit/test_pg_toolcall_offline.py --junitxml=docs/dev/evidence/B5a/20261028/junit.xml
```
- 证据目录：`docs/dev/evidence/B5a/20261028/`；保存 JUnit XML、失败/通过日志、脱敏 `[toolshape]` 摘要、triage 表。
- 脱敏：只记录 name hash 前 8 位、args/response 类型与键数、`has_signature`、`call_id` 是否存在；不记录正文、完整签名、cookie/header/API key。
- `f03` 只引用路径与字节数；证据不得预填“已通过”，失败也保留原始输出。

## 6. 失败停止
- 任一测试需要真实账号/IP/Google、真实浏览器或网络：立即停，改为离线 fixture/probe。
- 同一根因修复两轮仍不绿：停；记录最小失败输入、期望/实际 wire、最后命令。
- 需要改文件域之外的文件，或改冻结接口（wire 下标/SSE 契约）：停并请求用户裁决。
- PG-01..04 任一无法在不改变上游契约的前提下修复：停；不得用“模型/上游问题”绕过。
- 中断：保留本文件，状态改 `partial`/`blocked`，记录最后命令与证据路径；不得删文件或伪造通过。

## 7. 回滚
- 回滚点：B5a 开始前目标 worktree HEAD；受影响文件仅 §2 实现/测试文件。
- 回滚方式：按文件回退本包提交/补丁；删除新增测试；fixture 不动，`f03` 字节校验。
- 无数据/配置迁移；不触碰 `config.yaml`、账号目录、浏览器 session；证据保留供审计。

## 8. 未决
- 真实 preset 形状未验证：`functionResponse.response` 是字符串还是 dict、历史 `functionCall` 是否带签名、工具名是否每轮换 nonce。
- PG-05/06/07/12/13 是否阻塞 B5b 需以目标树离线结果裁决；09 旧结论不能直接复用。
- PG-03“短有界窗口”秒数未定；必须小于客户端超时，且不能默认 600s。
- `[toolshape]` 日志级别与采样策略、字符串 args 包装还是严格 400，需用户/reviewer 裁决。

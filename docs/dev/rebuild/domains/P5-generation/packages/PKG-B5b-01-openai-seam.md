# PKG-B5b-01 openai seam
- 步骤：B5b
- 状态：draft（中断时保留本文件并改为 partial/blocked）
- 基线：`.kilo/worktrees/rebuild-upstream`（`99c38ba` + `9b5cdea`，以目标 worktree HEAD 为准）
- 来源：D7 / D06-012 / D06-057 / P5-Q1 / 09（仅参考）

## 1. 范围
只做一个集成切片：1 账号 + 1 Context + 1 Page，经新 account/page/routing/active_requests/guard/log seam，发 1 个 OpenAI Chat 请求并收到完整回复。必须验证空 API Key 不放行、无 Page/Context 泄露、请求结束 `active_requests` 归零。

基线声明：
- 534 行 feature ledger 只是 inventory，不是任务清单；目标树是唯一实现基线。
- 旧 fork 的 `format_converters/openai_*`、`routes_openai.py` 只作行为参考，不是目标树现有实现文件。
- OpenAI Chat 是上游没有、fork 新增的适配器；不能用 Gemini/Responses 原生路径跑通来冒充 OpenAI 成功。

范围内：
- `POST /v1/chat/completions` 非流式最小闭环：请求转换、响应渲染、错误映射、API Key guard；流式 OpenAI SSE 仅在离线 fake 上验证 `data: {...}` / `[DONE]`；同时断言 1 账号 1 Context 1 Page 生命周期、`active_requests` 获取/释放、请求-尝试日志关联。

范围外：tools/vision/reasoning 全量 parity、Claude/Responses、UI/统计页、多账号/切号、超过 1 次的真实重试、未授权真实测试。

## 2. 文件域
新增：
- `src/aistudio_api/application/format_converters/__init__.py`
- `src/aistudio_api/application/format_converters/openai_request.py`：OpenAI Chat body → 目标树既有 normalized Gemini 请求结构。
- `src/aistudio_api/application/format_converters/openai_response.py`：非流式 `chat.completion` 与流式 `chat.completion.chunk`。
- `src/aistudio_api/application/openai_chat_service.py`：单请求切片；调用 B2 routing/active_requests seam，不复制完整 Gemini 重试机。
- `src/aistudio_api/api/routes_openai.py`：`/v1/chat/completions`；首事件预读复用 B5a 契约。
- `src/aistudio_api/api/format_errors.py`：OpenAI 错误体最小映射（403/429/500/502）。
- `tests/unit/test_openai_request.py`、`test_openai_response.py`、`test_routes_openai.py`、`test_openai_chat_seam.py`；fixture `tests/fixtures/openai/chat_basic.json`（合成、脱敏）。
修改：
- `src/aistudio_api/api/app.py`：注册 OpenAI router，依赖 `require_api_key`；与其它协议路由包共享，冲突风险单列。

只读共享（不修改）：
- `src/aistudio_api/application/chat_service.py`：复用 `normalize_gemini_request`；B5a 已改，B5b 不碰。
- `src/aistudio_api/infrastructure/gateway/client.py`、`session.py`：复用 `stream_generate_content` 与 `request_scope`；目标树 `session.py:136-147` 只是 `_in_flight` 前身，不是最终 `active_requests` seam。
- `src/aistudio_api/api/dependencies.py`、`config.py`：B1 负责 `security.api_allow_anonymous` 与空 Key 拒绝；本包只测试，不改 guard。

## 3. 依赖
- B5a 已通过（D7 门槛）；PG-01..04 回归绿。
- B1 已交付 API guard：`security.api_allow_anonymous` 默认 false；空 `api_keys`/空 Key 不放行；`AISTUDIO_WEB_PASSWORD` 语义不变。
- B2 已交付 account/page/routing/active_requests seam：同账号 1 Context 1 Page；acquire/release 可观测；无页/Context 泄露。
- B4 已交付日志关联；B3 非本切片硬依赖（本地 1 请求不验证 24h，但 VPS 跑 P5 前仍须 B3）。
- 真实测试：必须到测试位置、确认环境后取得用户实时授权；每次授权只跑 1 个请求。

## 4. 测试
离线：
- `test_openai_request.py::test_basic_chat_request_conversion`：messages/system/max_tokens → 目标 normalized 请求；不丢 role 顺序。
- `test_openai_response.py::test_non_stream_shape`：`id/object/created/model/choices[].message/usage` 完整，content 非空。
- `test_openai_response.py::test_stream_sse_shape`：`data: {...}` 增量与 `data: [DONE]`；无 Gemini 原生字段泄露。
- `test_routes_openai.py::test_empty_api_key_rejected`：默认 `api_allow_anonymous=false`，无 Key/空 `Authorization`/空 `x-api-key` → 401；`api_allow_anonymous=true` 时才允许无 Key；管理页密码不被 API Key 锁死。
- `test_routes_openai.py::test_error_envelope`：上游 403/429/500/502 → OpenAI 错误体与状态码；不返回 Gemini body。
- `test_openai_chat_seam.py::test_one_request_releases_active_requests`：fake seam 断言 acquire 1、page/context 各 1、finally release、结束 `active_requests==0`、无残留 page/context。

真实（仅用户授权后）：
- 只发 1 个非流式 `POST /v1/chat/completions`，合成短 prompt、小 `max_tokens`；不重试、不并发。
- 期望 HTTP 200、`choices[0].message.content` 非空、`usage` 存在；失败即停并收集脱敏日志。
- 请求后断言：账号数 1、Context 数 1、Page 数 1、`active_requests==0`、无额外 CDP target；否则立即判失败。

## 5. 命令与证据
离线命令（目标 worktree）：
```powershell
New-Item -ItemType Directory -Force docs/dev/evidence/B5b/20261028 | Out-Null
.venv\Scripts\python.exe -m pytest tests/unit/test_openai_request.py tests/unit/test_openai_response.py tests/unit/test_routes_openai.py tests/unit/test_openai_chat_seam.py -q --junitxml=docs/dev/evidence/B5b/20261028/junit.xml
```
- 真实命令（本任务不执行；授权后跑）：临时 `AISTUDIO_CONFIG_FILE`/`AISTUDIO_DATA_DIR` 启动服务，`curl.exe` 发 1 次 `/v1/chat/completions`，Key 从环境变量读取。
- 证据目录：`docs/dev/evidence/B5b/20261028/`；保存 JUnit XML、脱敏请求/响应、seam 计数快照、关联日志。Key/cookie 全隐藏，account id 与 page target id 只留 hash 前缀，正文截断。

## 6. 失败停止
- B5a 未通过、B1 guard 未落地、或空 Key 被放行：停，报 B1/B5a 阻塞，不自行改 guard。
- 出现第 2 个 active Page/Context、`active_requests` 非 0、或请求后 target 残留：立即停，先关闭测试页/Context 并保留现场证据。
- 未取得用户授权就执行真实请求：停；授权后每次只跑 1 个请求，失败不重试。
- 用 Gemini/Responses 原生路径冒充 OpenAI 成功：停，判定未验收。中断时保留本文件并改 `partial`/`blocked`，记录最后命令与证据路径。

## 7. 回滚
- 回滚点：B5b 开始前目标 worktree HEAD；回退 B5b 提交/补丁，删除新增 adapter/route/tests/fixture，恢复 `api/app.py` 注册行。
- 真实测试后回滚：关闭测试创建的 Page/Context，确认无孤儿 target/process；临时 data dir 删除或恢复；不触碰生产 config/账号/cookie；证据保留。

## 8. 未决
- B2 最终 `active_requests` API 名称/返回形状未冻结；按 B2 包接口适配，不能拿 `_in_flight` 直接冒充。
- B5b 是否复用 Gemini 重试/切号机未定；本切片选择单次 fail-fast，完整 P5 再接重试。
- OpenAI 错误体最小范围（是否含 `error.type/code/param`）与流式 tool_calls/thinking/images/usage 细节留待 reviewer/P5 后续；真实测试用哪个账号 tier/chat 模型需用户指定，403 按 D-04 返回，不请求期切号。

# REV-C3 — Claude Messages / OpenAI Responses 协议适配 独立只读终审（P2-1 修复复核）

- Audit date: 2026-11-08（本地 UTC+8；命令日志时间）。
- Target worktree: `D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`。
- Target HEAD: `af01faa34dc376aab907f12c4a04fab201e44186`（`af01faa`，C3 改动未提交）。
- Main repo: `D:\LLM\self_programming\aistudio-api`，HEAD `a9fee377a033a65ab7a70b9114720271d119638b`（`a9fee37`）。
- Audited delta: 6 个已跟踪修改 + 11 个未跟踪新增实现/测试文件 + 本报告：
  - `M defaults/config.schema.json`
  - `M defaults/config.yaml`
  - `M src/aistudio_api/api/app.py`
  - `M src/aistudio_api/api/routes_system.py`（P2-1 轮次新增 N-5 修复）
  - `M src/aistudio_api/api/ui_sandbox.py`
  - `M tests/unit/test_debug_dashboard.py`
  - `?? src/aistudio_api/api/routes_claude.py`（252 行）
  - `?? src/aistudio_api/api/routes_responses.py`（249 行）
  - `?? src/aistudio_api/application/contracts/claude.py`（53 行）
  - `?? src/aistudio_api/application/contracts/responses.py`（36 行）
  - `?? src/aistudio_api/application/format_converters/claude_request.py`（628 行）
  - `?? src/aistudio_api/application/format_converters/claude_response.py`（359 行）
  - `?? src/aistudio_api/application/format_converters/responses_request.py`（557 行）
  - `?? src/aistudio_api/application/format_converters/responses_response.py`（594 行）
  - `?? tests/unit/test_format_claude.py`（1,252 行）
  - `?? tests/unit/test_format_responses.py`（1,035 行）
  - `?? tests/unit/test_routes_claude_responses.py`（546 行）
- Method: 只读 diff/source/test 审查；离线 `uv run ruff check .`、`uv run pyright src tests`、`uv run pytest -q`；离线 `bun run lint`、`bun run type-check`、`bun test`、`bun run build`；离线 ASGI `httpx.ASGITransport` 手工探针；`git` 状态/差异/换行/暂存/跟踪检查。**未启动生产服务、未启动真实浏览器、未发起真实网络请求，未修改任何源码/测试/配置/AGENT/static；本报告仅写入目标树与主仓两份副本。未 stage、未 commit、未 push。**

## 0. Verdict

**PASS — 0 个未关闭 P0，0 个未关闭 P1，0 个未关闭 P2，0 个未关闭 Q。**

P2-1 已闭环：`HTTPException.headers` 在 Claude/Responses 的 405/401 路径被回填；Claude/Responses 405 返回 `Allow: POST`，401 返回 `WWW-Authenticate: Bearer`；OpenAI 405 仍为 `{"detail":"Method Not Allowed"}` 且保留 `Allow: POST`；Gemini 401 body 形状不变。N-1/N-4/N-5/N-7 已闭环；N-2/N-3/N-6/N-8..N-12 保留为非阻塞观察。C3 的 PG-08/PG-01/PG-04、Responses 无状态/宽松参数、SSE、API key 三头、默认四协议、层边界与 D-04/D-06/E-04 决策均仍成立。

## 1. P2-1 终审（修复闭环）

### 1.1 源码证据

- `src/aistudio_api/api/app.py:606-614` 新增 `_attach_exception_headers`：读取 `exc.headers` 并逐项写入 `JSONResponse.headers`。
- `src/aistudio_api/api/app.py:617-638` 的 `starlette_http_exception_handler`：Claude/Responses 路径包装为 `HTTPException` 后调用 `format_error_response`，再用 `_attach_exception_headers(...)` 回填 `exc.headers`；非 Claude/Responses 路径直接返回 `{"detail": ...}` 并保留 headers。
- `src/aistudio_api/api/app.py:641-674` 的 `http_exception_handler`：非 Gemini 分支改为 `_attach_exception_headers(format_error_response(exc, protocol), exc)`；Gemini 分支保持原有 body/header 行为。
- `src/aistudio_api/api/app.py:711-719` 的 validation handler 仅按协议渲染 body；无 headers 需求。

### 1.2 实际 HTTP 探针（离线 ASGI，无网络）

```text
GET /v1/messages            -> 405, allow=POST, body {"type":"error","error":{"type":"api_error","message":"Method Not Allowed"}}
GET /v1/responses           -> 405, allow=POST, body {"error":{"message":"Method Not Allowed","type":"api_error","code":null,"param":null}}
GET /v1/chat/completions    -> 405, allow=POST, body {"detail":"Method Not Allowed"}
POST /v1/messages (no key)  -> 401, www-authenticate=Bearer, Claude 错误体
POST /v1/responses (no key) -> 401, www-authenticate=Bearer, Responses 错误体
POST /v1/chat/completions   -> 401, www-authenticate=Bearer, OpenAI 错误体
POST Gemini generateContent -> 401, www-authenticate=Bearer, {"error":{"code":401,...,"status":"UNAUTHENTICATED"}}
```

结论：P2-1 的 405 `Allow` 与 401 `WWW-Authenticate` 均已恢复；OpenAI 405 body/header 与 Gemini 401 body 形状未回归。

### 1.3 回归测试证据

- `tests/unit/test_routes_claude_responses.py:184-200`：Claude/Responses 无 key 401 均断言 `WWW-Authenticate == "Bearer"`。
- `tests/unit/test_routes_claude_responses.py:311-320`：Claude/Responses GET 405 均断言 `Allow == "POST"`。
- `tests/unit/test_routes_claude_responses.py:323-328`：OpenAI 405 断言 `Allow == "POST"` 且 body 精确等于 `{"detail":"Method Not Allowed"}`。
- `tests/unit/test_routes_claude_responses.py:532-545`：Gemini 401 body status 为 `UNAUTHENTICATED`，OpenAI 401 带 `WWW-Authenticate: Bearer`。
- 定向执行 `uv run pytest -q tests/unit/test_routes_claude_responses.py`：**44 passed in 1.08s**。

## 2. N 项终审

| N | 状态 | 证据 |
|---|---|---|
| N-1 `defaults/config.yaml` 注释仍写默认关闭 | **CLOSED** | `defaults/config.yaml:108-109` 已改为“openai / claude / responses：默认启用，可显式设为 false 关闭”；`:113-115` 值为 true。 |
| N-2 设置页 UI 文案仍写默认关闭 | 保留（非阻塞） | `web/src/components/settings/ProtocolAtgCard.vue:161,176` 仍为“默认关闭”；C3 按任务仅后端，未改 UI；`bun run build` 后 static diff 0。 |
| N-3 文档仍描述旧默认值/未启用状态 | 保留（非阻塞） | `docs/changelog.md:49`、`docs/PROTOCOL_AND_TOOLCALL_SPEC.md:79,89,755` 仍为旧描述；非运行缺陷。 |
| N-4 `routes_responses.py` docstring 过期 | **CLOSED** | `routes_responses.py:1-5` 已改为“registered on the app ... defaults ship enabled”。 |
| N-5 `routes_system.formats_status` 缺省回退 false | **CLOSED** | `routes_system.py:84-86` 三个 `raw.get(..., True)`；测试 `test_system_status_format_fallbacks_default_true`（`test_routes_claude_responses.py:516-524`）通过。 |
| N-6 `_is_openai_path` 无调用方 | 保留（非阻塞） | `app.py:583-584` 仍为死代码；不影响行为。 |
| N-7 Claude 模型前缀剥离不完全对称 | **CLOSED** | `routes_claude.py:62-71` 改为循环剥离 `models/`/`chat/` 并 `.strip()`；测试 `test_format_claude.py:849-862` 覆盖 `chat/models/...`、`models/models/...`、带空白输入。 |
| N-8 Responses 响应对象硬编码字段 | 保留（非阻塞） | `responses_response.py:168-181` 仍固定 `tools: []`、`temperature: None`、`text.format=text`、`truncation: disabled`、`max_output_tokens: None` 等；当前 checklist 未要求请求回声。 |
| N-9 usage-only / routing_wait-only 流终态差异 | 保留（非阻塞） | Claude 预读跳过 `usage`（`routes_claude.py:111-115,155-158`），Responses 把 `usage` 视为首事件（`routes_responses.py:151-155,186-189`）；边缘语义观察。 |
| N-10 测试盲区（部分关闭） | 部分关闭（非阻塞） | 405 `Allow`/401 `WWW-Authenticate` 回归已新增（§1.3）；Responses usage-only 终态与 Responses seed caplog 文本仍无直接断言。 |
| N-11 Claude `reasoning_images` 走文本块 | 保留（非阻塞） | `claude_response.py:200-204` 仍与 `images` 同路径渲染为 text。 |
| N-12 429 类型在 app/route 路径存在既有差异 | 保留（非阻塞） | `error_mapping.py` 的 Responses 429 与 `format_errors.py` 的 429 类型映射不同；非 C3 新引入。 |

## 3. C3 关键决策复核

| 决策 | 结论 | 证据 |
|---|---|---|
| PG-08 Claude 未知 `tool_use_id` / Responses 未知 `call_id` 不 400，确定性 fallback | 通过 | `claude_request.py:298-341`；`responses_request.py:331-356,407-431`；测试 `test_format_claude.py:349,370,401,1171`；`test_format_responses.py:295,703`；全量 pytest 通过。 |
| PG-01 signature_cache / DUMMY | 通过 | `claude_request.py:287-295`；`responses_request.py:395-404`；测试 `test_format_claude.py:316-347`、`test_format_responses.py:168-210`。 |
| PG-04 function args/results 始终 Struct | 通过 | `claude_request.py:110-172`；`responses_request.py:144-197`；测试 `test_format_claude.py:431-492`、`test_format_responses.py:212-293`。 |
| Responses 无状态与宽松参数 | 通过 | `responses_request.py:438-457,478-538`；响应 `store:false`（`responses_response.py:160-181`）；测试 `test_format_responses.py:352-388`。 |
| Claude SSE 序列与 PG-02 | 通过 | `claude_response.py:155-228,246-356`；`routes_claude.py:97-162`；测试 `test_format_claude.py:817,938,970,995,1019,1042,1066`。 |
| Responses REST/SSE 序列、incomplete、failed id/sequence、output_index、keepalive | 通过 | `responses_response.py:149-185,297-512,544-587`；测试 `test_format_responses.py:472,567,589,604,625,766,801,819,845,863,881,904`。 |
| API key 三头与 401/403 body | 通过 | `dependencies.py:159-176`；测试 `test_routes_claude_responses.py:147-210`；401 headers 见 §1.2。 |
| 默认四协议 true + ui_sandbox 四开 + status 同步 | 通过 | `defaults/config.yaml:113-115`；`defaults/config.schema.json:163-170`；`ui_sandbox.py:1116-1151`；`routes_system.py:69-87`；测试 `test_routes_claude_responses.py:462-524`、`test_debug_dashboard.py:126-157`。 |
| 分层边界与统一 GenerationService/KeepaliveIterator/active_requests | 通过 | `test_layer_boundaries.py:76-95`；全量 pytest 通过；Claude/Responses 路由复用 `get_routing_pipeline` + `stream_generation` + `KeepaliveIterator`。 |
| D-04/D-06 tier 403 四字段、不切号；auth 403 原位重签；429 耗尽 503 | 通过 | `routing_pipeline.py:681-683,696-734,736-767,1012-1060`；`error_mapping.py:129-182`；测试 `test_routes_claude_responses.py:346-377`、`test_403_auth_refresh.py`、`test_routing_pipeline.py` 全量通过。 |
| E-04 seed 告警忽略且不写 `generationConfig` | 通过 | `claude_request.py:552-561`；`responses_request.py:315-317`；测试 `test_format_claude.py:599`、`test_format_responses.py:369`。 |

## 4. 工作树状态、范围与洁净

目标树 `git status --porcelain=v1` 为 6 个已跟踪修改 + 11 个未跟踪实现/测试文件 + 本报告：

```text
 M defaults/config.schema.json
 M defaults/config.yaml
 M src/aistudio_api/api/app.py
 M src/aistudio_api/api/routes_system.py
 M src/aistudio_api/api/ui_sandbox.py
 M tests/unit/test_debug_dashboard.py
?? docs/dev/rebuild/domains/P5-generation/reviews/REV-C3.md
?? src/aistudio_api/api/routes_claude.py
?? src/aistudio_api/api/routes_responses.py
?? src/aistudio_api/application/contracts/claude.py
?? src/aistudio_api/application/contracts/responses.py
?? src/aistudio_api/application/format_converters/claude_request.py
?? src/aistudio_api/application/format_converters/claude_response.py
?? src/aistudio_api/application/format_converters/responses_request.py
?? src/aistudio_api/application/format_converters/responses_response.py
?? tests/unit/test_format_claude.py
?? tests/unit/test_format_responses.py
?? tests/unit/test_routes_claude_responses.py
```

- `git diff --stat`：6 个已跟踪文件，111 insertions / 42 deletions；仅 defaults/api/tests，无 `web/` 或 `static/`。
- `git diff --cached --name-only` 为空；`git diff --cached --check` 退出码 0：无 staged 内容。
- 目标 HEAD 仍为 `af01faa34dc376aab907f12c4a04fab201e44186`；主仓 HEAD 仍为 `a9fee377a033a65ab7a70b9114720271d119638b`。
- `git diff --check` 退出码 0（仅 `app.py` 的 LF→CRLF advisory warning，非 whitespace error）。
- 两仓库 `git ls-files config.yaml` 均为空；根 `config.yaml` 未被跟踪（`.gitignore:15` / `.gitignore:62`）。
- 目标树没有 `web/` 或 `src/aistudio_api/static` 的 git diff；build 后 static diff 为 0。
- 写入报告前主仓干净；写入后主仓仅新增本报告未跟踪文件。未修改任何源码/测试/配置/AGENT/static。

## 5. 门禁结果

| 门禁 | 结果 | 证据 |
|---|---|---|
| `uv run ruff check .` | 通过 | `All checks passed!` |
| `uv run pyright src tests` | 通过 | `0 errors, 0 warnings, 0 informations` |
| `uv run pytest -q` | 通过 | **1249 passed in 28.41s**，0 failed，0 skipped；与 Worker 声称 1249 一致 |
| `uv run pytest -q tests/unit/test_routes_claude_responses.py` | 通过 | **44 passed in 1.08s** |
| `cd web && bun run lint` | 通过 | exit 0 |
| `cd web && bun run type-check` | 通过 | exit 0 |
| `cd web && bun test` | 通过 | **39 pass / 0 fail / 155 expect / 5 files**；与 Worker 声称 39 一致 |
| `cd web && bun run build` | 通过 | Vite 1788 modules，build 成功 |
| build 后 static diff | 通过 | `git diff -- src/aistudio_api/static` 为 0 行 |
| `git diff --check` | 通过 | exit 0（仅 CRLF advisory warning） |
| `git diff --cached --check` | 通过 | exit 0；无 staged 内容 |
| `git ls-files config.yaml`（双仓） | 通过 | 均为空 |
| HEAD 稳定性 | 通过 | 目标 `af01faa`，主仓 `a9fee37` |
| stage/commit/push | 通过 | 无 staged；未 commit；未 push |
| 临时文件/进程 | 通过 | 审计未创建临时文件；未启动服务/浏览器/网络 |

## 6. 问题清单（P0/P1/P2/Q/N 全覆盖，不省略）

### 6.1 P0（阻塞）

无。

### 6.2 P1（严重）

无。

### 6.3 P2（需修复后重审）

无。P2-1 已闭环，证据见 §1。

### 6.4 Q（待确认）

无开放 Q。

### 6.5 N（非阻塞观察 / 残余风险）

- **N-1（CLOSED）** `defaults/config.yaml:108-109` 注释已与 `:113-115` 的 true 对齐。
- **N-2（保留）** `web/src/components/settings/ProtocolAtgCard.vue:161,176` UI 文案仍写“默认关闭”；C3 仅后端，未触碰 UI，static diff 0。
- **N-3（保留）** `docs/changelog.md:49`、`docs/PROTOCOL_AND_TOOLCALL_SPEC.md:79,89,755` 仍描述旧默认值/未启用状态。
- **N-4（CLOSED）** `routes_responses.py:1-5` docstring 已更新为已注册、默认启用。
- **N-5（CLOSED）** `routes_system.py:84-86` 缺省回退已改为 true，并有 `test_system_status_format_fallbacks_default_true` 覆盖。
- **N-6（保留）** `app.py:583-584` 的 `_is_openai_path` 仍无调用方。
- **N-7（CLOSED）** `routes_claude.py:62-71` 已循环剥离 `models/`/`chat/`；测试覆盖 `chat/models/...` 等形态。
- **N-8（保留）** `responses_response.py:168-181` 仍硬编码 `tools: []`、`temperature: None`、`text.format=text`、`truncation:disabled` 等。
- **N-9（保留）** usage-only/routing_wait-only 流在预读超时后的终态：Claude 502 vs Responses 200 + 空 `response.completed`（`routes_claude.py:111-115,155-158`；`routes_responses.py:151-155,186-189`）。
- **N-10（部分关闭）** 405 `Allow`/401 `WWW-Authenticate` 回归已补；Responses usage-only 终态、Responses seed caplog 文本仍无直接断言。
- **N-11（保留）** `claude_response.py:200-204` 将 `reasoning_images` 与 `images` 一样渲染为 text。
- **N-12（保留）** 429 类型在 `error_mapping.py` 与 `format_errors.py` 的既有映射差异仍在。

## 7. 未解决项

- **无未关闭 P0/P1/P2/Q。**
- N-2/N-3 属 UI/文档文案同步，建议后续 UI/文档更新处理；N-6 为死代码清理；N-8/N-9/N-11 为协议兼容性观察；N-10 剩余测试盲区；N-12 为既有类型差异。全部非阻塞。

## 8. 只读声明、提交状态与报告副本

- 本次审计未修改任何源码、测试、配置、`AGENT.md` 或 `src/aistudio_api/static`；只写入本报告的 target 与主仓两份副本。
- 未启动生产服务、真实浏览器或真实网络；所有探针均为进程内 ASGI / 离线 fake pipeline。
- 未 stage、未 commit、未 push。目标 HEAD 仍为 `af01faa`，主仓 HEAD 仍为 `a9fee37`。
- 主仓写入后仅有本报告一个未跟踪文件；目标树无额外临时文件/进程。
- 双写路径：
  - `D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream\docs\dev\rebuild\domains\P5-generation\reviews\REV-C3.md`
  - `D:\LLM\self_programming\aistudio-api\docs\dev\rebuild\domains\P5-generation\reviews\REV-C3.md`

报告 SHA256 由最终消息返回；两份副本字节一致。

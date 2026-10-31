# Step B6 本地实机综合验收审计报告

- 日期：2026-10-31（B4 复核：2026-11-01 离线）
- 目标树：`.kilo/worktrees/rebuild-upstream`
- 代理：`http://127.0.0.1:10809`
- 浏览器：主工作区 CloakBrowser
- 服务启动次数：**1 次常驻**
- 生成请求：**5 次 / 预算 5 次，5 次成功**
- 结论：**PASS（功能实机全通过；B4 导出脱敏与历史日志清理为离线复核通过，未新增实机运行）**

## 1. 阶段 1：0 生成验收

| 项目 | 结果 | 证据 |
|---|---|---|
| `/health` | PASS 200 | 本地服务 |
| 空 API Key | PASS 401 | `/v1/chat/completions` |
| 有效 Key `/v1/models` | PASS 200，31 个 | live_page |
| `/v1beta/models` | PASS 200，31 个 | live_page |
| `model_discovery.last_source` | PASS `live_page` | 服务日志 |
| 实例池 | PASS 2 个 active：Free / Pro；in_flight 0 | `/api/instances` |
| 实例字段 | PASS `only_tier/min_tier/max_tier` | `/api/instances` |
| 控制面重复预热 | PASS 未发生；启动仅 `InstanceManager.startup()` | 服务日志 |
| `GET /api/keepalive` | PASS 200 | 本地 |
| Ultra 本地 403（Gemini） | PASS 403，含 `tier/model/required_tier/current_tier` | `veo-3.1-generate-preview` |
| Ultra 本地 403（OpenAI） | PASS 403，含相同四字段 | `/v1/chat/completions` |
| countTokens | PASS 200 JSON，`totalTokens` 可解析 | `gemini-3.8-flash` |

## 2. 阶段 2：生成与工具

| # | 场景 | 结果 | 证据 |
|---|---|---|---|
| 1 | OpenAI Free 非流式 `gemini-3.8-flash` | PASS 200，内容 `OK`，有 response id，命中 inst-1 | 200 |
| 2 | OpenAI Paid 流式 `gemini-3.6-flash` | PASS 200 SSE，3 个事件、`OK`、`[DONE]`，命中 inst-2 | 200 |
| 3 | 原生 Gemini 流式 `gemini-3.8-flash` | PASS 200，2 个事件、`OK`、`finishReason=STOP`、usage 存在 | 200 |
| 4 | 原生 tools / signature_cache / `[toolshape]` | PASS 200，`get_weather` functionCall，缓存 0→1，`[toolshape]` 日志存在 | 200 |
| 5 | PG-08 + ATG prefer 流式 | PASS 200，3 个事件、`OK`、done，无 ATG/伪工具名泄漏 | 200 |
| — | B4 日志页/SSE/导出 | PASS `/logs` 200 HTML、SSE 200、export 200 | 本地 |

## 3. B4 安全修正（离线复核）

首次验收发现历史归档仍含 B6 测试 API Key 前缀与 Cookie 头行。已修正并离线复核：

1. `routes_system.py` 的 create/delete/update API Key 日志不再输出 Key 或前缀，统一固定为 `***`。
2. `log_broadcast.sanitize_log_message` 在 ring/file 写入时整段遮蔽 `Authorization:`、`Cookie:`、`Set-Cookie:`、`x-goog-api-key`/`api_key`，并覆盖独立 `sk-*` 令牌与 `/api-keys/<key>` 路径。
3. `/logs/export` 单文件与 ZIP 导出在读取归档后逐行经过 `sanitize_log_message`，历史行不再原样外泄。
4. `data/logs/aistudio-*.log` 已一次性按行重写并验证幂等：`sk-b6-` 0 条、Cookie/Authorization 明文值 0 条；`[toolshape]` 2160 行与 `pg08` 474 行保留。
5. 上述复核均为离线静态/数据校验，未启动服务、未拉起浏览器、未发起网络请求，**未新增实机运行**。

## 4. 安全与清理

- 未打印任何邮箱、Cookie、API Key、真实 IP 明文；证据文件仅记录脱敏后的统计。
- 临时 API Key 已移除；`api_keys` 数量恢复为 0。
- `features.atg.mode` 已恢复默认 off。
- 根 `config.yaml` 仍 untracked，`git ls-files config.yaml` 为空。
- 单次服务已优雅停止；8080/9222 关闭，Chrome 进程 0。
- `data/logs` 仍被 git 忽略，清理未新增任何跟踪文件。

## 5. 证据

- `docs/dev/evidence/B6/results.json`
- `docs/dev/evidence/B6/diagnostics.json`

## 6. 结论

**B6 判定 PASS。**

- 单次启动、Free/Pro 池、live 模型发现、Free/Paid 非流式与流式、原生流式、原生 tools、signature_cache、`[toolshape]`、PG-08 + ATG、四字段 403、countTokens 全部通过。
- B4 导出脱敏与历史日志清理离线复核通过；没有新增实机运行，也没有重跑生成预算。
- 代码与证据保持未提交状态，交由 Reviewer 审核。

## 7. 最终定稿审计（独立只读离线复核，2026-11-01）

- 审计结论：**PASS**
- 阻断项：**无**（无 file:line）
- 审计方式：只读静态审计 + 离线门禁；未启动 `main.py server`、未拉起 Chromium/CloakBrowser、未发起网络请求、未执行 Docker build、未修改源码/测试/配置、未 stage/commit。

### 7.1 审计证据

| 审计项 | 结论 | 证据 |
|---|---|---|
| API Key create/delete/update 日志不含 Key/前缀 | PASS | `src/aistudio_api/api/routes_system.py:488`、`:536`、`:589` 固定 `***`；`tests/unit/test_routes_system.py:211-245` 断言 secret 与前缀均不出现 |
| ring/file 写入脱敏 | PASS | `src/aistudio_api/infrastructure/utils/log_broadcast.py:61-78` 覆盖 Authorization/Cookie/Set-Cookie/x-goog-api-key/api_key/sk-*/`/api-keys/`；`:172` ring publish、`:375` daily file 写入前调用 |
| 单 `.log` 与 ZIP 导出逐行脱敏 | PASS | `src/aistudio_api/api/routes_logs.py:224-235`、`:238-249`；`tests/unit/test_logs_routes.py:270-323` 证明历史 `sk-b6-`/Cookie/Authorization 被移除且 `[toolshape]`/`pg08` 保留 |
| 当前 `data/logs` 清理 | PASS | 只读扫描 0 `sk-b6-`、0 原始 Authorization/Cookie/Set-Cookie/x-goog-api-key、0 `AIza*`、0 原始 `/api-keys/`、0 泛化 `sk-*`；`[toolshape]` 2280 行、`pg08` 507 行仍保留（其中 2026-10-31 归档为证据快照 2160/474；2026-11-01 归档追加 120/33） |
| B6 功能证据一致性 | PASS | `docs/dev/evidence/B6/results.json`、`diagnostics.json` 与报告 §1–§2 一致：5/5 生成、countTokens 10、模型 31、双端 403 四字段、tools/signature_cache、PG-08/ATG |
| 安全结论口径 | PASS | 报告 §3.5 与 `results.json` 明确 B4 脱敏/历史清理为离线复核，未新增实机运行；`export_bytes` 明确为旧实机测量而非新导出 |
| `AISTUDIO_CONFIG_PATH` 优先级 | PASS | `src/aistudio_api/config.py:79-111` 显式路径 > `AISTUDIO_CONFIG_PATH` > `AISTUDIO_CONFIG_FILE` > 根 `config.yaml`；缺失时从 `defaults/config.yaml` 播种并建父目录 |
| 原子写与热重载 | PASS | `src/aistudio_api/infrastructure/gateway/model_defaults.py:1023-1034` 临时文件+fsync+`os.replace`；`:1041-1064` 写回；`:1065-1090` mtime 热重载，均经 `_resolve_config_path` |
| 配置路径测试 | PASS | `tests/unit/test_config_path_env.py:16-48` 覆盖播种、优先级、原子写；legacy 路径未被创建 |
| Dockerfile | PASS | `Dockerfile:10` 设置 `AISTUDIO_CONFIG_PATH`；`:91-93` `COPY src/`（含 static）与 `COPY defaults/config.yaml`，不再复制根 `config.yaml` |
| entrypoint | PASS | `docker-entrypoint.sh:4-23` 解析 env path、兼容 legacy、播种 `/app/data/config.yaml` |
| CI/镜像 | PASS | `.github/workflows/ci.yml:3-13` 增加 `rebuild-upstream`；`.github/workflows/docker.yml:3-8` push 分支、`:19` `ghcr.io/<owner>/aistudio-api`、`:50` `rebuild-upstream` 与 `latest`；保留 `workflow_dispatch` |
| 范围/回归 | PASS | `git status` 仅 16 个预期文件 + 新测试 `tests/unit/test_config_path_env.py`；无 UI/static 改动；根 `config.yaml` untracked；无 service/browser/network |
| 未提交状态 | PASS | `git diff --cached` 为空；HEAD 仍 `b000bb7`；无新 commit |

### 7.2 离线门禁（精确状态）

| 门禁 | 结果 |
|---|---|
| `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; .venv\Scripts\python.exe -m pytest -q` | **2 failed, 820 passed, 1 skipped in 10.81s** |
| 失败 1 | `tests/unit/test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser`（既有 Windows 路径语义失败，`tests/unit/test_browser_engine.py:59` 的 `/opt/cloakbrowser` 分支 `:100-137`） |
| 失败 2 | `tests/unit/test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly`（既有 `bun.CMD` Windows shim 失败，`tests/unit/test_browser_scripts_syntax.py:201`） |
| 跳过 | POSIX-only watchdog 用例 |
| `.venv\Scripts\python.exe -m ruff check src tests` | **All checks passed!**（exit 0） |
| `.venv\Scripts\python.exe -m pyright src tests` | **0 errors, 0 warnings, 0 informations**（exit 0） |
| `git diff --check` | exit 0（仅 LF→CRLF 警告） |
| `git diff --cached --check` | exit 0 |

*注：未设置 UTF-8 时，`test_response_parser`/`test_wire_codec` 会因 Windows GBK 默认编码读取 UTF-8 fixture 额外失败；设置 `PYTHONUTF8=1` 后恢复为上述 820/2/1，属环境编码问题，非代码回归。*

### 7.3 剩余 Q / N

- **Q-1 离线脱敏验证**：B4 导出脱敏与历史日志清理仅做离线逐行测试 + 归档重写验证；未做新的实机导出（生成预算已用尽，按会话约束不再启动服务/浏览器/网络）。PASS 依赖离线证据。
- **Q-2 未执行 Docker build**：本次只读审计未构建镜像；Dockerfile、entrypoint、workflow 仅静态核对，实际 `/app/data/config.yaml` 播种与 GHCR 推送未执行。
- **N-1 脱敏模式边界**：`_SK_TOKEN_RE` 可能过度遮蔽形如 `sk-learn` 的正常文本；`_COOKIE_HEADER_RE` 整段遮蔽 Cookie 头（含非敏感 cookie 名，属有意保守）；`_API_KEY_PATH_RE` 以空白/`?`/`#`/引号为终止，键值含这些分隔符时可能只遮蔽前段（URL 编码 `%2F` 仍覆盖）；`_BEARER_TOKEN_RE` 仅覆盖带 `bearer ` 前缀且长度 ≥8 的令牌。
- **N-2 计数快照差异**：当前 `data/logs` 合计 `[toolshape]` 2280 行 / `pg08` 507 行；其中 2026-10-31 归档恰为证据快照 2160/474，2026-11-01 归档追加 120/33；未发现明文泄漏。
- **N-3 历史清理一次性**：归档重写是一次性操作，后续依赖写入时脱敏 + 导出时二次脱敏（纵深防御）。
- **N-4 Windows 既有失败**：2 个 pytest 失败位于本 delta 未改动的文件，为既有 Windows 环境问题，不构成本次阻断。
- **N-5 证据字节数**：`results.json.export_bytes=6,260,304` 为脱敏修复前实机测量，已在文件中注明非新导出。
- **N-6 提交状态**：全部改动未 stage/commit；是否提交由用户/Reviewer 决定。

- 定稿 SHA256（本报告全文，排除本 SHA256 行）：1C9EB9DFBA5A73A81C5FD50EC67DA383D5F6FA3E741DF1B7AA3F1FC2775F6ECD

# REV-B4 — P7/D09 日志、SSE、导出与 dump 安全只读审查报告（复核更新）

- 步骤：B4（P7 / D09：统一日志三 sink、广播/SSE、按日归档导出、dump 脱敏、噪音过滤；不含 D10 统计）
- 目标工作树：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`，HEAD `4653ec7`
- 主仓留档：`D:\LLM\self_programming\aistudio-api`，HEAD `d68c147`
- 权威依据：目标树 `AGENT.md`；`docs/dev/rebuild/06-decision-log.md` P7 章节（312-338）；`docs/dev/rebuild/06-feature-index.csv` D09-001..D09-035；`docs/dev/rebuild/06-test-disposition.csv` 日志相关 4 行
- 审查方式：只读复核 B4 修复；对照实际 diff、生产代码、测试断言与实跑结果；**未修改源代码/测试/配置，未 commit/stage**；仅更新本报告两份副本
- 复核日期：2026-10-29（本地时区 UTC+8）

## 1. 结论

**PASS**

B-01、B-02 均已修复并复核通过；N-01、N-02、N-05 已修复；收尾复核确认 N-09、N-10、N-11 也已关闭。无剩余阻塞项。

本轮实跑（2026-10-30 收尾复核）：**531 passed / 2 failed / 1 skipped in 6.45s**（2 failed 为 `AGENT.md` 2.4 已列出的既有 Windows 平台差异；1 skipped 为 POSIX-only）；`ruff check src tests` 全绿；`pyright src tests` **0 errors, 0 warnings, 0 informations**；`git diff --check` 退出码 0。前端 type-check/lint 已在前次复核经临时 junction 独立通过；本轮按指令未重跑 build，已检查 `src/aistudio_api/static` 新产物。

## 2. 实际执行命令与结果

| 命令 | 结果 |
|---|---|
| `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q` | **531 passed, 2 failed, 1 skipped in 6.45s**。失败为既有 Windows 差异：`tests/unit/test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser`、`tests/unit/test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly`（`bun.CMD` 报 `\n was unexpected at this time`）；1 skipped 为 `tests/unit/test_browser_engine.py:179: watchdog POSIX only` |
| `uv run pytest -q tests/unit/test_log_broadcast.py tests/unit/test_log_archive.py tests/unit/test_logs_routes.py tests/unit/test_logging.py` | **63 passed in 1.02s**（B4 日志相关全部用例） |
| `uv run pytest -q tests/unit/test_logging.py -k "console_text_masks or safe_fields or noisy"` | **5 passed**（B-01/N-01 定向用例） |
| `uv run pytest -q tests/unit/test_logs_routes.py -k "utc or mixed or naive or seven or range or shortcut"` | **14 passed**（B-02 定向用例） |
| `uv run ruff check src tests` | `All checks passed!`（0 error） |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` |
| `git diff --check` | 退出码 0；无空白错误，仅 `instance_client.py`、`test_logging.py` 的 LF→CRLF 提示 |
| `bun run type-check`（web，临时 junction） | 退出码 0（`bun --bun x vue-tsc -b`） |
| `bun run lint`（web，临时 junction） | 退出码 0（`eslint src --max-warnings 0`） |
| `git status --porcelain` / `git diff --cached --stat` | 20 个已跟踪修改（8 个代码/测试/前端/AGENT + 12 个 static 状态修改；其中 static 仅 8 个有内容 diff、4 个仅行尾/stat 差异）+ 9 个未跟踪新增（8 个 B4 产物 + 本报告）；**staged 为空**；`web/tsconfig.tsbuildinfo` 无残留 diff；根 `config.yaml` 不在 status 中 |
| 目标树 / 主仓 HEAD | 目标树 `4653ec7`；主仓 `d68c147`；**B4 未 commit** |

## 3. 范围、文件与洁净检查

- 修改文件（20 个 tracked）：
  - 代码/测试/前端/过程（8）：`AGENT.md`（§10.2 UI 前置设计确认）、`src/aistudio_api/api/app.py`（logs 路由双路径挂载、`/logs` SPA refresh、`setup_logging` 启动）、`src/aistudio_api/config.py`（dump 默认目录落到 `data/dumps`）、`src/aistudio_api/infrastructure/gateway/instance_client.py`（raw dump safe_*、相对目录、模型分量清洗）、`src/aistudio_api/infrastructure/utils/logger.py`（三 sink、root DEBUG、前缀噪音过滤、dump safe 字段）、`tests/unit/test_logging.py`（三 sink、动态级别、噪音 effective-level/filter、dump safe 字段、instance raw dump、恶意 model 测试）、`web/src/components/layout/Sidebar.vue`（日志入口）、`web/src/router/index.ts`（`/logs` 路由）。
  - 生产构建产物（12 个 static 状态修改）：`src/aistudio_api/static/index.html`、`assets/index.js`、`assets/index.css`、`assets/AccountsView.js`、`assets/DashboardView.js`、`assets/LoginView.js`、`assets/SettingsView.js`、`assets/format.js`、`assets/useClipboard.js`、`assets/vendor-icons.js`、`assets/vendor-vue.js`、`assets/vendor-yaml.js`。其中 8 个有内容 diff（AccountsView/DashboardView/LoginView/SettingsView/index.css/index.js/vendor-icons/vendor-vue）；`index.html`、`format.js`、`useClipboard.js`、`vendor-yaml.js` 仅行尾/stat 差异（`git diff --numstat` 为空）。`index.js` 已引用 `assets/LogsView.js`。
- 新增文件（8 个 B4 产物 + 本报告）：
  - `src/aistudio_api/api/routes_logs.py`（SSE + 导出）
  - `src/aistudio_api/infrastructure/utils/log_broadcast.py`（ring/订阅/归档/脱敏）
  - `src/aistudio_api/static/assets/LogsView.js`（生产构建新 chunk，21,574 bytes）
  - `tests/unit/test_log_archive.py`、`tests/unit/test_log_broadcast.py`、`tests/unit/test_logs_routes.py`
  - `web/src/api/logs.ts`、`web/src/views/LogsView.vue`
- `git diff --stat`：`16 files changed, 641 insertions(+), 188 deletions(-)`（4 个 static 仅行尾/stat 差异未计入；未跟踪新增另计）。
- **根 `config.yaml` 未被跟踪**：`git check-ignore -v config.yaml` → `.gitignore:15:/config.yaml`；`git ls-files config.yaml` 无输出；`defaults/config.yaml` 仍是唯一 tracked 默认模板。
- **未新增 D10/统计代码**：`git diff --name-only` 与新增文件均只含 D09 日志/导出/UI、B4 收尾构建产物与 §10.2 流程规则；新代码无 stats/统计账本实现或路由。
- 报告落盘前目标树无 `docs` 改动；落盘后只新增本报告 1 个未跟踪文件（预期）。

## 4. D09 映射表（D09-001..D09-035）

| ID | 要求（摘要） | 状态 | 证据 |
|---|---|---|---|
| D09-001 | `UnifiedFormatter` 统一格式/TTY 颜色 | PASS | `logger.py:77-107`；`test_logging.py:40-61` |
| D09-002 | stderr sink + uvicorn 接入统一格式（propagate=False） | PASS | `logger.py:312-338`；`app.py:46-47` |
| D09-003 | 三 sink 装配（stderr + broadcast + daily file，幂等 clear） | PASS | `logger.py:307-338`；`test_logging.py:88-96` |
| D09-004 | root 固定 DEBUG，stderr 按 level；命名空间/三方过滤 | PASS | `logger.py:272-325`；实跑 `aistudio.cdp` effective WARNING；`test_logging.py:99-185` |
| D09-005 | 动态 `set_log_level`（aistudio/uvicorn 同步） | PASS | `logger.py:272-304`；`test_logging.py:99-124` |
| D09-006 | 启动读取 `logging.level` | PASS | `app.py:46-47` |
| D09-007 | 运行期 `PUT /config/logging` | PASS（沿用） | `routes_system.py:352-382`；`test_logging.py:333-369` |
| D09-008 | `DEBUG`/`AISTUDIO_DEBUG` 仅触发 dump | PASS | `test_logging.py:183-218` |
| D09-009 | `BroadcastHandler` → ring → SSE | PASS | `log_broadcast.py:136-302`；`test_log_broadcast.py:141-160` |
| D09-010 | ring 5000 + 单调整数 id | PASS | `log_broadcast.py:23,150-193`；`test_log_broadcast.py:24-38` |
| D09-011 | WARNING 保底淘汰 | PASS | `log_broadcast.py:26,194-201`；`test_log_broadcast.py:82-91` |
| D09-012 | `replay(level,last_id,limit)` 默认 5000 | PASS | `log_broadcast.py:25,239-265`；`routes_logs.py:34-46` |
| D09-013 | gap 事件（last_id/oldest_id/missed） | PASS | `log_broadcast.py:251-258`；`routes_logs.py:63-65`；`test_logs_routes.py:84-107` |
| D09-014 | 订阅上限 8，超限 503 | PASS | `log_broadcast.py:27,213-232`；`routes_logs.py:127-134`；`test_logs_routes.py:225-237` |
| D09-015 | 订阅队列 500，满丢最旧并计数 | PASS | `log_broadcast.py:24,267-277`；`test_log_broadcast.py:94-108` |
| D09-016 | dropped 事件下发 | PASS | `routes_logs.py:67-68,92-94`；`test_logs_routes.py:110-137` |
| D09-017 | 15s 心跳 + 断连清理 | PASS | `routes_logs.py:30,88-109` |
| D09-018 | `/logs/stream` level/Last-Event-ID/SSE 头/web auth | PASS | `routes_logs.py:112-153`；`app.py:223-225`；`test_logs_routes.py:158-246` |
| D09-019 | 总线/落盘 `sanitize_log_message` | PASS | `log_broadcast.py:40-88,182,385`；`test_log_broadcast.py:122-139`；`test_log_archive.py:47-61` |
| D09-020 | dump 头/查询参数脱敏 | PASS | `logger.py:360-386`；`test_logging.py:220-273` |
| D09-021 | dump 落盘 `req_*.json` + `latest_request.json` 只含 safe_* | PASS | `logger.py:499-524`；`test_logging.py:384-473` |
| D09-022 | dump 开关链 | PASS（沿用） | `logger.py:114-251`；`test_logging.py:183-218` |
| D09-023 | 请求生命周期 access log | PASS（沿用） | `app.py:304-407` |
| D09-024 | dump 响应缓存回放 + SSE 不消费 body | PASS（沿用） | `app.py:304-407` |
| D09-025 | 按日归档 + 跨天切换 | PASS | `log_broadcast.py:317-322,360-416`；`test_log_archive.py:41-77` |
| D09-026 | 7 天保留清理（启动 + 跨天） | PASS | `log_broadcast.py:28,329-357,399-408`；`logger.py:334-336`；`test_log_archive.py:80-102` |
| D09-027 | 导出单日 `.log`/多日 `.zip`/最多 7 天/web auth | PASS | `routes_logs.py:174-282`；`test_logs_routes.py:255-381` |
| D09-028 | LogsView SSE 消费、指数退避、401 跳登录 | PASS（代码审查） | `LogsView.vue:337-405`；`logs.ts:29-47` |
| D09-029 | 级别过滤 + 多词搜索高亮 | PASS（代码审查） | `LogsView.vue:116-237` |
| D09-030 | 暂停/继续、清屏、暂停计数 | PASS（代码审查） | `LogsView.vue:260-295,733-739` |
| D09-031 | 下载当前过滤结果 | PASS（代码审查） | `LogsView.vue:436-454` |
| D09-032 | 按时间导出（1/3/7 天、范围选择、ZIP） | PASS（B-02 已修） | `LogsView.vue:456-491`；`routes_logs.py:174-220`；`test_logs_routes.py:370-430` |
| D09-033 | 前端 5000 上限 + 虚拟滚动 | PASS（代码审查） | `LogsView.vue:29-30,175-182,283-295` |
| D09-034 | gap/dropped 提示条（字段对齐） | PASS | `LogsView.vue:324-334,741-755` |
| D09-035 | WARP/tunnel/CDP/三方 DEBUG 噪音抑制 | PASS（B-01 已修） | `logger.py:34-64,302-325`；`test_logging.py:127-185` |

## 5. 关键要求逐项核对

### 5.1 三 sink、级别与噪音（D09-002..005、D09-035）

| 检查点 | 结论 | 证据 |
|---|---|---|
| root 恰好挂 stderr + `BroadcastHandler` + `DailyLogFileHandler` | 通过 | `logger.py:307-331`；`test_logging.py:88-96` 断言 `len(root.handlers)==3` 且各 1 |
| root/aistudio 固定 DEBUG | 通过 | `logger.py:283-288`、`:327-332`；`test_logging.py:99-124` |
| stderr 级别跟随配置 level | 通过 | `logger.py:290-296`；`test_logging.py:104,109,115,120` |
| 启动/跨天 7 天清理 | 通过 | `logger.py:334-336`；`log_broadcast.py:399-408`；`test_log_archive.py:80-102` |
| 噪音前缀覆盖真实命名空间 | 通过 | `_NOISY_LOGGER_PREFIXES` 含 `aistudio.cdp/aistudio.browser.cdp/aistudio.warp/aistudio.tunnel`（`logger.py:34-47`）；`_is_noisy_logger` 前缀匹配（`:50-55`）；sink 级 `NoisyLoggerFilter`（`:58-64,321-325`） |
| 实跑 effective level / filter | 通过 | root DEBUG；`aistudio.cdp`、`aistudio.cdp.events`、`aistudio.browser.cdp`、`aistudio.warp`、`aistudio.tunnel` 均 WARNING；filter 对上述 DEBUG/INFO 返回 False、WARNING 返回 True；`aistudio.server/browser/routes.logs` 仍 DEBUG |
| 噪音测试不再只断言字面名 | 通过 | `test_logging.py:127-185` 断言 `get_logger("cdp").name == "aistudio.cdp"`、子 logger effective WARNING、filter 行为 |
| uvicorn 接入统一格式 | 通过（按 D09-002 propagate=False） | `logger.py:312-338` |

### 5.2 Ring / SSE（D09-009..018）

| 检查点 | 结论 | 证据 |
|---|---|---|
| 5000 容量、单调整数 id | 通过 | `log_broadcast.py:23,150-193` |
| WARNING+ 保底、DEBUG/INFO 先淘汰 | 通过 | `log_broadcast.py:194-201`；`test_log_broadcast.py:82-91` |
| 默认回放 5000 | 通过 | `log_broadcast.py:25,239-265`；`routes_logs.py:44` |
| 订阅上限 8 → 503 | 通过 | `log_broadcast.py:213-232`；`routes_logs.py:127-134` |
| 队列 500、丢最旧、dropped 计数 | 通过 | `log_broadcast.py:267-277`；`routes_logs.py:92-94` |
| gap 载荷 `last_id/oldest_id/missed` | 通过 | `log_broadcast.py:251-258`；`routes_logs.py:63-65` |
| Last-Event-ID / last_id | 通过 | `routes_logs.py:119-124`；`test_logs_routes.py:190-210` |
| 15s 心跳 | 通过（常量；测试用短 heartbeat） | `routes_logs.py:30,96-102` |
| level 校验 400 | 通过 | `routes_logs.py:41,131-132`；`test_logs_routes.py:213-222` |
| SSE 头 + web auth 双路径 | 通过 | `routes_logs.py:139-153`；`app.py:223-225`；`test_logs_routes.py:240-246` |

### 5.3 导出（D09-027、D09-032）

| 检查点 | 结论 | 证据 |
|---|---|---|
| `days` 1..7 校验 | 通过 | `routes_logs.py:191-197`；`test_logs_routes.py:328-341` |
| ISO start/end、start≤end、最多 7 天、最近 7 天 | 通过 | `routes_logs.py:199-220`；`test_logs_routes.py:304-367` |
| 单日 `.log` | 通过 | `routes_logs.py:264-272`；`test_logs_routes.py:255-267` |
| 多日 `.zip` | 通过 | `routes_logs.py:274-282`；`test_logs_routes.py:282-301` |
| 单日缺文件 404 | 通过 | `routes_logs.py:265-266`；`test_logs_routes.py:318-325` |
| web auth 双路径 | 通过 | `app.py:223-225`；`test_logs_routes.py:433-438` |
| 前端发送本地日边界 | 通过 | `LogsView.vue:469-470`（`YYYY-MM-DDT00:00:00` / `T23:59:59.999`，无 UTC 转换） |
| 后端 aware → server-local 后取日 | 通过 | `routes_logs.py:204-220`；`test_resolve_export_range_utc_payload_maps_to_local_calendar_day`（`:395-405`） |
| 7 天包含边界不变成 8 天 | 通过 | `routes_logs.py:213-218`；`test_resolve_export_range_utc_plus_8_seven_days_is_not_eight`（`:408-417`）；naive inclusive 测试 `:420-430` |
| 混用 aware/naive 返回 400 | 通过 | `routes_logs.py:204-205`；`test_export_rejects_mixed_aware_and_naive_range`（`:370-381`） |
| 实跑 UI 路径 | 通过 | 本地 UTC+8：naive `今天T00:00:00`..`今天T23:59:59.999` → 200 `.log`；mixed → 400 |

### 5.4 Dump 安全（D09-019..022）

| 检查点 | 结论 | 证据 |
|---|---|---|
| `req_*.json` / `latest_request.json` 只含 safe_* | 通过 | `logger.py:499-524`；`test_logging.py:384-473` 断言禁止键不存在 |
| 禁止 `headers/query_params/body/raw_body/response_*` | 通过 | 同上；`forbidden_keys.isdisjoint(data)` |
| 默认/相对 dump 目录落 `data/dumps` | 通过 | `config.py:294-310`；`logger.py:114-195`；`test_logging.py:476-492` |
| `instance_client._dump_raw_exchange` 只写 safe_* | 通过 | `instance_client.py:74-95`；`test_logging.py:494-543` |
| 控制台 dump 文本 key-based 脱敏 | 通过（JSON；非 JSON 见 N-13） | `_safe_pretty_payload`/`_sanitize_payload_value`（`logger.py:389-447`）；`test_logging.py:276-317`；实测 `password/secret/credentials/token` 值均被替换为 `***` |

### 5.5 App / 前端（D09-018、D09-028..034）

| 检查点 | 结论 | 证据 |
|---|---|---|
| `/logs/stream`、`/api/logs/stream`、`/logs/export`、`/api/logs/export` 双路径 | 通过 | `app.py:223-225`；`test_logs_routes.py:180-187` |
| `/logs`、`/logs/` SPA refresh 返回 index.html | 通过 | `app.py:446-465`；`test_logs_routes.py:441-449` |
| 前端使用 `/api/logs/*` | 通过 | `logs.ts:38,64` |
| gap/dropped 字段对齐（`missed`/`dropped`） | 通过 | `LogsView.vue:324-334` |
| 5000 上限 + 虚拟滚动 | 通过 | `LogsView.vue:29-30,175-182,283-295` |
| 过滤/搜索/暂停/下载/1-3-7 天导出/重连/401 | 通过（代码审查） | `LogsView.vue:116-173,260-295,337-405,436-491` |
| 自定义范围导出的本地日期边界 | 通过 | `LogsView.vue:469-470`；B-02 已修 |
| 路由 + 侧边栏 | 通过 | `router/index.ts:7,34-40`；`Sidebar.vue:1,34-41` |

### 5.6 安全红线

| 红线 | 结论 | 证据 |
|---|---|---|
| SSE/导出均要求 web auth | 通过 | `app.py:223-225`；`test_logs_routes.py:240-246,433-438` |
| dump JSON 只含 safe_*，无原始 headers/body | 通过 | `logger.py:499-524`；`test_logging.py:384-473` |
| instance raw dump 只含 safe_* | 通过 | `instance_client.py:88-93`；`test_logging.py:494-543` |
| 相对/默认 dump 目录在 `data/dumps` 下 | 通过 | `config.py:294-310`；`logger.py:114-195`；`instance_client.py:77-85` |
| 控制台 dump 日志脱敏 | 通过（N-01 已修） | `logger.py:389-447`；`test_logging.py:276-317` |
| 无真实网络/账号/IP | 通过 | 新测试使用 ASGI/Fake/`127.0.0.1`；无 Google/真实账号 |
| 根 `config.yaml` 未被跟踪 | 通过 | `.gitignore:15:/config.yaml`；`git check-ignore` |
| 无 D10 统计代码 | 通过 | diff 仅 D09 文件；新文件无 stats/统计实现 |
| instance raw dump 文件名清洗 | 通过（N-11 已修） | `instance_client.py:42-50`（`_sanitize_model_component`）、`:110`（文件名使用安全分量）；`test_logging.py:541-576`；姊妹路径见 N-14 |

### 5.7 测试质量

| 目标 | 结论 | 证据/缺口 |
|---|---|---|
| 三 sink/幂等、root DEBUG、stderr 跟随 level | 通过 | `test_logging.py:88-124` |
| 噪音真实命名空间 effective level + filter | 通过 | `test_logging.py:127-185`；B-01 已修 |
| ring 容量/单调 id/WARNING 保底/replay 过滤/gap | 通过 | `test_log_broadcast.py:24-91` |
| 队列溢出丢最旧 + dropped | 通过 | `test_log_broadcast.py:94-108`；`test_logs_routes.py:110-137` |
| 订阅上限 8/503 | 通过 | `test_log_broadcast.py:111-119`；`test_logs_routes.py:225-237` |
| SSE 心跳/断连清理 | 通过（短 heartbeat） | `test_logs_routes.py:140-155` |
| Last-Event-ID/gap | 通过 | `test_logs_routes.py:190-210` |
| 导出单日/多日/ISO/auth/404/400 | 通过 | `test_logs_routes.py:255-381` |
| UTC+8 aware→local、7 天包含、naive inclusive、mixed 400 | 通过 | `test_logs_routes.py:370-430` |
| dump safe 字段 + console 脱敏 + instance raw dump | 通过 | `test_logging.py:276-543` |
| 启动/跨天清理调用、默认 15s/5000、非法 Last-Event-ID 400 | 未覆盖（N-06/N-07） | 见 N-06/N-07 |
| 是否存在为过测试削弱断言 | 未发现 | 断言由弱变强；无删除既有用例；`test_dynamic_log_level_switching` 的改动是 D09-004 行为要求 |

## 6. 问题清单

### 6.1 阻塞项

**无。**

### 6.2 已解决阻塞项（复核记录）

#### B-01（已解决）噪音过滤覆盖真实 `aistudio.*` 命名空间，并有 sink 级兜底

- **修复位置**：`logger.py:34-47`（`_NOISY_LOGGER_PREFIXES` 同时列 `warp/tunnel/cdp` 与 `aistudio.warp/aistudio.tunnel/aistudio.cdp/aistudio.browser.cdp`）；`:50-55`（前缀匹配）；`:58-64`（`NoisyLoggerFilter` 丢弃 <WARNING）；`:302-304`（`set_log_level` 将噪音 logger 固定 WARNING）；`:321-325`（filter 挂到 stderr/broadcast/file 三个 handler）。
- **实跑证据**：`setup_logging("DEBUG")` 后 root/aistudio 为 DEBUG；`aistudio.cdp`、`aistudio.cdp.events`、`aistudio.browser.cdp`、`aistudio.warp`、`aistudio.tunnel` 的 effective level 均为 WARNING；`NoisyLoggerFilter` 对上述 DEBUG/INFO 返回 False、WARNING 返回 True；`aistudio.server`、`aistudio.browser`、`aistudio.routes.logs` 的 DEBUG 记录仍通过。`get_logger("cdp").name == "aistudio.cdp"`。
- **测试**：`test_logging.py:127-185`（effective level + 子 logger + filter），全部通过。
- **结论**：B-01 关闭。

#### B-02（已解决）自定义范围导出按本地日边界发送，后端 aware→server-local 归一化

- **修复位置**：`LogsView.vue:469-470` 发送 `YYYY-MM-DDT00:00:00` / `T23:59:59.999`（naive 本地日，不再 `toISOString()`）；`routes_logs.py:204-220`（混用 aware/naive → 400；aware → `astimezone(server local)` 后取 `.date()`；inclusive 7 天上限）。
- **实跑证据**：本地 UTC+8 下，naive `今天T00:00:00`..`今天T23:59:59.999` → HTTP 200 + 单日 `.log`；mixed aware/naive → HTTP 400；UTC+8 的 `10-29T16:00Z..10-30T15:59Z` 正确映射到 10-30；7 天 UTC+8 范围不变成 8 天。
- **测试**：`test_logs_routes.py:370-430`（mixed 400、UTC+8 单日、UTC+8 7 天、naive inclusive），全部通过。
- **结论**：B-02 关闭。

### 6.3 非阻塞项

#### N-01（已解决）控制台 dump 文本 key-based 凭据脱敏

- **修复位置**：`logger.py:389-447`（`_sanitize_payload_value` / `_safe_pretty_payload`）；`_SENSITIVE_FIELD_RE` 增加 `credential`（`:360-362`）。
- **证据**：`test_logging.py:276-317` 断言 `password`/`nested.secret`/`credentials`/`token` 值均不出现在控制台 dump；实测 `body-secret`/`s3`/`tok-value` 均被替换为 `***`，普通文本 `keep-body`/`keep-resp` 保留。
- **是否阻塞**：已解决（JSON 路径；非 JSON 表单/纯文本的残余见 N-13）。

#### N-02（已解决）混用 aware/naive 的 ISO 范围返回 400

- **修复位置**：`routes_logs.py:204-205`。
- **证据**：`test_logs_routes.py:370-381`；实测 HTTP 400，不再 500。
- **是否阻塞**：已解决。

#### N-03（非阻塞）多日导出静默跳过缺失归档

- **位置**：`routes_logs.py:274-282`；`_iter_zip` `:231-245`。
- **证据**：多日范围只要有一个文件存在就返回 ZIP，缺失日文件被 `if not path.is_file(): continue` 跳过；只有全部缺失才 404。
- **影响**：调用方无法从 ZIP 内容得知某天缺失；D09-027 只要求“单日缺文件 404”，未规定多日部分缺失。
- **是否阻塞**：非阻塞；可考虑在 ZIP 中附带 manifest。

#### N-04（非阻塞）uvicorn 日志只进 stderr，不进 broadcast/每日归档

- **位置**：`logger.py:312-338`（uvicorn/uvicorn.error/uvicorn.access 绑定 `_root_handler` 且 `propagate=False`）、`:327-331`（root 三 sink）。
- **影响**：uvicorn 的启动/错误/访问日志不会出现在 `/logs/stream` 与每日归档；与 D09-002 的 `propagate=False` 口径一致，但“三 sink”并非对所有 logger 生效。
- **是否阻塞**：非阻塞；如需完整归档可将 uvicorn handler 指向三 sink 组合。

#### N-05（已解决）噪音测试已断言真实命名空间与 filter

- **修复位置**：`tests/unit/test_logging.py:127-185`。
- **证据**：断言 `get_logger("cdp").name == "aistudio.cdp"`、`aistudio.cdp.events` effective WARNING、`NoisyLoggerFilter` 行为、业务 logger DEBUG 保留。
- **是否阻塞**：已解决（随 B-01）。

#### N-06（非阻塞）启动/跨天清理的“调用”未测试

- **位置**：`logger.py:334-336`（启动 cleanup）；`log_broadcast.py:399-408`（跨天 cleanup）；`tests/unit/test_log_archive.py:64-102`（只测文件切换与 cleanup 函数本身）。
- **影响**：如果 `setup_logging` 或 `_open_for_day` 未来漏掉 cleanup，现有测试不会失败。
- **是否阻塞**：非阻塞；建议补 monkeypatch/临时目录断言。

#### N-07（非阻塞）SSE 默认 15s/5000 回放与非法 Last-Event-ID 未断言

- **位置**：`routes_logs.py:30,44,119-124`；`tests/unit/test_logs_routes.py:45-63,140-155,190-210`。
- **证据**：测试均显式传 `heartbeat_seconds=0.01/0.05`，未断言默认 15.0；replay 测试最多 250 条，未以 >5000 条验证默认 limit；非法 `Last-Event-ID` 的 400 分支无测试。
- **影响**：默认值回归不会被捕获。
- **是否阻塞**：非阻塞。

#### N-08（非阻塞）`test_stream_route_headers_and_spa_bypass` 名不副实

- **位置**：`tests/unit/test_logs_routes.py:158-177`。
- **证据**：用例名包含 “spa_bypass”，实际只断言 SSE 头与首事件；未通过 HTTP 验证 `Accept: text/html` 的 `/logs/stream` 不会被 SPA middleware 拦截，也未验证 auth。SPA 行为另由 `:441-449` 覆盖 `/logs`。
- **影响**：测试可读性/覆盖声明偏差。
- **是否阻塞**：非阻塞。

#### N-09（已解决）dropped 提示仅在成功重连/显式重试后清除

- **位置**：`web/src/views/LogsView.vue:74`（`pendingDroppedClear`）、`:358-370`（`scheduleReconnect` 置位）、`:388-393`（成功建连后按标志清除）、`:407-412`（`restartStream` 显式重试置位）。
- **证据**：首次挂载 `pendingDroppedClear=false`，`connect()` 成功不清 `droppedNotice`；普通 `log` 事件只追加，不清除；只有 `scheduleReconnect()` 或 `restartStream()` 置位后的下一次成功建连才清空并复位。`LogsView.js` 构建产物包含同一逻辑（`Y=!1` / `Y&&(G.value=null,Y=!1)`）。
- **是否阻塞**：已解决。

#### N-10（已解决）tracked 静态前端已重建，FastAPI 可服务新日志页 bundle

- **位置**：`src/aistudio_api/static/assets/LogsView.js`（新 chunk，21,574 bytes）、`src/aistudio_api/static/assets/index.js`（引用 `assets/LogsView.js`）、`src/aistudio_api/static/index.html`（引用 `/assets/index.js`）。
- **证据**：`LogsView.js` 内含 N-09 的 `pendingDroppedClear` 逻辑与 `T00:00:00`/`T23:59:59.999` 边界；`index.js` 的 `__vite__mapDeps` 列表包含 `assets/LogsView.js`，路由 `logs` 懒加载该 chunk。`web/node_modules` 不存在；`web/tsconfig.tsbuildinfo` 无 diff。生产构建由 Worker 执行（`bun run build`，退出码 0）；本轮按指令未重跑 build，仅检查产出。
- **是否阻塞**：已解决。

#### N-11（已解决）instance raw dump 模型分量已白名单清洗

- **位置**：`src/aistudio_api/infrastructure/gateway/instance_client.py:42-50`（`_sanitize_model_component`）、`:110`（文件名 `aistudio_{kind}_{safe_model}_{timestamp}.json`）。
- **证据**：按 `[A-Za-z0-9._-]` 白名单逐字符过滤；`\`、`/`、控制符、Unicode 等替换为 `_`；连续 `..` 替换为 `._`；去掉首尾 `.`；空结果回退**字面量字符串 `model`**，不会返回原始 `model`。实测 `..\evil/../x` → `___evil_.__x`、`..` → `_`、`.`/空串 → `model`、`C:\temp\x` → `C___temp__x`，均无分隔符/`..`/控制符；`gemini-3.7-flash` 原样保留。聚焦测试 `test_instance_client_raw_dump_sanitizes_malicious_model` 断言落盘父目录为 dump 目录、文件名无 `/`/`\`/`..`，2 个 raw-dump 相关用例通过。
- **残余**：文件名中的 `kind` 当前仅由内部固定常量 `generate_content`/`generate_image` 传入，未清洗；若未来改为动态值需同样清洗（非阻塞）。姊妹类 `AIStudioClient._dump_raw_exchange` 仍为旧实现，见 N-14。
- **是否阻塞**：已解决。

#### N-12（非阻塞，新）aware 输入使用“当前本地固定偏移”归一化

- **位置**：`routes_logs.py:170-171`（`datetime.now().astimezone().tzinfo`）、`:207-209`。
- **影响**：服务器若处于 DST 时区，历史/未来日期的 aware 输入在午夜附近可能因当前偏移与目标日期偏移不同而偏移一天；浏览器与服务端时区不同时，前端 naive 日期按服务端本地日解释。当前主部署时区 UTC+8 无此问题，且前端走 naive 路径。
- **是否阻塞**：非阻塞；如需严格跨时区语义可改用命名时区并按目标日期解析。

#### N-13（非阻塞，新）非 JSON 表单/纯文本 body 仍只走正则脱敏

- **位置**：`logger.py:426-447`（`_safe_pretty_payload` 对非 JSON 仅调用 `sanitize_log_message`）、`:414-423`（`safe_json_payload` 解析失败时同样回退为 sanitized text）。
- **证据**：实测 `password=body-secret&token=tok-value` 经 `_safe_pretty_payload` 原样返回；`Authorization: Bearer abc123` 被掩码；JSON `{"password":"body-secret"}` 被掩码为 `***`。
- **影响**：Gemini 代理请求体为 JSON，当前主路径安全；若未来接受表单/纯文本 body，凭据可能进入 dump 日志/文件。
- **是否阻塞**：非阻塞；建议对非 JSON 文本增加 `password=`/`token=` 等 key-value 规则。

#### N-14（非阻塞，新，既有姊妹路径）`AIStudioClient._dump_raw_exchange` 仍为旧实现

- **位置**：`src/aistudio_api/infrastructure/gateway/client.py:99-124`（`safe_model = model.replace("/", "_")`；payload 仍写 `capture_prompt`/`modified_body`/`raw_response` 原始字段）。
- **证据**：该路径由控制面 `AIStudioClient` 使用（`client.py:254,334` 调用），`settings.dump_raw_response` 默认关闭；B4 修复的是 `InstanceRequestClient._dump_raw_exchange`（`instance_client.py:80-110`），未覆盖此姊妹路径。
- **影响**：若开启控制面 raw dump 且模型名含 `\`/`..`，仍可能有文件名路径风险，并写入原始报文；默认关闭，且不属于 B4 的 instance_client 验收点。
- **是否阻塞**：非阻塞；建议复用 `_sanitize_model_component` 与 safe_* 字段。

### 6.4 决策 / 条件项（P7-Q1..Q9 + 总原则 + 范围）

| 项 | 决策/条件 | 本次状态 | 证据 |
|---|---|---|---|
| Q-01（P7-Q1） | 日志页/导出 `D09-027..032` → keep | PASS（B-02 已修） | `routes_logs.py:174-282`；`LogsView.vue:456-491`；`test_logs_routes.py:370-430` |
| Q-02（P7-Q2） | 日志存储 `D09-010,012,016,025,026,033` → keep | PASS | `log_broadcast.py:23-28,194-201,239-265`；`LogsView.vue:29-30,175-182` |
| Q-03（P7-Q3） | dump 安全 `D09-021,022` → port | PASS（N-01 已修） | `logger.py:389-524`；`instance_client.py:74-95` |
| Q-04（P7-Q4） | 噪音 `D09-004,035` → port；root DEBUG + filter | PASS（B-01 已修） | `logger.py:34-64,302-325`；`test_logging.py:127-185` |
| Q-05（P7-Q5） | 事件账本 `D10-001..010,016,020,025,027` → port/keep | 超出 B4 范围；无 D10 代码 | `git diff --name-only` 仅 D09 |
| Q-06（P7-Q6） | 分母 `D10-027,028,032` → port | 超出 B4 范围 | 同上 |
| Q-07（P7-Q7） | 保留期/范围 `D10-010,022..024` → keep/port | 超出 B4 范围；D09 7 天保留已实现 | `log_broadcast.py:28,329-357` |
| Q-08（P7-Q8） | countTokens/paid/token `D10-020,021,025,029,031` → keep/port | 超出 B4 范围 | 同上 |
| Q-09（P7-Q9） | API 形态 `D09-013,018,D10-024` → keep | D09 部分 PASS；D10-024 超出范围 | `routes_logs.py:63-65,112-153` |
| Q-10（P7 总原则） | 日志先于统计；ring 5000 + 每日 7 天；dump 只落 safe_* 默认关；root DEBUG + filter；日志 SSE/统计 REST | PASS（统计为范围外） | `06-decision-log.md:312-338`；`log_broadcast.py:23-28`；`logger.py:307-338` |
| Q-11（B4 范围条件） | 仅 D09，不含 D10/统计/无关改动 | PASS | 20 tracked 修改（含 12 static 构建产物、AGENT §10.2 流程规则）+ 8 B4 新增均属 D09/收尾范围；无 stats 代码 |

## 7. 只读声明与状态

- 本次复核**未修改**任何源代码、测试、配置或 git 状态；仅更新本报告（目标树与主仓各一份，内容一致）。未 commit、未 stage、未联网安装依赖。
- 前端门禁：type-check/lint 已在前次复核经临时 junction 独立通过；本轮按指令未重跑 build，仅检查 `src/aistudio_api/static` 新产物（`LogsView.js` + `index.js` 引用）。`web/node_modules` 不存在，`web/tsconfig.tsbuildinfo` 无 diff。
- **根 `config.yaml` 仍为 untracked / ignored**：`git check-ignore -v config.yaml` → `.gitignore:15:/config.yaml`；`git ls-files config.yaml` 无命中；`defaults/config.yaml` 仍是唯一 tracked 默认模板。
- **未新增 D10 代码**：diff 与新文件均无统计/账本实现或统计路由。
- **B4 尚未 commit**：目标树 HEAD 仍为 `4653ec7`，主仓 HEAD 为 `d68c147`；工作区为 20 个已跟踪修改 + 9 个未跟踪新增（8 个 B4 产物 + 本报告）。
- **前端门禁限制**：type-check/lint 已通过；正式 `build` 由 Worker 执行，产出已检查，本轮未重复执行（见 N-10）。
- **未访问真实 Google/真实账号/真实 IP**；新测试使用 ASGI/Fake/`127.0.0.1`。

## 8. 报告双写与 SHA256

- 目标树：`docs/dev/rebuild/domains/P7-logs/reviews/REV-B4-p7-logs.md`
- 主仓：`docs/dev/rebuild/domains/P7-logs/reviews/REV-B4-p7-logs.md`
- 两份内容完全一致；SHA256 见交付消息。

## 9. N-09 / N-10 / N-11 收尾记录（2026-10-30）

- **N-09（已修）**：`web/src/views/LogsView.vue` 仅在 SSE 重连成功或显式「立即重试」成功时自动清除陈旧 `droppedNotice`：`scheduleReconnect()` 置 `pendingDroppedClear = true`，`restartStream()` 同样置位，`connect()` 成功建连后若该标志为真则清空并复位；连接中的普通 `log` 事件不清除丢弃告警，正常会话内旧告警保留，首次挂载连接不清空。
- **N-11（已修）**：`instance_client.py` 新增 `_sanitize_model_component()`，按 `[A-Za-z0-9._-]` 白名单逐字符过滤 `model`，其余字符（`\`、`/`、控制符等）统一替换为 `_`，并消除连续 `..`、去掉首尾 `.`，结果为空时回退**字面量字符串 `model`**（不会返回原始 `model`）；`_dump_raw_exchange` 文件名只使用该安全分量。新增 `tests/unit/test_logging.py::test_instance_client_raw_dump_sanitizes_malicious_model`，以 `..\evil/../x` 断言落盘文件父目录即 dump 目录、文件名无 `/`、无 `\`、无 `..`。
- **N-10（已修）**：经临时目录 junction `target/web/node_modules -> main/web/node_modules` 在目标树执行 `bun run build`（`vue-tsc -b && vite build`），退出码 0（Worker 报告 1752 modules transformed，built in 2.76s）。`src/aistudio_api/static/` 共 13 个状态变更：12 个 tracked 状态修改（其中 8 个有内容 diff：`assets/index.js`、`assets/index.css`、`assets/AccountsView.js`、`assets/DashboardView.js`、`assets/LoginView.js`、`assets/SettingsView.js`、`assets/vendor-icons.js`、`assets/vendor-vue.js`；`index.html`、`assets/format.js`、`assets/useClipboard.js`、`assets/vendor-yaml.js` 仅行尾/stat 差异）+ 1 个新增未跟踪 `assets/LogsView.js`（21,574 bytes）；`index.js` 已引用该 chunk。构建后已 `rmdir` 删除 junction；`web/tsconfig.tsbuildinfo` 已恢复为 HEAD（diff 为空），`web/tsconfig.node.tsbuildinfo` 无改动。未安装依赖、未联网。
- **AGENT.md §10.2（已加）**：新增「UI 前置设计确认（强制）」硬规则，要求 UI 步骤先只读盘点并提交《UI 页面与交互设计清单》并经用户明确确认后才可派 Worker 修改 `.vue`，且每次 UI 修改完工必须 `bun run build` 更新 `src/aistudio_api/static` 产物。
- **最终门禁（2026-10-30 实跑）**：
  - `uv run pytest -q` → **2 failed, 531 passed, 1 skipped**（6.45s，2026-10-30 收尾独立复核）；失败仍为既有 Windows 差异 `test_browser_engine.py::test_find_chromium_executable_strictly_requires_cloakbrowser` 与 `test_browser_scripts_syntax.py::test_evaluate_wrapper_expressions_parse_cleanly`，1 skipped 为既有 POSIX 用例，未改动这些测试。
  - `uv run ruff check src tests` → **All checks passed!**（退出码 0）。
  - `uv run pyright src tests` → **0 errors, 0 warnings, 0 informations**（退出码 0）。
  - `git diff --check` → 退出码 0（构建产物 `index.html` 已归一为 LF，无空白错误；仅余 core.autocrlf 常规 warning）。
  - `bun run lint` → 退出码 0；`bun run type-check` → 退出码 0（均在临时 junction 存在期间执行）。
  - 根 `config.yaml` 仍 untracked/ignored：`git check-ignore -v config.yaml` → `.gitignore:15:/config.yaml`，`git ls-files config.yaml` 无命中。
- **结论**：N-09 / N-10 / N-11 全部关闭，无新增阻塞项；**verdict 维持 PASS**。全部改动保持未提交、未 stage，交由 Integrator 处理。

## 10. B4 收尾独立复核（2026-10-30）

- **结论：PASS**。N-09 / N-10 / N-11 均按实际代码与产物复核通过，无阻塞项；Q-01..Q-11 状态不变（Q-01/Q-02/Q-03/Q-04 PASS，Q-05..Q-08 超出 B4 范围，Q-09 D09 部分 PASS，Q-10 PASS（统计范围外），Q-11 PASS）。
- **门禁（本轮独立实跑）**：`uv run pytest -q` → **531 passed, 2 failed, 1 skipped in 6.45s**（失败仅既有 Windows `test_find_chromium_executable_strictly_requires_cloakbrowser`、`test_evaluate_wrapper_expressions_parse_cleanly`；1 skipped 为 POSIX-only）；`uv run ruff check src tests` → **All checks passed!**；`uv run pyright src tests` → **0 errors, 0 warnings, 0 informations**；`git diff --check` → 退出码 0（仅 core.autocrlf LF→CRLF warning）。
- **N-09（通过）**：`LogsView.vue:74` 的 `pendingDroppedClear` 仅在 `scheduleReconnect()`（`:358-370`）与 `restartStream()`（`:407-412`）置位；`connect()` 成功建连后 `:388-393` 才清空 `droppedNotice` 并复位。首次挂载标志为 false，普通 `log` 事件不触发清除；构建产物 `LogsView.js` 同样包含 `Y=!1` / `Y&&(G.value=null,Y=!1)`。
- **N-11（通过）**：`instance_client.py:42-50` 的 `_sanitize_model_component` 按 `[A-Za-z0-9._-]` 白名单过滤，替换 `\`、`/`、控制符、Unicode，消除 `..` 并去首尾 `.`；空结果回退**字面量字符串 `model`**，没有返回原始 `model` 的 fallback。实测 `..\evil/../x` → `___evil_.__x`、`..` → `_`、`.`/空串 → `model`、`C:\temp\x` → `C___temp__x`、`a/../../b` → `a_.__.__b`，均无 `/`/`\`/`..`/控制符；`test_instance_client_raw_dump_sanitizes_malicious_model` 与 raw-dump safe 字段测试 **2 passed**，落盘父目录为 dump 目录。文件名中的 `kind` 当前是内部固定常量；未来若动态化需同样清洗（记入 N-14 的同类残余说明）。
- **N-10（通过）**：`web/node_modules` 不存在；`web/tsconfig.tsbuildinfo`、`web/tsconfig.node.tsbuildinfo` 均无 diff。`src/aistudio_api/static/assets/LogsView.js` 存在（21,574 bytes），`assets/index.js` 的 `__vite__mapDeps` 引用 `assets/LogsView.js`，`index.html` 引用 `/assets/index.js`；构建产物包含 N-09 的清除逻辑与本地日边界。生产构建由 Worker 执行，本轮按指令未重跑 build，仅检查产出。static 状态为 12 tracked 修改（8 个内容 diff + 4 个仅行尾/stat：`index.html`、`format.js`、`useClipboard.js`、`vendor-yaml.js`）+ 1 个新增 `LogsView.js`。
- **AGENT §10.2（通过）**：`AGENT.md:403` 新增「UI 前置设计确认（强制）」硬规则，明确 UI 步骤需先只读盘点并提交《UI 页面与交互设计清单》并经用户确认后才可修改 `.vue`，且每次 UI 修改完工必须执行 `bun run build` 更新 `src/aistudio_api/static` 产物。
- **Section 9 校正**：N-11 的“回退 `model`”已明确为“回退字面量字符串 `model`（不会返回原始 model）”；N-10 的静态文件计数已区分“12 tracked 状态修改（8 内容 diff + 4 行尾/stat）+ 1 新增”；门禁时间更新为本轮 6.45s。
- **新增非阻塞项**：**N-14** `AIStudioClient._dump_raw_exchange`（`client.py:99-124`）仍是旧实现（`safe_model = model.replace("/", "_")`，payload 写原始 `capture_prompt`/`modified_body`/`raw_response`）；默认 `dump_raw_response=false`，属既有控制面调试路径，B4 的 `InstanceRequestClient` 验收点已修复，故非阻塞；建议复用 `_sanitize_model_component` 与 safe_* 字段。
- **N 状态总表**：N-01/N-02/N-05/N-09/N-10/N-11 已解决；N-03/N-04/N-06/N-07/N-08/N-12/N-13/N-14 为非阻塞残余。
- **范围/洁净**：根 `config.yaml` 仍 untracked/ignored（`.gitignore:15`）；无 D10/统计代码；本轮仅更新本报告两份副本，未修改源代码/测试/配置、未 commit/stage；目标树 HEAD `4653ec7`，主仓 HEAD `d68c147`。
- **报告双写**：目标树与主仓 `docs/dev/rebuild/domains/P7-logs/reviews/REV-B4-p7-logs.md` 内容一致；SHA256 见交付消息。

# PKG-B4-01 P7 结构化日志 / 错误码 / 请求-尝试关联

- 步骤：B4
- 状态：draft（待用户审计；中断时保留本文件，不提交）
- 基线：`99c38ba` + 目标 worktree HEAD `755e28a`
- 来源：D-09/D-13 边界；D09-001..035；`06` P7-Q1..Q4/Q9；`05` B4；534 台账 P7=69 行（本包仅 D09 35 行）。命名说明：`06` 域批次 D09=日志/可观测，`08-09` 决策 D-09=模板 hash 诊断开关；本包范围用 D09 台账，边界用 D-09/D-13。

## 1. 范围

负责：
- 结构化事件：`ts/level/logger/msg/request_id/attempt_id/account/model/surface/error_code/latency_ms`；stderr 保持可读，ring/SSE/导出用同一 schema。
- 错误码：稳定枚举（AUTH/SESSION/USAGE/TIER/TRANSPORT/TIMEOUT/UPSTREAM/INTERNAL），仅用于日志关联；不改 API 错误体（D-04/D-06 归 P2/P5）。
- 请求-尝试关联：middleware 生成/透传 `request_id`，尝试循环生成 `attempt_id`，contextvars 注入日志；客户端可通过 `X-Request-ID` 取回。
- 日志存储/页/导出：ring 5000、每日归档 7 天、`/logs/stream` SSE、1/3/7 天导出、级别过滤/搜索/暂停/清屏/下载；web auth。
- 安全：dump 只落 `safe_*`，禁止明文 headers/raw_body；总线/落盘/SSE 统一脱敏；噪音按命名空间过滤。
不负责：
- 不含统计页、D10 34 行、事件账本聚合、client/attempt 成功率、countTokens/paid/token 汇总（C4）。
- 不改 dump 落盘为明文；不新增 notes/批量分组/死接口（D-13）；不把 `hash_include_code` 等诊断开关放进普通 UI（D-09）。
- 不重构 Web 布局/导航；日志入口方案受 D-12 约束，见未决。

## 2. 文件域

新增：`infrastructure/utils/log_broadcast.py`、`infrastructure/utils/log_context.py`、`domain/error_codes.py`、`api/routes_logs.py`；`web/src/components/logs/LogsPanel.vue`（或 `views/LogsView.vue`，取决于未决）、`web/src/api/logs.ts`、`web/src/types/logs.ts`；`tests/unit/test_log_context.py`、`test_error_codes.py`、`test_log_broadcast.py`、`test_logs_routes.py`、`test_log_archive.py`。
修改（共享语义，单列冲突风险）：`infrastructure/utils/logger.py`（结构化事件/safe dump，B4 独占）、`api/app.py`（request-id middleware + router，B1/B2 冲突高）、`application/api_service_gemini.py`（attempt 绑定，P2/P5 冲突高）、`web/src/router/index.ts` 与 `layout/Sidebar.vue`（仅独立页面获批时，D-12 冲突）、`tests/unit/test_logging.py`（dump 断言）。
交叉验证 534 与目标树：目标树已有 `logger.py:33-56,250-273,314-449`、`routes_system.py:322-360`、`app.py:194-280`、`test_logging.py`；目标树**不存在** fork 的 `log_broadcast.py`、`routes_logs.py`、`LogsView.vue`、`logs.ts`、`test_log_broadcast.py`、`test_logs_routes.py`、`test_log_archive.py`。后者为“参考/不适用”，B4 需 port 后新建；不得引用为既有测试。
边界：不新增 `notes`/`batchToggleGroup`；导出/SSE 路由必须有 UI 使用与测试，否则不建（D-13）。

## 3. 依赖

- 前置：B1 日志配置/lifespan；B2 账号/路由的请求入口；web 现有 Settings/Dashboard shell；P5 尚未实现，schema 先冻结再由 B5 绑定。
- 接口冻结：`LogEvent` 字段名、`X-Request-ID`、错误码枚举、SSE 事件名（`log/gap/dropped/heartbeat`）在 B4 开始前确认；P5 只追加字段。
- 配置复用 `logging.level/dump_requests/dump_to_file/dump_dir`；不新增普通 UI 诊断开关；ring/保留期是否可配置见未决。
- 环境：全程离线；日志页用本地服务/假日志，不访问真实账号/IP/Google；不上传日志。

## 4. 测试

- `test_log_context.py`：并发请求/尝试隔离、嵌套 reset、缺失字段回退；离线。
- `test_error_codes.py`：401/403/429/5xx/timeout/closed 映射稳定，且 API body 不变；离线矩阵。
- `test_log_broadcast.py`：ring 5000、单调 id、WARNING 保底、replay、gap、订阅上限 8/503、队列 500/dropped、脱敏。
- `test_logs_routes.py`：level 校验、Last-Event-ID、SSE 心跳/断连、web auth、1/3/7 天导出、缺失日 404（不得 200 空文件）、zip。
- `test_log_archive.py`：跨天切换、7 天清理、JSONL 可解析且字段齐全、脱敏。
- `test_logging.py`：`req_*.json` 仅 `safe_*`，无原始 headers/raw_body；现有 level/dump 配置不回归。
- 关联用例：同一 `request_id` 的两次 attempt 在 SSE/导出中 `attempt_id` 不同、错误码可检索；离线。
- UI：本地启动 + Playwright 截图/过滤/暂停/导出冒烟；不接真实账号；证据含 `logs-page.png`。

## 5. 命令与证据

```powershell
cd .kilo/worktrees/rebuild-upstream
python -m pytest tests/unit/test_logging.py tests/unit/test_log_context.py tests/unit/test_error_codes.py tests/unit/test_log_broadcast.py tests/unit/test_logs_routes.py tests/unit/test_log_archive.py -q
python -m ruff check src tests
cd web; bun run type-check; bun run lint; bun run build
```

证据：`docs/dev/evidence/B4/20261028/`（`pytest.txt`、`ruff.txt`、`type-check.txt`、`sse-sample.jsonl`、`export-1d.zip.sha256`、`request-attempt-trace.txt`、`logs-page.png`）；全部脱敏，禁止 cookie/token/API key/原始 body、真实账号/IP。

## 6. 失败停止

- dump/归档/SSE/导出出现明文 headers、cookie、token、raw_body → 立即停止并清理证据。
- 同一请求无法用 `request_id` 关联、attempt 混算、错误码不稳定 → 停止。
- 单日导出缺文件仍 200、导出范围超 7 天、无 web auth、订阅上限失效 → 停止。
- 引入统计页/D10 聚合、notes/死接口、普通 UI 诊断开关 → 停止。
- 目标树 `src` diff 非 0、前端 type-check/build 连续 2 次失败、需要真实账号/IP → 停止并请求用户裁决/授权。

## 7. 回滚

- 回滚点：B4 开始前 worktree HEAD（当前 `755e28a`），只 revert B4 提交。
- 文件：删除 log_broadcast/log_context/error_codes/routes_logs/前端 logs 文件与新增测试；还原 `logger.py`、`app.py`、`api_service_gemini.py`、`test_logging.py`；若新增路由/导航一并还原。
- 数据：删除 B4 生成的 `data/logs/`（或配置的日志目录）与 dumps；不影响账号/registry；证据归档保留但脱敏。
- 配置：恢复 `logging` 原值；不覆盖用户运行期 level/dump 选择。

## 8. 未决

- 日志页入口：D-12 禁止新增导航/路由，但 B4 要求日志页；默认复用 Settings/Dashboard 面板，是否允许独立 `/logs` 路由需用户裁决。
- 归档格式：台账写 `aistudio-YYYYMMDD.log`，结构化字段需 JSONL；是用 `.log` 存 JSONL 还是改 `.jsonl` 需裁决。
- 错误码枚举与 P2/P5 的协议错误体映射边界：B4 只观察，最终 code 表由谁冻结。
- `X-Request-ID` 是否对外暴露、是否接受客户端传入；非法/重复 id 的策略。
- ring 5000/保留 7 天/队列 500/订阅 8 是否可配置，还是固定常量；配置入口是否进现有 LogConfigCard。
- 前端无自动化测试基线；是否接受 Playwright 冒烟 + type-check/lint 作为 B4 验收。
- D09-004 的“root DEBUG + 噪音过滤”白名单范围（WARP/tunnel/CDP/websockets/httpx/uvicorn.access）需 reviewer 冻结。

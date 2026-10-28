# REV-B4-01 P7 结构化日志 / 错误码 / 请求-尝试关联 review

- 被审包：`docs/dev/rebuild/domains/P7-logs/packages/PKG-B4-01-p7-logs.md`
- reviewer：只读 agent
- 状态：CONDITIONAL
- 基线：目标 worktree `.kilo/worktrees/rebuild-upstream` HEAD `755e28a`（`99c38ba` + `9b5cdea`）
- 核对方式：只读文件与行号核对；未运行测试，未修改被审包。

## 结论

范围与 D-09/D-12/D-13 的边界总体清楚，日志/SSE/导出不含统计页；但前端文件域在 `LogsPanel.vue` 与 `views/LogsView.vue` 之间未闭合，`Sidebar.vue` 路径错误；dump 明文与单日缺档 404 属于台账“待用户决定”项，包内已先行定死但未列入未决。以上问题使 B4 尚不能按当前文本直接并行实施与验收。

## 发现

| 编号 | 严重度 | 文件:行 | 事实 | 影响 |
|---|---|---|---|---|
| P7-01 | 中 | `PKG-B4-01:23,74` | 新增前端文件写 `web/src/components/logs/LogsPanel.vue`（或 `views/LogsView.vue`，取决于未决）；§8 又写“默认复用 Settings/Dashboard 面板，是否允许独立 `/logs` 路由需用户裁决”。D-12 明确不得新增导航/路由。 | 前端文件域与测试路径未闭合，无法并行分派；日志页入口方案仍待用户决定。 |
| P7-02 | 中 | `PKG-B4-01:15,18` | 包要求“dump 只落 `safe_*`，禁止明文 headers/raw_body”；目标当前 `dump_request_exchange` 同时写 `headers`、`body`、`raw_body`（`src/aistudio_api/infrastructure/utils/logger.py:403-415`），`tests/unit/test_logging.py:302` 还断言 `data["raw_body"]`。台账 D09-021 的“待用户决定”是“是否只落 `safe_*`/禁止明文 body”（`D09-logs-observability.md:25`），§8 未列该决定。 | 会改变现有落盘行为与既有测试，但未作为用户裁决项暴露；审计时无法判断这是用户决定还是包内自定。 |
| P7-03 | 中 | `PKG-B4-01:40` | 测试写“缺失日 404（不得 200 空文件）”；台账 D09-027 的“待用户决定”是“单日缺档是否改 404”（`D09-logs-observability.md:31`），§8 未列该决定。 | 单日导出的状态码被包内先行定死，可能违背台账待决口径。 |
| P7-04 | 中 | `PKG-B4-01:24` | 修改文件域写 `web/src/layout/Sidebar.vue`；目标实际为 `web/src/components/layout/Sidebar.vue`（`web/src/layout/` 不存在）。`web/src/router/index.ts` 存在。 | 文件域路径错误，实施时需自行纠正。 |
| P7-05 | 低 | `PKG-B4-01:25` | 交叉验证写 `logger.py:33-56,250-273,314-449`；目标 `logger.py` 共 444 行，`314-444` 存在，`445-449` 不存在。 | 行号锚点轻微越界，审计需按实际行号修正。 |
| P7-06 | 中 | `PKG-B4-01:50-55` | 证据列出 `pytest.txt`、`ruff.txt`、`type-check.txt`、`sse-sample.jsonl`、`export-1d.zip.sha256`、`request-attempt-trace.txt`、`logs-page.png`，但命令只运行 pytest/ruff/bun，未重定向生成这些文件；目标 worktree 无 `web/node_modules`，`bun run type-check` 前未写 `bun install`；`web/package.json` 无 Playwright 依赖/脚本。 | 证据与 UI 冒烟不能按所写命令直接复现。 |
| P7-07 | 低 | `PKG-B4-01:19,76` | 新增 `domain/error_codes.py`，目标已有 `domain/errors.py`；错误码表由 B4 冻结还是 P2/P5 冻结，仅在 §8 提问。 | 可能出现两套错误分类的归属模糊；需在冻结时明确。 |
| P7-08 | 低 | `PKG-B4-01:30` | 依赖写“P5 尚未实现，schema 先冻结再由 B5 绑定”；B5 在 05 中拆为 B5a/B5b，此处“B5”指代不明。 | 依赖表述不精确，但不影响 B4 离线执行。 |

## 验收门槛

- 范围是否闭合：否。日志页入口（现有面板 vs `/logs` 路由）未定，前端文件域二选一未定。
- 文件域是否可并行：否。`LogsPanel.vue`/`LogsView.vue` 未定，`Sidebar.vue` 路径错误；`app.py`/`logger.py`/`api_service_gemini.py` 已标共享冲突。
- 测试是否可执行：部分。后端离线用例可写；前端无自动化基线，Playwright 未入项目依赖；web 依赖未安装。
- 证据是否可复现：部分。命令未生成所列证据文件，web 侧缺安装步骤。
- 失败停止/回滚是否明确：是。§6 有明文泄露、关联失败、导出越界等停止点；§7 有文件与数据回滚。
- 是否误用旧 fork 路径或 534 当任务：未把 534 当任务（§1/§2 明确 inventory，仅取 D09 35 行）；`views/LogsView.vue` 等 fork 文件已标“参考/不适用”，但 `layout/Sidebar.vue` 是错误路径。
- D-02/D-03 边界：本包不改 watchdog、CDP/WS、liveness，无冲突；日志噪音白名单只影响日志过滤。
- D-09/D-12/D-13 边界：`hash_include_code` 明确不进普通 UI，符合 D-09；日志页入口与 D-12 的“不新增导航/路由”存在冲突（见 P7-01）；未新增 notes/批量分组，符合 D-13。

## 给用户的裁决项

1. 日志页入口：复用 Settings/Dashboard 现有面板，还是允许独立 `/logs` 路由（D-12 明确禁止新增导航/路由，需用户重新裁决）？
2. dump 落盘：是否只保留 `safe_*`、禁止 `raw_body`/原始 headers（D09-021 待决）；这会改 `tests/unit/test_logging.py:302` 的现有断言。
3. 单日导出缺档：返回 404 还是保持 200 空文件（D09-027 待决）？
4. 归档格式：`.log` 内写 JSONL，还是改用 `.jsonl` 扩展名？
5. ring 5000/保留 7 天/队列 500/订阅 8：固定常量还是可配置；配置入口是否进现有 `LogConfigCard`。
6. 前端验收：是否接受 Playwright 冒烟 + `type-check`/`lint`（项目无前端自动化基线）？
7. 错误码表：由 B4 冻结，还是由 P2/P5 冻结后 B4 只消费；`X-Request-ID` 是否对外暴露、是否接受客户端传入。

## 只读声明

本次审查只读；除新增本报告外，未修改任何任务包、源码或其他文档，未 commit，未访问真实账号/IP/Google。

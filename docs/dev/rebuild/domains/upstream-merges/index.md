# 上游合并台账（upstream merges）

记录目标重建树吸收上游提交的范围、核心能力与受保护架构。每次合并保持未提交，待 Reviewer 审计后由用户裁决。

日期：2026-10-31
目标树：`.kilo/worktrees/rebuild-upstream`

## 1. `f2b1e28`

- **范围**：`f2b1e28`（单提交），合并点 `ca8496c`（父提交 `0cdfd35` + `f2b1e28`）。
- **核心吸收**：
  - 标签页 / 流式内存泄漏清理；
  - readiness watchdog 超时处理；
  - `Ultra_20X` tier 归一化；
  - 管理端配置编辑器缺陷修复。
- **受保护架构**：
  - 单页有界 rebind，禁止蓝绿/同账号双页；
  - B4 日志三 Sink 与 `safe_*` 脱敏口径；
  - 多实例池与 Free/Pro 分层。
- **落点**：`session.py`、`browser_engine.py`、`model_defaults.py`、`web/` 配置编辑器相关文件。

## 2. `16450cc & 99600c7`

- **范围**：`16450cc..99600c7`（2 个提交），合并点 `05a9df7`（父提交 `424f896` + `99600c7`）。
- **核心吸收**：
  - `16450cc`：移除请求级全局超时 watchdog，支持长流式输出；
  - `99600c7`：等待 BotGuard Wasm 快照就绪；按请求固定 `SessionContext`；移除上游 gzip 拒绝；启动账号同步。
- **受保护架构**：
  - B5a 仍保留 2s 工具调用空闲 STOP，不恢复无限等待；
  - 单页生命周期 D-05/D-10/D-11；
  - 池化实例启动预热，控制面不重复开页；
  - B4 日志脱敏与三 Sink。
- **落点**：`gateway/session.py`、`gateway/botguard_bootstrap.py`、`gateway/streaming.py`、`transport.py`、`api/app.py`。

## 3. `ccd15e3 & 852de53`

- **范围**：`99600c7..852de53`（2 个提交，53 个文件），本次合并（未提交）。
- **核心吸收**：
  - 403：auth 403 不加硬冷却锁；首次 403 同账号重新签名重试，二次 403 切换账号；`routing_pipeline.py` / `generation_service.py` 共用该策略；
  - 流式：截断 JSON 修复（`stream_parser.finish()`）、finish fallback、每次尝试重置 `stream_completed` 相关终态；
  - 启动：`tier_checked_at` 3 天内跳过重复 tier/email 探测；
  - 新账号 bootstrap：Temporary Chat 激活、干净 bootstrap、真实模板捕获、`finally` 输入框清理、更安全的 snapshot hook；
  - 日志：仅吸收安全 log-level 与 httpx/httpcore 噪音抑制；
  - 前端：Dashboard / QuickApi / Settings / Accounts 交互与文案改进，静态产物由 `bun run build` 重新生成；
  - 文档：403 避让口径与 BotGuard AST 哈希说明。
- **受保护架构**：
  - B1–B6：多实例池、Free/Pro 隔离、B6 不重复控制面预热与 `discovery_sessions` 动态模型发现；
  - 单页有界 drain，无 `SessionReadinessGate` / `_retiring_contexts` / 蓝绿；
  - B5a PG-01..04：首事件真实 HTTP 状态、finish-only、`CONTINUING=22`、2s 工具空闲 STOP；快照重试保持 **5×250ms**（拒绝上游 3×300ms）；
  - B4：三 Sink（5000 ring / 7d archive / SSE）、脱敏、`safe_*` dump、D09 hash；不恢复 raw headers/body/raw_response；
  - `quota_scope` 与 Paid 模型本地 `TierRequiredError` 403 不切号；
  - 根 `config.yaml` 保持未跟踪且保留本地 N18 修复内容；`defaults/config.yaml` 仅同步必要默认值。
- **落点**：`api/app.py`、`application/routing_pipeline.py`、`application/generation_service.py`、`infrastructure/gateway/{session,pooled_session,streaming,stream_parser,botguard_bootstrap,client,transport,capture}.py`、`infrastructure/browser/js/*`、`infrastructure/utils/logger.py`、`infrastructure/account/account_store.py`、`web/`、`src/aistudio_api/static/`。

### 3.1 Closeout 复审（2026-10-31）

- **复审结论**：PASS（0 blocker）；门禁 **767 passed / 2 documented Windows failures / 1 skipped**；`ruff`、`pyright`、`git diff --check`、`git diff --cached --check` 全部通过；0 unmerged。
- **已闭合**：
  - Q-10/N-27：`app.py:572-584` 参数校验日志仅 `loc/type/msg`，回归测试 `test_logging.py:654`；
  - Q-11/N-28：`logger.py:157-160` 恢复 `global _dump_to_file_override`，回归测试 `test_logging.py:627`；
  - Q-07/N-29：启动时先跑 keepalive metadata、`covered_ids` 去重，3 天内 Free 跳过 tier RPC；测试 `test_app_startup_warmup.py:159`、`test_account_keepalive.py:634/657`；
  - Q-08/N-31：`auth_error` 可选、存在必须数值、缺失默认 `0`；测试 `test_features_config.py:331/351`；
  - Q-09/N-30：`ARCHITECTURE.md:214` 与 `BOTGUARD_VERIFICATION_CHAIN.md:332` 删除 `auth_errors * 200.0` 表述；
  - N-34：手动 `detect-email`/`detect-tier` 写 ISO `checked_at`（`routes_accounts.py:489/544`）；
  - N-39：Paid `TierRequiredError` 403 不切号、首次 403→429 策略测试（`test_routing_pipeline.py:461/491`）。
- **部分/剩余**：
  - N-33 的“配置门控”子项未闭合：`app.py:400-413` 仅门控 keepalive，`_refresh_startup_account_metadata` 在 `metadata_refresh=false` / `metadata_refresh_on_startup=false` 时仍无条件执行；拆为 N-43（非阻断）。
  - N-42：手动 detect 路由的 ISO `checked_at` 无直接单测（代码已实现）。
  - N-44：5 项 closeout 当前为 unstaged（15 文件）；提交前需由用户显式 stage。
- **状态**：合并仍未提交；HEAD `b1a749e` + MERGE_HEAD `852de53`；根 `config.yaml` 仍未跟踪。报告：`reviews/REV-MERGE-852de53-oct31.md`（目标/主仓字节一致）。

## 合并守则

- 每次上游合并保持工作区未提交，交由 Reviewer 出审计报告，用户确认后再提交。
- 冲突解决优先级：目标重建架构（B1–B6、D-05/D-09/D-10/D-11）高于上游历史实现；上游仅吸收不破坏上述边界的能力。
- 自动合并文件同样逐文件语义复核，不能只依赖文本无冲突。

# Acceptance 工件规范（Gate 0.5）

用途：每个移植切片在提交前留下可复核的验收记录。模板与最低验收项来自 `12-gate0-inputs.md` §5；本目录只放记录，不放真实凭据。

## 目录与命名

- 每片一个文件：`docs/dev/rebuild/acceptance/<slice>.md`；P9-pre 为 `P9-pre.md`。
- 切片内可按测试项追加行；不要改写历史行，失败/重跑用新行并标注日期。
- 原始工件放 `docs/dev/evidence/<slice>-<date>/` 或 `tests/fixtures/`；记录里写相对路径，不写本机绝对路径。

## 必填字段

| 字段 | 内容 |
|---|---|
| Feature ID | `Dxx-NNN`；跨片时逐项列出 |
| 测试项 | 一句话行为断言 |
| 预期结果 | HTTP/状态/事件/日志的确定结果 |
| 命令 | `pytest ...` 或 fake harness 命令 |
| 工件 | 脱敏日志/SSE/fixture 路径 |
| 生命周期插入点 | Account/Instance/Pool-Router/Executor/Event Sink |
| 证据等级 | 离线 fixture / fake CDP / 受控真实（后置） |
| 状态 | planned / passed / failed / blocked |
| 日期 | `YYYY-MM-DD` |

## 规则

- 先写“测试项 + 预期结果”，再实现；不接受只写“测试通过”。
- 失败必须保留原始输出；修复后新增一行，不改旧结论。
- 证据只保留脱敏摘要或可离线复跑工件；禁止 cookie/auth header/API key/raw body。
- 真实账号/IP/Google 请求只允许在 IP 冷却后按 WARP 小流量单独立项，不得混入离线切片。
- 每个切片至少覆盖：正常路径、契约错误路径、生命周期释放路径（`active_requests` 归零）。

## 最低验收对照

- P3/P2：无 lease 断言；`active_requests` 在 `finally` 释放；候选含 SLEEPING/UNSTARTED；429 同号重试→切换→503；保存后切换重试。
- P5/PG：F-01/F-02/F-03/F-04 离线 fixture 通过；`[toolshape]` 已脱敏；F-05/06/07 后置。
- P6/P7：24h fake clock；build-only 回写；日志 SSE/1/3/7 天；client/attempt 分母；token 汇总不含 countTokens。

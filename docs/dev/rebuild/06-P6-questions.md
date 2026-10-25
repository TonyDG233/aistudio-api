# P6 决策问题：Keepalive / Rotation / 24h

P6 是 D08 的 25 行，全部非原生；8 个问题已于 2026-10-25 确认；最终决定见 `06-decision-log.md` 的 P6。这块直接对应“Pro 号还在、凭证失活”的保活问题。

| ID | 真正的问题 | 选项 | 涉及 feature IDs | 建议 |
|---|---|---|---|---|
| P6-Q1 | 保活服务与四类 job 保留哪些？ | A 保留服务、24h auth、24h metadata；B 只保留 auth；C 不做保活 | `D08-001`..`D08-005`, `D08-024` | A |
| P6-Q2 | 24h Cookie 回写覆盖哪里？ | A 覆盖 chat、build-only、active session，并按实际成功记账；B 只覆盖 chat；C 不回写 | `D08-008`..`D08-013` | A |
| P6-Q3 | metadata/tier/email 刷新怎么调度？ | A 启动 + 24h 统一 job；B 只启动刷新；C 不做 | `D08-006`, `D08-007`, `D08-023`, `D08-024` | A |
| P6-Q4 | 后台 auto switch/rebalance 保留吗？ | A 全部删除；B 保留；C 只保留手动 | `D08-015`..`D08-018` | A |
| P6-Q5 | 429 额度冷却与跨天重置保留吗？ | A 保留 60/300/600 + 美西午夜重置 + 账号标记；B 简化；C 删除 | `D08-020`, `D08-021` | A |
| P6-Q6 | active_activity 和 idle browser 回收怎么处理？ | A 保留 active_activity 作为可选页面保活（默认关）；idle 默认关，配手动重启；B 删 active_activity；C 总是 idle 回收 | `D08-014`, `D08-022` | A |
| P6-Q7 | 手动切号/手动跑保活/状态入口保留吗？ | A 保留手动切换、手动触发、状态查询；B 只保留日志；C 全删 | `D08-019`, `D08-025` | A |
| P6-Q8 | job 锁、busy、失败重试怎么处理？ | A 单循环串行；busy 跳过；失败单独短重试；B busy 排队；C 失败等下个 24h | `D08-003`..`D08-005` | A |

## 概念说明

- **保活**：定时加载账号页/会话并回写 Cookie，不是账号自动切换。
- **auth update**：默认 24h 回写 Cookie；当前 chat-ready 才回写，build-only 会被跳过。
- **metadata refresh**：刷新 tier/email/expired，避免 UI 和路由使用旧状态。
- **auto switch / rebalance**：后台主动改绑空闲实例的账号；P3/Q9 已决定丢弃，这里确认是否彻底删。
- **429 冷却**：账号触发额度/风控状态后标记，等额度刷新或跨天再解除；不是 slot `COOLDOWN`。
- **active_activity**：控制面 page 滚动并 `touch_activity`，会阻止 idle browser 回收，属于页面保活；不刷新账号凭证。已决定保留为可选项，默认关闭。
- **idle browser**：浏览器空闲后关闭；当前默认 0（禁用）。需要休眠唤醒 + 手动重启。
- **next_due**：下一次任务时间；busy 时是否推进会影响手动触发后的调度。

## Q8 的 job 锁说明

- 当前是**一个全局 job 锁**：metadata、auth_update、auto_switch、active_activity 同一时间只能跑一个。
- 为什么需要：这些 job 都会碰账号页、Cookie 或实例，两个 job 同时导航/回写会互相踩。
- 当前 busy 行为：拿不到锁就立即返回，并且仍推进 `next_due`；手动触发可能把定时任务推迟一个完整周期。
- 三个选项：
  - A：单全局串行；busy 跳过；失败按 job 短重试。最简单，账号少时够用。
  - B：全局排队；不跳过，但任务可能堆积、延迟。
  - C：按账号锁；同账号串行，不同账号并行。更贴合多实例，但复杂度更高。
- 建议先用 A；后续账号/实例规模上来，再换 C。

## 说明

- 本文件只列问题，不是已决策记录。
- 你按 `Q1 A, Q2 A...` 回复；确认后才写入 `06-feature-index.csv`、`06-feature-ledger.md`、`06-decision-log.md`。

# P2 决策问题：Routing / Candidate / Switch / Retry

状态：已全部确认（2026-10-24）。最终逐项决定见 `06-decision-log.md` 的 P2；本文件只保留问题定义。

P2 不是五个抽象词，而是 D05 的 43 条功能。下面只列真正需要你拍板的问题；当前全部是 `pending`，不是已决定。

| ID | 真正的问题 | 选项 | 涉及 feature IDs | 我的建议 |
|---|---|---|---|---|
| Q1 | 新核心还要不要 `channel=chat/pg/playground/build/official` 这层？还是只保留 `surface=chat/build`？ | A 保留 channel；B 只保留 surface；C 保留 channel 仅作 API 别名 | `D05-001..004` | B |
| Q2 | 旧策略组、账号 tag、quota group、affinity 打分还要不要？ | A 保留；B 全丢；C 只留 tag 给 UI | `D05-005,007,008,011..013,015,020` | B |
| Q3 | paid 模型是否继续要求 paid 账号且只能走 chat？ | A 保留；B 保留 paid 账号但允许 build；C 不限制 | `D05-006,009,010,014` | A |
| Q4 | 账号候选要保留现在的权重、公平分、affinity 排序，还是改成池内简单 FIFO/LRU？ | A 保留现排序；B 简单 FIFO/LRU；C 完全随机 | `D05-016..019` | B |
| Q5 | 租约/容量/等待怎么处理？build busy 是等、换号，还是直接 503？ | A 保留 30/15s + borrow；B 每池独立容量 + 明确等待；C 直接拒绝 | `D05-021..023,025,039` | B |
| Q6 | 204/404/hang 这类瞬态错误，应该同账号重试还是换号？ | A 先修 `retry_same`，同账号重试；B 保持换号；C 不重试 | `D05-029,030,032,033` | A |
| Q7 | 流式已经出了正文后，429/403 还要不要切号？ | A 不切；B 允许切但重放；C 直接断流 | `D05-034` | A |
| Q8 | 死熔断器和旧 `ensure_active_account` 回退要保留吗？ | A 接上熔断；B 丢掉两者；C 只留旧回退 | `D05-035,040` | B |
| Q9 | 自动 rebalance（后台改绑空闲实例）进第一版吗？ | A 第一版进；B 推到 P6；C 永远不做 | `D05-041` | B |
| Q10 | 强制 429 测试钩子保留吗？ | A 保留但只在测试开关；B 删除 | `D05-043` | A |

## 说明

- 这张表是 P2 的决策清单，不是已决策记录。
- 你按 `Q1 B, Q2 B, ...` 回复即可；我会逐项写入 `06-feature-index.csv`、`06-feature-ledger.md`、`06-decision-log.md`。
- P3 的 Browser/Context/Page 只是工作拆分，不是决策点，也不会在 P2 未确认前开始。

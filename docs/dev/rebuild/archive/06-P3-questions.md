# P3 决策问题：Browser / Context / Page / Instance / Pool / Lease

P3 是 D03 + D04 的 97 行（81 条非原生），9 个问题已于 2026-10-24 确认；最终逐项决定见 `06-decision-log.md` 的 P3。

| ID | 真正的问题 | 选项 | 涉及 feature IDs | 建议 |
|---|---|---|---|---|
| P3-Q1 | 一个“实例”到底按什么算？ | A 账号 + Context + Page + surface；B 现在 manager 的“账号+channel 记录”；C 一个 Context | `D04-001,002,004,015,061`; `D03-016,033` | A |
| P3-Q2 | 同账号 chat/build 怎么放？ | A 两个 Context、各一页、各算一个实例；B 一个 Context 两页；C 同一时间只开一个 | `D03-016`; `D04-005,006,007` | A |
| P3-Q3 | 实例数量/渠道/tier 准入在哪配？ | A 每个实例配 `count + surface + min_tier`，全局只做总上限；B 继续 channel 级配额；C 账号级配额 | `D04-004,009,027,062` | A |
| P3-Q4 | 休眠实例什么时候热机？ | A 消息一到，逐一热机所有合格休眠实例；B 只热机被选中的；C 不热机，等请求失败再恢复 | `D03-035`; `D04-031,034` | A |
| P3-Q5 | lease/容量满/关闭怎么做？ | A 保留内部 lease；容量满按 P2 路由/切号/503；关闭时关页并 dispose Context；B 去掉 lease；C 保留旧 max_contexts 和 defer | `D04-020,022,023,035..037,039,055..058`; `D03-014,030` | A |
| P3-Q6 | 手动固定/改绑实例要不要？ | A 保留手动 `pinned`/rebind/显式切换；不做自动 rebalance；B 不保留 pin；C 允许后台自动改绑 | `D04-032,038,039,045..052`; `D03-034` | A |
| P3-Q7 | Build 的 Context/Page 怎么建？ | A build 独立 Context+Page；保留临时 bootstrap chat 页做 BotGuard；B 同 Context 两页；C build 不走 BotGuard | `D03-027,028,033`; `D04-008,025,029` | A |
| P3-Q8 | 控制面还要不要蓝绿页？ | A 保留控制面蓝绿（登录/保活），请求池仍一 Context 一 Page；B 去掉蓝绿，直接换页；C 蓝绿扩展到请求池 | `D03-017,018,019,026` | A |
| P3-Q9 | 死状态/死 API 怎么处理？ | A drop `COOLDOWN`、`retire_idle`、`_runtime_occupancy`；B 接线；C 原样保留 | `D04-033,040,043` | A |

## 概念说明

- **Context**：隔离的浏览器配置/cookie 罐。目标是一个 Context 只放一个 Page。
- **Page**：一个浏览器 tab。
- **实例**：建议定义为账号 + Context + Page + surface；不是现在的 manager 记录数。
- **lease**：请求临时占用实例的内部凭证；request 结束必须 release，不是账号租约。
- **蓝绿**：控制面先开新页，新页 ready 后再切换 active，旧页 drain 后关闭；只用于登录/保活控制面。
- **pinned**：手动固定实例，禁止后台改绑。
- **休眠/热机**：浏览器空闲关闭或 stale 后，消息到达时重新加载 context/page。
- **COOLDOWN / retire_idle / _runtime_occupancy**：当前没有实际调用方或没有赋值点的死状态/死 API。

## 说明

- 本文件只列问题，不是已决策记录。
- 你按 `Q1 A, Q2 A, ...` 回复；确认后才写入 `06-feature-index.csv`、`06-feature-ledger.md`、`06-decision-log.md`。

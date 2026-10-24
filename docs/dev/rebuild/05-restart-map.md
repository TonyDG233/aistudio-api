# 05 Restart Map

状态：2026-10-24 重写。本文只保留重构方向、决策规则和大致步骤；逐功能决定在 `06-feature-ledger.md`、`06-feature-index.csv`、`06-decision-log.md`。当前进度：P1/P2/P3 已决定；P4 进行中。

## 1. 重构大方向

目标不是继续修当前 fork，而是：

- 以 `upstream/master` 的相对干净实现为骨架；
- 新建独立分支/worktree，不合并当前 `master` 或 `rebuild`；
- 当前 fork 只作为功能参考、失败样本和证据来源；
- 按垂直切片逐项移植，不做大爆炸式合并。

核心原则：

- `tier` 保留，以上游原生解析为准，fork 不重复定义。
- `paid` 模型进 Pro/paid 池；free 模型进 free 池；QQ bot 固定 free 池。
- 一个 Context 只放一个 Page；同账号 chat/build 需要两个 Context。
- 不再按反代途径/channel 做第一层路由；只保留 `surface=chat/build` 和模型 tier。
- 旧策略组、打分、账号 tag、quota group、死 UI 不进入新骨架。
- 日志页保留为原始事件流；统计页保留为聚合视图，后端使用单一事件模型。
- Cookie 保活保留；OAuth/refresh_token 当前不存在，本轮不做。

## 2. 决策治理

- `D01-D12`：冻结的 domain。
- `Dxx-NNN`：功能唯一 ID，见 `06-feature-index.csv`。
- `P1-P10`：决策包/移植波次，只做分组，不是事实标准。
- 每个决定必须落到 feature ID；不能只写“某 domain 保留”。
- AI 默认建议不是决定；未获明确确认前只能记为提案。
- `05` 只写方向和步骤，不复制 515 行。
- `06-feature-ledger.md` 是人类台账；`06-feature-index.csv` 是机器索引；`06-decision-log.md` 是决策记录。
- 变更 06 后必须同步重新生成 index，禁止两套各改各的。

## 3. 目标骨架

建议只保留五个稳定层：

1. **Account/Auth**：账号身份、tier、Cookie、expired 状态。
2. **Instance**：账号会话 + 一个 Context + 一个 Page + 一个 surface。
3. **Pool/Router**：free/pro 池隔离；按模型 `paid` 标记选池。
4. **Executor**：一次请求的 lease、attempt、release；不承担账号选择策略。
5. **Event Sink**：日志原始事件 + 统计聚合事件，UI 只消费稳定接口。

Build 是独立 surface，不是一个 pool tier。Build 页面、9998、signer 只通过明确接口接入，不反向控制 router。

## 4. 移植顺序

0. 冻结 P1-P10 的逐项决定。
1. 审计 `upstream/master` 的扩展缝：账号身份、Context/Page 工厂、请求执行、事件出口。
2. 在 clean 分支建立最小 core，不接 FastAPI/UI。
3. P1：Account / Auth / Paid。
4. P2：Routing / Retry（先修 `retry_same` 语义）。
5. P3：Context / Page / Pool / Lease。
6. P4：Build Surface（native/signer 分离）。
7. P5：Generation / Protocol / Wire。
8. P6：Keepalive / 24h / 自动切号。
9. P7：Logs / Stats（单一事件模型）。
10. P8-P10：API/UI、Boot/Config、Tests/Evidence。

每一片只能在前一片的验收通过后进入下一片。

## 5. 验收底线

- 不需要真实 Google 请求即可证明：
  - paid 只进 Pro 池；
  - free/QQ bot 有独立池；
  - 一个 Context 只有一个 Page；
  - 同账号 chat/build 使用两个 Context；
  - 关闭实例不误杀其他实例；
  - 429/auth/transient 错误的重试路径符合新契约。
- 统计页能回答：哪个账号、模型、surface、尝试、错误、用量、延迟。
- 日志页保留原始事件，不把统计聚合混进日志总线。

## 6. 不做的事

- 不修复当前 fork 的业务代码；
- 不合并当前 `master`；
- 不把 `rebuild` 的 95 个提交整体移植；
- 不恢复旧策略组、打分、channel 路由、tag、quota group；
- 不在风控期访问真实账号/IP/Google；
- 不写新 WP，不用长文档替代可执行验证。

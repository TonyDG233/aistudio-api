# 05 Restart Map

状态：收敛版地图；骨架 `99c38ba`；当前分支 `rebuild`（执行时用 `git rev-parse HEAD`，不写死旧 SHA）；目标 worktree `.kilo/worktrees/rebuild-upstream`；当前不是 handoff-ready。

## 1. 目标与边界

- 以 `99c38ba` 为骨架建独立 worktree，不合并 `master`/`rebuild`；旧 fork 只作参考与失败样本。
- 请求实例固定 `1 账号 + 1 Context + 1 Page + 1 surface`；同账号 chat/build 不并存。
- 并发原语只用 `active_requests`；不恢复 lease/pinned/自动 rebind/defer。
- 控制面只做登录与保活，不占请求容量。
- 不访问真实账号/IP/Google，除非用户明确授权。

## 2. 权威与文档职责

| 文档 | 职责 |
|---|---|
| `06-decision-log.md` | 用户决策与顺序 |
| `08-08-upstream-99c38ba-full-diff-audit.md` | 上游事实 |
| `08-09-decision-record.md` | D-01..D-14 与冲突闭合 |
| `domains/P5-generation/09-pg-toolcall-analysis.md` | P5 PG/toolcall/preset 依赖分析；不是历史 |
| `06-feature-index.csv` + `06-feature-ledger.md` | inventory，不是计划 |
| `05-restart-map.md` | 本页地图 |
| `10-handoff-prompt.md` | 一页 handoff |
| `00-chat-postmortem.md` | 事故复盘，只读 |
| `08-10` / `08-11` | P4 事故记录/附录，P4 前不进入主线 |
| `12` / `13` | Gate 旧文档；A5 归档，不再作权威 |

## 3. 当前状态

- 实际迁移代码 0 行；docs 曾是主要产出。
- 目标 worktree 的 `src` diff 为 0；A5/A6 已完成并提交，目标 docs 已同步。
- 534 行索引一致，但只是 inventory。
- A 阶段文档收敛进行中；B/C 未开始。
- `06` 的 D5 旧顺序已修；发现任何旧顺序或旧 D-14 残留，先停。
- 管理页只由 `AISTUDIO_WEB_PASSWORD` 控制（空 = 免密）；D-14 不重开。

## 4. 阶段与顺序

- Gate0/G0.x 不再作为独立体系，改为 A/B/C 阶段 + 每步审计。
- A 文档收敛：A1 `06` 顺序 → A2 D-14 → A3 05 → A4 10 → A5 归档旧审计 → A6 同步目标 docs。
- B 实现：P9-pre → P1+P3+P2 → P6 → P7 → B5a PG/toolcall 复现 → B5b P5 OpenAI。
- C 扩展：P4 Build（先按 `08-10` §4 探索）→ P8/UI → Claude/Responses → 统计页 → P10。
- 顺序 P6 → P7 → P5 固定；每阶段前一步审计通过才进下一步。

### 4.1 步骤详情

| Step | 范围 | 退出条件 |
|---|---|---|
| A1 | `06` D5 顺序与预检 | `P6→P7→P5`，无旧顺序/伪日期，索引通过 |
| A2 | D-14 引用与 D11 台账 | 空 Key 不开放；管理页 env-only；D11-015=rewrite；D11-016=drop |
| A3 | `05` 一页地图 | 不超过 100 行；含目标/权威/顺序/审计/停止条件 |
| A4 | `10` 一页交接 | 不超过 100 行；下一任可独立执行 B |
| A5 | 归档旧审计/Gate 文档 | 旧 01-04/07/08-01..07/12/13/P*-questions 移入 `archive/`；09 保留为 P5 输入 |
| A6 | 同步目标 worktree | 目标 docs 与 A1-A5 一致；`src` diff 仍为 0 |
| B1 | P9-pre 装配/配置/API guard | 启停/配置校验通过；空 Key 拒绝；不写业务逻辑 |
| B2 | P1+P3+P2 账号/页面/路由 | 1 账号 1 Context 1 Page；`active_requests`；路由/重试测试 |
| B3 | P6 保活/24h | 凭证回写覆盖 chat/build-only；fake clock；VPS 可用 |
| B4 | P7 日志/错误关联 | 日志页/导出；请求-尝试关联；不含统计页 |
| B5a | PG/toolcall/preset 复现 | 见 `domains/P5-generation/index.md` |
| B5b | P5 OpenAI 集成切片 | 见 `domains/P5-generation/index.md`；B5a 未过不得进入 |
| C1 | P4 Build | 先按 `08-10` §4 探索；400 分类；无 9998 无限重试 |
| C2 | P8 API/UI | 现有 UI 增量；D-12；ModelCatalog 单一所有者 |
| C3 | Claude/Responses | OpenAI 稳定后；协议映射与测试 |
| C4 | 统计页 | 单一事件账本；client/attempt 分母分离 |
| C5 | P10 收尾 | 测试/证据/changelog/文档同步；无未验证项隐瞒 |

每个 Step 的审计通过并经用户确认后才 commit；前一 Step 未通过不进入下一 Step。

## 5. Agent 执行与审计规则

- 主 agent = integrator，唯一提交权；worker 只接一个 `Dxx-NNN` 或明确文件域；reviewer 只读。
- 默认用能返回报告的 `task` 子 agent，报告回主 agent；不用无法回收的独立会话。
- 每步先产出审计表：Step、改动文件、diff、依据、检查命令/结果、未解决项、是否新增决策/Gate/范围、PASS/CONDITIONAL/FAIL。
- 每个 Step 一个主题；用户审计确认后才 commit；不合并、不 push。
- 文档每页一个职责；05/10 不超过一页；不新增文档；不把 534 当 MVP；不用文档自洽冒充进展。
- 真实账号/IP/Google 测试须事前得到用户同意；证据在实现/验收时生成，不预填。
- 文档布局：根只放稳定入口；域细节进 `domains/Px/`，事故进 `incidents/`，历史进 `archive/`，证据进 `docs/dev/evidence/`；每 Step 不新建文档。
- B5a 详情只放 `domains/P5-generation/09-pg-toolcall-analysis.md`；`05` 一行步骤，`10` 一个指针。
- worker/reviewer 报告必须原样交用户；用户审计前主 agent 不直接改。

## 6. 停止条件

- 文档超过一页，或开始重复 05/06/08-09。
- 某步需要新增 D-xx、Gate、顺序或范围。
- 目标 worktree 的 `src` diff 不再为 0。
- B5a/B5b 无法形成可运行请求且没有明确错误证据。
- 上游 `99c38ba` 变化但未重新对账。
- 再次用文档/审计替代代码、测试、日志。

## 7. 指针

- `docs/dev/rebuild/06-decision-log.md`
- `docs/dev/rebuild/06-feature-index.csv`
- `docs/dev/rebuild/06-feature-ledger.md`
- `docs/dev/rebuild/08-08-upstream-99c38ba-full-diff-audit.md`
- `docs/dev/rebuild/08-09-decision-record.md`
- `docs/dev/rebuild/domains/P5-generation/index.md`
- `docs/dev/rebuild/domains/P5-generation/09-pg-toolcall-analysis.md`
- `docs/dev/rebuild/10-handoff-prompt.md`

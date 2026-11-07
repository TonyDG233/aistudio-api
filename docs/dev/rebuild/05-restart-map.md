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
| `incidents/INC-20261028-01-build-400.md` / `INC-20261028-02-build-400-trace.md` | P4 事故记录/附录，Post-C 前不进入主线 |
| `12` / `13` | Gate 旧文档；A5 归档，不再作权威 |

## 3. 当前状态

- A/B 阶段已完成并提交；B7 配置自愈、分层解耦与文档审计已收口。
- C 阶段按 2026-11-01 新顺序执行：C1 → C2 → C3 → C4 → C5；旧顺序（C1=P4 Build → P8/UI → Claude/Responses → 统计页 → P10）已于 2026-11-01 被取代，见 `06` 同日决策节；`06` 的 D5 旧顺序已修，发现旧顺序或旧 D-14 残留先停。
- C1 与 C2（C2a/C2b/C2c）已完成并提交；当前进入 C3：Claude `/v1/messages` 与 OpenAI Responses `/v1/responses` 协议适配。
- Build（P4/P3-F）不再是 C1 阻塞项：渠道修复/永久废弃决策移到 Post-C（单仓独立阶段）处理；历史事故记录 `INC-20261028-01` 保留。
- 管理页只由 `AISTUDIO_WEB_PASSWORD` 控制（空 = 免密）；D-14 不重开。

## 4. 阶段与顺序

- Gate0/G0.x 不再作为独立体系，改为 A/B/C 阶段 + 每步审计。
- A 文档收敛：A1 `06` 顺序 → A2 D-14 → A3 05 → A4 10 → A5 归档旧审计 → A6 同步目标 docs → A7 agent 工作流准备。
- B 实现：P9-pre → B0 目标 `AGENT.md` → P1 → P3-D03 → P3-D04 → P2 → P6 → P7 → B5a → B5b。
- C 扩展（2026-11-01 重排）：C1 浏览器按账号指纹加固 + `/v1beta/models` 401 噪音根因修复 + B7-P1 Tier/发现单源收敛 → C2 P8 API/Web 管理台增量（先确认 UI 预设计清单）→ C3 Claude/Responses 协议适配 → C4 P7 统计页（单一事件账本 + client/attempt 分母分离）→ C5 P10 收尾。
- Post-C（单仓独立阶段）：browserless（WAA/BotGuard 纯沙箱）探索；Build 渠道修复/永久废弃决策；不阻塞 C1-C5。
- 顺序 P6 → P7 → B5a → B5b 固定；每阶段前一步审计通过才进下一步。

### 4.1 步骤详情

| Step | 范围 | 退出条件 |
|---|---|---|
| A1 | `06` D5 顺序与预检 | `P6→P7→P5`，无旧顺序/伪日期，索引通过 |
| A2 | D-14 引用与 D11 台账 | 空 Key 不开放；管理页 env-only；D11-015=rewrite；D11-016=drop |
| A3 | `05` 一页地图 | 不超过 100 行；含目标/权威/顺序/审计/停止条件 |
| A4 | `10` 一页交接 | 不超过 100 行；下一任可独立执行 B |
| A5 | 归档旧审计/Gate 文档 | 旧 01-04/07/08-01..07/12/13/P*-questions 移入 `archive/`；09 保留为 P5 输入 |
| A6 | 同步目标 worktree | 目标 docs 与 A1-A5 一致；`src` diff 仍为 0 |
| A7 | agent 工作流准备 | 任务包 8 字段、A→B 检查、incident 规则、真实测试闸门可执行；不写 AGENT.md |
| B1 | P9-pre 装配/配置/API guard | 启停/配置校验通过；空 Key 拒绝；不写业务逻辑 |
| B2-1 | P1(D02) 账号/Cookie/预设/Paid | 账号注册表、Cookie 跨域保真、D-08、D-13、Paid 识别/资格；`active_requests` |
| B2-2 | P3-D03 浏览器/CDP/Context | PooledSession、watchdog D-02、WS ping D-03、关闭多余 Page D-11、去控制面蓝绿/无限 drain D-10 |
| B2-3 | P3-D04 多实例池 | 1 账号+1 Context+1 Page+1 surface；去 lease/defer/borrow/pinned/自动 rebind；只留 `active_requests`；D-05 有界 RETIRING |
| B2-4 | P2(D05) 路由 | Free/Pro 分流；403 不切号 D-04/D-06；`quota_scope` D-01；429 同号重试→切现有实例→503 |
| B3 | P6 保活/24h | 凭证回写覆盖 chat/build-only；fake clock；VPS 可用 |
| B4 | P7 日志/错误关联 | 日志页/导出；请求-尝试关联；不含统计页 |
| B5a | PG/toolcall/preset 复现 | 见 `domains/P5-generation/index.md` |
| B5b | P5 OpenAI 集成切片 | 见 `domains/P5-generation/index.md`；B5a 未过不得进入 |
| C1 | 浏览器指纹加固 + 401 噪音 + Tier 单源 | 按账号浏览器指纹加固（P3-F / D03-027..028）；`/v1beta/models` 401 噪音根因修复（HEALTHCHECK 改 `/health` + 只读缺 Key 去重）；B7-P1 Tier/发现单源收敛 |
| C2 | P8 API/Web 管理台增量 | 现有 UI 增量；先确认 UI 预设计清单；D-12；ModelCatalog 单一所有者 |
| C3 | Claude/Responses 协议适配 | OpenAI 稳定后；协议映射与测试 |
| C4 | P7 统计页 | 单一事件账本；client/attempt 分母分离 |
| C5 | P10 收尾 | 更新根目录两份 README；清理主 `master`；把 `rebuild-upstream` 合并/提升到主 `master`；移除 `.kilo/worktrees/rebuild-upstream`；结束双 worktree 模式 |
| Post-C | 单仓独立阶段 | browserless（WAA/BotGuard 纯沙箱）探索；Build 渠道修复/永久废弃决策；不阻塞 C1-C5 |

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
- 不再预写纸面 PKG；直接依据 `06`/`08-09`/台账/测试表，参考主仓 `rebuild` 移植代码并跑通 `pytest`；单步审查发现问题只做当前步骤内局部返工，严禁推翻整体计划或重写 `05/06/08-09`。旧纸面批次草稿与初审已归档至 `archive/batch-drafts-20261029/`，仅作历史留底，严禁作为施工依据；B 阶段每步真实代码施工后的审查报告写入对应 `domains/Px/reviews/`。

## 6. 停止条件

- 文档超过一页，或开始重复 05/06/08-09。
- 某步需要新增 D-xx、Gate、顺序或范围。
- A 阶段要求目标 worktree 的 `src` diff 为 0；进入 B 阶段后按批准步骤修改代码。
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

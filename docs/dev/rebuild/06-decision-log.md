# Feature Decision Log

范围：`docs/dev/rebuild/06-feature-ledger.md` 的逐包决策记录。只记录决定，不复制台账行；未确认项标 `pending`。

修订：2026-10-27 按 D1-D8 修复索引、状态机与顺序；2026-10-28 按 08-08/08-09 闭合 99c38ba 的 14 项决策、17 个 NO_FEATURE_ROW 与 20 个冲突。`08-01-execution-readiness-audit.md`、`09-pg-toolcall-analysis.md` 保留历史审计原文，不回改；修复后需重新独立审计。

## 重构大方向（2026-10-24）

- 以 `upstream/master` 的干净实现为新骨架；当前 fork 只作功能参考、失败样本和证据来源。
- 从 upstream 新建独立分支/worktree；不合并当前 `master`/`rebuild`。
- 先审计 upstream 扩展缝：账号身份/tier、Context/Page 工厂、请求执行/`active_requests`、日志/统计事件出口。
- 按垂直切片逐项移植，不做大爆炸合并。
- 保留：`tier`、paid/free 分流、Cookie 保活、Build、协议适配、日志页、统计页。
- 丢弃：旧策略组、打分、channel 第一层路由、账号 tag、quota group、死 UI。
- 统计页保留，但后端要改成单一事件/聚合模型；日志页保持原始事件流。
- OAuth/refresh_token 当前不存在，本轮 defer；现有保活是 Cookie 会话刷新。

## 编号标准

- `D01-D12`：台账冻结的 domain，唯一 domain 标准。
- `Dxx-NNN`：每行功能的唯一 feature ID，来自 `docs/dev/rebuild/06-feature-index.csv`。
- `P1-P10`：决策包/移植波次，只做分组，不是事实标准。
- 所有最终决定必须写到 feature ID；不能只写“D02 保留”或“P1 保留”。
- AI 的默认建议不是用户决策；未获用户明确确认前，CSV 的 `decision` 必须为 `pending`，06 必须为 `待决定`。
- `06-feature-index.csv` 是机器可读桥接表；`06-feature-ledger.md` 是人类阅读版。两者行顺序一一对应。

## 决策包到 Domain 的桥接

| 包 | Domain | 台账总行 | 非原生行 | 决策内容 |
|---|---|---:|---:|---|
| P1 | D02 | 34 | 25 | Account / Auth / Paid |
| P2 | D05 | 47 | 46 | Routing / Candidate / Switch / Retry |
| P3 | D03 + D04 | 98 | 82 | Browser / Context / Page / Instance / Pool / Lease |
| P4 | D07 | 44 | 44 | Build Surface |
| P5 | D06 | 74 | 54 | Generation / Protocol / Wire |
| P6 | D08 | 25 | 25 | Keepalive / Rotation / 24h |
| P7 | D09 + D10 | 69 | 53 | Logs / Stats |
| P8 | D11 | 42 | 34 | API / UI |
| P9 | D01 | 64 | 43 | Boot / Config |
| P10 | D12 | 37 | 36 | Tests / Evidence |
| **合计** | — | **534** | **442** | 与 CSV 实际行数一致（08-09 新增 16 行） |

## 执行前置 / 顺序 / 状态机（D1-D8，2026-10-27）

`08-01-execution-readiness-audit.md` 的 D1-D8 已全部按 A 确认。本节是执行口径；与旧 CSV 冲突时以 P2/P3 决策和本节为准。08/09 保留为历史审计，不修改其原文；修复后必须重新独立审计，再把 `10-handoff-prompt.md` 交给全新 agent。

| 决策 | 执行规则 |
|---|---|
| D1 文档权威 | P2/P3 决策日志与 08-09 为行为权威；CSV/ledger 只是索引。`ledger_line` 必须指向实际台账行；feature 文本、行顺序、decision 三者必须一致。历史 feature 文本可能描述旧 lease/defer；`decision_note` 的“改写为…”是执行口径。桥接行数以 CSV 为准：P1 34、P2 47、P3 98、P4 44、P5 74、P6 25、P7 69、P8 42、P9 64、P10 37，总 534。 |
| D2 并发原语 | 取消 lease / reservation / lease 超时预算；只保留 `active_requests`。acquire 返回 `active_requests` 句柄；成功、失败、取消、断连、流关闭都在 `finally` 释放。**手动关闭/改绑**在有 `active_requests` 时仍可拒绝；但**禁用账号/应用预设的 rebind 不得直接拒绝**（D-05）：必须进入 `RETIRING`、停止新请求、有界等待 `active_requests=0` 或超时强制关闭，再关旧页/切号。容量按实例数计算，不再有 reservation 计数。 |
| D3 控制面 | 控制面只负责登录与保活，不是请求实例，不占池容量。请求实例固定为 `1 账号 + 1 Context + 1 Page + 1 surface`；同账号 chat/build 不并存。账号有活动实例时，Cookie 回写走实例页；否则允许独立控制页。控制面不做蓝绿；切号必须关闭旧页再重建。 |
| D4 sleep / wake / rebind | 消息到达时唤醒所有合格 SLEEPING/UNSTARTED 实例，候选包含休眠/未启动账号；不局限于存活实例。idle browser 自动回收默认关闭（默认不自动关）；失败/stale 才手动重启，禁止自动 stale 复活。rebind：后台自动 rebind 丢弃；手动显式关闭旧实例再重建允许。禁用账号/应用预设触发同一控制面 rebind（D-05）：RETIRING → 停新请求 → 有界等待/超时强制关闭 → 关旧页 → 切号，不得直接拒绝。禁止 pinned、自动 rebalance。429 先同账号重试，再唤醒/切现有实例，保存消息后切换实例再重试；耗尽返回 503 + 等待/管理页提示。已出正文不再切号。 |
| D5 顺序 | 预检（A 文档收敛，不是独立 Gate）→ P9-pre 装配/API Key guard → P1+P3+P2 → P6 保活/24h → P7 日志/导出 → P5 OpenAI/Gemini → P4 Build（启动时先按 08-10 做前期探索）→ P8/UI → Claude/Responses → 统计页 → P10 收尾。VPS 运行 P5 前必须先有 P6 保活和 P7 可用的 debug 日志。每片验收通过后才进入下一片。 |
| D6 迁移 | 只迁移 `docs/dev/rebuild` + 脱敏 evidence 显式 allowlist；禁止 `-- docs` 整树。迁移前 secret scan；旧 WP 归档到 `docs/dev/evidence/p1-ark-merge-archive/`，关键文档可留 `docs/dev/` 根。 |
| D7 PG 修复门槛 | P5 验收前完成 F-01（functionCall 缺 thoughtSignature 兜底）、F-02（首事件前真实 HTTP 4xx/5xx，不再 200 SSE 伪装）、F-03（finish-only/工具流终止兜底）、F-04（functionResponse/functionCall 强制 Struct）和脱敏 `[toolshape]` 摘要。F-05/06/07 后做，不阻塞 P5 最小闭环；全部离线 fixture 可验证。 |
| D8 UI / 模型目录 | upstream Vue 原生布局、文案、交互细节不得无故重写或删除；新 UI 以原生增量为先。ModelCatalog 是模型/paid 元数据唯一所有者；日志、统计、路由只消费，不各自维护 paid map。 |

## P1 Account / Auth / Paid

### P1-A 账号身份与 Cookie

Feature IDs：`D02-001`, `D02-003`..`D02-013`, `D02-019`, `D02-029`..`D02-031`

决策：`keep`

内容：注册表、旧 auth 迁移、Cookie 导入/解析/保真/注入/回写、探活导入、删除、重复标记、身份校验。

### P1-B 账号状态与 Tier

Feature IDs：`D02-002`, `D02-014`..`D02-017`

决策：`keep`，但 tier 以上游原生解析为准，fork 只保存/刷新；24h 调度归 P6。`D02-002` 的 tags 部分归 P1-E1。

### P1-C Paid 模型识别

Feature IDs：`D02-021`..`D02-023`

决策：`keep`，合并双份 paid fallback 种子，只保留单一来源。

### P1-D Paid 资格与 Free/Pro 分流

Feature IDs：`D02-025`..`D02-028`

决策：`keep`；paid 模型需 paid 账号且先走 chat；修掉 free 默认只开 build 的旧残留。

### P1-E1 旧账号 tags / routing 覆盖

Feature ID：`D02-020`

决策：`drop`。`tier` 不受影响；tag 是旧策略组/打分体系的一部分。

### P1-E2 OAuth / refresh_token

Feature ID：`D02-018`

决策：`defer`。当前代码没有 OAuth/refresh_token；现有保活是 Cookie 会话刷新。若未来需要浏览器外登录，再单独立项。

### P1-E3 Paid map

Feature ID：`D02-024`

决策：`keep`，但迁移为模型目录/发现服务拥有的元数据；统计页只消费，不拥有。

## P2 Routing / Candidate / Switch / Retry（已确认 2026-10-24）

### 总原则

- 只保留一套渠道分类；不再保留 `pg/playground/chat` 重复别名。API 默认裸模型名；只有两个渠道都实际启用且实例分配分流时，才暴露渠道前缀。
- 丢弃策略组、tag、quota group、affinity 打分；消息按简单均衡负载分配。
- paid 模型必须使用 Pro 及以上账号；tier/渠道准入配置放到实例级，不再在渠道层重复设置。
- 候选排序用 sticky + 简单 LRU/FIFO；优先保持当前健康账号，避免反复登录。
- 容量满时路由到现有可用实例，并把满的实例切到下一个候选；连续切号到上限后 503，错误文案提示等待切号/去管理页检查。
- 上游瞬态错误：先重试，再路由，再重试；现有实例都试过或触发连续切号上限后报错。重试策略与重试后路由回退分别给开关。
- 正文已出后被截断不切号；下一次请求再触发 429/403 时才走切号。
- 保留连续切号上限 + 最终 503；丢弃旧 `ensure_active_account/try_switch_account` 全局回退和死熔断器。
- 自动 rebalance 丢弃，不做后台主动改绑。
- 保留强制 429 测试钩子。

### P2-A Channel / surface 解析

Feature IDs：`D05-001`..`D05-004`

决定：`drop` 旧 channel/alias 抽象。新设计只保留一套渠道分类和条件式 API 前缀。

### P2-B 旧策略组 / tags / quota / affinity

Feature IDs：`D05-005`, `D05-007`..`D05-008`, `D05-011`..`D05-013`, `D05-015`, `D05-020`

决定：`drop`。策略组、tag、quota group、account routing 覆盖、affinity、旧 rotator fail-open 均不进入新核心。

### P2-C Paid / free 资格与实例级准入

Feature IDs：`D05-006`, `D05-009`..`D05-010`, `D05-014`

决定：
- `D05-006`、`D05-014`：`port`，简化为 paid/free 池和实例级 tier/channel 配置。
- `D05-009`：`keep`，paid 模型必须 Pro 及以上账号。
- `D05-010`：`drop` 硬编码 chat-only；由实例的 channel + 最低 tier 配置决定。

### P2-D 候选调度 / 公平性

Feature IDs：`D05-016`..`D05-019`

决定：
- `D05-016`..`D05-018`：`drop`。affinity、组/账号权重、公平分不进入新核心。
- `D05-019`：`port`，只保留 warm slot / ready 账号的同分 tie-breaker；排序仍是池内 sticky + 简单 LRU/FIFO，不做打分。

### P2-E Acquire / capacity / defer

Feature IDs：`D05-021`..`D05-023`, `D05-025`, `D05-039`

决定：
- `D05-021`：`port`，但 acquire 不再返回 lease，而是 `active_requests` 句柄；无 reservation、无 30s/15s 预算。候选包含休眠/未启动账号，消息到达先唤醒所有合格休眠实例。
- `D05-022`：`port`，borrow 只对未试账号再次 acquire，不新开实例、不恢复 lease。
- `D05-023`：`port`，容量拒绝映射保留；build→`surface_instance_busy`，chat→`surface_quota_exceeded`，统一 503。
- `D05-025`、`D05-039`：`drop` 固定 defer、pinned 和**后台自动 rebind**；手动显式关闭旧实例再重建保留。容量满按 P2 路由/切号，耗尽返回 503（`D05-038`）。

### P2-F 错误 / 重试 / 切换

Feature IDs：`D05-024`, `D05-026`..`D05-038`, `D05-040`, `D05-042`..`D05-043`

决定：
- `D05-027`..`D05-028`、`D05-031`、`D05-034`、`D05-042`..`D05-043`：`keep`。
- `D05-026`：`port`，429 路径改为“先同账号重试 → 唤醒/切现有实例 → 保存消息后切换再重试 → 耗尽 503”；与 `D05-038` 一致。
- `D05-036`..`D05-037`：`port`，成功闭环与 finally 释放改写为 `active_requests`；不保留 lease。
- `D05-024`：`port` 连续切号预算。
- `D05-029`、`D05-030`、`D05-032`、`D05-033`：`port`，先修 `retry_same`，瞬态错误先同账号重试。
- `D05-038`：`port`，切号耗尽改为 503 + 等待/管理页提示。
- `D05-035`、`D05-040`：`drop` 死熔断器和 legacy 全局回退。

### P2-G InstanceSwitcher / 自动重排

Feature ID：`D05-041`

决定：`drop`。不做后台主动改绑空闲实例；请求驱动切号由 P2-F 承担。

## P3 Browser / CDP / Context / Page / Instance / Pool / Lease（已确认 2026-10-24）

### 总原则

- 请求实例固定为：`1 账号 + 1 Context + 1 Page + 1 surface`；控制面不是请求实例。
- 同账号不同时开 chat/build；需要切换就关闭当前实例再重建，或换账号。
- 实例数量、surface、最低 tier 只在实例配置里设置；全局只做总上限。
- 控制面只负责登录/保活，不占池容量。账号有活动实例时 Cookie 回写走实例页；否则允许独立控制页。
- 消息到达时唤醒所有合格休眠实例，不只限存活实例；不做复杂性能调度。
- 取消 lease/reservation 和 30s/15s 预算；只保留 `active_requests`，用于防止关闭/改绑杀流。
- 容量满按 P2 路由/切号/503；不自动新开实例，不恢复 defer。
- 关闭即释放实例；失败/stale 才手动重启，禁止自动 stale 复活。
- rebind：后台自动 rebind 丢弃；手动显式关闭旧实例再重建允许；禁用账号/应用预设触发同一控制面 rebind（D-05）：`RETIRING` → 停新请求 → 有界等待/超时强制关闭 → 关旧页 → 切号，不得直接拒绝；丢弃 pinned、自动 rebalance。
- Build 使用独立 Context/Page；BotGuard 参考项目实现研究/移植，不把临时 chat bootstrap 页作为常态。
- 丢弃控制面蓝绿；控制面保持单页，切号关闭重建。
- 丢弃 slot COOLDOWN、retire_idle、_runtime_occupancy；账号额度冷却/解封逻辑保留在账号层。

### 单元与 Feature IDs

- **P3-A Browser/CDP 原生能力**：`D03-001..015`, `D03-020..026`, `D03-030..032` → keep；`D03-029`、`D03-034` drop（stale/pinned 自动复活删除，stale 手动重启）。
- **P3-B 一账号一 Context 一 Page**：`D03-016`, `D03-019`, `D03-033`; `D04-005`..`D04-007`, `D04-019`, `D04-028`, `D04-061` → 保留单页模型；`D03-016`, `D03-019`, `D04-006`, `D04-007`, `D04-028` drop；其余 port。`D03-019` 只归 drop，不再同时列为 port。
- **P3-C Manager / startup / config**：`D04-001`..`D04-004`, `D04-009`..`D04-018`, `D04-062` → port；`D04-003`, `D04-009`, `D04-013`, `D04-015`, `D04-027` drop；`D04-012`, `D04-014`, `D04-017`, `D04-018` keep。
- **P3-D Capacity / lease / close**：`D04-020`..`D04-026`, `D04-029`, `D04-030`, `D04-035`..`D04-037`, `D04-041`, `D04-042`, `D04-044`, `D04-052`, `D04-055`..`D04-058` → lease/global-page/borrow/defer drop；`D04-023`, `D04-037`, `D04-041`, `D04-042`, `D04-052`, `D04-056` port；`D04-057` drop（改为容量满 503）；`D04-031` 归 P3-E。
- **P3-E Sleep / wake**：`D03-035`, `D04-034` → port/keep；`D04-031` drop（stale 手动重启）；消息到达唤醒所有合格休眠实例。
- **P3-F Build / BotGuard**：`D03-027`, `D03-028`; `D04-008`, `D04-053`, `D04-054` → port；build 独立 Context/Page；BotGuard 参考项目研究，暂不把 chat bootstrap 作为常态。
- **P3-G 控制面蓝绿**：`D03-017`..`D03-019` → drop。
- **P3-H 手动切换 / pin / auto rebind**：`D04-032`, `D04-038`..`D04-040`, `D04-045`..`D04-051`, `D04-059`, `D04-060` → 手动切换（含显式关闭旧实例后重建）保留；后台自动 rebind、pinned、auto drop。
- **P3-I 死状态/死 API**：`D04-033`, `D04-043` → drop；账号额度冷却不在 P3 清理范围。

## P4 Build Surface / 9998 / Signer（原始确认 2026-10-25；P4-B0 后 Q4 待重审）

### 总原则

- Build 实现优先级下降；以下决定保留，但排在多实例/路由、保活、日志之后。
- `Q1 B`：Build app 配置优先，保留 Ark 默认。
- `Q2 C`：健康时复用页面；异常、换号、断线时重建。
- `Q3 A`：参考项目移植 BotGuard 到 build；失败时保存 PNG + 页面 HTML。
- `Q4 A`：native 9998 主，signer 首帧前回退。**2026-10-28 事故后，在 P4 实现阶段重新确认前不得作为现行实现依据**；当前不进入 P4，不要求现在做溯源或补证据。
- `Q5 A`：human gate、focus、过场点击全部保留；仍标未验证。
- `Q6 A`：单 9998 端口 + 每实例 realm。
- `Q7`：不限制 build 页并发；按均衡负载路由。`BUILD_BUSY` 只作为换号/重载保护，不是并发上限。
- `Q8 A`：build-only 在成功/关闭时回写 Cookie，并提供手动重启入口。
- `Q9 A`：Build 使用实例自己的 session/scope；配额来自实例配置，不用全局页计数。
- `Q10 A`：保留非流式 Build。

### 单元与 Feature IDs

- **P4-Q1 Build app 来源**：`D07-001` → keep。
- **P4-Q2 页面复用/清理**：`D07-002`, `D07-003`, `D07-042` → port。
- **P4-Q3 BotGuard/失败现场**：`D07-004`, `D07-005`, `D07-044` → port。
- **P4-Q4 native/signer/解析**：`D07-012`..`017`, `D07-019`..`022`, `D07-027`..`029`, `D07-032`..`037`, `D07-043` → port/keep。**2026-10-28 起暂停作为实现依据**：Build native `streamGenerateContent` 返回 `400 PROXY_GENERATION_DISABLED`，P4 必须先按 `08-10-build-surface-incident-20261028.md` 重新溯源，确认 native/signer 端点与回退条件后再实现。
- **P4-Q5 反自动化层**：`D07-006`..`009` → keep；`D07-044` → port。
- **P4-Q6 9998 sink/realm**：`D07-010`, `D07-011`, `D07-018`, `D07-030` → port。
- **P4-Q7 并发/中止**：`D07-023`..`026`, `D07-031` → `D07-024` keep 作为换号/重载保护，`D07-026` keep（PAGE_CLOSED 三处检测），其余 port；不限制请求并发。
- **P4-Q8 build-only Cookie**：`D07-040` → port；成功/关闭回写，手动重启。
- **P4-Q9 身份/配额**：`D07-038`, `D07-039` → port；使用实例 session/scope 与实例配置。
- **P4-Q10 非流式**：`D07-041` → port。

### P4 Build 渠道事故记录（2026-10-28，未来 P4 阶段处理）

- 日志：`build_surface:stream` native 首帧前断开；上游 `400 INVALID_ARGUMENT`，`PROXY_GENERATION_DISABLED: streamGenerateContent is not supported`；进入 `9998` 重连后仍报 `NATIVE_ERROR`。
- 影响：Build native 端点可能被渠道/账号/代理级禁用；P4-Q4 的 native 主 + signer 回退假设在 P4 实现阶段必须重新确认。
- 当前不处理：这不是 P4 当前轮次，不要求现在溯源、补证据、改代码或写回 Q4。等到 P4 Build 实现阶段启动时，先按 `08-10-build-surface-incident-20261028.md` §4 做前期探索与功能可行性重试。
- P4 阶段验收：明确 400 时 fail-fast 或有限回退；禁止 9998 无限循环；客户端收到准确 Build 错误；若当前无成功样本，必须明确“无基线”并补 native/signer 成功 fixture。

## 实现优先级（按 D5 固定：P6 → P7 → P5）

0. 预检（A 文档收敛，不是独立 Gate）：文档/索引/证据冻结，CSV 与 06 对齐，secret scan，迁移与重审。
1. P9-pre：lifespan/config 装配钩子 + API Key guard；目标 seam、状态机、config/event schema 作为输入。
2. P1 基础 + P3 多实例/Context/Page + P2 路由/重试；只用 `active_requests`，不引入 lease。
3. P6 凭证保活与 24h。
4. P7 日志页 + 导出（D09，不含统计页）。
5. P5 最小 chat 生成 + OpenAI/Gemini + API 配置，形成最小可持续运行；P5 验收前过 PG 门槛（D7）。
6. P4 Build Surface：当前不执行；P4 阶段启动时先按 08-10 §4 做前期探索，再实现。
7. P8 API/UI；upstream Vue 原生细节优先，ModelCatalog 单一所有者。
8. Claude/Responses。
9. P7 统计页（D10）。
10. P10 Tests/Evidence 收尾、证据索引与文档同步。

## P5 Generation / Protocol / Wire（已确认 2026-10-25）

### 总原则

- OpenAI Chat Completions 是硬需求，必须进入最小可用路径；Claude 在 Build 之后再实现。
- 保留协议中立核心 + Gemini/OpenAI/Claude/Responses 适配器；Gemini + OpenAI 先行。
- ATG 保留，默认 off，按协议/模型开启。
- 流式首事件统一预读，尽量保留真实 HTTP 状态；已出正文后不切号。
- tool_calls、thinking、images、usage、response_id、thought_signature 全部保留。
- chat 继续 capture/replay + wire/JSPB；不改成直接 HTTP。
- countTokens：
  - 默认沿用当前粗略实现：把 system、tools、content 统一打包成字符串计数，不是只算 content；
  - 后续在 UI 提供可选官方 API key，仅用于精确 countTokens；
  - 官方 key 只允许用于 countTokens，不得用于生成、不得回显、不得进日志。
- 非流式与同账号空响应重试保留。
- raw dump 保留但必须脱敏/按需；错误映射与 feature gate 保留。
- build REST body 推到 P4，不在 P5 做。

### 单元与 Feature IDs

- **P5-Q1 OpenAI Chat 硬需求**：`D06-012`, `D06-057` → port，首批。
- **P5-Q2 其他协议**：`D06-004`..`D06-013`, `D06-058`, `D06-059` → port；Claude 在 Build 后，Responses 更后。
- **P5-Q3 协议中立核心**：`D06-001`..`D06-003`, `D06-014`, `D06-066` → port；`D06-066` 的 lease 释放改写为 `active_requests` 释放，不保留 lease。
- **P5-Q4 ATG**：`D06-042`..`D06-053` → port。
- **P5-Q5 流式状态/错误**：`D06-007`, `D06-008`, `D06-010`, `D06-014`, `D06-017`, `D06-018` → port。
- **P5-Q6 内容能力**：`D06-015`, `D06-016`, `D06-019`..`D06-028`, `D06-030`, `D06-060`, `D06-069` → port/keep。
- **P5-Q7 capture/replay/wire**：`D06-028`..`D06-038`, `D06-068` → keep/port。
- **P5-Q8 countTokens**：`D06-054`..`D06-056`, `D06-070` → port；默认把 system/tools/content 统一打包成字符串计数；官方 key 可选且仅用于精确计数，不生成/不回显/不落日志。
- **P5-Q9 非流式/空响应重试**：`D06-001`, `D06-004`, `D06-050`, `D06-051`, `D06-065` → port。
- **P5-Q10 raw dump/错误映射/gate**：`D06-061`..`D06-064`, `D06-067` → port/keep。
- **P5-Q11 build REST**：`D06-039`..`D06-041` → defer 到 P4。

## P6 Keepalive / Rotation / 24h（已确认 2026-10-25）

### 总原则

- 保留 keepalive 服务、24h auth update、24h metadata/tier/email refresh。
- 24h Cookie 回写覆盖 chat、build-only、active session，并按逐 slot 实际成功记账。
- 删除后台 auto switch / rebalance / legacy rotator 回退；请求驱动 429 切号仍归 P2。
- 保留 429 额度冷却（60/300/600）与美西午夜跨天重置。
- `active_activity` 保留为可选页面保活（默认关）：它滚动控制页并 `touch_activity`，只阻止 idle browser 回收，不刷新凭证。
- idle browser 自动回收默认关闭（默认不自动关）；消息到达唤醒 SLEEPING/UNSTARTED 合格实例；失败/stale 才手动重启，不写成“只手动重启”。
- 保留手动切号、手动触发保活、状态查询入口；手动切号不再走蓝绿。
- job 锁采用 A：单全局串行；busy 跳过且**不推进 next_due**；失败按 job 短重试。

### 单元与 Feature IDs

- **P6-Q1 保活服务/job**：`D08-001`..`D08-005`, `D08-024` → port/keep。
- **P6-Q2 24h Cookie 回写**：`D08-008`..`D08-013` → keep/port；build-only 必须覆盖。
- **P6-Q3 metadata/tier/email**：`D08-006`, `D08-007`, `D08-023`, `D08-024` → keep/port。
- **P6-Q4 auto switch/rebalance**：`D08-015`..`D08-018` → drop。
- **P6-Q5 429 冷却/跨天**：`D08-020`, `D08-021` → keep。
- **P6-Q6 active_activity/idle browser**：`D08-014`, `D08-022` → 上游已无 idle 自动回收，不实现 idle 关闭；active_activity keep 可选；消息到达唤醒 SLEEPING/UNSTARTED，失败/stale 才手动重启。
- **P6-Q7 手动切换/触发/状态**：`D08-019`, `D08-025` → port/keep。
- **P6-Q8 job 锁/重试**：`D08-003`..`D08-005` → port；A 方案。

## P7 Logs / Stats（已确认 2026-10-25）

### 总原则

- 日志页先于统计页实现：查看、级别过滤、搜索、暂停、1/3/7 天导出；P7 gate 只覆盖日志。
- 统计事件 schema 基线在 P9-pre 前冻结（以 08-09 与 06 为准；12 归历史）；P5 只扩展字段并实际记账，P7 只做聚合与展示，不另起账本。
- 日志存储：ring 5000 + 每日文件 7 天。
- dump 只落 `safe_*`，默认关闭；禁止明文 headers/raw_body。
- root DEBUG 保留，但必须按命名空间/三方库过滤 WARP/tunnel/CDP/websockets/httpx 噪音。
- 统计后端改为单一事件账本：request/attempt/usage/error/latency/account/model/surface/paid；聚合派生。
- 统计页 client 与 attempt 分区显示，绝不混算成功率。
- daily 保留 90 天 + all-time；`start` 返回实际生效值。
- countTokens 独立统计；paid 从模型目录读取并缓存。
- 统计页已有突出 token 汇总大数字；口径是 generation + input + cache 的集合，不含 countTokens。该能力归既有 `D10-030`/`D10-032`，不新增重复行。
- 日志走 SSE，统计走 REST 聚合。

### 单元与 Feature IDs

- **P7-Q1 日志页/导出**：`D09-027`..`D09-032` → keep。
- **P7-Q2 日志存储**：`D09-010`, `D09-012`, `D09-016`, `D09-025`, `D09-026`, `D09-033` → keep。
- **P7-Q3 dump 安全**：`D09-021`, `D09-022` → port。
- **P7-Q4 噪音**：`D09-004`, `D09-035` → port；root DEBUG + filter。
- **P7-Q5 事件账本**：`D10-001`..`D10-010`, `D10-016`, `D10-020`, `D10-025`, `D10-027` → port/keep。
- **P7-Q6 分母**：`D10-027`, `D10-028`, `D10-032` → port；client/attempt 分区。
- **P7-Q7 保留期/范围**：`D10-010`, `D10-022`..`D10-024` → keep/port。
- **P7-Q8 countTokens/paid/token 汇总**：`D10-020`, `D10-021`, `D10-025`, `D10-029`, `D10-031` → keep/port。
- **P7-Q9 API 形态**：`D09-013`, `D09-018`, `D10-024` → keep。

## P8 API / UI（已确认 2026-10-25）

### 总原则

- OpenAI Chat + Gemini 首批；Claude 在 Build 后；Responses 更后。
- 模型列表默认裸 ID；只有双渠道实际启用且实例分配分流时，才暴露条件前缀。
- API 免密只由 `security.api_allow_anonymous` 控制且默认关闭；空 Key ≠ 开放；管理页只由 `AISTUDIO_WEB_PASSWORD` 控制（空=免密）；不引入 IP/CIDR；管理页不被 API Key 锁死；API Key 与 Web 密码互不复用。见文末 08-09 节。
- 保留协议感知错误体与明确 HTTP 契约。
- 最小 UI：登录、总览、账号、实例、日志、设置；统计页后置。
- 实例 UI 一实例=账号+Context+Page+surface；提供手动切换/重启。
- 删除旧 `/rotation/next`、孤儿组件、渠道矩阵。
- API Key 只显示掩码；控制台登录用服务端会话，不用 localStorage token。
- 协议开关按实际能力；示例随 `features.formats` 隐藏。

### 单元与 Feature IDs

- **P8-Q1 协议端点**：`D11-001`..`D11-011` → port。
- **P8-Q2 模型列表**：`D11-004`, `D11-005`, `D11-007` → port。
- **P8-Q3 鉴权**：`D11-015` → rewrite（D-14）；`D11-016` → drop；`D11-017`、`D11-018`、`D11-035`、`D11-036` → keep/port。**D-14 注**：API 免密由单一开关控制且默认关闭；管理页 no-auth（`AISTUDIO_WEB_PASSWORD` 空）保留，管理页不被 API Key 锁死；不引入 IP/CIDR；API Key 与 Web 密码互不复用；CF Tunnel 绑定不变。
- **P8-Q4 错误映射**：`D11-013`, `D11-014` → port。
- **P8-Q5 最小 UI**：`D11-022`..`D11-034`, `D11-037`..`D11-039`, `D11-042` → port；统计页后置。
- **P8-Q6 实例 UI**：`D11-029`..`D11-031`, `D11-039`, `D11-040` → port。
- **P8-Q7 旧切号/旧 UI**：`D11-021`, `D11-041` → drop。
- **P8-Q8 Key/密码 UI**：`D11-018`, `D11-035`, `D11-036` → port。
- **P8-Q9 开关/示例**：`D11-012`, `D11-037` → keep/port。

## P9 Boot / Config（已确认 2026-10-25）

### 总原则

- P9 拆成 P9-pre 与 P9 收尾：P9-pre 只做 lifespan/config 装配钩子、启动/关闭顺序、manager/pool/keepalive 接线；必须在 P1/P3/P2 编码前完成。
- 保留 upstream lifespan 骨架和关闭清理顺序；只替换装配清单、增加 manager/pool/keepalive 钩子、补生命周期测试。
- API Key guard 在 P9-pre 完成，P5 暴露端点前必须生效：必须实现 `security.api_allow_anonymous` 且默认 false；空 `api_keys` 不放行；管理页只由 `AISTUDIO_WEB_PASSWORD` 控制；API Key 不锁管理页；不引入 IP/CIDR；API Key 与 Web 密码互不复用。见文末 08-09 节。
- 启动顺序：active 预热 → 实例 startup → 模型发现 → keepalive；idle/tier 保持。
- 删除 fork 的 groups/channel/打分配置；保留 upstream rotator 的账号状态与 429 冷却能力。
- 配置统一为 YAML + env + 默认值单一 schema；实例配置使用 `count/surface/min_tier`。
- 删除 channel registry，只保留 `surface=chat/build`。
- 保留热更新与原子写回；legacy 配置键一次性迁移后删除。
- 关键配置 fail-fast，非关键配置回退默认。**（rotator 按 D-07 深合并默认 + 启动校验；不得静默/请求期 KeyError）**
- 全局原生优先：upstream 原生实现无必要修改则不动，尤其 UI 细节不得无故重写/删除。

### 关键 Feature IDs

- **P9-Q1 lifespan**：`D01-001`..`D01-004`, `D01-014` → keep。
- **P9-Q2 启动顺序**：`D01-008`..`D01-013` → keep/port。
- **P9-Q3 rotator/channel/groups**：`D01-006` → port（保留账号状态/冷却）；`D01-054`..`D01-058` → drop。`D01-059`..`D01-062` 是日志功能，保持 port/keep，不能 drop。
- **P9-Q4 配置来源**：`D01-029`..`D01-033`, `D01-039`, `D01-040` → keep/port。
- **P9-Q5 channel/surface**：`D01-047`, `D01-054`..`D01-058` → drop。
- **P9-Q6 实例配置**：`D01-048`, `D01-049` → port。
- **P9-Q7 热更新/原子写**：`D01-045`, `D01-046` → port。
- **P9-Q8 legacy 键**：`D01-053` → port。
- **P9-Q9 启动错误**：`D01-030`..`D01-031`, `D01-038` → keep/port。

## P10 Tests / Evidence（已确认 2026-10-25）

### 总原则

- 现有 942 单测保留为“理解旧项目预期行为”的参考，不是新契约；按新逻辑调整，彻底过时的删除。逐文件处置见 `06-test-disposition.csv`（P9-pre 前完成）。
- 新测试必须先说明测试项和预期结果，便于用户对齐意图；关键步骤不允许错误滚雪球。
- 仓库不提交敏感内容；能迁移到新项目做参考、或能通过 Git 查看细节的证据必须保留，避免重复测试和 IP 风控。
- 尽量减少真实测试；必要时可用 WARP 作为中转层做少量定向测试/单独测试。
- 最终镜像/真实账号 E2E 不现在做，等 IP 冷却后按 WARP 小流量验证。
- 测试钩子只允许测试环境。
- 旧 WP/规划文档归档规则与清单见 `docs/dev/evidence/p1-ark-merge-archive/README.md`；正文留 `rebuild`，新分支不复制，关键文档可暂留 `docs/dev/` 根。
- 证据按 domain 重新索引；每次验收附 raw 工件或脱敏摘要。
- 验收后必须指出功能在生命周期/架构中的插入位置，并同步 `AGENT.md`、根目录两份 README、`docs/ARCHITECTURE.md`，新增 `docs/changelog.md`。

### 关键 Feature IDs

- **P10-Q1 单测基线**：`D12-001`..`D12-006`, `D12-008`, `D12-010`, `D12-012`, `D12-014`, `D12-016`..`D12-018`, `D12-021` → keep/port。
- **P10-Q2 证据保留**：`D12-007`, `D12-009`, `D12-011`, `D12-015`, `D12-019`, `D12-020`, `D12-022`, `D12-025`..`D12-031`, `D12-033`..`D12-036` → keep/port；原始凭据不入库。
- **P10-Q3 E2E**：`D12-007`, `D12-020`, `D12-022`, `D12-023` → 离线优先，WARP 小流量后置。
- **P10-Q4 测试钩子**：`D12-002` → keep，仅测试环境。
- **P10-Q5 旧 WP 归档**：`D12-024` → drop 出活动区；归档规则/清单见 `docs/dev/evidence/p1-ark-merge-archive/README.md`，正文留 `rebuild`，不复制凭据。
- **P10-Q6 凭据**：仓库已有不入库约束；`D12-015`, `D12-032`, `D12-036` 只保留脱敏形状/结论。
- **P10-Q7 证据索引**：`D12-033` → port。
- **P10-Q8 验收工件与文档同步**：`D12-035`, `D12-037` → port；同步 AGENT/READMEs/ARCHITECTURE/changelog。

## 99c38ba 上游裁决（2026-10-27，追加；不改 P1-P10 原结论）

本节只追加新裁决、标注被替代的旧表述；`08-07-upstream-99c38ba-reaudit.md` 是完整影响矩阵。feature 集不变，仍为 518 行（已被 08-09 的 534 行取代）；不新增 feature 行。

| 决策包 | 动作 | 裁决 |
|---|---|---|
| P1 Account / Auth / Paid | adopt | `AccountMeta.enabled/group`、`AccountPreset`/`presets.json`、分组/预设 CRUD 与路由（保留分组/预设；**notes 与 batchToggleGroup 按 D-13 删除**）、`get_active_account` 只回退 enabled 账号，全部采上游原生；禁用账号不入候选。 |
| P2 Routing / Candidate / Switch / Retry | rewrite | `AccountStats` 的 `daily_models`、`resolve_tier_quota`、`tier_weights/stickiness/drip_penalty`、`required_tiers` 只作现有实例候选的排序/准入；不新增请求实例、不后台改绑、不另立并发账本。付费模型无合格账号的本地 403 fast-fail adopt；与 429→503 分开。 |
| P3 Browser / CDP / Context / Page / Instance / Pool / Lease | adopt + rewrite | **保留 pipe watchdog（D-02，仅父进程死亡后的孤儿 Chromium 清理）**；**只保留 WebSocket ping（D-03）**，liveness checker/轮询 disable；idle 回收删除；adopt `capture._ensure_template` 的 `await session.ensure_ready()`；page prune 按 D-11 关闭所有非当前请求/非当前活跃账号的 Page/Context；drain 只在旧 Page/Context 上有界，期间不新建新页；不采用 `session.py:211` 的 blue-green `create_new_page`/`_retiring_contexts` 后台 drain。1 账号 + 1 Context + 1 Page + 1 surface、无 lease 不变。 |
| P4 Build Surface | keep | Build 范围保持；只继承 wire/transport 修复。2026-10-28 事故记录：native `streamGenerateContent` 返回 `400 PROXY_GENERATION_DISABLED`；当前不进入 P4，不要求现在溯源/补证据；P4 实现阶段启动时先按 08-10 §4 做前期探索和可行性重试。不得把 P4-Q4 的 native 主路径当已验证事实。 |
| P5 Generation / Protocol / Wire | adopt | `session_token`/`request_context`/`SEED=18`/`WIRE_FINISH_REASON_CONTINUING=22`/角色守卫/结构化 functionCall/functionResponse/per-chunk usage/response_id/统一 thought+signature 写回均采上游；F-01/F-04 仍用离线 fixture 证明。 |
| P6 Keepalive / Rotation / 24h | keep + disable | 24h 回写、手动保活、429 冷却/跨天 keep；**watchdog keep（D-02）**、**WebSocket ping keep（D-03）**、**liveness checker disable**；idle 回收 disable；`active_activity` 因失去“阻止 idle 回收”的语义置 disable；无后台 stale 复活。 |
| P7 Logs / Stats | adopt | `reasoning_tokens`、`cached_tokens`、`finish_reasons`、`account_breakdown`、`cachedContentTokenCount` 采上游；`account_breakdown` 是 stats REST 派生字段，不是事件类型；token 汇总仍不含 countTokens。 |
| P8 API / UI | adopt + rewrite | 账号分组/预设/toggle、Dashboard 统计 UI 采上游原生；API Key 守卫按 **D-14** rewrite：单一免密开关默认关闭，空 Key ≠ 开放；管理页 `AISTUDIO_WEB_PASSWORD` 空=免密且不被 API Key 锁死；不引入 IP/CIDR；API Key 与 Web 密码互不复用；CF Tunnel 绑定不变；其余最小 UI 不重写。 |
| P9 Boot / Config | adopt + rewrite | `rotator/paid_model_rules/tier_quotas/account_presets/defaults/config.yaml/video+imagen profile` 纳入单一 schema；删除浏览器 idle 超时键；超时默认 600；rotator 深合并默认 + 启动校验（D-07）；删除 channel registry 不变。 |
| P10 Tests / Evidence | rewrite | 保留主 `rebuild` 80 个测试处置；99c38ba 实际 28 个测试文件（含 conftest）；`test_account_presets.py` 改 rewrite、`test_tier_preemption.py` 改 rewrite，共 86 行；**保留 watchdog 用例，删除 liveness/idle 用例**；28/28 仅文件覆盖，不等于行为覆盖。 |

被本节替代/修正的旧表述：

- P6-Q6 的 “active_activity 阻止 idle 回收” 与 P6 总原则的 idle 自动回收默认关闭：上游已无 idle 回收，active_activity 置 disable；`SLEEPING` 仍保留为消息唤醒状态，但只由显式释放进入。
- 索引行随 99c38ba 调整（该轮不改 518 行集合，只改 decision/note；后由 08-09 增至 534 行）：`D01-012`、`D01-038`、`D03-035`、`D04-042` → `drop`（idle 回收/超时/监控不再存在）；`D03-004` → `keep`（保留 pipe watchdog/进程组/Job Object/atexit，D-02）；`D03-009` → `rewrite`（删 liveness checker 与轮询，保留 WebSocket ping 与页面失败即失败，D-03）；`D08-022` → `rewrite`（只保留消息唤醒 SLEEPING/UNSTARTED 与失败/stale 手动重启，idle 回收不实现）。CSV 与 ledger 已同步。
- `08-06` 基于 `4623109` 的 handoff 结论失效，只作历史；新基线为 `99c38ba`。
- 旧 “26/26 新 worktree 测试覆盖” 修正为 `99c38ba` 28/28；26 是 `b8af406` 计数，不能与主 `rebuild` 80/80 混同。

D12/lease 措辞复核：D02/D05/D06/D12 相关行已按“无 lease、`active_requests` 唯一账本、429→503 retained”执行；**该句中的“不新增 feature 行/不改变 518 行集合”已被 08-09 取代（见下节）**。

## 08-09 决策闭合（2026-10-28）

用户已确认 D-01..D-14（完整选择、影响与边界见 `08-09-decision-record.md` §1）；本节只记录对台账的落地结果，不复制决策正文。

### 行数与桥接

- feature 集由 518 行增至 **534 行**：17 个 `NO_FEATURE_ROW` 中 16 个真实功能新增独立行，N03（notes）按 D-13 删除、不新增行；tooling/build artifact 不进入 feature 数。
- 桥接：P1 34、P2 47、P3 98、P4 44、P5 74、P6 25、P7 69、P8 42、P9 64、P10 37，总 534；`errors=0`。
- 20 冲突 C01..C20 全部闭合；C14/C17/C18/C20 是 `ENGINEERING_DEFAULT`（可回退），C06 由 D-04 + E-07 共同闭合，其余由用户决策闭合。

### 新增 feature ID

| Feature ID | 来源 | 决定 | 边界 |
|---|---|---|---|
| `D01-063` | N17 | port | `defaults/config.yaml` 运行时引导模板；首启初始化是运行时行为。 |
| `D01-064` | N15 | port | video_models/Veo + imagen profile；不改变实例模型。 |
| `D02-032` | N01 | port | enabled/disabled + 候选过滤 + active 仅在 enabled 回退；禁用触发有界 rebind。 |
| `D02-033` | N02 | port | 分组 CRUD + UI 过滤；batchToggleGroup 按 D-13 删除。 |
| `D02-034` | N04 | port | presets.json；锁/写失败按 D-08 修复。 |
| `D03-036` | N16 | port | CDP `$rpc/` 传输证据链。 |
| `D05-044` | N05 | port | `quota_scope` 双口径 + tier 软配额。 |
| `D05-045` | N06 | rewrite | 打分配置 + 深合并默认/启动校验（D-07）。 |
| `D05-046` | N07 | port | paid_model_rules 准入；规则优先级 E-07。 |
| `D05-047` | N08 | port | 只保留准入 + 本地 403；不请求期切号（D-04/D-06）。 |
| `D06-071` | N11 | port | seed 仅 Gemini；其他协议接受并告警忽略（E-04）。 |
| `D06-072` | N12 | port | ≥96KiB gzip + base64。 |
| `D06-073` | N13 | port | wire 字段语义改名。 |
| `D06-074` | N14 | port | finish 18/22 + 新编号表（E-03）。 |
| `D10-033` | N09 | port | `/stats` account_breakdown。 |
| `D10-034` | N10 | port | finish_reasons 先进 API（E-02）。 |

### 对上一节（99c38ba 上游裁决）的修正

- P3 的“移除 watchdog/liveness checker/CDP ping”改为：**watchdog 保留（D-02，仅孤儿进程回收）**；**只保留 WebSocket ping，删除 liveness checker/轮询（D-03）**。
- P9 的“缺失/部分 rotator fail-fast”改为 **深合并默认 + 启动校验（D-07）**，不得静默、不得请求期 `KeyError`。
- P8 的“API Key 守卫 rewrite 为默认拒绝、显式空 Key 才开放”改为 **D-14：API 独立免密开关（默认关闭）+ 管理页 `AISTUDIO_WEB_PASSWORD` 空值免密**；不引入 IP/CIDR/设备白名单。
- “feature 集不变、仍为 518 行”不再成立；以本节 534 行为准。



# Feature Decision Log

范围：`docs/dev/rebuild/06-feature-ledger.md` 的逐包决策记录。只记录决定，不复制台账行；未确认项标 `pending`。

## 重构大方向（2026-10-24）

- 以 `upstream/master` 的干净实现为新骨架；当前 fork 只作功能参考、失败样本和证据来源。
- 从 upstream 新建独立分支/worktree；不合并当前 `master`/`rebuild`。
- 先审计 upstream 扩展缝：账号身份/tier、Context/Page 工厂、请求执行/租约、日志/统计事件出口。
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
| P1 | D02 | 31 | 22 | Account / Auth / Paid |
| P2 | D05 | 43 | 42 | Routing / Candidate / Switch / Retry |
| P3 | D03 + D04 | 97 | 81 | Browser / Context / Page / Instance / Pool / Lease |
| P4 | D07 | 43 | 43 | Build Surface |
| P5 | D06 | 69 | 49 | Generation / Protocol / Wire |
| P6 | D08 | 25 | 25 | Keepalive / Rotation / 24h |
| P7 | D09 + D10 | 67 | 51 | Logs / Stats |
| P8 | D11 | 42 | 34 | API / UI |
| P9 | D01 | 62 | 41 | Boot / Config |
| P10 | D12 | 36 | 35 | Tests / Evidence |

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

决定：`port` 为池内 sticky + 简单 LRU/FIFO；丢弃权重、公平分、affinity 打分。

### P2-E Acquire / capacity / defer

Feature IDs：`D05-021`..`D05-023`, `D05-025`, `D05-039`

决定：
- `D05-021`..`D05-023`、`D05-039`：`port`，保留内部 lease/容量概念，重写单实例 failover。
- `D05-025`：`drop` 固定 1s defer；容量满直接路由/切号，最终 503 并带等待提示。

### P2-F 错误 / 重试 / 切换

Feature IDs：`D05-024`, `D05-026`..`D05-038`, `D05-040`, `D05-042`..`D05-043`

决定：
- `D05-026`..`D05-028`、`D05-031`、`D05-034`、`D05-036`..`D05-037`、`D05-042`..`D05-043`：`keep`。
- `D05-024`：`port` 连续切号预算。
- `D05-029`、`D05-030`、`D05-032`、`D05-033`：`port`，先修 `retry_same`，瞬态错误先同账号重试。
- `D05-038`：`port`，切号耗尽改为 503 + 等待/管理页提示。
- `D05-035`、`D05-040`：`drop` 死熔断器和 legacy 全局回退。

### P2-G InstanceSwitcher / 自动重排

Feature ID：`D05-041`

决定：`drop`。不做后台主动改绑空闲实例；请求驱动切号由 P2-F 承担。

## P3 Browser / CDP / Context / Page / Instance / Pool / Lease（已确认 2026-10-24）

### 总原则

- 实例固定为：`1 账号 + 1 Context + 1 Page + 1 surface`。
- 同账号不同时开 chat/build；需要切换就关闭当前实例再重建，或换账号。
- 实例数量、surface、最低 tier 只在实例配置里设置；全局只做总上限。
- 消息到达后逐一热机现有休眠实例；不做复杂性能调度。
- 取消 lease/reservation；保留 `active_requests` 仅用于防止关闭/改绑杀流。
- 容量满按 P2 路由/切号/503；不自动新开实例。
- 关闭即释放实例；休眠实例提供手动重启入口。
- 丢弃 pinned、后台 rebind、自动 rebalance。
- Build 使用独立 Context/Page；BotGuard 参考项目实现研究/移植，不把临时 chat bootstrap 页作为常态。
- 丢弃控制面蓝绿；控制面也保持单页，切号关闭重建。
- 丢弃 slot COOLDOWN、retire_idle、_runtime_occupancy；账号额度冷却/解封逻辑保留在账号层。

### 单元与 Feature IDs

- **P3-A Browser/CDP 原生能力**：`D03-001..015`, `D03-020..026`, `D03-030..032`, `D03-034` → keep；`D03-029` drop。
- **P3-B 一账号一 Context 一 Page**：`D03-016`, `D03-019`, `D03-033`; `D04-005`..`D04-007`, `D04-019`, `D04-028`, `D04-061` → 保留单页模型；`D03-016`, `D04-006`, `D04-007` drop；其余 port。
- **P3-C Manager / startup / config**：`D04-001`..`D04-004`, `D04-009`..`D04-018`, `D04-027`, `D04-062` → port；`D04-003`, `D04-009`, `D04-013`, `D04-015` drop；`D04-012`, `D04-014`, `D04-017`, `D04-018` keep。
- **P3-D Capacity / lease / close**：`D04-020`..`D04-026`, `D04-029`, `D04-030`, `D04-035`..`D04-037`, `D04-041`, `D04-042`, `D04-044`, `D04-052`, `D04-055`..`D04-058` → lease/global-page/borrow/defer drop；`D04-023`, `D04-037`, `D04-041`, `D04-042`, `D04-052`, `D04-056`, `D04-057` port；`D04-031` 归 P3-E。
- **P3-E Sleep / wake**：`D03-035`, `D04-031`, `D04-034` → port/keep；消息到达逐一热机。
- **P3-F Build / BotGuard**：`D03-027`, `D03-028`; `D04-008`, `D04-053`, `D04-054` → port；build 独立 Context/Page；BotGuard 参考项目研究，暂不把 chat bootstrap 作为常态。
- **P3-G 控制面蓝绿**：`D03-017`..`D03-019` → drop。
- **P3-H 手动切换 / pin / auto rebind**：`D04-032`, `D04-038`..`D04-040`, `D04-045`..`D04-051`, `D04-059`, `D04-060` → 手动切换保留；pinned/rebind/auto drop。
- **P3-I 死状态/死 API**：`D04-033`, `D04-043` → drop；账号额度冷却不在 P3 清理范围。

## P4 Build Surface / 9998 / Signer（已确认 2026-10-25）

### 总原则

- Build 实现优先级下降；以下决定保留，但排在多实例/路由、保活、日志之后。
- `Q1 B`：Build app 配置优先，保留 Ark 默认。
- `Q2 C`：健康时复用页面；异常、换号、断线时重建。
- `Q3 A`：参考项目移植 BotGuard 到 build；失败时保存 PNG + 页面 HTML。
- `Q4 A`：native 9998 主，signer 首帧前回退。
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
- **P4-Q4 native/signer/解析**：`D07-012`..`017`, `D07-019`..`022`, `D07-027`..`029`, `D07-032`..`037`, `D07-043` → port/keep。
- **P4-Q5 反自动化层**：`D07-006`..`009` → keep；`D07-044` → port。
- **P4-Q6 9998 sink/realm**：`D07-010`, `D07-011`, `D07-018`, `D07-030` → port。
- **P4-Q7 并发/中止**：`D07-023`..`026`, `D07-031` → 024 keep 作为换号/重载保护，其余 port；不限制请求并发。
- **P4-Q8 build-only Cookie**：`D07-040` → port；成功/关闭回写，手动重启。
- **P4-Q9 身份/配额**：`D07-038`, `D07-039` → port；使用实例 session/scope 与实例配置。
- **P4-Q10 非流式**：`D07-041` → port。

## 实现优先级（2026-10-25 调整）

1. 新分支先更新 `ARCHITECTURE.md` 与 upstream 扩展缝文档，保证架构可参考。
2. P1 基础 + P3 多实例/Context/Page + P2 路由/重试。
3. P6 凭证保活与 24h。
4. P7 日志页 + 导出（D09，不含统计页）。
5. P5 最小 chat 生成 + API 配置，形成最小可持续运行。
6. P4 Build Surface。
7. 其他渠道、P5 其余协议、P8 UI。
8. P7 统计页（D10）。
9. P9 Boot/Config、P10 Tests/Evidence。

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
- **P5-Q3 协议中立核心**：`D06-001`..`D06-003`, `D06-014`, `D06-066` → port。
- **P5-Q4 ATG**：`D06-042`..`D06-053` → port。
- **P5-Q5 流式状态/错误**：`D06-007`, `D06-008`, `D06-010`, `D06-014`, `D06-017`, `D06-018` → port。
- **P5-Q6 内容能力**：`D06-015`, `D06-016`, `D06-019`..`D06-028`, `D06-030`, `D06-060`, `D06-069` → port/keep。
- **P5-Q7 capture/replay/wire**：`D06-028`..`D06-038`, `D06-068` → keep/port。
- **P5-Q8 countTokens**：`D06-054`..`D06-056`, `D06-070` → port；默认 content-only，官方 key 可选且仅计数。
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
- idle browser 默认关闭；需要时手动重启实例。
- 保留手动切号、手动触发保活、状态查询入口；手动切号不再走蓝绿。
- job 锁采用 A：单全局串行；busy 跳过且**不推进 next_due**；失败按 job 短重试。

### 单元与 Feature IDs

- **P6-Q1 保活服务/job**：`D08-001`..`D08-005`, `D08-024` → port/keep。
- **P6-Q2 24h Cookie 回写**：`D08-008`..`D08-013` → keep/port；build-only 必须覆盖。
- **P6-Q3 metadata/tier/email**：`D08-006`, `D08-007`, `D08-023`, `D08-024` → keep/port。
- **P6-Q4 auto switch/rebalance**：`D08-015`..`D08-018` → drop。
- **P6-Q5 429 冷却/跨天**：`D08-020`, `D08-021` → keep。
- **P6-Q6 active_activity/idle browser**：`D08-014`, `D08-022` → active_activity keep 可选；idle 默认关+手动重启。
- **P6-Q7 手动切换/触发/状态**：`D08-019`, `D08-025` → port/keep。
- **P6-Q8 job 锁/重试**：`D08-003`..`D08-005` → port；A 方案。

## P7 Logs / Stats（已确认 2026-10-25）

### 总原则

- 日志页先实现：查看、级别过滤、搜索、暂停、1/3/7 天导出。
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
- API Key 默认要求；显式配置才允许空 Key；loopback 免鉴权不保留。
- 保留协议感知错误体与明确 HTTP 契约。
- 最小 UI：登录、总览、账号、实例、日志、设置；统计页后置。
- 实例 UI 一实例=账号+Context+Page+surface；提供手动切换/重启。
- 删除旧 `/rotation/next`、孤儿组件、渠道矩阵。
- API Key 只显示掩码；控制台登录用服务端会话，不用 localStorage token。
- 协议开关按实际能力；示例随 `features.formats` 隐藏。

### 单元与 Feature IDs

- **P8-Q1 协议端点**：`D11-001`..`D11-011` → port。
- **P8-Q2 模型列表**：`D11-004`, `D11-005`, `D11-007` → port。
- **P8-Q3 鉴权**：`D11-015`..`D11-018`, `D11-035`, `D11-036` → keep/port；`D11-016` drop。
- **P8-Q4 错误映射**：`D11-013`, `D11-014` → port。
- **P8-Q5 最小 UI**：`D11-022`..`D11-034`, `D11-037`..`D11-039`, `D11-042` → port；统计页后置。
- **P8-Q6 实例 UI**：`D11-029`..`D11-031`, `D11-039`, `D11-040` → port。
- **P8-Q7 旧切号/旧 UI**：`D11-021`, `D11-041` → drop。
- **P8-Q8 Key/密码 UI**：`D11-018`, `D11-035`, `D11-036` → port。
- **P8-Q9 开关/示例**：`D11-012`, `D11-037` → keep/port。

## P9 Boot / Config（已确认 2026-10-25）

### 总原则

- 保留 upstream lifespan 骨架和关闭清理顺序；只替换装配清单、增加 manager/pool/keepalive 钩子、补生命周期测试。
- 启动顺序：active 预热 → 实例 startup → 模型发现 → keepalive；idle/tier 保持。
- 删除 fork 的 groups/channel/打分配置；保留 upstream rotator 的账号状态与 429 冷却能力。
- 配置统一为 YAML + env + 默认值单一 schema；实例配置使用 `count/surface/min_tier`。
- 删除 channel registry，只保留 `surface=chat/build`。
- 保留热更新与原子写回；legacy 配置键一次性迁移后删除。
- 关键配置 fail-fast，非关键配置回退默认。
- 全局原生优先：upstream 原生实现无必要修改则不动，尤其 UI 细节不得无故重写/删除。

### 关键 Feature IDs

- **P9-Q1 lifespan**：`D01-001`..`D01-004`, `D01-014` → keep。
- **P9-Q2 启动顺序**：`D01-008`..`D01-013` → keep/port。
- **P9-Q3 rotator/channel/groups**：`D01-006` → port（保留账号状态/冷却）；`D01-054`, `D01-058`..`D01-062` → drop。
- **P9-Q4 配置来源**：`D01-029`..`D01-033`, `D01-039`, `D01-040` → keep/port。
- **P9-Q5 channel/surface**：`D01-047`, `D01-054`..`D01-058` → drop。
- **P9-Q6 实例配置**：`D01-048`, `D01-049` → port。
- **P9-Q7 热更新/原子写**：`D01-045`, `D01-046` → port。
- **P9-Q8 legacy 键**：`D01-053` → port。
- **P9-Q9 启动错误**：`D01-030`..`D01-031`, `D01-038` → keep/port。

## P10 Tests / Evidence（已确认 2026-10-25）

### 总原则

- 现有 942 单测保留为“理解旧项目预期行为”的参考，不是新契约；按新逻辑调整，彻底过时的删除。
- 新测试必须先说明测试项和预期结果，便于用户对齐意图；关键步骤不允许错误滚雪球。
- 仓库不提交敏感内容；能迁移到新项目做参考、或能通过 Git 查看细节的证据必须保留，避免重复测试和 IP 风控。
- 尽量减少真实测试；必要时可用 WARP 作为中转层做少量定向测试/单独测试。
- 最终镜像/真实账号 E2E 不现在做，等 IP 冷却后按 WARP 小流量验证。
- 测试钩子只允许测试环境。
- 旧 WP/规划文档归档到 `docs/dev/evidence/p1-ark-merge-archive/`；关键文档可暂留 `docs/dev/` 根。
- 证据按 domain 重新索引；每次验收附 raw 工件或脱敏摘要。
- 验收后必须指出功能在生命周期/架构中的插入位置，并同步 `AGENT.md`、根目录两份 README、`docs/ARCHITECTURE.md`，新增 `docs/changelog.md`。

### 关键 Feature IDs

- **P10-Q1 单测基线**：`D12-001`..`D12-006`, `D12-008`, `D12-010`, `D12-012`, `D12-014`, `D12-016`..`D12-018`, `D12-021` → keep/port。
- **P10-Q2 证据保留**：`D12-007`, `D12-009`, `D12-011`, `D12-015`, `D12-019`, `D12-020`, `D12-022`, `D12-025`..`D12-031`, `D12-033`..`D12-036` → keep/port；原始凭据不入库。
- **P10-Q3 E2E**：`D12-007`, `D12-020`, `D12-022`, `D12-023` → 离线优先，WARP 小流量后置。
- **P10-Q4 测试钩子**：`D12-002` → keep，仅测试环境。
- **P10-Q5 旧 WP 归档**：`D12-024` → drop 出活动区，归档到 `docs/dev/evidence/p1-ark-merge-archive/`。
- **P10-Q6 凭据**：仓库已有不入库约束；`D12-015`, `D12-032`, `D12-036` 只保留脱敏形状/结论。
- **P10-Q7 证据索引**：`D12-033` → port。
- **P10-Q8 验收工件与文档同步**：`D12-035`, `D12-037` → port；同步 AGENT/READMEs/ARCHITECTURE/changelog。



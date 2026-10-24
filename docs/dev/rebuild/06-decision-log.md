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

## 待做

- P4 Build Surface
- P5 Generation / Protocol / Wire
- P6 Keepalive / Rotation / 24h
- P7 Logs / Stats
- P8 API / UI
- P9 Boot / Config
- P10 Tests / Evidence

# 03 会话上下文与调查总结

- 日期：2026-10-24
- 用途：压缩本次长会话，供后续 `rebuild` 分析使用
- 状态：总结，不是新的需求文档

## 1. 当前事实

- 仓库：`D:\LLM\self_programming\aistudio-api`
- 当前 HEAD：`8e5e0cb`，已推送；Docker workflow `39639126777` 成功。
- Build 双并发本地验证：两条均 200，约 0.9–1.2 秒；请求期间 `Page.navigate=0`、`ERR_ABORTED=0`、`429=0`。但这是非流式、Free 账号、临时配置下的验证。
- 当前主要未解问题：工具调用识别、消息超时、自动换号瘫痪、24h 实例失效，以及 Build 之外的功能回归。
- 约束：
  - 不碰真实网页做日常回归；
  - 不在真实 IP 上反复测试；
  - 不再写 500 行规划；
  - 不无条件信任 agent 结论；
  - 未经明确要求，不提交、不构建镜像。

## 2. 文档审计结论

- 仓库没有最初需求原文；最早的 `24486f8` 已经是计划书，只能从 `PLAN-ark-merge-20261017.md` 反推。
- 原始诉求大致是：free 走 Build、Pro/Ultra 走 chat、1GB 小鸡多账号、增量移植、不做完整 UI 复刻。
- 实际扩成 WP0–WP11，并增加统计页、日志页、三协议、实例切换，已超出“增量移植”。
- 主要语义漂移：
  - 实例定义从“账号会话”改为“账号+渠道”；
  - 2c1p/1c2p/2c3p 反复变化；
  - Build 主路径从 signer 改为 native 9998，旧验收证据测的是旧主路径；
  - 本地容量错误从 429 改为 503；
  - 启动模型文档写按账号，代码实际按渠道；
  - 24h 保活只覆盖 chat，未覆盖 build-only；
  - quota_pool 已废弃但文档/默认配置仍残留。
- 应废弃/合并：
  - WP5-V4、WP7-V2；
  - WP7-UI-DESIGN、UI-CHANNEL-DESIGN；
  - WP5-V3 设计与实现计划；
  - 两份 HANDOFF；
  - WP6 两份重复设计。
- 最危险的偏航点：
  1. 用内部 Context/Page/Slot 直接设计用户 UI，导致 UI 稿三次推倒。
  2. 容量代数和错误码在“验收通过”后反复改，旧测试/文档全部失效。
  3. Build 主备反转，同时保活只覆盖 chat，留下静默失效。

## 3. 架构审计结论

真实结构不是“单 Chromium 单页面”，而是：

- 1 个控制面 `BrowserSession`；
- N 个 `InstanceSlot`，每个 slot 一个 BrowserContext；
- 每个 chat slot 一个 `PooledSession`/page；
- 每个 build slot 另开 `BuildSurface` page；
- 同账号 chat+build 是两条实例记录，共用一个 context。

请求链路：

`route → 候选 → acquire 租约 → 复用/新建/rebind slot → session/build ensure_ready → 发送 → 流式/非流式 → 失败切号/重试 → release`

最关键的冲突：

- 控制面账号与请求面 slot 身份分离；手动激活、保活、自动切号可能不作用于真实请求。
- 单实例时路由候选只保留已有实例账号，429/401/403 无账号可切，最终变成 503。
- `retry_same` 从未置真，瞬态 404/204/hang 被当成换号。
- build-only 槽位不回写 Cookie，24h 后必然失效。
- 加开渠道 60s 超时小于 build ready 120s；acquire 超时不覆盖 bootstrap。
- 关页后 realm 缓存不失效，chat→build→chat 可能命中已关闭页。
- auto_switch 不看 pinned，可能改掉固定实例且 manager 记录不同步。
- Free 被硬编码为 build-only；若默认 `build_surface=false`，会出现全部 403。
- BrowserContext 在 CDP detach 后可能消失，`disposeOnDetach=True` 放大问题，且当前无重建。

高危排序：

1. Free/build-only 与默认 build 关闭导致 403；
2. 单实例 failover 被候选过滤锁死，错误码变 503；
3. `retry_same` 永不置真；
4. 控制面/请求面账号分离；
5. build-only 凭证不回写，24h 失效；
6. BrowserContext 消失后不重建。

最小可信核心：

`BrowserSession._ensure_browser_cdp` → `PooledSession.bootstrap` → capture/streaming/transport；`BuildSurface` native 路径；`session_auth` 灌/存 cookie；AccountStore/auth.json。

应隔离或删除：

- InstanceManager 的编号/UI 与 record 同步（唯一权威或纯只读视图）；
- InstanceSwitcher 自动重排；
- surface_orchestrator 组/配额与 channel_registry、routing_eligibility 三套重复策略；
- routing_safety 预算/熔断；
- account_rotator sticky + orchestrator 切号锁 + 控制面蓝绿；
- build signer 备用路径。

## 4. 项目规模与分支事实

- `p1-ark-merge`：`2026-10-18 15:46` 创建，`2026-10-21 16:00` 最后提交。
- 95 个独有提交，全部 TonyDG；34 feat、32 docs、15 fix。
- 相对上游：475 个文件，+60,539 / -1,922。
- 已 fast-forward 合回 `master`；`master` 之后另有 8 个修复提交。
- 当前项目：615 个跟踪文件，约 9.8 万行文本；Python 5.86 万行、Vue 1.48 万行、文档 1.1 万行。

## 5. 后续分析方式

- 一个子系统、一个证据链、一个小切口。
- 先只读，确认后再改。
- 不写新的 WP；不把建议写成既定方案。
- 不以文档长度证明理解；只以代码、日志、测试和可复现证据说话。

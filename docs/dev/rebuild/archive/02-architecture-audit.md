# 02 当前架构与生命周期冲突（调查原文）

- 日期：2026-10-24
- 范围：`src/aistudio_api` 运行时链路、实例/会话/Build/路由/切号/保活，对照 `docs/ARCHITECTURE.md` 与 `AGENT.md`
- 状态：只读审计，未修改代码

## 一、真实生命周期

**启动**：`config.yaml/features` → `AIStudioClient` 建控制面 `BrowserSession`（app.py:70-73）→ lifespan 预热把 active 账号切到控制页（app.py:114-133）→ `InstanceManager.startup` 按启用渠道与 `max_contexts` 生成槽位（instance_manager.py:117-149,834-864）→ `pool.create_instance` 建 BrowserContext（instance_pool.py:533-606）→ `PooledSession.bootstrap`：建 page、灌 cookie、导航、BotGuard（pooled_session.py:51-99）→ READY。build 渠道随后关掉 bootstrap chat 页（instance_pool.py:1028-1036），`BuildSurface` 在同 context 新开页、注 cookie/signer、导航 `/apps/<id>`、等 9998 WS（build_surface.py:975-1188）。请求可用条件：路由资格 + 已占实例 + 页存活（instance_pool.py:103-124）。

**请求**：`generation_service._begin_route`（160-177）→ `begin` 固定 channel、算账号资格、排序候选（routing_pipeline.py:241-355；只保留已有实例账号 251-259）→ `acquire` 租约（419-497）→ 同账号复用/新建/rebind slot（instance_pool.py:411-531）→ chat 用该 slot 的 PooledSession 与 realm 级 capture/stream（client.py:128-156,557-628）→ capture 模板+快照+codec → replay/stream → 解析 → release。build 用 `lease.adapter` 的 auth 调 `BuildSurface.ensure_ready/stream_generate`（client.py:158-177,581-600）。流式在 acquire 前已发 `routing_wait`（generation_service.py:589-594），HTTP 头先出。

**切号**：异常 → `handle_attempt_exception`；有 routing 时只 `record_switch` 并返回 retry（upstream_errors.py:186-199,221-234,247-270）→ 释放 lease、`next_candidate` 排除失败账号（generation_service.py:305-313）→ 无候选抛 503（180-190）。旧 `try_switch_account`/蓝绿只在 routing 不可用时执行。

**释放**：`lease.release` 只减 reservations（instance_pool.py:832-850）；关实例才 `close_instance/close_channel`（689-750）；浏览器只在 shutdown 或 idle timeout 关（session.py:585-603；app.py:175-195）。

## 二、实例/渠道/上下文/页面

`InstanceManager.record` 是“账号+渠道”的用户实例，仅存 `runtime_ref`（instance_manager.py:78-89,536-590）；`InstanceSlot` 才是 BrowserContext（instance_pool.py:49-78）；chat 每 slot 一个 PooledSession/page（pooled_session.py:35-49,106-107）；build 另开 BuildSurface page（build_surface.py:394-410,1044-1056）。同一账号的 chat+build 是两条 record、共用一个 slot/context。文档不一致：ARCHITECTURE.md:204-208 仍称单 Chromium 单页面、无实例/渠道；实际是 1 控制页 + N 个 context/page；ARCHITECTURE.md:223-228 的请求期蓝绿已被 pool 取代。

## 三、冲突表

| A | B | 冲突点 | 代码证据 | 现场症状 |
|---|---|---|---|---|
| BrowserSession 控制面 | InstancePool 请求面 | 两套会话/账号权威；build 仍用控制 session 计在途 | instance_pool.py:8-10；client.py:589,699；app.py:175-195 | 手动切 active 不影响请求；idle/在途统计错；控制页被杀留下死 slot |
| build-only slot | PooledSession/auth | 建 build 后关 chat session；回写/保活只认 `chat_ready` | instance_pool.py:1028-1036；client.py:187-197；account_keepalive.py:311-338 | build 账号 cookie 永不回写，24h 失效仍报 saved |
| 全局 active 账号 | slot 身份 | 手动激活只改控制页+marker；manager 记录不同步 | app.py:83-90；routes_accounts.py:270-287；instance_manager.py:455-464,970-987 | 点“激活”请求仍走旧实例；rebind 后列表账号不变 |
| ensure_ready/导航 | 实例容量 | 加开渠道 60s 超时 < build ready 120s；BUILD_BUSY→502；同 auth 快路径不重注 cookie | instance_pool.py:28,662-677；build_surface.py:75,1004-1032；app.py:316-323 | 同账号加 build 必超时；并发换号 502；换 cookie 不生效 |
| 路由候选 | 实例上限 | begin 只保留已有实例账号；单实例时 429 无别的账号可切 | routing_pipeline.py:251-259；instance_manager.py:809-810；instance_pool.py:862-917 | 单实例 429/403 直接 503 |
| 启动选路 | 渠道/账号资格 | `_startup_slots` 固定先 chat；Free 只允许 build | instance_manager.py:834-864；surface_orchestrator.py:35-43 | Free+开 build+`max_contexts=1`：启动 0 实例，请求 403 |
| 租约超时 | bootstrap | acquire 的 30/15s 只包 condition.wait，不包 90s×3 bootstrap | instance_pool.py:432-434,490-525；pooled_session.py:60 | 首请求可阻塞数分钟，与超时日志不符 |
| auto_switch | 用户实例 | rebalance 与 rebind 都不看 pinned，manager 记录不同步 | instance_switch.py:51-137；instance_pool.py:785-830 | 自动切号可把固定实例换账号，UI 显示旧账号 |
| 429/401/403 | 错误码 | routing 分支一律 switch，耗尽变 503；旧 429/401/403 成死路 | upstream_errors.py:195-199,261-288；surface_orchestrator.py:61-68；app.py:303-308 | 配额耗尽返 503 非 429；鉴权失败也 503 |
| 流式 | HTTP 状态 | routing_wait 先写 SSE，策略错误只能塞进 200 体 | generation_service.py:589-594；api_service_gemini.py:234-257 | SDK 看到 200，错误码在 data 里 |
| retry | 同账号重试 | `record_retry` 从不置 `retry_same=True`，瞬态 404/204/hang 被当换号 | routing_pipeline.py:514-522；generation_service.py:305,445；upstream_errors.py:177-179,290-346 | 单实例偶发 404 直接 503 |
| realm 缓存 | close_channel | 关页不 bump generation、不清理 realm 服务；重开同 realm 命中旧 session | client.py:128-156；instance_pool.py:689-737,976-984 | chat→build→chat 后 capture/stream 用已关闭页报错 |
| 24h 失效 | rotator/routing | 24h `session_expired` 只在旧路径；routing 记 300s auth_error | account_orchestrator.py:130-131；upstream_errors.py:186-194；account_rotator.py:398-414 | 24h 隔离形同虚设，真正失效靠 metadata 401 置 expired |
| 死配置 | 实现 | `global_max_pages/max_pages` 不参与预算；熔断失败无调用点 | instance_pool.py:314-335；routing_pipeline.py:548-555 | 改配置无效果，熔断永不打开 |

## 四、失败原因排序

**高危（先修）**

1. `[架构×配置]` Free 被硬编码为 build-only，默认 `build_surface:false`（config.yaml:16,93-97）；metadata 刷新把账号写成 Free（account_info_refresher.py:362-381；cookie_parser.py:23-53）→ 全部 403，且从“无 tier 可用 chat”突然掉线。
2. `[架构]` 单实例下 failover 被实例过滤锁死，429/401/403 统一变 503；流式还包成 200（见上表）。
3. `[bug]` `retry_same` 永不置真，瞬态错误不再原地重试。
4. `[架构]` 手动切号/保活/激活作用于控制面，请求面走 slot，用户操作与真实请求脱节。

**中危**

5. `[bug]` build-only 凭证不回写，24h 后必然失效。
6. `[bug/架构]` 60s vs 120s、acquire 超时不包 bootstrap、BUILD_BUSY→502。
7. `[bug]` close_channel 后 realm 服务缓存陈旧。
8. `[bug]` auto_switch 可改 pinned 用户实例且 manager 不更新。
9. `[bug]` idle 回收只杀浏览器不清理 pool（默认关，属潜在雷）。

**低危**

10. `[死代码]` 熔断、`global_max_pages`、`max_pages`。
11. `[行为]` 裸模型选路受运行时 mounted channel 影响（channel_registry.py:390-399）。
12. `[文档]` ARCHITECTURE.md 与 WP5 后代码严重漂移。

**配置/账号问题（非代码冲突）**：cookie/401 过期、auth.json 缺失、build app 不可达、代理挡 9998、`max_contexts=1` 下顺序切换属预期。

## 五、最小可信核心与应隔离层

保留：`BrowserSession._ensure_browser_cdp` 单浏览器（session.py:564-583）→ `PooledSession.bootstrap`（51-99）→ capture/streaming/transport；`BuildSurface` native 路径（ensure_ready 975-1188、stream 1553+）；`session_auth` 灌/存 cookie；AccountStore/auth.json。

隔离或删除：InstanceManager 的编号/UI 与 record 同步（要么唯一权威、要么纯只读视图）；InstanceSwitcher 自动重排（只允许非 pinned，或删除）；surface_orchestrator 组/配额与 channel_registry、routing_eligibility 三套重复策略收成一套；routing_safety 预算/熔断（未接通）；account_rotator sticky + orchestrator 切号锁 + 控制面蓝绿（请求路径已不用）；build signer 备用路径（非核心）。

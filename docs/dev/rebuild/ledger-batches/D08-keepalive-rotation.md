# D08 Keepalive / Rotation / 24h

基线：`upstream/master`（4623109）；审计分支：`rebuild`。逐行证据以当前工作树代码与测试为准。

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 待用户决定 |
|---|---|---|---|---|---|---|---|
| D08 | Keepalive 服务启停与单循环生命周期 | `1f9ba61 src/aistudio_api/application/account_keepalive.py:100-188,426-458`; `1f9ba61 src/aistudio_api/api/app.py:165-173,255-256` | 新增 | partial | `未验证`（无 start/stop 生命周期测试） | keep | 是否要求启停幂等与异常退出自愈 |
| D08 | 四类 job 与 feature 默认值（auth 24h / metadata 24h / auto_switch 3h / activity 60s） | `1f9ba61 account_keepalive.py:27-84`; `1f9ba61 src/aistudio_api/infrastructure/gateway/model_defaults.py:588-597` | 新增 | working | `tests/unit/test_keepalive_config.py:14-32`（本轮 20 passed） | keep | 默认 24h/3h 是否保留 |
| D08 | 单一 job 锁（串行、busy 立即返回） | `1f9ba61 account_keepalive.py:125,228-252` | 新增 | partial | `tests/unit/test_routes_keepalive.py:55-63` 仅 Fake busy 映射 | keep | busy 时是丢弃还是排队 |
| D08 | next_due 计算与 status 暴露 | `1f9ba61 account_keepalive.py:143-152,190-217,445-447`; `1f9ba61 src/aistudio_api/api/routes_keepalive.py:20-30` | 新增 | partial | `未验证`（重启后重算、busy 仍推进 due） | keep | 是否持久化 next_due |
| D08 | 失败/重试语义（仅 auth_update/no_active_page 60s 重试） | `1f9ba61 account_keepalive.py:181-188,206-217,236-244` | 新增 | partial | `未验证`（其余任务失败后等下个完整周期） | keep | 是否统一退避重试 |
| D08 | metadata_refresh（启动即跑、按 tier_checked_at 判到期、排除 expired、互斥跳过） | `1f9ba61 account_keepalive.py:265-309`; `dad9cf7 src/aistudio_api/infrastructure/account/account_info_refresher.py:309-344` | 新增 | working | `tests/unit/test_account_keepalive.py:164-222` | keep | 启动即刷新是否保留 |
| D08 | metadata 失败只记 attempt、24h 后才再试 | `1f9ba61 account_keepalive.py:289-309`; `dad9cf7 src/aistudio_api/infrastructure/account/account_store.py:680` | 新增 | partial | `未验证`（5xx 不改状态，但 due 仍被推到下周期） | keep | 是否对失败账号单独重试 |
| D08 | auth_update（24h、pool 优先、active session 兜底、no_active_page 60s） | `1f9ba61 account_keepalive.py:311-355`; `1f9ba61 src/aistudio_api/infrastructure/gateway/session.py:542-550` | 新增 | working | `tests/unit/test_account_keepalive.py:128-160,405-428` | keep | 24h 回写是否足够 |
| D08 | pool cookie 回写（逐 slot 写各自 auth.json） | `1201a37 src/aistudio_api/infrastructure/gateway/client.py:187-197`; `1f9ba61 account_keepalive.py:312-338` | 新增 | working | `tests/unit/test_account_keepalive.py:340-366` | keep | 是否要求逐 slot 成功计数可见 |
| D08 | auth_update 对 build-only 实例的覆盖 | `1201a37 client.py:192`; `1f9ba61 account_keepalive.py:320-323` | 新增 | broken | `未验证`（仅 `chat_ready()` 才回写/记账，build-only 被跳过） | port | 是否必须覆盖 build-only 会话 |
| D08 | auth_update 将 chat_ready 槽位全部标记已保存（不看逐 slot 结果） | `1f9ba61 account_keepalive.py:318-325`; `1201a37 client.py:194-196` | 新增 | partial | `未验证`（`save_pool_cookies` 吞异常只返回总数） | port | 是否改为按实际成功记账 |
| D08 | active session cookie 回写 | `1f9ba61 session.py:542-550`; `1f9ba61 client.py:183-185` | 新增 | unverified | `未验证`（keepalive 测试用 FakeClient） | keep | 是否保留控制面会话回写 |
| D08 | session.close 关闭前保存 cookies | `4586271 session.py:585-589`; `1f9ba61 session.py:587-588` | 改造 | unverified | `未验证`（`test_session_lifecycle.py:126-141` 仅覆盖回收） | keep | 关闭失败是否阻塞回收 |
| D08 | active_activity（控制面 page 滚动 + touch_activity，默认关） | `1f9ba61 account_keepalive.py:415-424`; `1f9ba61 model_defaults.py:596-597` | 新增 | unverified | `未验证`（无该 job 测试；实例化后无稳定控制页） | drop | 是否彻底移除 active_activity |
| D08 | auto_switch job（默认关、3h、pool 存在即走 rebalance） | `1f9ba61 account_keepalive.py:357-374`; `1f9ba61 model_defaults.py:588-589` | 新增 | working | `tests/unit/test_account_keepalive.py:370-402` | keep | 默认关闭是否保持 |
| D08 | InstanceSwitcher.rebalance 规划与 rebind（仅 idle、最受限优先、按 group/channel 资格） | `dd5e359 src/aistudio_api/application/instance_switch.py:71-137`; `b59d6f3 instance_switch.py:140-216` | 新增 | working | `tests/unit/test_instance_switch.py:52-171`（本轮 20 passed） | port | 是否改为按模型 tier 而非 group/channel |
| D08 | auto switch 是否尊重 pinned | `79901b0 src/aistudio_api/infrastructure/gateway/instance_pool.py:76,771,866`; `dd5e359 instance_switch.py:81-87,105-136,186-194` | 新增 | broken | `未验证`；planner 不读 `pinned`，`rebind` 不拒绝 pinned 槽 | port | pinned 是否作为硬约束禁止 auto switch |
| D08 | auto switch 的 rotator 兜底路径与 keepalive 候选（按 auth_saved_at LRU） | `1f9ba61 account_keepalive.py:376-413`; `1f9ba61 src/aistudio_api/application/account_rotator.py:742-772` | 新增 | legacy | `tests/unit/test_account_keepalive.py:226-315` 仅 FakeClient；生产 pool 恒存在 | drop | 是否删除不可达兜底路径 |
| D08 | switch_to_account 共享切号锁与 require_idle | `1f9ba61 src/aistudio_api/application/account_orchestrator.py:32-65`; `routes_accounts.py:280-282`; `routes_system.py:378-380` | 新增 | unverified | `未验证`（路由/手动切换实际调用，无直接单测） | keep | 手动切换是否仍需蓝绿切换 |
| D08 | 429 冷却分层与 drip（60s→300s→600s） | `d8bbb22 account_rotator.py:335-367`; 基线 `4623109` 已有 date-key/cooldown | 改造 | working | `tests/unit/test_account_orchestrator.py:327-344`；`test_rotator_surface_keys.py`（本轮 43 passed） | keep | 60/300/600 阈值是否保留 |
| D08 | 美西午夜跨天重置与 session_expired 86400 隔离 | `9fd237d account_rotator.py:241-265`; `413496f account_rotator.py:398-415`; `d8bbb22 account_rotator.py:241-265` | 改造 | partial | `test_account_orchestrator.py:305-324`；`test_account_rotation_concurrency.py:306-309`（只验 0..86400） | keep | 是否需 DST/精确边界测试 |
| D08 | idle browser 回收监控（pool 有活跃工作时 touch、否则 check_idle_timeout） | `4aa35cb api/app.py:175-195`; `86391c8 app.py:182-191`; `86391c8 src/aistudio_api/config.py:276-294`; `4623109 session.py:197-215` | 改造 | partial | `tests/unit/test_session_lifecycle.py:126-141,159-189`；app 监控循环未验证 | keep | 默认 0（禁用回收）是否保持 |
| D08 | tier/email 启动自动校验（仅 email 缺失或 tier=Free 时探测） | `6384ca9 api/app.py:197-241`; `47cec70 app.py:208` | 改造 | unverified | `未验证`（无 `_auto_refresh_tiers` 测试） | keep | 是否并入 metadata job 统一调度 |
| D08 | AccountInfoRefresher 批量刷新/进程互斥/401 标过期/5xx 不改状态/邮箱回填 | `dad9cf7 account_info_refresher.py:208-344`; `0a3342b`; `1ef8ac2`; `fcd6f1f` | 新增 | working | `tests/unit/test_account_info_refresh.py`（本轮 38 passed 含 session） | keep | 探测失败是否应重试 |
| D08 | Keepalive HTTP 状态与手动运行（409 busy / 400 unknown） | `1f9ba61 src/aistudio_api/api/routes_keepalive.py:1-50`; `1f9ba61 app.py:38,274-278` | 新增 | working | `tests/unit/test_routes_keepalive.py:32-76` | keep | 是否保留手动触发入口 |

## 冲突/缺口

| # | 冲突/缺口 | 证据 | 影响 |
|---|---|---|---|
| 1 | auto switch 不尊重 `pinned` | `instance_switch.py:81-87,186-194` 未检查；`instance_pool.py:76,866` pinned 仅影响请求分配 | 用户显式实例会被后台 rebalance 改绑 |
| 2 | auth update 不覆盖 build-only 实例 | `account_keepalive.py:320-323` 仅 `chat_ready()`；`client.py:192` 同 | build-only 账号 cookie 可能长期不刷新 |
| 3 | auto switch 仍按 group/channel 资格打分 | `instance_switch.py:79-136` 使用 `eligible_accounts`/groups | 与“按模型 paid/free tier 映射、拒绝策略组打分”的既有决策冲突 |
| 4 | metadata 失败无短周期重试 | `account_keepalive.py:212-217` 仅 auth_update/no_active_page 重试 | 一次 5xx/地域失败要等 24h |
| 5 | chat_ready 槽位被无条件标记已回写 | `account_keepalive.py:318-325`；`client.py:194-196` | 回写失败时 `auth_saved_at` 仍前进 |
| 6 | 调度任务遇 busy 仍推进 next_due | `account_keepalive.py:206-217`，`run_now` busy 返回后照常赋值 | 手动任务可把定时任务推迟一个完整周期 |
| 7 | active_activity 只操作控制面 page 且默认关 | `account_keepalive.py:415-424`；`model_defaults.py:596-597` | 实例化架构下基本无效 |
| 8 | idle 回收默认禁用 | `86391c8 config.py:276-294` | “idle 回收”实际只在显式配置时生效 |

# D03 Browser / CDP / Context / Page

基线：`upstream/master`(4623109)；范围：`upstream/master..HEAD`。只读审计，未运行真实 Chromium/账号。

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 待用户决定 |
|---|---|---|---|---|---|---|---|
| D03 | 控制面 `BrowserSession`：懒启动、`ensure_ready` 就绪门、profile dir 派生 | upstream/master(4623109); session.py:67-215,564-583 | 原生 | working | test_session_lifecycle.py:18,160；test_browser_session.py:175 | keep | 待用户决定 |
| D03 | Chromium 直接子进程启动（CloakBrowser 独占、stealth/low-mem args、fingerprint key=user_data_dir） | upstream/master(4623109); browser_engine.py:61-227,1013-1071 | 原生 | working | test_browser_engine.py:34,59,141,184 | keep | 待用户决定 |
| D03 | 遗留/孤儿 Chromium 清理、端口等待、Singleton/DevTools 锁文件删除 | upstream/master(4623109); browser_engine.py:471-680,1024 | 原生 | working | test_browser_engine.py:153,232 | keep | 待用户决定 |
| D03 | 进程守护：pipe watchdog、进程组 SIGTERM/SIGKILL、Windows Job Object、atexit/signal hook | upstream/master(4623109); browser_engine.py:683-898,931-1010 | 原生 | working | test_browser_engine.py:169,203 | keep | 待用户决定 |
| D03 | CDP WebSocket JSON-RPC：请求 future、事件分发、超时、断线失败全部 pending | upstream/master(4623109); cdp_client.py:93-308 | 原生 | working | test_cdp_client.py:38-93 | keep | 待用户决定 |
| D03 | `connect_page`：复用首个 page target、关闭多余 about:blank tab、3 次重试 | 4aa35cb,0fe5821; cdp_client.py:871-918 | 原生 | working | test_cdp_client.py:235-260 | keep | 待用户决定 |
| D03 | page 创建/关闭：`create_new_page`、`CDPPage.close`(Page.close+WS close)、`close_target` | f110d51a,8960de9; cdp_client.py:810-819,920-939,1038-1050 | 原生 | working | test_session_blue_green_readiness.py:82；无真实 target 关闭测试，未验证 | keep | 待用户决定 |
| D03 | Inspector.detached 与 Inspector/Target.targetCrashed：标记 page closed 并立即失败 pending futures | 4aa35cb,4586271; cdp_client.py:164-198,375-396 | 原生 | working | test_cdp_client.py:200-213 | keep | 待用户决定 |
| D03 | page liveness (`Runtime.evaluate 1`) + 浏览器进程 liveness checker（发送/等待命令中轮询） | 4c39542,4aa35cb; cdp_client.py:107-117,216-276,338-350 | 原生 | working | test_cdp_client.py:184-196,216-231；test_browser_session.py:175 | keep | 待用户决定 |
| D03 | 资源拦截拆分：chat 面全量清单；build 面放行 monaco/codemirror/vs/mathjax/katex | 1cc634e; cdp_client.py:26-80,398-407 | 改造 | working | test_cdp_client.py:24-35 | keep | 待用户决定 |
| D03 | BrowserContext 创建：`Target.createBrowserContext {disposeOnDetach:True}`，进程内串行锁 | 28c981f; cdp_client.py:941-968 | 新增 | partial | test_cdp_browser_context.py:28-44 断言参数；真实 detach 语义未验证 | keep | 待用户决定 |
| D03 | `create_page_in_context`：Target.createTarget 后轮询 /json/list 取 WS，失败关 target | 28c981f; cdp_client.py:983-1036 | 新增 | unverified | 无直接单测；仅被 PooledSession 生产路径调用，未验证 | keep | 待用户决定 |
| D03 | `dispose_browser_context`：忽略不存在/已释放 context | 28c981f; cdp_client.py:970-981 | 新增 | partial | test_cdp_browser_context.py:36-44 仅覆盖成功路径，未验证 | keep | 待用户决定 |
| D03 | context/page 归属清理：slot 关闭先关页再 dispose context，关闭 browser 级连接 | 1201a37,b59d6f3; instance_pool.py:1214-1240; cdp_client.py:1044-1050 | 新增 | working | test_instance_pool.py:306-324,366-380,562-574 | keep | 待用户决定 |
| D03 | `PooledSession`：在指定 context 内建页、注 cookie、导航、BotGuard、快照/传输 | dd5e359; pooled_session.py:51-99 | 新增 | unverified | 无 test_pooled_session*.py；pool 测试均用 FakeSession，未验证 | keep | 待用户决定 |
| D03 | **当前事实：一 context 多 page**（同账号第二 channel 复用同一 context，chat+build 并存两页） | 79901b0,a59c885; instance_pool.py:533-606,642-687,1051-1094 | 新增 | partial | test_instance_pool.py:811-832,913-934；受 max_contexts/页预算约束，见冲突表 | port（需决定是否一 context 一 page） | 待用户决定 |
| D03 | 蓝绿 page 切换：已有 context 时 `create_new_page`，首启 `connect_page`，新页就绪后原子换 active | 4586271; session.py:221-310 | 原生 | working | test_session_blue_green_readiness.py:82-155；test_session_lifecycle.py:18 | keep | 待用户决定 |
| D03 | retiring drain：旧 context in_flight 归零或 12s 超时后关页 + close_target，不打断在途流 | 4586271; session.py:317-338; session_state.py:23-45 | 原生 | working | test_session_blue_green_readiness.py:58-78,82-155 | keep | 待用户决定 |
| D03 | `SessionReadinessGate`：UNINITIALIZED/PREPARING/READY/FAILED，waiters fail-fast | 4586271; session_state.py:48-125 | 原生 | working | test_session_blue_green_readiness.py:20-54 | keep | 待用户决定 |
| D03 | BotGuard bootstrap：`say '1'` 探针、hook 安装、in-page/网络模板截获、临时 chat、恢复输入框 | 4586271,1cc634e; botguard_bootstrap.py:22-323 | 原生 | working | test_browser_session.py:516-565；真实上游未跑，未验证 | keep | 待用户决定 |
| D03 | BotGuard 捕获期间跳登录页：重新注入 cookie 后继续（upstream 4623109 曾移除，HEAD 恢复） | 4623109..HEAD; botguard_bootstrap.py:252-262 | 改造 | unverified | 无对应分支测试；仅代码路径，未验证 | keep | 待用户决定 |
| D03 | cookie 注入：清空 browser cookies/cache 后注入 auth_file 全量 cookie（含 OSID/__Secure-OSID/accounts 域） | 9786b3a; session_auth.py:121-149 | 改造 | working | test_cookie_persistence.py:62-84 | keep | 待用户决定 |
| D03 | cookie 回写：按 name+domain+path 合并、保留未过期旧 cookie、写 storage_state（不再整体覆盖） | 9786b3a; session_auth.py:366-436 | 改造 | working | test_cookie_persistence.py:86-147 | keep | 待用户决定 |
| D03 | CDP `Network.setCookies` 归一化：`__Host-` 用 url、sameSite 映射、批量失败逐条重试、缓存快照 | f110d51a,ae465d68,8df49ad; cdp_client.py:660-743 | 原生 | working | test_cdp_client.py:145-181 | keep | 待用户决定 |
| D03 | 控制面 cookie 导入：临时 `create_new_page` + set/get cookies + bootstrap + close_target | upstream/master(4623109); session.py:499-527 | 原生 | unverified | 无直接单测；test_cookie_import.py:251-328 未覆盖该方法，未验证 | keep | 待用户决定 |
| D03 | BrowserSession 关闭前回写当前 cookie；供 keepalive/巡检读 in_flight、touch_activity | 1f9ba61; session.py:101-108,542-550,585-589 | 改造 | unverified | 无直接单测（test_account_keepalive.py:63 仅 Fake），未验证 | keep | 待用户决定 |
| D03 | build 面取控制面 CDPClient：`ensure_browser_cdp_client` 复用 BrowserSession 的进程/连接 | 1cc634e; session.py:183-187; client.py:94-95 | 改造 | unverified | 无直接单测（test_build_surface 用 Fake），未验证 | keep | 待用户决定 |
| D03 | build-only：加载后用 bootstrap chat 页完成 BotGuard，随即关 chat 页/清 session，仅留 context+build 页 | a59c885（b59d6f3 起）; instance_pool.py:1028-1038 | 新增 | working | test_instance_pool.py:890-909 | keep | 待用户决定 |
| D03 | `close_channel`：只关指定 channel 页，保留 context 与另一页，可再次 `ensure_channel` 重开 | a59c885; instance_pool.py:689-719 | 新增 | working | test_instance_pool.py:835-857,912-934 | keep | 待用户决定 |
| D03 | slot 重载/关闭/取消：先关页再 dispose 旧 context，generation+realm_id 自增并回调旧 realm | 1201a37,b59d6f3; instance_pool.py:963-1027,1214-1240 | 新增 | working | test_instance_pool.py:306-324,366-380,562-574 | keep | 待用户决定 |
| D03 | realm 服务缓存：capture/streaming/replay 按 realm_id 复用，slot retire 时 pop 清缓存 | 1201a37,6305377; client.py:75-100,128-156 | 新增 | partial | test_instance_pool.py:562-574 仅证回调触发；缓存确实删除未验证 | keep | 待用户决定 |
| D03 | build 面 realm WS 分桶：`?realm=` 解析，default/各 realm 连接列表隔离 | 1cc634e; build_surface.py:136-205 | 新增 | working | test_cdp_browser_context.py:47-71 | keep | 待用户决定 |
| D03 | 单实例单页：全局页预算=max(1,max_contexts)，chat/build 页预算由 max_instances 派生；第二页拒绝 | 7cd6d74,9786b3a; instance_pool.py:202-255,314-363 | 改造 | working | test_instance_pool.py:938-1031 | keep | 待用户决定 |
| D03 | 浏览器回收后 stale/pinned slot 复活：同账号复用原 slot 重载 context/page | 86391c8; instance_pool.py:919-961; test_instance_pool.py:1034-1058 | 新增 | working | test_instance_pool.py:1034-1058 | keep | 待用户决定 |
| D03 | 浏览器空闲回收：`browser_idle_timeout<=0` 关闭；LOADING/PREPARING 不回收；冷启动刷新时间戳 | 4aa35cb,4623109,86391c8; session.py:197-215,564-583 | 原生 | working | test_session_lifecycle.py:127-189；默认 0（86391c8 config），未验证运行时 | keep | 待用户决定 |

## 冲突/缺口

| 冲突/缺口 | 说明 | 证据 |
|---|---|---|
| 两套 page 生命周期模型 | 控制面 BrowserSession 仍用默认 browser context 的 tab（connect_page/create_new_page）；请求池用隔离 BrowserContext。蓝绿只切换控制面 tab，与池内 context 无关 | session.py:251-258; cdp_client.py:871-939; instance_pool.py:126-140 |
| `disposeOnDetach=True` 真实语义未验证 | 只断言了参数；浏览器级共享 `_browser_conn` 断开时是否连带释放所有 context、与 pool 显式 dispose 的交互无真实 CDP 证据 | cdp_client.py:941-981; test_cdp_browser_context.py:28-44; 未验证 |
| 隔离 context 内清 cookie 的作用域未验证 | `load_cookies_to_page` 每页执行 `Network.clearBrowserCookies`/`clearBrowserCache`；在多个 BrowserContext 并存时是否只影响当前 context 无真实证据 | session_auth.py:130-140; instance_pool.py:986-1003; 未验证 |
| 一 context 多 page vs 单实例单页 | max_contexts>=2 时同账号可在 1 个 context 放 chat+build 两页（耗 2 页预算）；max_contexts=1 时第二页被拒，chat/build 互斥 | instance_pool.py:333-335,546-606; test_instance_pool.py:811-832,938-1009 |
| BotGuard 登录重注入为 fork 分叉 | upstream 4623109 删除了捕获期重注入直接抛 SessionExpired；HEAD 恢复“重注入后继续”，行为与上游基线不同 | botguard_bootstrap.py:252-262; 4623109 diff |
| `create_page_in_context` 无单测 | target 出现在 /json/list 的轮询、失败关 target、WS 缺失分支均无覆盖 | cdp_client.py:1002-1036; 未验证 |
| realm 缓存淘汰无直接断言 | 仅验证 on_slot_retired 回调；client 三个 realm dict 的 pop 行为无测试 | client.py:97-100; test_instance_pool.py:562-574 |
| 真实 CDP/账号不可复跑 | 所有结论来自代码与 mock 单测；未启动真实 Chromium、未访问 AI Studio | 全局约束；未验证 |
| 与 D04 行重叠 | pool 预算/lease/slot 状态归 D04；本文件只保留 BrowserContext/Page 资源创建、关闭、隔离语义，合并时去重 | D04-instance-pool-lease.md |

# P3-pool-instance / 浏览器、WAA 双引擎与多实例池域

- 域标识：`P3-pool-instance`（已归并原 `P3-browser`）
- 核心职责边界：
  - 单实例硬边界：双引擎全局二选一（默认 `browser`），1 实例 = 1 账号 + 1 Context/BrowserlessSession + 1 Page（仅 `browser`）+ 1 surface（chat 或 build）；同账号 chat/build 不并存。
  - `browser` 引擎：纯 Python CDP WebSocket ping（D-03）、pipe watchdog 孤儿回收（D-02）、单页有界 drain（D-05/D-10/D-11）、D-09 snapshot hash、按账号确定性 Windows 指纹加固（C1）、256 字符尾截断与快照预热（C1d）。
  - `browserless` 引擎（Stage 2.1）：`waa-signer`（Go 1.27 + SpiderMonkey 152 + Firefox 152 uTLS）守护进程、每槽独立 `BrowserlessSession`、事务级热引擎切换（`engine_switch.py`）、Go 热 Worker 基线检测与释放、502 登录态终态映射及同源兄弟文件同步。
- 核心代码模块：
  - `src/aistudio_api/domain/instances.py`
  - `src/aistudio_api/application/instance_manager.py`
  - `src/aistudio_api/application/engine_switch.py`
  - `src/aistudio_api/application/engine_runtime.py`
  - `src/aistudio_api/infrastructure/browser/`（`cdp_client.py`、`browser_engine.py`、`fingerprint.py`、`scripts.py`、`js/*.js`）
  - `src/aistudio_api/infrastructure/gateway/`（`instance_pool.py`、`pooled_session.py`、`session.py`、`session_state.py`、`botguard_bootstrap.py`）
  - `src/aistudio_api/infrastructure/waa/`（`browserless_session.py`、`signer_client.py`、`local_daemon.py`）
  - `waa-signer/`（Go + mozjs 签名器）

## 任务计划与里程碑看板

| 里程碑 / 任务 | 核心目标与交付物 | 状态 | 对应审查报告 |
|---|---|---|---|
| **B2-2 P3-D03 浏览器与单页生命周期** | `PooledSession`、pipe watchdog（D-02）、WS ping（D-03）、单页有界 drain（D-10/D-11）、`hash_include_code`（D-09） | 已完成（PASS） | `reviews/REV-B2-2-p3-d03-browser.md` |
| **B2-3 P3-D04 多实例池** | `InstancePool` + `InstanceManager`、Free/Paid 双 lane、`active_requests` 唯一并发原语、D-05 控制面 rebind | 已完成（PASS） | `reviews/REV-B2-3-p3-d04-pool.md` |
| **上游 `99600c7` 合并吸收** | 取消请求超时 watchdog 支持无限流式、BotGuard 就绪与 5×250ms 快照重试、gzip 移除 | 已完成（PASS） | `reviews/REV-merge-99600c7.md` |
| **C1 浏览器指纹加固与 401 噪音修复** | 按账号确定性 Windows 指纹脚本/metrics、`/health` 探活与 5 分钟缺 Key 去重、Tier 单源收敛 | 已完成（PASS） | `reviews/REV-C1-fingerprint-and-auth-noise.md` |
| **C1d 快照截断与 WAA 预热** | 256 码点尾截断、首次/>600s/>32KB/403 预热、低质量重投、`activate(force=True)` 强制重启 | 已完成（PASS） | `reviews/REV-C1d-waa-snapshot-warmup-fix.md` |
| **Stage 2.1-A 双引擎与事务热切换** | `waa-signer` 集成、`BrowserlessSession`、ContextVar 事务锁与取消回滚、orphan 观测 | 已完成（PASS） | `reviews/REV-2.1-A.md` |
| **Stage 2.1.3 无浏览器终态与兄弟同步** | 502 登录态终态、`save_current_cookies()` 兄弟同步（防旧覆盖新）、`st_mtime_ns` 热重载 | 已完成（PASS） | `reviews/REV-2.1.3.md` |
| **后续演进：Build surface 评估（P4）** | Post-C / 后续评估 Build 渠道修复或永久废弃决策（当前仅保留配置/接口骨架） | 待评估 | — |

## `reviews/` 审查报告与截图索引

- [`reviews/REV-B2-2-p3-d03-browser.md`](reviews/REV-B2-2-p3-d03-browser.md) — B2-2 浏览器/CDP/Context/单页生命周期审查（2026-10-29，PASS）
- [`reviews/REV-B2-3-p3-d04-pool.md`](reviews/REV-B2-3-p3-d04-pool.md) — B2-3 多实例池与 D-05 rebind 审查（2026-10-29，PASS）
- [`reviews/REV-merge-99600c7.md`](reviews/REV-merge-99600c7.md) — 上游 `99600c7` 合并复核（2026-10-30，PASS）
- [`reviews/REV-C1-fingerprint-and-auth-noise.md`](reviews/REV-C1-fingerprint-and-auth-noise.md) — C1 浏览器按账号指纹/401 噪音/Tier 单源复核（2026-11-01，PASS）
- [`reviews/REV-C1d-waa-snapshot-warmup-fix.md`](reviews/REV-C1d-waa-snapshot-warmup-fix.md) — C1d WAA 快照预热/截断/强制重启复核（2026-11-06，PASS）
- [`reviews/REV-2.1-A.md`](reviews/REV-2.1-A.md) — Stage 2.1-A 双引擎共存与事务热切换终审（PASS）
- [`reviews/REV-2.1.3.md`](reviews/REV-2.1.3.md) — Stage 2.1.3 502 登录态终态/同源兄弟整组隔离/toolshape 降级审查（PASS）
- 截图资产：无（UI 截图归档于 `P8-api-ui/reviews/assets/`）。

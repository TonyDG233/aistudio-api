# P6-keepalive / 保活与冷却调度域

- 域标识：`P6-keepalive`
- 核心职责边界：24h `auth_update`（逐 slot 覆盖 chat 与 build-only，含控制面、`all(results)` 记账、`/RotateCookies` 轮换）、24h `metadata_refresh`（`tier_checked_at` 判到期与 3 天 Free 新鲜度跳过）、单全局串行 job 锁（busy 跳过不推进 `next_due`，失败按 job 短周期重试）、429 额度冷却与美西午夜跨天重置、`active_activity` 可选配置（默认关闭，无 idle 浏览器自动回收）、手动保活触发入口（`GET /api/keepalive`、`POST /api/keepalive/run`）。严禁恢复后台 `auto_switch` / `rebalance`。
- 核心代码模块：
  - `src/aistudio_api/application/account_keepalive.py`
  - `src/aistudio_api/application/account_rotator.py`（429 冷却与 PST 午夜重置）
  - `src/aistudio_api/api/routes_keepalive.py`

## 任务计划与里程碑看板

| 里程碑 / 任务 | 核心目标与交付物 | 状态 | 对应审查报告 |
|---|---|---|---|
| **B3 P6/D08 24h 保活闭环** | `AccountKeepaliveService` 生命周期、逐 slot 回写、`RefreshInProgressError` 互斥、单全局 job 锁、手动路由、旧配置键警告迁移 | 已完成（PASS） | `reviews/REV-B3-p6-keepalive.md` |
| **Stage 2.1.1 保活自动 Cookie 轮换** | `_auth_update` 阶段集成 `rotate_account_cookies` 与同源兄弟同步，增强浏览器外长效凭据保活 | 已完成（PASS） | 见 `P8-api-ui/reviews/REV-2.1.1.md` §2.4 |
| **后续演进：控制面会话归属与禁用态展示（P6-Q6）** | 保持 `active_activity` 默认关闭、无 idle 浏览器回收 | 稳定运行中 | — |

## `reviews/` 审查报告与截图索引

- [`reviews/REV-B3-p6-keepalive.md`](reviews/REV-B3-p6-keepalive.md) — B3 24h Cookie 保活、元数据刷新、job 锁与手动触发审查（2026-10-29，PASS）
- 截图资产：无（保活栏 UI 截图归档于 `P8-api-ui/reviews/assets/`）。

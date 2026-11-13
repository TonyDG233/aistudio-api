# P1-account / 账号、Cookie、预设与 Tier 域

- 域标识：`P1-account`
- 核心职责边界：账号注册表与原子持久化、Cookie 跨域保真（`accounts.google.com` LSID/GAPS 等与外部导入瞬态过滤）、`/RotateCookies` 轮换与同源兄弟同步、BenefitTier 安全探测（D02-014..017）、分组与预设持久化（D-08）、notes/batchToggleGroup 清理（D-13）、以及 toggle/apply_preset 的 D-05 rebind 信号。
- 核心代码模块：
  - `src/aistudio_api/application/account_service.py`
  - `src/aistudio_api/domain/model_access.py`
  - `src/aistudio_api/infrastructure/account/account_store.py`
  - `src/aistudio_api/infrastructure/account/account_info_refresher.py`
  - `src/aistudio_api/infrastructure/account/benefit_tier.py`
  - `src/aistudio_api/infrastructure/account/cookie_parser.py`
  - `src/aistudio_api/infrastructure/account/cookie_refresher.py`
  - `src/aistudio_api/api/routes_accounts.py`

## 任务计划与里程碑看板

| 里程碑 / 任务 | 核心目标与交付物 | 状态 | 对应审查报告 |
|---|---|---|---|
| **B2-1 P1 基础闭环** | 账号注册表、Cookie 跨域 twin 保真与导入过滤、D-08 预设 `RLock`、D-13 删除 notes、D-05 toggle/preset rebind 信号、Paid 种子与发现同步 | 已完成（PASS） | `reviews/REV-B2-1-p1-account.md` |
| **C1b Tier 安全与 403 刷新修复** | `httpx[socks]` 代理全覆盖、`strict_tier()` 失败不覆盖既有 Pro/Ultra、`detect-tier` 400/502 语义与池同步 | 已完成（PASS） | `reviews/REV-C1b-vps-tier-and-403-fix.md` |
| **Stage 2.1.1/2.1.3 轮换与同源兄弟同步** | `/RotateCookies` 轮换、`sync_sibling_storage_state_files`（含过期时间比较防回灌）、`mark_session_expired_group` 整组隔离 | 已完成（PASS） | 见 `P8-api-ui/reviews/REV-2.1.1.md`、`P3-pool-instance/reviews/REV-2.1.3.1-session-expired-sibling-sync.md` |
| **后续演进：OAuth / refresh_token 评估（P1-E2）** | 评估是否补充非 Cookie OAuth 凭据链路（当前保持 defer） | 延后（Defer） | — |

## `reviews/` 审查报告与截图索引

- [`reviews/REV-B2-1-p1-account.md`](reviews/REV-B2-1-p1-account.md) — B2-1 账号/Cookie/预设/Paid 只读审查（2026-10-29，PASS）
- [`reviews/REV-C1b-vps-tier-and-403-fix.md`](reviews/REV-C1b-vps-tier-and-403-fix.md) — C1b SOCKS + Tier 安全 + 403 刷新 + Warmup 复核（2026-11-02，PASS）
- 截图资产：无（纯后端域，UI 截图归档于 `P8-api-ui/reviews/assets/`）。

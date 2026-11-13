# P2-routing / 路由准入、错误映射与重试域

- 域标识：`P2-routing`
- 核心职责边界：Free/Pro 多实例池分流（paid 模型强制 chat-only）、`active_requests` 唯一并发准入、SLEEPING/UNSTARTED 唤醒与简单负载均衡排序、Paid `TierRequiredError` 本地 403（D-04/D-06，不切号不降级）、403 两阶段（首次原位重签、二次切号，`auth_cooldown=0.0`）、`verify-account` 移动设备验证自动熔断隔离、429 同账号退避重试→切换→耗尽 503、`quota_scope` 双口径（D-01）。
- 核心代码模块：
  - `src/aistudio_api/application/routing_pipeline.py`
  - `src/aistudio_api/application/routing_eligibility.py`
  - `src/aistudio_api/application/routing_safety.py`
  - `src/aistudio_api/application/account_rotator.py`
  - `src/aistudio_api/application/account_orchestrator.py`
  - `src/aistudio_api/domain/errors.py`
  - `src/aistudio_api/api/error_mapping.py`

## 任务计划与里程碑看板

| 里程碑 / 任务 | 核心目标与交付物 | 状态 | 对应审查报告 |
|---|---|---|---|
| **B2-4 P2/D05 路由闭环** | Free/Pro 分流、Paid chat-only、403 本地 `TierRequiredError`、`quota_scope`（`account`/`account_surface`）、429 重试/切换/503、204/瞬态 404 同号重试 | 已完成（PASS） | `reviews/REV-B2-4-p2-routing.md` |
| **Stage 2.1.2 `accessModes` Ultra 提权与 `verify-account` 熔断** | `accessModes [4]` 优先于宽泛 `gemini-4` 规则；`AuthError.verification_required` 跳过同号重签直接 `session_expired` 隔离并切号 | 已完成（PASS） | 见 `P8-api-ui/reviews/REV-2.1.2.md` §3.1/§7 |
| **Stage 2.1.3 同源兄弟失效传播** | `account_orchestrator` 将 `session_expired` 传播至同源兄弟账号，rotator 镜像过期态排除调度 | 已完成（PASS） | 见 `P3-pool-instance/reviews/REV-2.1.3.md` |
| **后续演进：异步/多槽竞态优化** | 持续观测 `release-before-reassign` 竞态与 `quota_scope` 旧键迁移表现 | 按需跟进 | — |

## `reviews/` 审查报告与截图索引

- [`reviews/REV-B2-4-p2-routing.md`](reviews/REV-B2-4-p2-routing.md) — B2-4 多实例池路由、Tier 403、429 重试切换只读审查（2026-10-29，PASS）
- 截图资产：无（纯后端路由域）。

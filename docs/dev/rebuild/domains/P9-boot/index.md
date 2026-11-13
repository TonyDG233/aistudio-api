# P9-boot / 启动装配、配置口径与鉴权边界域

- 域标识：`P9-boot`
- 核心职责边界：
  - 单一配置模板与深合并（D-07）：`defaults/config.yaml` 为唯一 tracked 默认模板，根 `config.yaml` 仅作为本地运行时配置（untracked + gitignored）；`_deep_merge` 补齐缺失键，关键块启动 fail-fast，非关键块 warning + 回退默认；原子写回（`_atomic_write_text`）与写前校验。
  - API 与管理页鉴权分离（D-14）：`security.api_allow_anonymous` 默认 `false`（空 API Key ≠ 开放）；管理页仅由 `AISTUDIO_WEB_PASSWORD` 控制，两者互不复用；只读模型端点支持 API Key 或合法 Web 会话。
  - 统一启动装配与关闭顺序：`validate_config()` 位于 `lifespan` 首行；关闭顺序严格为取消后台 task → `keepalive.stop` → `instance_manager.close` → `client.close` → 清空状态。
- 核心代码模块：
  - `main.py`、`src/aistudio_api/main.py`
  - `src/aistudio_api/config.py`
  - `src/aistudio_api/api/app.py`、`src/aistudio_api/api/dependencies.py`
  - `src/aistudio_api/infrastructure/gateway/model_defaults.py`
  - `defaults/config.yaml`、`defaults/config.schema.json`、`config/config.schema.json`

## 任务计划与里程碑看板

| 里程碑 / 任务 | 核心目标与交付物 | 状态 | 对应审查报告 |
|---|---|---|---|
| **B1 P9-pre 装配/配置/API 鉴权闭环** | D-07 深合并与启动校验、D-14 API/Web 鉴权分离、原子写回、lifespan 关闭钩子、`defaults/config.yaml` 单一跟踪模板 | 已完成（PASS） | `reviews/REV-B1-p9-pre.md` |
| **B7-3 配置单一来源与启动迁移** | 删除内嵌 235 行默认基线，启动迁移移除 legacy 键、归一 Free-only selector 并原子写回 | 已完成（PASS） | 见 `docs-and-audit/reviews/REV-B7-config-sync-and-green-tests.md` |
| **Stage 2.1 引擎配置接通与死配置清理** | `engine` 配置校验与 `rollback_on_partial_failure`/`read_timeout_s`/`request_timeout_s` 接线，清理未实现死配置 | 已完成（PASS） | 见 `P3-pool-instance/reviews/REV-2.1-A.md` |
| **后续演进：Stage 2.2 APK 启动锁定** | Android APK 模式下强制锁定 `engine.type=browserless` 并跳过桌面环境探测（详见 `P10-android-apk`） | 规划中 | — |

## `reviews/` 审查报告与截图索引

- [`reviews/REV-B1-p9-pre.md`](reviews/REV-B1-p9-pre.md) — B1（P9-pre）装配/配置/鉴权只读审查（2026-10-29，PASS）
- 截图资产：无（配置与鉴权 UI 截图归档于 `P8-api-ui/reviews/assets/`）。

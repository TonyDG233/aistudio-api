# P8-api-ui / API 管理面与 Web 管理控制台域

- 域标识：`P8-api-ui`
- 核心职责边界：FastAPI 管理端点与响应模型、Vite + Vue 3 + TypeScript + Tailwind 管理控制台（Dashboard Bento / Accounts & 实例池 / Settings & 75 词条字典 / Logs）、`AISTUDIO_UI_DEBUG=1`（`--ui-debug`）纯内存/临时目录离线沙盒（100% 阻断真实浏览器与外网请求）、多分辨率（`1440x900`/`1024x900`/`768x900`）响应式验收、油猴长效抓取助手与全格式拖拽导入、D-12 增量不重排原则。
- 核心代码模块：
  - `src/aistudio_api/api/`（`routes_system.py`、`routes_accounts.py`、`routes_instances.py`、`routes_debug*.py`、`ui_sandbox.py`、`response_models.py`）
  - `src/aistudio_api/tools/aistudio-cookie-grabber.user.js`
  - `src/aistudio_api/static/`（`bun run build` 生产构建产物）
  - `web/src/`（`views/`、`components/`、`stores/`、`api/`、`types/`、`utils/`）

## 任务计划与里程碑看板

| 里程碑 / 任务 | 核心目标与交付物 | 状态 | 对应审查报告 |
|---|---|---|---|
| **C2a UI Debug 沙盒与 AccountsView v3** | `UiDebugIsolation` + `UISandbox`、单一状态分类器 `accountStatus()`、B1/B2/B3 实例操作与批量管理 | 已完成（PASS） | `reviews/REV-C2a.md` |
| **C2b SettingsView 与配置重置契约** | `PATCH /config`、`POST /config/reset`、全中文 6 卡与 75 词条字典弹窗、还原/部分保存分离 | 已完成（PASS） | `reviews/REV-C2b.md` |
| **C2c Dashboard Bento 与四协议示例** | 4 行实例卡、四协议多行 cURL `QuickApiExamples`、Stats v1→v2 迁移与保留键过滤、日志沙盒注入 | 已完成（PASS） | `reviews/REV-C2c.md` |
| **C2d 幽灵行清理与多实例徽章** | 组头多 `激活实例 #X`、子行 `ACTIVE` 优先绿框、通用标识与设置 4 张只读运行态卡 | 已完成（PASS） | `reviews/REV-C2d.md` |
| **Stage 2.1-B 双引擎设置 UI 与 Docker 双引擎** | `--ui-debug` CLI 标志、浏览器/WAA 引擎切换卡与 YAML 保存门控、首页引擎徽章、Dockerfile 多阶段内置双引擎 | 已完成（PASS） | `reviews/REV-2.1-B.md` |
| **Stage 2.1.1 拖拽导入与油猴在线安装** | 全页面拖拽与多文件 Bundle 合并、4 种凭据格式兼容、油猴脚本在线安装与移动端阻断 | 已完成（PASS） | `reviews/REV-2.1.1.md` |
| **Stage 2.1.2 实例换绑 `max_tier` 归一化 UI** | `Pro 至 Ultra_20X` 槽位摘要与 `ReassignAccountModal` 兼容过滤联动实测 | 已完成（PASS） | `reviews/REV-2.1.2.md` |
| **Stage 2.1.3.5 UI 极简审美对齐与移动端防挤压** | 账号行双层响应式（375px 邮箱不挤压）、槽位操作按钮统一样式、引擎徽章去重与全站去 Emoji/杂色块 | 已完成（PASS） | `reviews/REV-2.1.3.5-ui-polish-mobile.md` |
| **后续演进：文案统一与 Schema 同步** | 清理空态 `API Key` 文案差异（N-12/N-15）及协议默认开启文案同步（C3 N-2） | 按需微调 | — |

## `reviews/` 审查报告与截图索引

### 审查报告
- [`reviews/REV-C2a.md`](reviews/REV-C2a.md) — C2a UI 调试沙箱 / AccountsView v3 审计（2026-11-07，PASS）
- [`reviews/REV-C2b.md`](reviews/REV-C2b.md) — C2b SettingsView / `PATCH /config` / `POST /config/reset` 终审（2026-11-07，PASS）
- [`reviews/REV-C2c.md`](reviews/REV-C2c.md) — C2c Dashboard Bento / 四协议示例 / 日志沙盒终审（2026-11-07，PASS）
- [`reviews/REV-C2d.md`](reviews/REV-C2d.md) — C2d Stats 幽灵行清理 / 多实例绿框 / 通用标识终审（2026-11-08，PASS）
- [`reviews/REV-2.1-B.md`](reviews/REV-2.1-B.md) — Stage 2.1-B 双引擎 UI / Docker 多阶段 / `--ui-debug` 终审（PASS）
- [`reviews/REV-2.1.1.md`](reviews/REV-2.1.1.md) — 2.1.1 凭据拖拽全格式导入与油猴在线安装终审（PASS）
- [`reviews/REV-2.1.2.md`](reviews/REV-2.1.2.md) — 2.1.2 Ultra 提权 / 换绑 tier 归一化 / 403 续期 / `verify-account` 终审（2026-11-11，PASS）
- [`reviews/REV-2.1.3.5-ui-polish-mobile.md`](reviews/REV-2.1.3.5-ui-polish-mobile.md) — 2.1.3.5 UI 极简审美对齐与 375px 移动端防挤压审查（2026-11-13，R2 PASS）

### 截图资产（`reviews/assets/`）
- **C2b**：[`c2b-settings-overview-1440.png`](reviews/assets/c2b-settings-overview-1440.png)、[`c2b-settings-protocol-atg-1440.png`](reviews/assets/c2b-settings-protocol-atg-1440.png)
- **C2c**：[`c2c-dashboard-overview-1440.png`](reviews/assets/c2c-dashboard-overview-1440.png)、[`c2c-logs-debug-1440.png`](reviews/assets/c2c-logs-debug-1440.png)
- **C2d**：[`c2d-accounts-instances-1440.png`](reviews/assets/c2d-accounts-instances-1440.png)、[`c2d-dashboard-ghost-1440.png`](reviews/assets/c2d-dashboard-ghost-1440.png)、[`c2d-settings-runtime-1440.png`](reviews/assets/c2d-settings-runtime-1440.png)
- **2.1-B**：[`c21b-dashboard-engine-badge-1440.png`](reviews/assets/c21b-dashboard-engine-badge-1440.png)、[`c21b-dashboard-engine-badge-768.png`](reviews/assets/c21b-dashboard-engine-badge-768.png)、[`c21b-settings-browser-1440.png`](reviews/assets/c21b-settings-browser-1440.png)、[`c21b-settings-engine-waa-1440.png`](reviews/assets/c21b-settings-engine-waa-1440.png)、[`c21b-settings-engine-waa-1024.png`](reviews/assets/c21b-settings-engine-waa-1024.png)、[`c21b-settings-engine-waa-768.png`](reviews/assets/c21b-settings-engine-waa-768.png)
- **2.1.1**：[`c211-accounts-import-1440.png`](reviews/assets/c211-accounts-import-1440.png)、[`c211-accounts-import-768.png`](reviews/assets/c211-accounts-import-768.png)
- **2.1.2**：[`c212-reassign-pro-modal-1440.png`](reviews/assets/c212-reassign-pro-modal-1440.png)
- **2.1.3.5**：[`c216-accounts-desktop-1440.png`](reviews/assets/c216-accounts-desktop-1440.png)、[`c216-accounts-mobile-375.png`](reviews/assets/c216-accounts-mobile-375.png)、[`c216-import-modal-1440.png`](reviews/assets/c216-import-modal-1440.png)、[`c216-dashboard-1440.png`](reviews/assets/c216-dashboard-1440.png)、[`c216-dashboard-mobile-375.png`](reviews/assets/c216-dashboard-mobile-375.png)、[`c216-settings-1440.png`](reviews/assets/c216-settings-1440.png)

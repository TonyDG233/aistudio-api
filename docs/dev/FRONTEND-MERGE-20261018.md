# 前端/管理页移植调研（2026-10-18）

> 目的：为 WP1/WP3/WP5/WP6 的管理页增量做落点定稿；核查"对面必需的开关按钮与统计项如何并入本仓前端"的阻力。
> 结论：**前端阻力低，无阻塞项**；机制（schema 驱动配置编辑器、YAML 全量往返、现成 UI 组件库）全部就绪，剩余为确定性组件开发。

---

## 1. 双方前端架构对比

| 维度 | Ark（对面） | 本仓（web/） |
|---|---|---|
| 框架 | Vue 3 + Element Plus + less | Vue 3.5 + TypeScript + Pinia + vue-router + Tailwind 4 + lucide |
| 结构 | 单巨页 `StatusPage.vue`（**321KB**）+ AuthPage(28KB) | 4 视图（Dashboard/Accounts/Settings/Login）+ 30+ 组件（ui/accounts/config/dashboard/settings/modals/layout） |
| 配置编辑 | `.env` 文本编辑 + EnvVarTooltip 说明 | **config.yaml 可视化编辑器**（schema 驱动：`CONFIG_SCHEMA` + FieldHelpTip + SchemaDictionaryModal），YAML 全量 parse/stringify **保留未知键** |
| i18n | zh/en（utils/i18n.js + locales） | 单语中文（无 i18n 层） |
| 构建 | vite → dist/（由 Node 服务直接托管） | **bun + vue-tsc + vite → 产物提交入库** `src/aistudio_api/static/` |
| 统计展示 | 巨页内嵌：多张表 + 时间范围筛选（all/1h/6h/24h/7d/30d/custom）+ JSONL 导入导出 | DashboardView（4 StatCard + ModelStatsTable）+ AccountsView（ModelQuotaGrid） |
| 主题/语言切换 | 有（auto/light/dark；zh/en） | 无（Tailwind 单主题） |

**总判断**：本仓前端是结构化、可扩展的组件工程；对面 UI 为不可移植的巨页单体。策略维持 PLAN 原则"不做完整 UI 复刻"——**只抽取必需控件/统计，用本仓模式重建**。

## 2. 必需控件/统计 → 本仓落点映射

| # | 对面项（迁移需求） | 本仓落点 | 阻力 |
|---|---|---|---|
| 1 | 功能开关组：ATG 模式（off/prefer/required，per-format）、Build 面总开关、AUTO_SWITCH+间隔、ACCOUNT_REFRESH_CONCURRENCY、ROUTE_BY_TIER、fallback 开关组（默认 off） | `VisualConfigEditor` 新增 **FeaturesCard.vue** + `schema.ts` 新 category`features` + `types.ts` `ParsedConfigYaml.features`（ConfigSwitch/ConfigSelect/FieldHelpTip 全现成） | 低 |
| 2 | 分组编排编辑：组名 / match_tier / match_emails_regex / allowed_surfaces / max_page_instances | 新 **GroupEditorCard.vue**（结构仿 ModelOverrideCard：卡片列表+增删+字段组） | 中（最大新组件） |
| 3 | 页面实例面板：各面实例数/配额/内存估算/手动开关 | AccountsView 顶部新 **InstancePanel.vue** + 新 `/instances` 只读+操作端点 | 中 |
| 4 | tier 徽章（free/pro/ultra/plus） | `AccountRow.vue` 增 Badge（样式仿现成 `session_expired` 徽章；Badge 组件已有） | 低 |
| 5 | 过期徽章 | **已有**：`session_expired` 完整 UI（红边、状态胶囊、恢复按钮） | 零 |
| 6 | 账号刷新按钮（单个/批量，409 互斥） | `AccountRow.vue` 单账号按钮 + `AccountsView.vue` 批量条 → WP3 端点 `POST /accounts/refresh` | 低 |
| 7 | 统计 surface 拆分 | `ModelStatsTable.vue` 增列（或按 surface 分组行）+ `stores/system.ts`、`types/stats.ts` + `/stats` 扩展 | 低 |
| 8 | ATG 命中统计（syntheticHit/syntheticCount/realToolCount/repaired/conflict） | `DashboardView` 新 StatCard + `ModelStatsTable` 列 | 低 |
| 9 | format breakdown（api_format） | 同上（列或卡片） | 低 |
| 10 | refresh 统计（成功/过期/upstream_error） | 账户页小卡或 SettingsView | 低 |
| 11 | usage 明细（序列号/耗时/尝试链）+ 时间范围筛选 + JSONL 导入导出 | **二期**（PLAN 已列）；落点为 Dashboard 扩展或新 StatsDetailView | 重（暂缓） |
| 12 | 日志级别开关 | **已有** LogConfigCard | 零 |
| 13 | 主题切换 / zh-en 语言 | **决策：不移植**（单语中文、单主题；"开关文案自解释"由 schema `description/explanation` + FieldHelpTip 承担） | — |
| 14 | `.env` 编辑 UI + EnvVarTooltip | **不需要**：本仓 config.yaml 可视化编辑器为超集（且热重载 PUT /config/yaml 现成） | — |

## 3. 文件级工作清单（并入各 WP）

- WP1：`schema.ts`（+features category、~12 条 FieldDefinition）、`types.ts`（ParsedConfigYaml.features）、新 `FeaturesCard.vue`、`VisualConfigEditor.vue`（挂载卡片）。
- WP3：`AccountRow.vue`（tier Badge + 刷新按钮）、`AccountsView.vue`（批量刷新）、`api/accounts.ts` + `stores/accounts.ts`（刷新调用）。
- WP5：新 `GroupEditorCard.vue`、新 `InstancePanel.vue`、`api/system.ts`（/instances）。
- WP6：`ModelStatsTable.vue`、`DashboardView.vue`、`types/stats.ts`、`stores/system.ts`。
- 构建：`web/` bun run build → `src/aistudio_api/static/`（沿用"产物入库"现状）。

**工作量估计**：UI 部分合计 ≈ **3.5–5 人日**（分散计入 WP1/WP3/WP5/WP6），无架构级阻力。

## 4. 验证项/注意点

1. **YAML 往返已核实**：编辑器全量 `YAML.parse/stringify`（`VisualConfigEditor.vue:74,90`），手写 `features:` 段不会被可视化编辑丢弃 ✓。
2. **"开关文案自解释"约束落法**：每个新开关在 `CONFIG_SCHEMA` 必填 `description`（一句话）+ `explanation`（开=什么/关=什么/何时用/失败怎么办）+ `recommendedValue`（SchemaDictionaryModal 会自动渲染推荐值徽章）。
3. **ModelStatsTable surface 形态**：列扩展 vs 分组行（数据量大时）留待 WP6 实测后定。
4. **静态产物策略**：保持现状（产物提交）。构建需 bun + vue-tsc 全绿（质量门本就包含页面 JS 校验）。
5. 对面 `StatusPage.vue`（321KB）**不引入**，仅作控件文案/字段参照。
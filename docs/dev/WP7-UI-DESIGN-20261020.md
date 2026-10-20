# WP7 UI 统一设计与实施稿

- 日期：2026-10-20
- 分支：`p1-ark-merge`
- 状态：用户已确认关键决策，进入实施
- 范围：P1 控制面板、P2 账号管理、P3 系统与模型配置、P4 统计页收尾、P5 日志页与最小 SSE
- 提交规则：每个阶段完成即中文 commit；不 push，最终测试报告由用户决定是否 push

---

## 0. 冻结语义

- Context = 独立 cookie jar / 账号隔离，计入 `max_contexts`。
- Page = Context 内的 surface 页面，计入 `max_pages`。同一页面可承载多个在途请求，页面忙不是 429 理由。
- 槽位 = `(账号 × 渠道)` 预留实例位，是“计划实例数”的来源，不是永久 Context。
- 单模式/多模式：渠道注册表 enabled 数 ≥2，或启用槽位行数 ≥2，即多模式；否则单模式。
- 策略组是账号级约束层，不是独立实例池；组有序、首个命中生效。
- 模型名中的 `pro` 不决定账号资格或 surface；`paid` 仅作为内部 discovery 元数据，不暴露到模型拉取 API。
- 统计 client 层与 upstream 层分开；cache read 是输入子集，reasoning 是输出子集；缺失字段显示 `—`，不是 0。
- P4 `/stats` 不重写，只做入口、文案、视觉一致性收尾。
- P5 只做 raw log 流与留档下载，不做请求级日志表。

---

## 1. 已确认决策

1. 日志留档保留 **7 天**；按天写 `data/logs/aistudio-YYYYMMDD.log`，超过 7 天自动清理。
2. 日志页面虚拟滚动：内存最多 5000 条，DOM 约 60 条，不无限追加。
3. 日志下载：支持“下载当前视图”；支持按时间范围导出完整 raw log；跨天/多段导出打包 ZIP；导出默认包含该时段全部级别。
4. 凭证批量导入以浏览器本地文件为主：点击导入打开系统文件选择窗口，支持一次多选 `.json`；支持把多个 JSON 拖到账号列表中央触发批量导入。
5. 不实时监控 `data/` 目录；服务器路径保留为高级折叠项，仅手动读取，不自动监听。
6. P3 深度区放在“模型规则”上面，模型规则层下移；深度开关默认关，浏览器记住选择。
7. P1 先实现，再实际打开 1440×900 / 1024×768 / 390×844 检查并微调。
8. 后端与接线按 TDD；所有按钮验收时必须真实点击并核对网络请求、loading、成功/失败反馈。
9. 测试必须结合视觉截图；每个页面至少三档截图，另加空/加载/错误/断流/暂停状态。

---

## 2. 全局视觉规范

### 2.1 颜色

沿用现有浅色设计系统，不引入深色主题。日志控制台为代码区域深色，不改变全站主题。

| 用途 | Token | 值 |
|---|---|---|
| 页面背景 | `gray-50` | `#F9FAFB` |
| 卡片背景 | `white` | `#FFFFFF` |
| 卡片边框 | `gray-200/80` | `#E5E7EB` 80% |
| 主文字 | `gray-900` | `#111827` |
| 次文字 | `gray-500` | `#6B7280` |
| 弱文字 | `gray-400` | `#9CA3AF` |
| 品牌主色 | `brand-600` | `#059669` |
| 品牌辅助 | `brand-500` | `#10A37F` |
| 品牌浅底 | `brand-50` | `#ECFDF5` |
| 成功 | `emerald-600` | `#059669` |
| 警告/超预算 | `amber-500/600` | `#F59E0B / #D97706` |
| 错误/429/断流 | `rose-500/600` | `#F43F5E / #E11D48` |
| 信息/挂载 | `sky-600` | `#0284C7` |
| 代码块 | `gray-950` | `#030712` |
| 代码文字 | `gray-100` | `#F3F4F6` |

### 2.2 字号与字重

| 层级 | 规格 |
|---|---|
| 页面主标题 | Topbar 18px / 700 / tracking-tight |
| 区块标题 | 14px / 700 / gray-900 |
| 卡片小标题 | 12px / 600 / gray-500 / uppercase / tracking-wider |
| 大指标 | 30–36px / 700 / tabular-nums / tracking-tight |
| 正文 | 13–14px / 400–500 |
| 辅助说明 | 11–12px / 400 / gray-400 |
| 徽章 | 10–11px / 600 |
| 代码 | 11.5–12.5px / mono / line-height 1.65 |
| 日志控制台 | 12px / mono / line-height 1.55 |

### 2.3 间距、圆角、阴影

- 页面纵向区块间距 24px；卡片内部 20–24px；小卡 16px。
- 间距尺度：4 / 6 / 8 / 12 / 16 / 20 / 24 / 32px。
- 卡片圆角 16px（`rounded-2xl`）；内部面板/代码块 12px；输入框/按钮 10–12px。
- 阴影 `shadow-xs`，hover 不放大阴影。
- 动效 150–200ms ease-out；尊重 `prefers-reduced-motion`。
- 触控目标最小 40×40px；移动端主按钮高度 40px。

### 2.4 响应式

| 视口 | 主内容 |
|---|---|
| 1440×900 | Sidebar 256px；主区最大 1280px；左右 padding 32px |
| 1024×768 | Sidebar 保留；主区 padding 24px；多列降为 1–2 列 |
| 390×844 | Sidebar 抽屉；主区 padding 16px；全部单列；表格卡片化 |

### 2.5 组件

复用 `Button`、`Badge`、`Modal`、`ConfigInput`、`ConfigSwitch`、`ConfigSelect`、`FieldHelpTip`、`ToastContainer`。

新增/抽取：

- `SectionCard`：标题、说明、右上操作、内容插槽。
- `MetricValue`：大数字 + 子指标 + tooltip。
- `StatusBadge`：三态/挂载/槽位统一视觉。
- `EmptyState`：图标 + 标题 + 说明 + 主操作。
- `ErrorState`：错误摘要 + 详情折叠 + 重试。
- `SkeletonBlock`：页面级/卡片级。
- `CodeBlock`：标题、复制、语言标签、横向滚动。

无障碍：

- 展开按钮带 `aria-expanded`；开关带 `role="switch"` 与 `aria-checked`。
- 颜色不是唯一状态表达，徽章同时有文字。
- 控制台支持键盘滚动；复制按钮有成功/失败 toast。

---

## 3. P1 控制面板

### 3.1 桌面布局

12 列网格：

- 左列 `col-span-5 row-span-2`：当前激活账号 / 活跃 Context 卡。
- 右区上排 `col-span-7`：可用账号卡 + 实例数卡，内部两列。
- 右区下排 `col-span-7`：总请求数卡。
- 实例详情展开面板横跨 12 列，位于摘要区与模型表之间。
- 模型表、格式卡全宽。

```text
┌ 当前激活账号（跨两行） ┐  ┌ 可用账号 ┐ ┌ 实例数 ┐
│ Context 1: 账号 / 槽位  │  └──────────┘ └────────┘
│ Context 2: 账号 / 槽位  │  ┌ 总请求数（429 子指标） ┐
└────────────────────────┘  └───────────────────────┘
┌ 实例详情（展开时） ────────────────────────────────┐
┌ 模型调用概览 ─────────────────────────────────────┐
┌ 快速调用示例（格式选项卡） ───────────────────────┐
```

1024×768：

- 活跃 Context 整行置顶。
- 可用账号 + 实例数并排两列；总请求数整行。
- 不强行保持左右跨两行，避免主区过窄。

390×844：

- 活跃 Context → 可用账号 → 实例数 → 总请求数 → 模型卡 → 格式卡。
- 每张卡单列，按钮换行。

### 3.2 活跃 Context 卡

标题 `当前激活账号`，副标题 `按 Context 逐行列出；同一页面可承载多个在途请求，不是并发数。`

数据来源：`GET /surface/instances` 的 `contexts[]`、`active_account`、`pages`。

每行：

- 状态点：绿色 ready；琥珀 busy/cooldown；灰色 empty。
- 账号名/邮箱：优先 email，单行截断，tooltip 全称。
- tier 徽章：`pro` / `free` / `ultra` / `plus`；缺失 `—`。
- 槽位徽章：`@槽1`、`@槽2`；无则 `未绑槽位`。
- 挂载：`PG ✓` / `BUILD ✓`，未挂载灰色 `—`。
- 在途：`在途 N`，不写“并发实例”。
- 当前标记：与 `active_account.id` 匹配显示 `当前`。
- 行高 56px，分隔线 `gray-100`；多行不使用表格表头。

空态：`暂无活跃 Context`；说明首个请求到达时自动创建；次按钮 `前往账号管理`。

加载：3 行 56px skeleton。错误：卡片内错误条 + 重试，不影响其他区域。

### 3.3 可用账号卡

- 大数字 `x / y`。
- x = 未过期、未冷却、可用于调度的账号数；y = 账号总数。
- 副文 `x 可用 · y 总数`；无账号 `—`。
- 点击标题/箭头跳 `/accounts`；卡本身不是按钮。
- 空态 `0 / 0`，副文 `尚未导入账号`。

### 3.4 实例数卡

大数字：`当前页面实例 / 计划实例`。

- 当前 = `pages.chat + pages.build` 实时页面数。
- 计划 = `planned_instances`（启用槽位行数）。
- 右上 chip：`Context 1 / 2`，来自 `budgets.contexts` 与 `budgets.max_contexts`；未知显示 `Context —`。

卡下灰色小字：

- 格式 `chat 1/1 · build 1/0`。
- 只显示计划 > 0 的渠道；最多 2 个；多余 `+N`。
- `+N` hover/点击出小浮层列出全部渠道比值。
- 全部计划为 0：显示 `—`，不显示 `0/0`。
- 某渠道实际 > 计划：仅该比值变 `amber-600`，不加整句警告。
- 计划 0 但实际 > 0：显示 `build 1/0` 并标琥珀，tooltip 说明可能处于切换/回收过渡期。

右下角小号 `详情` + chevron，`aria-expanded`；不整卡可点。

详情展开面板：

- 预算 chips：页面、Context、计划槽位、熔断状态。
- Context 表：槽位 / 账号 / 状态 / 页面 / 在途 / PG / BUILD / 最近使用。
- 路由快照：候选、冻结原因、熔断剩余时间（只读）。
- 全部来自 `/surface/instances`；无写操作；展开时加载，失败可重试。

### 3.5 总请求数卡

- 大数字 `client_requests`（`/api/stats?range=all`）。
- 子指标 `成功 218 · 失败 12 · 中断 0`。
- 429 子指标 `429 尝试 6`，tooltip 说明上游尝试层计数，同一客户端请求可能多次尝试。
- 429 点击跳 `/logs?levels=warn,error&q=429`。
- 右下 `查看统计 →` 到 `/stats`。
- stats 失败显示 `—` + 错误条 + 重试，不用 rotation 数字冒充。

### 3.6 模型调用概览

- 数据来自 stats `models`，取请求数前 5。
- 列：模型 / 请求 / 成功 / 429 / 错误 / Token / 最后调用。
- 模型名统一去掉 `models/` 前缀。
- 标题右侧 `查看统计 →`。
- 桌面表格；640–1024 横向滚动，`min-w-[760px]`；<640 改卡片：
  - 模型名 + 最后调用
  - 2×2 指标网格
  - Token 单行
- 空态 `暂无模型调用记录`。
- P1 不展开输入/输出/推理/缓存明细。

### 3.7 格式选项卡

顺序：`Gemini 原生` / `OpenAI Chat` / `OpenAI Responses` / `Claude Messages` / `Token 计数`。

可用性：

| Tab | 显示条件 |
|---|---|
| Gemini 原生 | 始终 |
| OpenAI Chat | `features.formats.openai !== false` |
| OpenAI Responses | `features.formats.responses !== false` |
| Claude Messages | `features.formats.claude !== false` |
| Token 计数 | 始终 |

配置读取失败时只显示 Gemini，并提示 `格式配置读取失败，仅展示 Gemini 原生端点` + 重试。

每个 tab：

- 端点 mono chip。
- Base URL mono chip + 复制。
- 鉴权头：`x-goog-api-key: <YOUR_API_KEY>` 或 `Authorization: Bearer <YOUR_API_KEY>` / `x-api-key: <YOUR_API_KEY>`。
- 示例模型选择器：当前模型列表前 10 个，默认第一个；无模型时 `your-model`。
- 两个代码块：`curl`、`SDK`；桌面并排，<1280 单列；高度自适应，禁止大块空白；移动端代码块内部横向滚动。
- 每个代码块复制按钮，成功显示 `已复制` 1.5s。
- 底部 `适用客户端` chips。

端点映射：

| Tab | 端点 | Base URL | 鉴权 |
|---|---|---|---|
| Gemini 原生 | `POST /v1beta/models/{model}:generateContent`；流式 `:streamGenerateContent?alt=sse` | `window.location.origin` | `x-goog-api-key` |
| OpenAI Chat | `POST /v1/chat/completions` | `origin + '/v1'` | `Authorization: Bearer` |
| OpenAI Responses | `POST /v1/responses` | `origin + '/v1'` | `Authorization: Bearer` |
| Claude Messages | `POST /v1/messages` | `origin` | `x-api-key` |
| Token 计数 | Gemini `POST /v1beta/models/{model}:countTokens`；Responses `POST /v1/responses/input_tokens`；Claude `POST /v1/messages/count_tokens` | 对应 origin / origin + `/v1` | 对应格式 |

Token tab 只展示计数端点，不展示生成端点；说明 `预检请求，不计入生成 token`。

API key 一律 `<YOUR_API_KEY>` 占位。移动端 tab 独立横向滚动。

### 3.8 状态与数据映射

| 状态 | 表现 |
|---|---|
| 加载 | 摘要 skeleton、模型表 3 行 skeleton、格式卡骨架 |
| 空 | Context 空态、可用账号 0/0、请求 0、模型空态；格式卡仍可用 |
| 局部错误 | 哪个接口失败哪张卡显示错误条+重试 |
| 全局错误 | `/surface/instances` 与 `/api/stats` 都失败时顶部全局错误条 |
| 无格式配置 | 只显示 Gemini + 配置读取失败提示 |

数据映射：

- `/surface/instances`：Context、挂载、槽位、预算、计划实例、熔断。
- `/api/stats?range=all`：总请求、成功/失败/中断、429 上游尝试、模型汇总。
- `/api/rotation` 或账号列表：可用账号 x/y。
- `/api/config`：`features.formats`。
- 模型列表：展示前剥 `models/`。

---

## 4. P2 账号管理

### 4.1 保留结构

- 保留 Cookie 凭据组 → 子账号。
- 组头：凭据组短 ID、导入时间、会话指纹、子账号数、删除整组。
- 子账号保留：账号名/邮箱、ID、tier、过期/登录态失效、今日/累计、冷却/配额耗尽、刷新、探测邮箱、模型配额展开、删除。
- 不重写地域 Cookie，不伪造账号状态。

### 4.2 单模式账号行

- 第二行固定 `挂载：PG 页 ✓ / BUILD 页 ✓`，来自 `/surface/instances` 当前 Context。
- 非当前活跃 Context：`挂载：—`，tooltip `仅当前活跃 Context 会持有页面；未挂载不代表账号不可用`。
- 激活按钮文案 `设为默认活跃`。
- 不显示矩阵与槽位徽章。

### 4.3 多模式账号行

行头增加：

- 槽位徽章 `@槽1`、`@槽2`。
- 三态徽章：
  - 资格：账号级资格（未过期 + routing 允许该渠道 + 渠道 enabled）；tooltip 明确 `不含策略组运行时收窄`。
  - 挂载：来自 Context 的 `chat_ready/build_ready`。
  - 命中：该账号当前为活跃 Context，或该 Context 在途 >0；tooltip `当前调度快照；不是策略组命中`。
- 颜色：通过 emerald；未通过 gray；在途 brand/blue；过期 rose。
- 三态是状态，不是开关。

渠道矩阵展开区：

- 标题 `账号 × 渠道`，右侧 `继承默认 / 自定义`。
- 每个启用且已实现渠道一个 cell：
  - 渠道名 `PG / Chat` 或 `Build`。
  - 逐格开关写 `PUT /api/accounts/{id}/routing` 的 `surfaces`。
  - 状态行：资格 / 挂载 / 命中。
  - 槽位行 `@槽N` 或 `未绑定槽位`。
- 关闭渠道时若有挂载/在途，确认 `新请求不再选择它，在途不打断`；保存失败回滚 + toast。
- `继承默认`：无显式 routing 时所有渠道开关为继承；首次关闭创建显式 surfaces；自定义时提供 `恢复继承`（DELETE routing）。
- 不提供 quota pool 编辑。

激活区：

- `设为默认活跃` 调用现有 activate。
- `绑定到槽位 N`：默认 N = enabled 槽位行数 + 1；点击 popover 选择渠道 + 目标槽位号 + 确认；写 `PUT /config/features` 的 `slots` 全量数组，保留其他行与 note；成功后行头出现 `@槽N`。
- 无可用渠道时按钮 disabled + tooltip。

### 4.4 批量导入

入口一：点击 `导入凭据` 打开系统文件选择窗口：

- `multiple`，接受 `.json`。
- 可一次勾选多个文件。
- 选择后显示文件列表、识别类型、账号数量、失败原因。
- 确认后批量导入。

入口二：把多个 JSON 拖到账号列表中央：

- 列表中央出现虚线覆盖层 `松开导入 N 个凭据`。
- 松开后进入同一预览/确认流程。
- 支持多个文件；不做目录递归拖拽承诺。

入口三：高级折叠 `服务器路径`：

- 手动输入文件或目录路径；目录仅匹配 `auth-*.json`。
- 不实时监控 `data/`，不自动监听文件变化。

格式识别：

- 单账号旧 auth JSON（含 `cookies` / `origins`）。
- 数组。
- `{accounts:[...]}` / `{profiles:[...]}`。
- 含 `cookies` 的 bundle 项。
- 逐文件报告成功/失败，不因一个坏文件吞掉整批。

导入后：

- 关闭弹窗，刷新账号列表。
- 对导入 IDs 自动调用 `/api/accounts/refresh` 一次。
- 409 显示 `账号信息刷新进行中，稍后自动重试`；不伪造 tier。
- 失败 tier 显示 `—`，行内 `待刷新` + 重试。
- toast `已导入 N 个账号；tier 刷新中/已完成/待刷新`。

### 4.5 状态与响应式

- 加载：组卡 skeleton（组头 + 2 行账号）。
- 空：`还没有凭据组` + `导入 Cookies / JSON` + 支持格式说明。
- 错误：页首错误条 + 重试；已有数据可显示 stale 提示。
- 行内保存失败：开关回滚 + 行内红字 + toast。
- 1024：矩阵两个渠道并排；账号行操作换行。
- 390：组头纵向；账号行三行（身份 / 状态 / 操作）；矩阵 cell 单列；弹窗全屏可滚动。

---

## 5. P3 系统与模型配置

### 5.1 页面层级

基础区：

1. 顶部标题 + `显示深度功能` 开关。
2. 运行状态 4 卡：端口/浏览器、流式超时、代理、鉴权。
3. 免密提醒。
4. 运行与调度：auto_switch、间隔、刷新并发。
5. 基础功能开关：build_surface、route_by_tier。
6. 日志与调试：现有 LogConfigCard。
7. 接入密钥：现有 ApiKeyManagerCard。
8. 模型规则与安全：现有 VisualConfigEditor 的 profiles / models / safety（下移）。

深度区（开关开启时渲染在模型规则上面）：

1. 渠道注册表
2. 策略组
3. 槽位表
4. 路由安全
5. 分组编排
6. 高级协议与回落（ATG / fallback）

### 5.2 深度开关

- 位置：页面标题右侧；`role="switch"`。
- 默认关；首次访问 localStorage 无值时关闭。
- 记住选择：`settings.showAdvanced=true/false`；提供 `恢复默认（关闭）`。
- 关闭时不渲染深度区 DOM，不只 `v-show`。
- 若隐藏时深度字段有未保存修改：开关旁显示 `深度区有未保存修改`，关闭不丢弃，保存时仍写入。
- 顶部提示：`深度模式：修改会直接影响路由、槽位和实例预算。max_pages 是页面上限，不是并发数。`

### 5.3 渠道注册表

说明：`渠道是请求能力的第一层；max_pages 是该渠道同时存活的页面数上限，不是并发请求上限。`

每渠道一行/卡：

| 字段 | 控件 | 说明 |
|---|---|---|
| 渠道 | 只读 | `PG / Chat`、`Build`、`Official` |
| enabled | Switch | 关=该渠道请求显式拒绝，不静默换面 |
| max_pages | 数字 0–16 | 参与页面预算取最严；0=不允许该渠道页面 |
| quota_pool | Select | `不限制 / free / pro`；null 表示 any |
| 实现状态 | 只读徽章 | chat/build `已实现`；official `未实现（WP9 前不可用）` |
| 成本提示 | 小字 | 每页约 +66–71MB；每个 Context 约 +180MB |

Official 行置灰，enabled 不可开启。`max_instances` 不展示、不写回。字段旁使用 `FieldHelpTip`。

### 5.4 策略组

复用并升级 `FeatureGroupsEditor`：

删除：

- 顶部“实时实例”整块（迁 P1）。
- `max_page_instances` 输入。
- 与现语义冲突的“argon/Pro 强制 chat”文案。

每组字段：

- 组名
- enabled
- weight（0.1–100）
- match_tier（逗号分隔）
- match_emails_regex（可空）
- allowed_surfaces（PG/Build 复选）
- allowed_quota_pools（free/pro/any 复选）
- max_pages（0–16）
- 组序上移/下移
- 删除

组头显示 `#1`、`首个命中生效`、enabled/disabled、max_pages 摘要。空 surfaces 显示 `该组被禁用，调度层直接排除`。

空态：`未配置策略组；按 tier 默认规则分发（free→build，pro/ultra/plus→chat）`。

保存走 VisualConfigEditor 统一保存，不在组卡内单独保存。

### 5.5 槽位表

说明：`每行 = 一个 (账号 × 渠道) 预留页面位；计划实例数 = 启用行数。槽位不绕过全局/渠道/组预算。`

列：`#` / 账号（email + tier） / 渠道（PG/Build） / enabled / 备注 / 操作。

- `#` 为 1-based `@槽N`。
- 添加行默认第一个未占用账号 + PG。
- 重复 `(account_id, channel)` 保存前校验，红色行内错误，不提交。
- 过期账号标灰但可选择，由资格层拒绝。
- 计划摘要：`启用 3 行 / 上下文上限 2`；计划 > max_contexts 显示琥珀提示。
- 删除行立即改本地状态，统一保存。
- 空态：`暂无槽位；零配置仍可按 tier 默认规则运行`。

### 5.6 路由安全

字段：

- max_switches（单请求换账号/换实例动作上限）
- global_max_pages（全局页面硬顶）
- max_contexts（Context 上限）
- borrow（默认关）
- prewarm（默认关）
- 熔断：window_seconds / failure_threshold / cooldown_seconds

文案：

- max_switches 与同账号重试并存；耗尽返回显式 503 `surface_switch_exhausted`。
- global_max_pages 是所有渠道/Context 合计页面数，不是每 Context。
- max_contexts 与页面数独立计数。
- borrow 只在首字节前临时借入就绪槽，默认关。
- prewarm 增加内存成本，默认关。

布局：2 列字段网格；熔断子卡灰底。

### 5.7 分组编排

只读 + 顺序摘要：

- 流程：`① 能力解析 → ② 候选池 → ③ 组约束 → ④ 排序 → ⑤ 实例落地`。
- 组序列表：`#1 pro-group`、`#2 free-group`，显示 tier/surfaces/max_pages 摘要。
- 上移/下移在策略组卡；此处只显示当前顺序与“首个命中生效”。
- 默认规则：`未命中任何组时按 tier 默认规则兜底；空组/停用组视同不存在。`
- 明确：`策略组是约束层，不是独立实例池。`

### 5.8 高级协议与回落

- 现有 ATG / fallback 控件收进折叠卡，默认折叠。
- 保留所有字段与保存语义。
- 说明：属于落地参数，与路由/实例预算独立。
- 关闭深度模式时不渲染。

### 5.9 状态、保存与响应式

- 配置加载：整页 skeleton；失败错误卡 + 重试，不显示空表单。
- YAML 解析失败：保持现有“无法切换可视化”；错误详情可复制。
- 未保存修改：现有 `onBeforeRouteLeave` 保留；标题旁 `未保存` 点。
- 保存中：按钮 loading；成功后 toast + 重新拉取；失败保留草稿。
- 1024：基础卡 2 列；深度表单单列；渠道表横向滚动。
- 390：全部单列；槽位表/渠道表改卡片；保存按钮底部 sticky。

---

## 6. P4 统计页收尾

只做最小修改：

1. P1 增加两处入口：总请求卡右下、模型概览标题右侧 `查看统计 →`。
2. 去掉与 Topbar 重复的页内 H2；保留一行说明：
   `Token 用量、缓存命中与账号调度状态；账号面板是当前调度状态，不是所选时间范围。`
3. 与全局规范对齐：卡片 `rounded-2xl`、边框 `gray-200/80`、页面间距 24px；缺失字段继续 `—`。
4. 不改 API 字段、不恢复旧 `/stats` JSON 端点、不重写图表、不改趋势断线处理。
5. 若发现实际 bug，只做最小修复并补截图，单独列出。

---

## 7. P5 日志页与后端

### 7.1 页面布局

```text
┌ 日志                                                          实时 ● 已连接 ┐
│ 原始日志流；不是结构化请求记录表。                                           │
├──────────────────────────────────────────────────────────────────────────────┤
│ [Debug] [Info] [Warn] [Error]  [搜索]  [暂停] [清屏] [下载当前] [按时间导出] │
├──────────────────────────────────────────────────────────────────────────────┤
│ 10:36:12.104  INFO   surface    [surface] request=… channel=chat surface=chat │
│ 10:36:12.350  WARN   routing    429 account=… channel=chat switch=1/2         │
│ ...                                                                          │
│                                                     [回到最新 ↓]             │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 7.2 路由与导航

- `/logs`，组件 `web/src/views/LogsView.vue`。
- Sidebar 增加 `日志`，图标 `Terminal`。
- 顺序：控制面板 / 用量统计 / 账号管理 / 系统与模型配置 / 日志。
- `/logs/stream` 必须在 SPA/静态路由前注册，并把 `/logs` 加入 API 前缀排除列表。
- 若 SSE 未通过质量门，隐藏入口并文档暂缓，不留死路由。

### 7.3 工具栏

- 级别 chips：Debug / Info / Warn / Error；默认 Info、Warn、Error 开，Debug 关；显示计数。
- 搜索：客户端过滤 message/logger；300ms debounce；空格分词全部包含。
- 暂停/继续：暂停时 SSE 继续接收并写入缓冲，UI 不追加、不自动滚动；显示 `已暂停 · 缓冲 N 条`；继续时一次性追加。
- 清屏：清空客户端缓冲与 DOM，不断流。
- 下载当前：导出当前过滤后的缓冲为 `.log`，文件名 `aistudio-logs-YYYYMMDD-HHmmss.log`。
- 按时间导出：日期/时间范围选择，导出服务端完整 raw log；跨天/多段打包 ZIP。
- 自动滚动：默认跟随；用户上滚超过 48px 显示 `回到最新` 浮标。

### 7.4 虚拟滚动与渲染上限

- 实时 buffer 最多 5000 条。
- DOM 只渲染视口 + overscan，约 60 条。
- 日志行固定高度，message 单行截断，点击展开单条详情；避免变高破坏虚拟滚动。
- 超过 5000 条丢弃最旧并显示系统行 `已丢弃 N 条旧日志；可用按时间导出查看完整记录`。
- 不使用无限 `v-for` 全量渲染。

### 7.5 日志行

桌面网格：`96px` 时间 / `64px` 级别 / `160px` logger / `1fr` message。

- 时间 `HH:mm:ss.SSS`。
- 级别 badge：DEBUG 灰、INFO 蓝/灰、WARNING 琥珀、ERROR/CRITICAL 玫红。
- logger 截断，hover 全称。
- message 单行截断，点击展开详情。
- 搜索词高亮，Vue 文本插值，不注入 HTML。

高亮规则（左侧 3px 边框 + 浅底）：

| 类型 | 匹配 | 颜色 |
|---|---|---|
| surface 决策 | `[surface]`、`surface=`、`channel=` | brand/emerald |
| 账号切换 | `切换`、`switch`、`account=` | amber |
| 4xx/5xx | `HTTP 4xx/5xx`、`status=4/5` | rose |
| borrow | `borrow`、`借入` | violet |
| 熔断 | `circuit`、`熔断`、`cooldown` | rose/amber |

高亮不改原始 message。

### 7.6 状态

| 状态 | 表现 |
|---|---|
| 连接中 | 控制台骨架 + `正在连接日志流…` |
| 已连接 | 绿点 + `实时`；累计条数 |
| 重连中 | 琥珀条 `连接中断，3s 后重试（第 N 次）`；指数退避至 15s；保留缓冲 |
| 断流/失败 | 玫红条 + 错误详情 + 立即重试；已收日志仍可查看/下载 |
| 401 | 清 token，跳登录并带 redirect |
| 空 | `暂无日志。触发一次请求或切换账号后这里会出现记录。` |
| 过滤空 | `没有匹配当前过滤条件的日志` + 清除过滤 |
| 暂停 | 顶部状态条 + 缓冲计数 |
| 缓冲丢弃 | 黄色系统行 + 导出提示 |

### 7.7 P1 联动

- 429：`/logs?levels=warn,error&q=429`
- 错误：`/logs?levels=warn,error&q=HTTP`
- 进入页面读取 `levels` 与 `q` 初始化 chips/搜索框，显示 `来自控制面板的过滤` chip，可清除。
- 先完成只读流 + 过滤，再做数字联动；不做请求级记录表。

### 7.8 响应式

- 1440：控制台高度 `calc(100vh - 18rem)`，最小 420px，最大 800px；工具栏单行。
- 1024：隐藏 logger 列；工具栏两行。
- 390：级别 chips 横向滚动；搜索全宽；操作按钮图标+短标签；日志行两行（meta + message）；无横向滚动。
- `overscroll-behavior: contain`。

### 7.9 后端日志广播与留档

新增 `src/aistudio_api/infrastructure/utils/log_broadcast.py`：

- 线程安全 ring buffer 2000 条。
- 订阅者集合；每订阅者 `asyncio.Queue(maxsize=500)`。
- 单调递增 id。
- 队列满丢弃最旧并向该订阅者发 dropped 计数，绝不阻塞业务日志。
- 同步日志线程通过 `loop.call_soon_threadsafe` 投递。
- 对 Authorization / Cookie / API key / Bearer / SAPISID 脱敏。

新增 `BroadcastHandler(logging.Handler)`：

- 在 `setup_logging()` 挂到 root，级别 DEBUG。
- 只读取 `record.getMessage()`、level、logger、时间；不做颜色格式化。
- handler 自身不写日志，避免递归。

新增日志文件 sink：

- `data/logs/aistudio-YYYYMMDD.log`，按天。
- 保留 7 天，启动时和跨天时清理。
- 纯文本：`时间 level logger message`。
- 与 SSE 使用同一脱敏后的 message。
- 不做实时文件 tail；文件只用于按时间导出/留档。

新增 `GET /logs/stream`：

- 挂在 protected router，沿用 `require_web_auth`。
- 查询：`level=DEBUG|INFO|WARNING|ERROR`，`last_id` 可选；支持 `Last-Event-ID`。
- `StreamingResponse`，`text/event-stream; charset=utf-8`。
- 响应头：`Cache-Control: no-cache, no-transform`、`X-Accel-Buffering: no`、`Connection: keep-alive`。
- 连接时回放 ring 最近 100 条（按 level 过滤），再实时。
- 心跳每 15s 注释 `: ping`。
- 事件：`event: log`、`id: <seq>`、`data: <JSON>`；字段 `id, ts, level, logger, message`。
- 重连带 last_id 补发；超出 ring 发 `event: gap`。
- 最大并发订阅者 8；超出 503。
- 不传输请求体/响应体。

新增 `GET /logs/export`：

- 参数 `start`、`end`（ISO8601），可重复 `range` 段或 `days`。
- 只允许最近 7 天；单次范围上限 7 天。
- 单段返回 `.log`；多段/跨天返回 ZIP。
- 文件名 `aistudio-logs-YYYYMMDD-HHmmss.log` / `.zip`。
- 受保护路由；导出内容为同一脱敏格式。
- 按行读取归档文件，流式返回，不一次性读入内存。

### 7.10 前端 SSE 连接

- 不使用 `EventSource`，因为无法设置 Authorization 头。
- 使用 `fetch()` + `ReadableStream` 读取 SSE，带 `Authorization: Bearer <web token>`。
- 手动解析 `event:` / `id:` / `data:` / 空行。
- 401 走现有登录跳转。
- 重连带 `last_id`；指数退避 3/6/12/15s。
- 页面卸载/路由离开时 `AbortController.abort()`。

### 7.11 UI 视觉

- 工具栏白卡、`rounded-2xl`、`border-gray-200/80`。
- 控制台 `bg-gray-950`、`text-gray-100`、`rounded-2xl`、内边距 12–16px。
- 行 hover `bg-white/5`；高亮左侧边框 3px。
- 状态条高度 36px，圆角 10px。
- 等宽字体 `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`。

---

## 8. TDD 与质量门

### 8.1 后端 TDD

先写失败测试，再实现：

- 日志 ring buffer：容量、序列号、丢弃、订阅者队列溢出。
- level 过滤、last_id 回放、gap。
- SSE 生成器：事件格式、心跳、断开清理。
- 日志脱敏：Authorization / Cookie / API key / Bearer / SAPISID。
- 7 天文件归档：按天写入、跨天切换、过期清理。
- `/logs/export`：单段、跨天 ZIP、非法范围、7 天限制、鉴权。
- 批量导入：多文件内容、单 auth、数组、bundle、坏文件逐项报告。
- 槽位/配置写入：重复槽位校验、保留其他行。
- 账号 routing 更新与回滚。

### 8.2 前端验证

- `bun run type-check`
- `bun run lint`
- `bun run build`
- 所有按钮用 Playwright/agent-browser 真实点击，核对：
  - 发起正确网络请求
  - loading 状态
  - 成功 toast/状态更新
  - 失败回滚/错误提示
  - 禁用条件正确
- 不只读 Vue 源码宣称完成。

### 8.3 后端质量门

```text
uv run pytest tests/unit -q
uv run ruff check .
uv run ruff format --check .
bun x pyright src tests
```

---

## 9. 视觉验收与证据

五页：

- P1 `/`
- P2 `/accounts`
- P3 `/settings`
- P4 `/stats`
- P5 `/logs`

视口：

```text
1440×900
1024×768
390×844
```

状态：

- P1：正常 / 空 / 加载 / stats 错误 / 实例详情展开 / 格式 tab 切换
- P2：单模式 / 多模式 / 空 / 加载 / 错误 / 导入弹窗 / 拖拽覆盖层
- P3：深度关闭 / 深度开启 / 加载 / 配置错误
- P4：正常 / 空 / 加载 / 错误（重拍）
- P5：实时 / 暂停 / 重连 / 断流 / 空 / 过滤空 / 移动端

证据目录：

```text
docs/dev/evidence/wp7-20261020/
```

包含：

- 截图
- README：测试环境、账号/渠道/槽位配置、状态复现方式
- console error 记录
- network 失败记录
- 质量门输出摘要

不使用静态截图代替实际页面；不使用假 API 数据冒充真实数据。空/错误态用隔离测试实例或受控停止服务产生，不污染生产数据。

---

## 10. 实施阶段与提交

### 阶段 1：共享基础 + P1

- 抽取通用 UI 组件。
- P1 布局、Context 列表、实例卡/详情、请求卡、模型表、格式选项卡。
- 三档截图 + 状态截图。
- 质量门相关前端检查。
- commit：`feat(ui): 完成 WP7 控制面板改版`

### 阶段 2：P2

- 单/多模式行、矩阵、槽位徽章、批量导入、拖拽导入、tier 刷新。
- 截图与交互验证。
- commit：`feat(ui): 完成 WP7 账号管理多模式与批量导入`

### 阶段 3：P3

- 深度开关、深度区上移、渠道注册表、策略组、槽位表、路由安全、分组编排、高级协议。
- 文案清理。
- commit：`feat(ui): 完成 WP7 系统配置深度分区`

### 阶段 4：P5 + P4

- 后端日志广播、7 天归档、SSE、导出、测试。
- LogsView、虚拟滚动、过滤/暂停/下载/联动。
- P4 入口与视觉收尾。
- commit：`feat(logs): 完成 WP7 日志页与日志留档`

### 阶段 5：收尾

- 全站文案、响应式、空/加载/错误/断流态。
- 全部质量门。
- 重建 `src/aistudio_api/static/`。
- 更新 README / AGENT.md。
- 三档截图与证据 README。
- commit：`chore(wp7): 完成 UI 收尾与验收证据`

最终向用户报告测试结果，由用户决定是否 push 或继续修复。

---

## 11. 风险与不改范围

### 风险

1. P1 实例语义误读：页面数不是并发；UI 必须写“在途请求”“页面实例”。
2. P2 三态数据来源：资格按账号级推导，不含策略组运行时收窄；命中按当前 Context/在途解释，tooltip 必须写清。
3. P2 槽位写入：走 `PUT /config/features` 全量 slots，需保留其他行、note、顺序；失败回滚。
4. P3 整份 YAML 保存：保留未保存离开确认，不自动保存。
5. P3 旧字段兼容：UI 不写 `max_page_instances`，读取时兼容旧 YAML。
6. P5 日志量：ring/subscriber 会丢弃；必须显示丢弃提示，不能假装完整。
7. P5 认证：不用 EventSource，避免 token 放 URL。
8. P5 反向代理：`X-Accel-Buffering: no` 与 no-transform，避免假死。
9. 日志归档磁盘：7 天保留，启动清理；导出限制 7 天。
10. 移动端表格：P1 模型表、P2 矩阵、P3 槽位表必须在 390 改卡片。
11. 构建产物：改前端后必须 `bun run build`，否则 `static/` 与源码不一致。

### 不改范围

- 不重写 WP5 v3 实例池、路由管线、账套/冷却逻辑。
- 不改 WP10 保活、WP11 计数链路、WP8 协议转换。
- 不改 `/api/stats` 字段、stats schema、趋势图断线策略。
- 不恢复旧 `/stats` JSON 端点。
- 不把 `paid` / `access_tier` 暴露到 `/v1beta/models` 或 `/v1/models`。
- 不做 API key/租户/中转站管理，不新增请求级日志表。
- 不做本地 tokenizer 估算。
- 不引入深色主题；日志控制台深色仅限代码/控制台区域。
- 不把模型名 `pro` 当账号凭证 `pro`；不把同页并发显示成多实例。
- 不修改生产 `data/stats.json`；证据使用临时 `AISTUDIO_STATS_FILE` 或隔离实例。

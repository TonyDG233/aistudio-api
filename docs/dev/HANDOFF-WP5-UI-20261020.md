# WP7 UI 统一修改与收尾 · 接续提示词

你是接续本仓库最后阶段 UI 工作的新 agent。请先完整阅读本文，再开始任何改动。

## 0. 仓库与基线

```text
仓库：D:\LLM\self_programming\aistudio-api
分支：p1-ark-merge
```

关键提交：

```text
1adf3c2 feat(stats): 完成 WP6 独立用量统计页
1980ada fix(routing): 拆分 Paid 模型与账号 pro 凭证判定
```

WP6 统计页已完成并提交；Paid 模型路由适配已完成并提交。当前阶段只做 **WP7：UI 统一修改、收尾、文档与质量门**，除非 P5 日志页确需后端 SSE，否则不要重写后端。

开工前先执行：

```text
git status
git log --oneline -12
```

不要先写代码。先输出一份“当前 UI 与 UI 设计稿的差异清单 + 实施顺序”，再动手。

## 1. 必读文档

按顺序读：

```text
AGENT.md
README.md
docs/dev/PLAN-ark-merge-20261017.md 的 WP7 / 收尾段
docs/dev/UI-CHANNEL-DESIGN-20261018.md 全文（P1–P5）
docs/dev/WP5-V3-DESIGN-20261019.md
docs/dev/WP5-V3-IMPLEMENTATION-PLAN-20261019.md
docs/dev/WP6-STATS-PAGE-DESIGN-20261020.md
docs/dev/evidence/wp6-20261020/README.md（若存在）
docs/dev/evidence/ui-p1-20261020/
```

冲突时以 `WP5-V3-DESIGN-20261019.md` 与 `UI-CHANNEL-DESIGN-20261018.md` 的业务语义为准；不要重新定义 Context、Page、max_pages、切换预算或模型前缀。

## 2. 已完成，不要重做

- WP5 v3 统一 Context 实例池、策略组、路由管线。
- WP6 独立统计页 `/stats`：
  - `/api/stats`
  - `/api/stats/timeseries`
  - stats schema v2（`models` + `daily`）
  - client/attempt 两层计数
  - cache/reasoning/count_tokens/usage_missing
  - 模型名归一、90 天日聚合
  - 三档截图与空/加载/错误/缺字段证据
- Paid 模型路由：
  - `model_access.py` Paid 缓存
  - ListModels 快照 `data/models_cache.json`
  - 启动预热 + 每 600 秒刷新
  - Paid 模型要求 pro/paid 凭证且限制 chat/pg
  - 模型名含 `pro` 不再影响账号资格
- P1/P2/P3 已有页面和部分 UI；P4 统计页已完成。
- `/surface/instances` 新旧响应 shape 兼容修复已完成。

不要重新设计 `/stats`，不要改 stats API 字段，不要恢复旧 `/stats` JSON 端点，不要把 Paid 字段重新塞进 `/v1beta/models` 或 `/v1/models`。

## 3. 本阶段范围

只做以下内容：

1. P1 控制面板完成 UI 稿要求。
2. P2 账号管理完成单/多模式行、渠道矩阵、槽位/三态徽章。
3. P3 系统与模型配置完成“显示深度功能”和分区收编。
4. P4 统计页只做入口、文案、视觉一致性收尾；不重写。
5. P5 日志页：新建 `/logs` 与后端 SSE 日志流；如后端不存在，按最小只读实现，不能留死路由。
6. 全局视觉一致性、移动端、错误/空/加载态、文档、质量门、截图证据。

非目标：

- 不重写 WP5 v3 实例池。
- 不改 WP10 保活、WP11 计数链路、WP8 协议转换。
- 不做 API key/租户/中转站管理。
- 不做本地 tokenizer 估算。
- 不引入深色主题（现有系统只有浅色；除非用户另行明确要求）。
- 不做请求级日志表；P5 只做 raw log 流。
- 不把“同页并发”显示成多实例。
- 不把模型名 `pro` 当账号凭证 `pro`。

## 4. 逐页任务

### P1 控制面板 `/`

按 UI 稿：

- 左列纵向跨两行：当前激活账号多行列表（多实例时逐行；单实例一行）。
- 右区上排：
  - 可用账号 `x/y`
  - 实例数卡：大数字 `当前 / 计划`
  - 卡下灰色小字：`chat 实际/计划 · build 实际/计划`，只显示计划 > 0 的渠道，最多 2 个，其余 `+N`；全部 0 显示 `—`
  - 小“详情”或 chevron 展开 `/surface/instances`，不要整卡可点
- 右区下排：总请求数；429 作为子指标并入，不要单独一张大卡。
- 格式选项卡：Gemini / OpenAI Chat / OpenAI Responses / Claude / Token 计数；每 tab 给 endpoint、Base URL、鉴权头、curl + SDK 示例、复制按钮、适用客户端；地址用 `window.location.origin`。
- 未启用格式隐藏或置灰；不得展示不存在端点；API key 只用占位符。
- P1 不展开 Token 明细；加“查看统计”入口跳到 `/stats`。
- 移动端模型/账号统计不得出现竖排表头；横向滚动或卡片化。
- 模型名去掉 `models/` 前缀。

### P2 账号管理 `/accounts`

- 保留 Cookie 凭据组 → 子账号结构。
- 单模式：账号行 + `挂载：pg 页 ✓ / build 页 ✓`。
- 多模式：渠道矩阵展开区（账号×渠道逐格开关）+ 槽位绑定徽章 + 资格/挂载/命中三态。
- 激活按钮：单模式“设为默认活跃”；多模式“设为默认活跃”+“绑定到槽位 N”。
- 导入单个旧项目 auth JSON、accounts bundle、目录路径；导入后 tier/expired 正常。
- 不得在 UI 层重写地域 Cookie 或伪造账号状态。

### P3 系统与模型配置 `/settings`

- 顶部“显示深度功能”总开关，默认关。
- 关闭：基础卡（基础设置、运行与调度、基础功能开关）。
- 开启：渠道注册表、策略组、槽位表、路由安全、分组编排。
- “实时实例”不再放 P3；入口归 P1。
- 文案与新语义对齐：max_pages 不是并发上限；Context 与 Page 分开；策略组是约束层。
- 使用现有 `FeatureGroupsEditor`、`VisualConfigEditor`、`FieldHelpTip` 风格；不要另造一套表单。

### P4 统计页 `/stats`

已完成，不要重写。只做：

- P1 的“查看统计”入口。
- 与全局导航/字号/间距一致性。
- 如发现 stats 页 bug，最小修复并补截图；不要改 API 契约。
- 保持三档截图证据与设计稿一致。

### P5 日志 `/logs`

UI 稿：

- 新建 `LogsView.vue`，Sidebar 入口。
- `GET /logs/stream` SSE：级别过滤 debug/info/warn/error；暂停/继续/清屏/下载 `.log`。
- 后端从 logging 队列广播，不做文件 tail。
- 高亮：surface 决策、账号切换、4xx/5xx、borrow、熔断。
- P1 错误/429 数字可跳转并自动过滤（先做只读流+过滤，再做联动）。
- 如果当前没有日志广播后端：实现最小内存队列 + SSE；不要用假日志或前端轮询文件。
- raw log 页不要塞入结构化请求记录表；请求级记录属于未来中转站模式。

## 5. 语义冻结

### Context / Page

- Context = 独立 cookie jar/账号隔离，计入 `max_contexts`。
- Page = Context 内 surface 页面，计入 `max_pages`。
- 同一页面可承载多个在途请求；页面忙不是 429 理由。
- `live_instances` 不是并发数；不要用它冒充并发。

### 路由与账号

- 组是账号级约束层，不是请求级单组。
- 换号方向是实例切换，不是 API 请求方向。
- 429 显示“已切换/正在切换”，只有候选/预算耗尽才显示本地错误。
- 模型列表：单模式裸 ID；多模式仅 `pg/`、`build/`；`chat/`、`playground/` 只作输入别名；`official/` 未实现前不列。

### Paid 与 pro 凭证

- `account_is_paid(tier)`：pro/ultra/plus/paid 凭证。
- `model_requires_paid_account(model)`：只看 discovery 的 `paid` 元数据。
- `is_chat_only_model(model)`：只表示 Paid 模型只能 chat。
- 模型名里的 `pro` 不得决定账号资格或 surface。
- 不要把 `paid`/`access_tier` 暴露到模型拉取 API。

### 统计

- client 层：`client_requests/client_success/client_errors/client_aborted`。
- upstream 层：`attempts/attempt_rate_limited/attempt_errors`。
- count_tokens：`count_requests/count_success/count_errors/count_input_tokens`，不计入生成 token。
- cache read 是输入子集，reasoning 是输出子集；总量不等于卡片相加。
- 旧记录缺字段显示 `—`，不是 0。
- 账号面板是当前调度状态，不是所选时间范围。

## 6. 已知缺口/风险

- P5 日志后端目前没有 `/logs/stream`；需要新增最小 SSE。
- P1 格式选项卡、实例数卡细节可能尚未完成；先审计再改。
- P2 多模式渠道矩阵/槽位徽章可能只有部分实现。
- P3 “显示深度功能”可能未完成；不要直接删现有深度区，先加总开关和折叠。
- `src/aistudio_api/static/` 是已跟踪构建产物；改前端后必须重新 `bun run build`。
- `web/tsconfig.tsbuildinfo` 也会变化；提交前确认仓库惯例是否纳入。
- 统计 fixture/临时服务不要写入生产 `data/stats.json`；用临时 `AISTUDIO_STATS_FILE`。
- 不得只读 Vue 源码就宣称 UI 完成；必须浏览器截图。

## 7. 质量门

后端：

```text
uv run pytest tests/unit -q
uv run ruff check .
uv run ruff format --check .
bun x pyright src tests
```

前端：

```text
cd web
bun run type-check
bun run lint
bun run build
```

## 8. 视觉验收

用 Playwright MCP 或 agent-browser 打开真实页面，至少：

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

- 正常
- 空数据
- 加载态
- 后端错误/断流
- 移动端

记录 console error 与网络失败。证据写入：

```text
docs/dev/evidence/wp7-20261020/
```

不要用静态截图代替实际页面；不要用假 API 数据冒充真实数据。

## 9. 工作顺序

1. 输出差异清单与实施计划（先不写代码）。
2. P1 布局/格式卡/实例数卡/统计入口。
3. P2 单多模式行与渠道矩阵。
4. P3 深度开关与分区。
5. 如范围允许，P5 日志页 + 最小 SSE；否则明确暂缓并隐藏入口。
6. 全局文案/响应式/错误态收尾。
7. 跑质量门、三档截图、回归。
8. 更新 README/AGENT.md（如新增页面/API）。
9. 向用户展示结果；**明确同意后才 commit，中文 commit，不 push**。

## 10. 第一轮回复格式

先给：

1. 当前 UI 与 UI 稿的差异表（页面/项/现状/需要改什么）；
2. 本阶段实施顺序；
3. 是否有 P5 后端 SSE 缺口及处理方案；
4. 风险与不改范围。

不要直接贴代码 diff，不要先改 `web/` 或后端。

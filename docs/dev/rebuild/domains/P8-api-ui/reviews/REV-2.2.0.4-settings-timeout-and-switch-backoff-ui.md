# REV-2.2.0.4 — 设置页超时同步（300s）、切号退避等待（`switch_backoff_sec`）前后端打通与移动端防挤压审查

- 审查类型：独立只读 UI + 全栈 Reviewer（代码契约 + 门禁 + `--ui-debug` 离线沙盒 Playwright 多分辨率实测）
- 审查时间：2026-11-14
- 审查范围（相对路径）：
  - `defaults/config.yaml`
  - `defaults/config.schema.json`
  - `src/aistudio_api/infrastructure/gateway/model_defaults.py`
  - `src/aistudio_api/infrastructure/waa/engine_runtime.py`
  - `src/aistudio_api/application/routing_pipeline.py`
  - `web/src/components/settings/SchedulingCard.vue`
  - `web/src/components/config/schema.ts`
  - `tests/unit/test_routing_pipeline.py`
  - `tests/unit/test_engine_runtime_lifecycle.py`
  - `tests/unit/test_features_config.py`
  - `web/tests/configTooltip.test.ts`
- 只读声明：本次审查**未修改任何业务代码、测试或配置文件**。唯一写入为本报告、两张实测截图与两份域索引文档。

## 1. 结论

**PASS**

- 超时链路（UI `请求超时 (秒)` → `signer.request_timeout_s` + `transport.read_timeout_s` → `SignerClient` / `BrowserlessSession`）与切号退避链路（UI `切换账号退避等待` → `features.routing.switch_backoff_sec` → `RoutingPipeline._switch_backoff`）前后端完全对齐，无脱节。
- 5 个「请求频率限制冷却与重试（秒）」选项在 `1440x900` / `1024x900` / `768x900` / `375x812` 下分别为 **5 / 3 / 2 / 1** 列，`375x812` 下每项独占一行（301px 卡片 / 277px 输入框），无横向滚动条、无文字截断与重叠挤压。
- 死框「鉴权失败冷却」已从 UI 移除，第 5 项为「切换账号退避等待」默认 `4`。
- 全部门禁实跑通过；P0/P1 为空，2 项 P2 清理项已于收口施工中关闭（见 §4 关闭证据、§9 复核门禁），另余 2 项 Q/N 观察项。

## 2. 门禁实跑结果

| 门禁 | 命令 | 结果 |
|---|---|---|
| Ruff | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync ruff check .` | `All checks passed!` |
| Pyright | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync pyright` | `0 errors, 0 warnings, 0 informations` |
| Pytest | `uv run pytest tests/unit/test_routing_pipeline.py tests/unit/test_engine_runtime_lifecycle.py tests/unit/test_features_config.py -q` | `120 passed in 1.29s` |
| 前端 Lint / 类型 / 测试 | `workdir=web`: `bun run lint && bun run type-check && bun test` | eslint `--max-warnings 0` 通过；`vue-tsc -b` 通过；`bun test` **96 pass / 0 fail**（11 文件，823 expect） |
| Diff 卫生 | `git diff --check` | exit 0，无空白错误 |

## 3. 契约对齐矩阵（逐层核对）

| 层 | 证据 | 结论 |
|---|---|---|
| 分发默认配置 | `defaults/config.yaml:120-125`（注释声明 0..30、±25% 抖动、0 不等待；`switch_backoff_sec: 4.0`） | 对齐 |
| 结构 Schema | `defaults/config.schema.json:312-317`（`type: number`、`0..30`、`default: 4.0`） | 对齐 |
| 后端常量与归一 | `model_defaults.py:55-57`（4.0 / 0.0 / 30.0）、`model_defaults.py:1408-1439`（布尔/非数值/NaN 回退 4.0，越界夹到 0..30） | 对齐 |
| 非关键回退补齐 | `model_defaults.py:244-246`（启动校验时把该键补进 `features.routing`，旧配置自动迁移写回） | 对齐 |
| 默认值表 | `model_defaults.py:1442-1447`（`_routing_defaults()` 三个键同源） | 对齐 |
| 读取入口 | `model_defaults.py:1946-1975`，关键在 `1972-1974`（`switch_backoff_sec` 与 `max_switches`/`retry_same_delay_sec` 同一次读取返回） | 对齐 |
| 运行期解析 | `routing_pipeline.py:665-694`（显式构造器 > 注入 provider 键 > 环境变量 > 配置 > 生产默认 3.0..5.0）、`routing_pipeline.py:345-349`（±25% 抖动展开）、`routing_pipeline.py:1302-1318`（每次切号前重新解析，支持热更新） | 对齐 |
| 退避触发点 | `routing_pipeline.py:1207-1217`（仅在 `429` / `verification_required` 且**先退避后改绑**，`record_budget=False` 的预算耗尽预热同样退避） | 对齐 |
| 超时采集 | `engine_runtime.py:45-56`（`_float` 容错）、`engine_runtime.py:155-174`（`SignerClient` 的 `request_timeout_s` 与 `read_timeout_s=max(request, read)`）、`engine_runtime.py:238-267`（`BrowserlessSession.read_timeout_s` 同样取二者最大值） | 对齐 |
| UI 写入 | `SchedulingCard.vue:127-136`（`setRequestTimeout` 同时写 `signer.request_timeout_s` 与 `transport.read_timeout_s`，1..600）、`SchedulingCard.vue:138-145`（`setSwitchBackoff` 夹到 0..30） | 对齐 |
| UI 默认值展示 | `SchedulingCard.vue:810-823`（`请求超时 (秒)` 回退 300）、`SchedulingCard.vue:1125-1151`（第 5 项「切换账号退避等待」，`min=0 max=30 step=0.5`，回退 4） | 对齐 |
| Schema 文案 | `schema.ts:437-447`、`schema.ts:553-563`（均 300，且说明与请求超时同步）、`schema.ts:748-758`（新增退避词条，`recommendedValue: 4`） | 对齐 |
| 测试覆盖 | `test_routing_pipeline.py:1629-1715`（env/默认/provider 优先级、±25% 抖动、`_switch_backoff` 实睡值）、`test_engine_runtime_lifecycle.py:81-155`（默认 300/300 与 read≥request 三组参数）、`test_features_config.py:104-140`（默认 4.0、夹取、非法回退、`validate_config_mapping` 补齐）、`configTooltip.test.ts:33-34`（schema 推荐值与分发默认一致性） | 对齐 |

## 4. 问题清单

### P0（阻断）
无。

### P1（严重）
无。

### P2（不阻塞交付，建议后续维护清理；本报告发布时 2 项已全部关闭）

- **P2-1 残留死词条元数据（已关闭）**：`web/src/components/config/schema.ts:818-828` 曾保留 `rotator.cooldowns.auth_error`（标题「鉴权失败冷却」）。UI 输入框已移除（`SchedulingCard.vue:1056-1152` 仅 5 项），该词条无任何渲染入口，`web/tests/configTooltip.test.ts:42` 仍在给它断言推荐值，会让「死框已移除」的语义在字典层继续留存。功能无影响（仅字典元数据），建议下次清理时一并删除词条与该断言。
  - **关闭证据**：已删除 `web/src/components/config/schema.ts` 中该词条（现 `rate_limited_drip_extended` 结束于 `schema.ts:807-817`，`paid_model_rules` 紧随于 `schema.ts:818`），并移除 `web/tests/configTooltip.test.ts` 中对应断言（保留 `features.routing.switch_backoff_sec` 断言于 `configTooltip.test.ts:33-34`）。`bun run lint` / `bun run type-check` / `bun test`（96 pass / 0 fail，819 expect）/ `bun run build` 全绿；重建后的 `src/aistudio_api/static` 全量检索 `auth_error` 为 0 命中。
- **P2-2 注入 provider 但缺键时的隐式降级（已关闭）**：`routing_pipeline.py:677-694` 曾在 `routing_settings_provider` 存在却未返回 `switch_backoff_sec` 且环境变量未设置时直接返回生产默认 `(3.0, 5.0)`，不会回读 `features.routing.switch_backoff_sec`。当前生产装配（`src/aistudio_api/api/app.py:557-564`）未传 provider，因此**现网行为正确**（走 `get_configured_routing_settings()` 读实时配置）；此项是未来若为 `max_switches` 单独接入 provider 时的隐患，建议在 `_resolve_switch_backoff_range` 缺少该键时继续回落到 `get_configured_routing_settings()`。
  - **关闭证据**：`routing_pipeline.py:689-698` 现于 `settings is None or "switch_backoff_sec" not in settings` 时改读 `get_configured_routing_settings()`，仅当实时配置仍无该键才回退 `DEFAULT_SWITCH_BACKOFF_RANGE`；新增单测 `tests/unit/test_routing_pipeline.py:1678-1693`（provider 仅返回 `max_switches`、env 未设置、配置为 `2.0` → 断言 `(1.5, 2.5)`）。`pytest tests/unit/test_routing_pipeline.py` **60 passed**。

### Q（待确认，非缺陷）

- **Q-1 数值形态回写**：沙盒中保存后 `retry_same_delay_sec` 由 `2.0` 回写为 `2`（`GET /config` 的 `yaml_content` 观测）。YAML 语义等价，`model_defaults._coerce_retry_same_delay` 统一转 float，无功能影响；如需保持书写风格一致可在 UI 侧保留一位小数。
- **Q-2 清空输入框的语义**：`asNumber('')` 返回 `0`（`web/src/components/config/configDraft.ts:48-51`），因此清空「请求超时」会被夹成 `1` 秒、清空「切换账号退避等待」会变成 `0`（即关闭退避）。与既有 `ConfigInput` 数值项行为一致，且 0 秒是文档化的合法语义，建议仅知会用户，不视为回归。

### N（记录，无动作）

- **N-1** 第 5 项使用原生 `<input>`（`SchedulingCard.vue:1135-1143`）而非 `ConfigInput`，用于承载行内「0-30 秒」提示；实测与其余 4 项基线对齐、无高度错位，可接受。
- **N-2** 审查过程中我误用了两个不存在的路径 `GET /api/system/config`、`GET /api/config`，服务端如返回 404（见 §6 日志）。真实端点为 `GET /config`（`routes_system.py:679`），两次 404 属于审查者探测失误，不是产品缺陷。

## 5. 四分辨率 bounding box 实测

测量对象为 `SchedulingCard.vue:1056` 的 5 个卡片容器（`getBoundingClientRect`，单位 CSS px），同时在每个视口断言 `documentElement.scrollWidth <= clientWidth`（无横向滚动）。

| 视口 | 列数 × 行数 | 每项宽 | 每项坐标（x / y） | 横向溢出 | 文档横向滚动 |
|---|---|---|---|---|---|
| `1440x900` | 5 列 × 1 行 | 204 | 313/504、529/504、746/504、962/504、1179/504 | 无（`scrollWidth` = `clientWidth`） | 无 |
| `1024x900` | 3 列 × 2 行 | 210 | 313/640、535/640、757/640、313/708、535/708 | 无 | 无 |
| `768x900` | 2 列 × 3 行 | 329 | 49/700、390/700、49/768、390/768、49/836 | 无 | 无 |
| `375x812` | **1 列 × 5 行** | **301** | 37/2184、37/2252、37/2320、37/2388、37/2456 | 无（输入框宽 277） | 无 |

补充断言：

- 5 项标签文字 `scrollWidth == clientWidth`，`labelOverflow=false`，无截断；`375x812` 下「切换账号退避等待」与右侧「0-30 秒」提示同排不重叠。
- 引擎 4 个参数框：`375x812` 下 1 列（宽度 247，`y` 依次 -1410/-1342/-1274/-1206，均为单列）；`1024x900` 下为 4 列（宽度 122，标签单行无溢出）。即 `xl:grid-cols-4 → lg:grid-cols-4`（`SchedulingCard.vue:761`）未在 1024 造成挤压。
- 视觉结论：两张截图经视觉复核，卡片间距均匀、标题与输入框左对齐、帮助图标位于右上角且不与标签碰撞；移动端为清晰的单列纵向堆叠，**未出现 5 个选项并排挤压**。

截图：

- `docs/dev/rebuild/domains/P8-api-ui/reviews/assets/rev-2.2.0.4-settings-desktop-1440.png`（1440x900，5 项并排一行，第 5 项值 `5` 已保存）
- `docs/dev/rebuild/domains/P8-api-ui/reviews/assets/rev-2.2.0.4-settings-mobile-375.png`（375x812，5 项单列独占一行）

## 6. API 保存联动实测（`--ui-debug` 离线沙盒）

沙盒启动：`uv run python main.py server --port 18089 --ui-debug`（`ready.port=18089`，纯内存/临时目录，`data_dir=<temp>/aistudio-ui-debug-*`，未启动真实浏览器、未访问外网）。

1. 引擎卡切换为「无浏览器 WAA（轻量沙盒）」后，4 个参数框读数：`最大工作池数量=8`、`闲置回收时间 (秒)=900`、`启动超时 (秒)=30`、**`请求超时 (秒)=300`**。
2. 展开「高级调度、保活与软配额参数」→「请求频率限制冷却与重试（秒）」5 项读数：`短时限流冷却=60`、`持续限流冷却=300`、`延长冷却=600`、`同账号重试等待=2`、**`切换账号退避等待=4`**；**无「鉴权失败冷却」输入框**。
3. 修改 `请求超时 (秒)` → `360`，点击「保存并切换引擎」（沙盒模拟切换，日志 `配置已局部更新: engine`）：
   - `GET /config` → `engine_settings.browserless.signer.request_timeout_s = 360`、`engine_settings.browserless.transport.read_timeout_s = 360`、`engine = browserless`。
4. 修改 `切换账号退避等待` → `5`，点击「保存调度配置」（日志 `配置已局部更新: features, instances, max_contexts, paid_model_rules, rotator, tier_quotas`）：
   - `GET /config` 的 `yaml_content` 中 `features.routing.switch_backoff_sec: 5`（保存前为 `4.0`）；
   - 同一次读取中 `engine_settings` 仍为 `browserless` 且 `360 / 360`，说明调度保存未回退引擎段。
5. 回读热更新能力：`get_configured_routing_settings()` 与 `write_config_text()`（`model_defaults.py:1556-1574`，写后刷新缓存）保证保存即生效；`routing_pipeline.py:1302-1318` 每次切号前重新解析，无需重启。

沙盒日志中的两行 404（`GET /api/system/config`、`GET /api/config`）为审查者探测错误路径所致，属审查副作用；其余请求均为 200，无 warning/error。

## 7. 沙盒收尾确认

- `playwright_browser_close` 已关闭浏览器（无残留标签页）。
- 本次共启动两次沙盒（首次主流程实测、第二次补测 1024 引擎 4 列布局），两次均以 `background_process action="stop"` 停止，进程 `28896` / `26628` 均不存在。
- `Get-NetTCPConnection -LocalPort 18089 -State Listen` → **`PORT-FREE`**（端口已释放）。
- `git status --porcelain` 显示业务代码/测试/配置的修改集合与审查开始时一致（未由本次审查产生新增改动），仅新增本报告与两张截图。

## 8. 交付建议

P0/P1 为空、门禁全绿、UI 四分辨率与 API 联动实测均通过，**建议主控 Integrator 按 `AGENTS.md §10.3` 以 `REV-2.2.0.4` 归档后直接提交并推送**；P2-1 / P2-2 已在本次收口施工中关闭（见 §4 关闭证据与 §9 复核门禁），Q-1 / Q-2 仍为后续观察项。

## 9. P2 收口复核门禁（2026-11-14 收口 Worker 实跑）

| 门禁 | 命令 | 结果 |
|---|---|---|
| Ruff | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync ruff check .` | `All checks passed!` |
| Pyright | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run --no-sync pyright` | `0 errors, 0 warnings, 0 informations` |
| Pytest（路由） | `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest tests/unit/test_routing_pipeline.py -q` | `60 passed in 0.61s` |
| 前端全量 | `workdir=web`: `bun run lint && bun run type-check && bun test && bun run build` | eslint `--max-warnings 0` 通过；`vue-tsc -b` 通过；`bun test` **96 pass / 0 fail**（11 文件，819 expect）；`vite build` 成功（1800 modules，4.65s） |
| Diff 卫生 | `git diff --check` | exit 0，无空白错误 |
| EOL 卫生 | 逐字节检查上述源码/测试/报告/`static/index.html` | 全部 CRLF=0，纯 LF |

改动清单（仅白名单文件）：`web/src/components/config/schema.ts`（删 11 行词条）、`web/tests/configTooltip.test.ts`（删 1 行断言）、`src/aistudio_api/application/routing_pipeline.py:665-699`（缺键回读实时配置）、`tests/unit/test_routing_pipeline.py:1678-1693`（新增 P2-2 单测）、`src/aistudio_api/static/**`（`bun run build` 重建产物，`index.html` 已归一 LF）。

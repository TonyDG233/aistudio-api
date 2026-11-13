# REV-2.1.6-ui-polish-mobile — 前端 UI 极简审美对齐与 375px 移动端防挤压审查（R2 复审）

- **审查对象**：`2.1.6` 前端 UI 极简审美对齐与移动端防挤压整改（R1 8 个 + R2 6 个共 14 个 `web/src/**/*.vue` 源文件 + `web/tests/dashboardStatsUi.test.ts` + 7 个 `src/aistudio_api/static/assets/*` 构建产物 + 6 张多分辨率截图）
- **工作区**：`D:\LLM\self_programming\aistudio-api`
- **基线**：`HEAD = a3f3931`（改动均为未提交工作区增量）
- **审查方式**：R1 + R2 独立只读静态代码审查 + `--ui-debug` 离线沙盒（`127.0.0.1:18097`）Playwright 多视口 DOM 实测（`1440x900` / `1024x900` / `768x900` / `375x812`）+ 前端全量门禁实跑。
- **只读声明**：除写入本报告 `REV-2.1.6-ui-polish-mobile.md`、更新 `docs/dev/rebuild/domains/P8-api-ui/index.md` 与 `docs/changelog.md` 外，未修改任何业务代码、配置或测试文件；未执行 `git commit` / `git push` / `git tag`；未启动真实生产服务或真实浏览器（18097 端口沙盒已在 `finally` 停止并确认无监听进程残留，Playwright 浏览器已关闭）。

---

## 结论

**PASS（0 项 P0 / 0 项 P1 / 0 项 P2 / 0 项 Q / 3 项 N）。**

R2 复审确认 R1 遗留的 **`P2-1`**（设置页 3 张兄弟配置卡 `SecurityBrowserCard.vue`、`LogConfigCard.vue`、`SchedulingCard.vue` 的 4 个 `💾` 原生按钮）与 **`P2-2`**（`ModelStatsTable.vue`、`QuickApiExamples.vue`、`YamlSourceEditor.vue` 残留彩色 Emoji 与文案引用）已 **100% 修复闭环**：

1. 全站 14 个受检 `.vue` 文件已完整解决 4 大核心痛点：`AccountRow.vue` 在 `375px` 移动端下通过 `flex-col sm:flex-row` 实现「徽章行 + 邮箱行」双层分离，邮箱父容器实测宽度恢复至 **`283px`**（全站四视口 `overflowX = false`）；`AccountRow.vue` 与 `InstancePoolCard.vue` 槽位操作按钮统一样式与 `26px` 高度；`InstancePoolCard.vue` 与 `DashboardView.vue` 引擎徽章去重并统一中性灰胶囊风格。
2. 设置页全部保存按钮（顶部「保存并热重载」+ 5 张配置卡共 7 个按钮）经 `--ui-debug` 沙盒实测 100% 统一为 `<Button variant="primary" size="sm">` + `<Save />`（实测 `height = 28px`、`borderRadius = 12px`、`backgroundColor = rgb(16, 163, 127)`、`hasSvg = true`）。
3. 生产代码全量排查（`web/src/` 正则扫除 debug 沙盒菜单外）已实现零彩色 Emoji，静态构建产物同步更新，前端 lint / type-check / 96 项单元测试全部通过。

- **P0**：0（无阻断性缺陷）
- **P1**：0（无主链逻辑或布局崩溃缺陷）
- **P2**：0（R1 的 `P2-1`、`P2-2` 均已于 R2 修复关闭）
- **Q**：0（无未解决存疑事项）
- **N**：3（375px 下 43 字符超长测试邮箱的预期截断、调试菜单沙盒专属 Emoji、静态产物与构建状态）

---

## 一、门禁实跑输出（R2 Reviewer 亲自执行）

| 门禁命令 | 实际输出 | 结果 |
|---|---|---|
| `git diff --check` | 无输出（exit 0，无空白符错误） | **PASS** |
| `git ls-files config.yaml` | 空输出（未跟踪，符合口径） | **PASS** |
| `cd web && bun run lint` | `$ bun --bun x eslint src --max-warnings 0`（0 errors, 0 warnings） | **PASS** |
| `cd web && bun run type-check` | `$ bun --bun x vue-tsc -b`（0 errors） | **PASS** |
| `cd web && bun test` | `96 pass, 0 fail, 819 expect() calls, Ran 96 tests across 11 files [122.00ms]` | **PASS** |

---

## 二、`--ui-debug` 沙盒多视口实测与 6 张截图核验

- **沙盒命令**：`uv run python main.py server --port 18097 --ui-debug`（`background_process` 启动，就绪后测量；测试完毕已调用 `background_process stop` 并用 `Get-NetTCPConnection -LocalPort 18097 -State Listen` 确认无监听进程，Playwright 已调用 `close`）。
- **控制台检查**：`playwright_browser_console_messages(level="warning")` 返回 **0 errors / 0 warnings**。
- **路由说明**：前端使用 `createWebHistory()`（`web/src/router/index.ts:47`），实测路径为 `/accounts`、`/`、`/settings`。

### 2.1 四视口横向溢出与关键 DOM 实测

| 页面 | 视口 | `scrollWidth / innerWidth` | `overflowX` | 核心 DOM 实测结果 |
|---|---|---|---|---|
| `/accounts` | `1440x900` | `1440 / 1440` | `false` | 12 个账号中 11 个完整展示；引擎徽章仅 `InstancePoolCard` 头部出现 1 次（`130.28 x 22 px`） |
| `/accounts` | `375x812` | `375 / 375` | `false` | 邮箱父容器 `.min-w-0.flex-1` 实测宽度 **`283px`**（行卡宽 `309px`）；操作按钮（`换槽`/`激活`/`恢复`/`换绑`/`重启`/`绑定账号`/`关闭`）统一高度 **`26px`**；引擎徽章仅头部 1 次（`130.28 x 22 px`） |
| `/` | `1440x900` | `1440 / 1440` | `false` | R2 复测：`foundEmojis = []`（`💾`/`📋`/`📈`/`💡`/`🔗`/`📄` 全清零）；「列表明细」「趋势图表」按钮均为 `h=24px, hasSvg=true`；「反代接入地址」渲染正常 |
| `/` | `375x812` | `375 / 375` | `false` | R2 复测：无横向溢出，`foundEmojis = []` |
| `/settings` | `1440x900` | `1440 / 1440` | `false` | R2 复测：可视化模式与 YAML 源码模式均 `foundEmojis = []`、`yamlHintClean = true`；全页 7 个保存按钮（`保存并热重载`、`保存安全配置`、`保存日志配置`、`保存调度配置`、`保存并切换引擎`、`保存协议配置`、`保存模型规则`）实测 100% 统一为 `h=28px, hasSvg=true, bg=rgb(16, 163, 127), radius=12px` |
| `/settings` | `1024x900` | `1024 / 1024` | `false` | 无横向溢出 |
| `/settings` | `768x900` | `768 / 768` | `false` | 无横向溢出 |

### 2.2 `375x812` 下 `/accounts` 全部 12 个账号邮箱 DOM 实测宽度

| 账号主标签文本 | `emailClientWidth` | `emailScrollWidth` | `parentWidth` | `rowWidth` | 是否截断 |
|---|---|---|---|---|---|
| `free.ready@example.com` | `154px` | `154px` | `283px` | `309px` | `false` |
| `ID: acc_free_nomail` | `116px` | `116px` | `283px` | `309px` | `false` |
| `plus.production.team+long-alias@example.com` | `265px` | `291px` | `283px` | `309px` | `true`（43 字符超长测试邮箱，见 N-1） |
| `pro.production@example.com` | `184px` | `184px` | `283px` | `309px` | `false` |
| `pro.expired@example.com` | `163px` | `163px` | `283px` | `309px` | `false` |
| `pro.session@example.com` | `161px` | `161px` | `283px` | `309px` | `false` |
| `ultra.cooldown@example.com` | `184px` | `184px` | `283px` | `309px` | `false` |
| `ultra20x.disabled@example.com` | `197px` | `197px` | `283px` | `309px` | `false` |
| `free.quota@example.com` | `156px` | `156px` | `283px` | `309px` | `false` |
| `free.quota.idle@example.com` | `181px` | `181px` | `283px` | `309px` | `false` |
| `cookie.shared.a@example.com` | `187px` | `187px` | `283px` | `309px` | `false` |
| `cookie.shared.b@example.com` | `188px` | `188px` | `283px` | `309px` | `false` |

### 2.3 6 张归档截图清单（`docs/dev/rebuild/domains/P8-api-ui/reviews/assets/`）

| 截图文件名 | 视口 / 路由 | 文件大小 | 状态 |
|---|---|---|---|
| `c216-accounts-desktop-1440.png` | `1440x900` `/accounts` | `361,335 bytes` | 已归档（R1） |
| `c216-accounts-mobile-375.png` | `375x812` `/accounts` | `325,707 bytes` | 已归档（R1） |
| `c216-import-modal-1440.png` | `1440x900` `/accounts`（导入弹窗） | `173,820 bytes` | 已归档（R1） |
| `c216-dashboard-1440.png` | `1440x900` `/` | `150,084 bytes` | 已重拍覆盖（R2） |
| `c216-dashboard-mobile-375.png` | `375x812` `/` | `116,182 bytes` | 已重拍覆盖（R2） |
| `c216-settings-1440.png` | `1440x900` `/settings` | `655,074 bytes` | 已重拍覆盖（R2） |

---

## 三、8 个前端文件逐行改动审计

1. **`web/src/components/accounts/AccountRow.vue`（+100/-96）**
   - `:170-290`：将左侧单行容器改为 `flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-2.5 min-w-0 flex-1`，上半部分徽章行增加 `flex-wrap`（`:171`），使邮箱名称容器 `.min-w-0.flex-1`（`:272`）在 `<640px` 移动端独占整行（实测 `283px`）。
   - `:209`：常规非激活、非失效态 `u/{{ account.auth_user }}` 徽章由蓝色块收口为 `bg-gray-100 text-gray-700 border border-gray-200`。
   - `:365-397`：移除旧 `Button` 导入，将「恢复」「换槽」「激活」统一为 `inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium ... rounded-lg`（实测 `26px` 高），分别搭配 Lucide `<RefreshCw>` / `<ArrowLeftRight>` / `<Play>` 线性图标。
2. **`web/src/components/accounts/CookieGroupBlock.vue`（+3/-2）**
   - `:7, 116-120`：将 `⚠ {{ expiredCount }} 个失效` 替换为 `<AlertTriangle class="w-3 h-3" />{{ expiredCount }} 个失效` 与极简 `text-xs text-rose-600 font-medium`。
3. **`web/src/components/accounts/InstancePoolCard.vue`（+13/-23）**
   - `:90, 98-102`：头部容器补 `flex-wrap` 防窄屏挤压；`引擎: {{ engineName }}` 徽章去除蓝/靛色块，改为中性灰 `bg-gray-100 text-gray-700 border-gray-200/80 whitespace-nowrap`。
   - 删除原每行实例内重复展示的 `{{ engineName }}` 胶囊（原 `:190-198`），消除冗余与窄屏占位。
   - `:224-273`：将「换绑」「重启」「唤醒」「绑定账号」「关闭」全部统一为 `px-2.5 py-1 text-xs font-medium rounded-lg`（实测 `26px` 高）并补齐 `<ArrowLeftRight>` 等线性图标，与 `AccountRow.vue` 完全一致。
4. **`web/src/components/modals/CookieImportModal.vue`（+18/-16）**
   - `:16, 423-474`：彻底移除油猴引导卡的琥珀黄渐变背景（`from-amber-50/90 to-amber-100/40`）与 `🧩`/`⚠️`/`📥` Emoji，改用 `rounded-2xl border border-gray-200 bg-gray-50/70` 极简卡片、`<Puzzle class="w-3.5 h-3.5 text-brand-600" />`、翡翠绿 `bg-brand-50 text-brand-700` 标签与白底细灰框安装按钮。
5. **`web/src/views/AccountsView.vue`（+7/-5）**
   - `:36, 229, 260-268`：去除拖拽遮罩 `📂` 与按钮 `🧩` Emoji，将「油猴抓取脚本」统一为 `variant="secondary"` + `<Puzzle class="w-4 h-4" />`。
   - `:294-301`：「保存当前方案」改为白底细灰框 `px-2.5 py-1 text-xs font-medium text-gray-700 bg-white border-gray-200 rounded-lg` + `text-brand-600` 图标。
6. **`web/src/views/DashboardView.vue`（+6/-8）**
   - `:139-143`：引擎徽章同步 `InstancePoolCard.vue` 改为 `bg-gray-100 text-gray-700 border-gray-200/80 whitespace-nowrap`。
   - `:161-166`：用 CSS 绿点 `<span class="w-1.5 h-1.5 rounded-full bg-emerald-500 shrink-0" />` 替代 `🟢` Emoji。
   - `:262, 290`：`<Activity>` 与 `<Database>` 图标由蓝/青色统一收口为 `text-brand-600`。
7. **`web/src/views/SettingsView.vue`（+59/-34）**
   - `:356-358`：沙盒横幅用 `<Wrench class="w-4 h-4 text-amber-600" />` 替代 `🛠️`。
   - `:463-471`：同步状态用 CSS 圆点（`bg-amber-500`/`bg-emerald-500`）替代 `🟠`/`🟢`。
   - `:475-538`：顶部操作栏全部收口为 `<Button variant="secondary|primary" size="sm">` + `<BookOpen>`/`<Undo2>`/`<RotateCcw>`/`<Save>`，移除 `📖`/`●/○`/`↩`/`↺`/`💾`。
   - `:596-607`：「保存模型规则」替换为 `<Button variant="primary" size="sm">` + `<Save>`。
8. **`web/src/components/settings/ProtocolAtgCard.vue`（+13/-8）**
   - `:119, 125-136`：`<Plug>` 由 `text-sky-600` 改为 `text-brand-600`；「保存协议配置」替换为 `<Button variant="primary" size="sm">` + `<Save>`，移除 `💾`。
9. **`web/src/components/settings/SecurityBrowserCard.vue`（R2 收口，+15/-9）**
   - `:5-7, 63-74`：引入 `<Button>` 与 `<Save>`，将原 `💾 保存安全配置` 原生按钮替换为 `<Button variant="primary" size="sm" :loading="saving">` + `<Save v-if="!saving" class="w-3.5 h-3.5" />`。
10. **`web/src/components/settings/LogConfigCard.vue`（R2 收口，+16/-10）**
   - `:7-9, 56, 62-73`：卡片图标 `<Activity>` 由 `text-purple-600` 统一为 `text-brand-600`；将 `💾 保存日志配置` 原生按钮替换为 `<Button variant="primary" size="sm" :loading="saving">` + `<Save>`。
11. **`web/src/components/settings/SchedulingCard.vue`（R2 收口，+22/-14）**
   - `:9, 30, 551, 557-569, 576, 593-606`：`<Scale>` 与 `<Cpu>` 统一为 `text-brand-600`；将「保存调度配置」与「保存并切换引擎」两处 `💾` 原生按钮均替换为 `<Button variant="primary" size="sm">` + `<Save>`（保留各自 `:disabled` 门控与 `:loading` 状态）。
12. **`web/src/components/dashboard/ModelStatsTable.vue`（R2 收口，+8/-4）与 `web/tests/dashboardStatsUi.test.ts`（+6/-2）**
   - `:15-16, 47-66`：引入 `<List>` / `<TrendingUp>` 线性图标，替换「📋 列表明细」「📈 趋势图表」；同步更新 `dashboardStatsUi.test.ts:17-22` 断言，强制校验不含 `📋`/`📈` 并包含 `<List` / `<TrendingUp`。
13. **`web/src/components/dashboard/QuickApiExamples.vue`（R2 收口，+4/-3）**
   - `:3, 92-93, 130`：引入 `<Lightbulb>` 与 `<Link2>`，分别替换未启用协议提示条中的 `💡` 与反代接入地址标题中的 `<span>🔗</span>`。
14. **`web/src/components/config/YamlSourceEditor.vue`（R2 收口，+4/-4）**
   - `:3, 51-53, 87`：引入 `<FileCode2>` 替换标题中的 `📄`，并将底部说明文案中的「💾 保存并热重载」同步更新为「保存并热重载」。

---

## 四、问题清单（`P0 / P1 / P2 / Q / N`）

### P0
- 无。

### P1
- 无。

### P2
- 无（R1 的 `P2-1`、`P2-2` 均已于 R2 修复关闭）：
  - ~~`P2-1`（R2 已修复关闭）~~：设置页 `SecurityBrowserCard.vue`、`LogConfigCard.vue`、`SchedulingCard.vue` 的 4 个 `💾` 原生按钮已全部替换为 `<Button variant="primary" size="sm">` + `<Save>`，全页 7 个保存按钮样式实测 100% 统一。
  - ~~`P2-2`（R2 已修复关闭）~~：`ModelStatsTable.vue`、`QuickApiExamples.vue`、`YamlSourceEditor.vue` 的 6 处彩色 Emoji 与说明引用文案已全部替换为 Lucide 线性图标与干净文案，`dashboardStatsUi.test.ts` 已同步覆盖校验。

### Q
- 无。

### N

1. **`N-1`：375px 移动端下 43 字符超长测试邮箱仍存在预期内的尾部省略截断。** `plus.production.team+long-alias@example.com` 全长渲染需要 `291px`，在 `375px` 视口下其独立整行父容器 `.min-w-0.flex-1` 为 `283px`（扣除右侧重命名编辑图标后文本宽度为 `265px`），触发单行 `truncate`；其余 11 个常规账号邮箱均 `emailClientWidth === emailScrollWidth`（100% 完整展示，无挤压）。
2. **`N-2`：沙盒调试菜单仍含专属调试 Emoji。** `web/src/components/debug/UiDebugMenu.vue:58-60, 132`（`⚡`/`🟠`/`🔌`/`🛠️`）与 `web/src/components/debug/LogsDebugMenu.vue:27, 32, 37, 94, 139`（`💉`/`🔒`/`📏`/`🛠️`/`🗑️`）仅在 `--ui-debug` 模式显示，不面向生产用户。
3. **`N-3`：前端静态产物与构建状态一致。** `src/aistudio_api/static/assets/` 下 7 个产物文件已随本次 `web/src/` 修改同步更新（R2 `--ui-debug` 沙盒实测已直接验证最新构建产物生效）；`git status` 未出现 `index.html` 脏 diff。

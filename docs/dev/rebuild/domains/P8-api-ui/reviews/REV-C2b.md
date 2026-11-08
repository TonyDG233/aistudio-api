# REV-C2b — C2b SettingsView / PATCH `/config` / POST `/config/reset` 终审

- 终审日期：2026-11-07（第三轮最终复审，UTC）。
- Target worktree：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`。
- Target branch / HEAD：`rebuild-upstream` / `c3fef585f01e525e5251044dab5fb01be4473910`（未变）。
- Target 工作树：`git status --porcelain=v1` 共 **48** 个条目（31 M + 3 D + 14 untracked）。
- Main repo：`D:\LLM\self_programming\aistudio-api`，HEAD `94e6477df6d63bfa86ed42f759d12c2612df5f35`（未变）。
- Method：完整读取当前 `git diff`、全部新增/修改文件、双写报告、两张 assets；独立执行源码与 Playwright DOM 文案审计；后端/前端全套门禁；另以 `AISTUDIO_UI_DEBUG=1`、`AISTUDIO_WEB_PASSWORD=''`、`127.0.0.1:32067` 启动 UI Debug 沙盒，实测 Settings 全部卡片、字典、还原/重置、部分保存、1024/768/1440，以及 Accounts 新建/激活/换绑弹窗、账号表/行、实例池、保活栏；结束后停止服务并确认端口/进程/临时目录。
- 只读声明：本轮**未修改源码/测试/配置/AGENT**；仅更新本报告并校验两张既有 assets。**未 stage、未 commit、未 push**。未启动 `ui_debug=false` 服务，未拉起真实 CloakBrowser/Chromium，未访问真实 Google/账号/IP。
- 沙盒停止证据：`127.0.0.1:32067`（`--browser-port 32068` 未使用），pid `27792`；`background_process.stop` 后 `Test-NetConnection 127.0.0.1:32067` = `False`，`Get-NetTCPConnection -LocalPort 32067` 无 `Listen`，`Get-Process` 无 `python/uv/aistudio`；本轮 `%TEMP%\aistudio-ui-debug-s7gf7wx_` 与 `aistudio-ui-debug-paths-6pfdkq7e` 已清理。真实 `config.yaml` SHA256 全程保持 `42AC88882808E71B7D455DC4CA572228D6FE94803098B278A416B0990D40626F`（mtime `2026-11-01T02:11:23`）。

## 0. Verdict

**PASS — 0 个未关闭 P0/P1/P2/Q。**

上一轮 3 个 P2 已逐项独立核实关闭；P2-1/Q-1/Q-2/N-1/N-6 继续有效。仅余 N 级观察（N-12 等），不构成阻塞。

## 1. 终审关闭核对

| 项 | 状态 | 独立证据 |
|---|---|---|
| P2-2 去除 `🗑️`，保留 Trash2 SVG + `删除` | **CLOSED** | `web/src` 全目录 `🗑` 计数 **0**；`SchedulingCard.vue:551,909,1041` 仅 `Trash2` SVG；1440 展开调度高级区后 DOM `deleteEmoji=0`、`textContent==='删除'` 的按钮 **4** 个；overview 截图显示图标 + `删除` |
| P2-3 `SettingsView.vue` 根 `</template>` 后无脏 token | **CLOSED** | `SettingsView.vue` 共 560 行，末两行为 `  </div>` / `</template>`，无 `e>`；全部 20 个本轮修改 `.vue` 的最后非空行均为 `</template>` |
| P2-4 AccountsView 裸露英文键名/枚举中文化 | **CLOSED** | `utils/instances.ts:11-25` 保留底层值 `chat/build/Free/Plus/Pro/Ultra/Ultra_20X`，新增 `TIER_LABELS`/`SURFACE_LABELS` 中文映射；`CreateInstanceModal.vue:129-155` 标签 `绑定账号/服务渠道/账号等级`、选项 `对话与工具 (Chat)/应用构建 (Build)`、`免费账号 (Free)/Plus 会员/Pro 会员/Ultra 会员/Ultra 20X 会员`；`BindSlotModal.vue`/`ReassignAccountModal.vue`/`AccountRow.vue`/`AccountTable.vue`/`InstancePoolCard.vue`/`KeepaliveBar.vue`/`UiDebugMenu.vue` 实测无裸值；`/accounts` 页面 text/aria-label/title/placeholder/alt 检索 `Surface/Tier/chat/build/Ultra_20X/in_flight/latest/current/ID:/UI Debug/all_states/default` 均为 0（仅允许的 `(Chat)` 大写形式） |
| P2-1 还原/重置拆分 | **CLOSED（复验）** | `SettingsView.vue:225-250,465-480`；实测还原 confirm 后恢复 baseline、磁盘不变；重置 confirm 后 `POST /config/reset`，max_contexts 6→2、API Key `final-keep` 保留、`ui_debug: true`、`🟢 已同步` |
| Q-1 部分保存 warning 精确 | **CLOSED（复验）** | `SettingsView.vue:185`；同动作内实测 `exactWarn=true`、`dirty=true`、磁盘其他 section 未覆盖 |
| Q-2 内联槽位 | **CLOSED（复验）** | `SchedulingCard.vue:256-332`；only/min/max 三态与 `指定单一等级/最低等级要求/自定义等级区间` 实测齐全 |
| N-1 1024 溢出 / 第 4 卡长 token | **CLOSED（复验）** | 1440/1024/768 展开调度+ATG 高级区均 `docScrollWidth==innerWidth`、非滚动溢出 **0**；1024 token `sw=cw=310` |
| N-6 字典 Badge/options/含义 | **CLOSED（复验）** | 字典 75 条、分类 Badge 76、`可选值` 区块、推荐值/原理齐全；正文无 `hash_include_code`，构建产物 0 命中 |

## 2. 全中文 UI 独立审计（源码 + DOM）

### 2.1 SettingsView（PASS）

- 1440 源码/DOM 检索以下 token 均 **0**：`surface/only_tier/min_tier/max_tier/max_contexts/account_surface/drop_unsupported_params/quota_scope/mode_by_format/control_position/empty_retries/side_buffer_bytes/max_response_bytes/text_model_pattern/non_text_model_pattern/tool_prefix/client_synthetic_guard/paid_model_rules/tier_quotas`。
- 黑话 `死开关`/`D-14`/`P3-F` 可见文本 **0**；卡片标题（`h3`）为 `客户端 API 密钥/安全与浏览器配置/日志与调试/实例池、配额与路由调度/协议开关与防截断网关/模型规则与安全策略`，无重复 Emoji，仅左侧 SVG 图标。
- 保留项：`Gemini/OpenAI/Claude/Responses`、`Plus/Pro/Ultra/Ultra 20X`、`(Chat)/(Build)`、`API 密钥`、`YAML`、`config.yaml`、`AISTUDIO_WEB_PASSWORD`、品牌 `AI Studio Proxy`。

### 2.2 AccountsView（P2-4 关闭）

- `/accounts` 页面与三个弹窗（新建实例、激活账号到实例槽位、换绑账号）实测：`Surface`/`Tier`/裸 `chat`/`build`/`Ultra_20X`/`in_flight`/`latest`/`current`/`ID:`/`UI Debug`/`all_states` = **0**；aria-label/title/placeholder/alt 审计 0 命中。
- 可见文案示例：`服务渠道`、`账号等级`、`对话与工具 (Chat)`、`应用构建 (Build)`、`免费账号 (Free)`、`Plus 会员`、`Pro 会员`、`Ultra 会员`、`Ultra 20X 会员`、`最新：inst-1`、`进行中 1`、`当前绑定`、`账号等级不兼容`。
- 底层提交值未变：`SURFACE_OPTIONS=['chat','build']`、`TIER_OPTIONS=['Free','Plus','Pro','Ultra','Ultra_20X']`（`utils/instances.ts:11-13`）；新建实例实测提交 `chat/Free` 成功。
- C2a 回归：`/accounts` 新建实例提交成功（沙盒实例数 9→10，`inst-10` 绑定 `acc_free_ready`、`state=active`）；激活/换绑弹窗打开、候选过滤与中文选项核对通过；账号表/行、实例池、保活栏渲染正常。

## 3. 关键功能与 reset 契约复验

- `POST /config/reset`（`routes_system.py:486-547`，`protected_router` + `app.py:555` `require_web_auth`）：
  - 读取 `defaults/config.yaml`（`config.py:85`）；保留当前 `api_keys`；沙盒强制 `diagnostics.ui_debug=true`；`write_config_text`（validate→原子写→`invalidate_config_cache`）；返回完整 SystemConfig + `ok/message`。
  - 沙盒实测：创建 API Key `final-keep` + max_contexts=6 → reset → Key 保留、max_contexts=2、`ui_debug: true`、`response_ui_debug=true`、真实 config SHA 不变。
- 还原修改：改 max_contexts 5→还原→2、`🟢`、磁盘仍 2（未写服务端）。
- D-09：reset 后 `diagnostics.hash_include_code: true` 保留；日志卡保存不暴露该键。
- 字典：75 条、分类 Badge、`可选值`（中文 label + value + 含义）、推荐值；`hash_include_code` 不出现在普通用户字典。
- 三宽度：1440/1024/768（高级区展开）零内部溢出/无跨卡重叠。
- Console：`/settings` 与 `/accounts` 正常操作期间 error=0；停止服务后未计入。

## 4. 门禁结果（目标区独立执行）

| 命令 | 结果 |
|---|---|
| `uv run ruff check .` | All checks passed!（exit 0） |
| `uv run pyright src tests` | 0 errors / 0 warnings / 0 informations（exit 0） |
| `uv run pytest -q` | **1096 passed**（26.42s，0 failed / 0 skipped） |
| `cd web && bun run lint` | exit 0 |
| `cd web && bun run type-check` | exit 0 |
| `cd web && bun test` | **24 pass / 0 fail**（79 expect，3 files） |
| `cd web && bun run build` | exit 0；1783 modules，4.37s |
| 构建前后静态产物 | 13 个 `static/assets/*` + `static/index.html` SHA256 **全同** |
| `git diff --check` | exit 0（仅 CRLF→LF 规范化 warning） |
| 双仓 `git ls-files config.yaml` | 均为空 |
| 双仓 `git diff --cached --name-only` | 均为空 |
| `static/index.html` CRLF | **0** |
| 主仓 `git status --short` | 仅 `REV-C2b.md` + `assets/`（untracked） |
| 目标区 `git status --porcelain` | 48 条目；无临时 png/log |
| 沙盒停止 | 32067 已关闭，无进程残留；真实 config SHA 不变 |

> Worker 声称 **1096 tests / 24 frontend tests** 与实际一致；两张 assets SHA256 与其声称一致（见 §5）。

## 5. 截图与 assets（两侧字节一致）

| 相对路径 | 尺寸 | SHA256 |
|---|---|---|
| `assets/c2b-settings-overview-1440.png` | 1440×2100 | `3D31944EF120BC6EC666036BFBB7AC1D0E9C7553DF6842CB7875AEF291933FB8` |
| `assets/c2b-settings-protocol-atg-1440.png` | 1440×900 | `97B085EA3EF99EC73971A80103400C50128DEDD07E7B1E42A1EE93084D98C96D` |

- overview 为最终 Settings 全页（沙盒横幅、4 运行时卡、免密警告、全局控制栏、API Keys/安全/日志/调度）；删除按钮已为图标 + `删除`。
- protocol-atg 为字典打开于协议卡（`全部 (75)`、`协议与ATG` Badge、`features.atg.tool_prefix`、推荐值 `atg_emit_`）。
- 其余临时截图仅存于 `%TEMP%\kilo-playwright-mcp\`，未进入任何仓库。

## 6. 问题清单（P0/P1/P2/Q/N 全量，含状态）

### 6.1 P0（阻塞）

- **无。**

### 6.2 P1（提交前必须修复）

- **无。**

### 6.3 P2（应修复）

- **P2-1（CLOSED）** 还原/重置拆分与 reset 契约。
- **P2-2（CLOSED）** `🗑️ 删除` 已去除，保留 Trash2 SVG + `删除`。
- **P2-3（CLOSED）** `SettingsView.vue` 末尾孤立 `e>` 已删除，全部本轮 `.vue` 文件尾干净。
- **P2-4（CLOSED）** AccountsView 裸露英文键名/枚举已中文化，底层值不变。
- **无未关闭 P2。**

### 6.4 Q（需用户裁决）

- **Q-1（CLOSED）** 局部保存 warning 精确。
- **Q-2（CLOSED）** 内联槽位编辑确认。
- **无未关闭 Q。**

### 6.5 N（非阻塞观察，含残余）

- **N-1（CLOSED）** 1024 溢出修复；第 4 卡 token 换行。
- **N-6（CLOSED）** 字典 Badge/options/含义补齐；`hash_include_code` 不出现。
- **N-2** 保存/重置经 `yaml.dump`/`YAML.stringify` 会剥离运行时 `config.yaml` 注释；`defaults/config.yaml` 模板注释保留。
- **N-3** `B-docs-and-audit/index.md:89` 旧路径位于历史观察小节，无需更新。
- **N-4** `security.web_password`/`logging.daily_dir/retention_days` 删除安全（env-only/常量，无 YAML 消费者）。
- **N-5** 三份 spec 文档的 `defaults/config.yaml` 行号因注释扩容漂移。
- **N-7** Dockerfile 未复制 `defaults/config.schema.json`；运行期不需要。
- **N-8** 强制停止不触发 lifespan 清理；本轮创建的 `s7gf7wx_`、`paths-6pfdkq7e` 已清理。
- **N-9** 停止服务后页面轮询 `/health` 会产生 `ERR_CONNECTION_REFUSED`；服务运行时 error=0。
- **N-10** 无 Worker 独立书面报告；1096/24 与 assets SHA 均与实际一致。
- **N-11** 06 台账仍引用已删除的 `VisualConfigEditor.vue`；按 AGENT 10.1 Worker 不得改 06。
- **N-12** `ApiKeyManagerCard.vue:172` 空态仍见 `API Key`/`Key`（overview 截图可见），与其余 `API 密钥` 不一致；属文案统一问题，不是 YAML 键名。
- **N-13** 空密码沙盒无法 live 触发 reset 401/模板缺失 500；已由 `tests/unit/test_config_reset.py:190-238` 覆盖，路由确在 `require_web_auth` 保护下。
- **N-14** `assets/c2b-settings-protocol-atg-1440.png` 背景未模糊（headless backdrop-filter 差异）；弹窗内容与最终构建一致，不作为缺陷。
- **N-15** 未绑定槽位的“绑定账号”入口打开 `ReassignAccountModal`，标题/按钮仍为 `换绑账号`/`确认换绑`；文案语义可再优化，功能正常。
- **N-16** 空态 `API Key` 文案与 N-12 同源，建议后续统一为 `API 密钥`。

## 7. Worker 报告与事实对照

- 测试数字：声称 **1096 tests / 24 frontend tests**，实际 `pytest` 1096 passed、`bun test` 24 pass，完全一致。
- P2-2/P2-3/P2-4：源码、构建产物与 Playwright DOM 三侧一致，均确认关闭。
- assets：声称 overview `3D31944E…`、protocol-atg `97B085EA…`，独立重算完全一致，两侧字节相同。
- 未发现夸大、越界改主仓、stage/commit/push、临时截图入库或真实环境启动。

## 8. 结论与下一步

- **PASS**：0 未关闭 P0/P1/P2/Q；所有返工项与关键功能、全中文 UI、reset 契约、三宽度、门禁、沙盒停止均独立复核通过。
- **未 commit / 未 stage / 未 push**；本报告双写目标区与主仓同路径，两张 assets 两侧字节一致，**等待用户审阅**。
- 后续仅建议在提交前顺手处理 N-12/N-15 等文案统一项（不阻塞）。

## 9. 只读声明与报告完整性

- 本轮未修改源码/测试/配置/AGENT；仅更新本报告并校验既有 assets；未 stage/commit/push。
- 目标 HEAD 仍 `c3fef58`，主仓 HEAD 仍 `94e6477`。
- 沙盒仅在 `AISTUDIO_UI_DEBUG=1`、`AISTUDIO_WEB_PASSWORD=''`、`127.0.0.1:32067` 下运行；已停止，端口关闭，无进程残留，真实 config 未变。
- 两份报告字节一致；SHA256 见 Reviewer 最终消息。

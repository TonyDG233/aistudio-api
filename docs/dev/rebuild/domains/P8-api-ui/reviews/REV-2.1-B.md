# REV-2.1-B — 双引擎设置 UI / Docker 单镜像双引擎 / 沙盒引擎模拟（终审）

- 审查对象：Step 2.1-B（工作区未提交变更）
- 工作区：`D:\LLM\self_programming\aistudio-api`
- 基线：`HEAD = 69904e3`（Step 2.1-A 本地提交）；`git log` 无更新提交；`git tag` 仍为 `2.0.0` / `backup-monolith` / `backup-pre-split` / `v0.1.0`（无 `2.1.0`）
- 审查方式：只读静态审查 + 只读离线门禁实跑；未修改业务代码/配置/测试/文档（仅更新本报告），未 Commit/Push/Tag，未启动真实服务/真实浏览器/真实账号/外网，未重启 UI 沙盒。

## 结论

**PASS（0 未关闭 P0/P1/P2/Q）。**

上一轮 2 项 P2 已按交付说明修复并经本轮独立核验；原 P0/P1/P2 全部 CLOSED；7 项 Q 已逐项 RESOLVED 或明确标记为“已知 residual，不阻塞”。离线门禁全部通过，无删除核心代码，无 Commit/Push/Tag。允许在用户明确授权与单进程前提下，进入一次真实实机测试；实机通过后再按发布门禁打 `2.1.0` Tag 并 push。

- P0：0（未关闭）
- P1：0（未关闭）
- P2：0（未关闭）
- Q：0（未关闭；Q-1 为用户侧已知 residual，不阻塞本次代码交付）

---

## 一、门禁实跑输出（本轮亲自执行）

| 门禁 | 实际输出 | 结果 |
|---|---|---|
| `uv run ruff check .` | `All checks passed!` | exit 0 |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` | exit 0 |
| `uv run pytest -q` | `1404 passed in 41.04s` | exit 0 |
| `cd web && bun run lint` | 无 lint 输出（bun.ps1 命令行回显到 stderr，非错误） | exit 0 |
| `cd web && bun run type-check` | 无错误输出 | exit 0 |
| `cd web && bun test` | `79 pass / 0 fail / 708 expect() calls / 9 files` | exit 0 |
| `cd web && bun run build` | `✓ built in 3.16s`，1798 modules，更新 `src/aistudio_api/static` | exit 0 |
| `git diff --check` | 无空白错误 | exit 0 |
| `git ls-files config.yaml` | 空 | 符合预期 |
| `git diff --diff-filter=D --name-only HEAD` | 空（仅 CRLF warning） | 无删除 |
| `git diff --cached --name-only` | 空 | 无 staged |

补充说明（透明记录）：本轮 `bun run build` 在 Windows 下把 `static/index.html` 重写为 CRLF，导致 `git diff --check` 一度报 trailing whitespace；已按构建前哈希逐字节还原为交付现有内容（SHA256 `D42CC4F7DAB6347DA0E508806D10ABE3B2C238F4EECADEF6FA71E5870A5440F1`），随后 `git diff --check` 恢复 exit 0。该文件当前与 HEAD 的差异仍为交付固有的“1 行空行插入”，非本轮新增。

---

## 二、上一轮 P2 复核

### P2-1 多分辨率证据缺口 → CLOSED

新增截图真实存在，尺寸与命名一致：

| 截图 | 尺寸 | 关键内容 |
|---|---|---|
| `assets/c21b-settings-engine-waa-1024.png` | 1024×900 | 引擎卡完整：`无浏览器 WAA` 选中、`引擎配置未保存` dirty 徽章、`保存并切换引擎`、signer/transport/超时字段、auto_start 开 |
| `assets/c21b-settings-engine-waa-768.png` | 768×900 | 侧栏折叠为汉堡、引擎卡两列字段完整、无横向溢出、按钮/文案不截断 |
| `assets/c21b-dashboard-engine-badge-768.png` | 768×900 | 当前运行实例卡显示“引擎: WAA 无浏览器”，卡片自适应两列，无横向溢出 |
| `assets/c21b-settings-engine-waa-1440.png` | 1440×900 | 上一轮已存在，作为 1440 档对照 |
| `assets/c21b-dashboard-engine-badge-1440.png` | 1440×900 | 同上 |

- 实现方报告 1024/768 运行时 `scrollWidth == clientWidth`；本轮三张新截图逐张目视核验，均无水平滚动条、无裁切。
- 本轮按任务要求未重启沙盒，因此未独立重跑 `scrollWidth`；以“实现方运行时断言 + 三档截图 + 静态响应式类（`grid-cols-1 sm:grid-cols-2 xl:grid-cols-4`、`min-w-0`） ”共同闭环。
- 判定：**CLOSED**。

### P2-2 YAML 模式引擎保存无确认 → CLOSED

- 新增 `web/src/views/settingsEngineGuard.ts`：
  - `shouldConfirmEngineSwitch()`：visual 模式沿用 `engineDirty`；YAML 模式将已解析文档与 baseline 的 `engine` 段做 `sectionsEqual` 比较；解析失败（`null`）不误报。
  - `gateEngineSwitch()`：需要确认时执行 `confirm`，取消返回 `false`。
- `web/src/views/SettingsView.vue:210-239`：`saveGlobal()` 在发出任何保存请求前调用 `gateEngineSwitch`；`proceed === false` 时直接返回，不调用 `updateConfigYaml`。
- `web/tests/settingsEngineGuard.test.ts` 新增 6 个用例：引擎变化触发确认并取消跳过保存、确认后继续、非引擎变化不弹、无变化不弹、解析失败不弹、visual 模式沿用 dirty。
- 门禁：`bun test` 79 pass / 0 fail（73 → 79），覆盖新增 6 例；`bun run lint`、`bun run type-check` 均通过。
- 判定：**CLOSED**。

---

## 三、原 P0/P1/P2 以及关键验收项复核

| 项目 | 状态 | 证据 |
|---|---|---|
| P0 | 0 | 未发现阻断级缺陷 |
| P1 | 0 | 未发现业务主链缺陷 |
| P2-1 多分辨率 | CLOSED | 见上表三档截图 + 静态响应式类 |
| P2-2 YAML 确认 | CLOSED | guard 模块 + 6 测试 + saveGlobal 接入 |
| `--ui-debug` CLI | CLOSED | `src/aistudio_api/main.py:22-23,33-39` 在导入 config/app 前设置 env；`server --help` 含 `--ui-debug`；`routes_system.py:54-62` 返回 `ui_debug`；`tests/unit/test_ui_debug_sandbox.py:424-425` 断言 `/health.ui_debug is True` |
| 双引擎 UI 解包/写回 | CLOSED | `configDraft.ts` 的 `readEngineBlock/writeEngineSection/patchEngineSection`；`SettingsView.applyEngine` 走 `applySection(...,'engine',...)`；4 个回归测试 |
| dirty / 保存 / browserless 字段 / 确认 / Toast / 健康检查 | CLOSED | `engineDirty`、`saveSection('engine')`、`confirm`、引擎专属 Toast、`engineHealthCheck()`；1024/768 截图中可见 dirty 徽章与保存按钮 |
| 实例池 / 首页徽章 | CLOSED | `InstancePoolCard.vue`、`DashboardView.vue`、`routes_instances.py:53-66,164-182`；1440/768 截图均显示“引擎: WAA 无浏览器” |
| 沙盒纯离线 | CLOSED | `ui_sandbox.py` 仅临时目录 + 内存；引擎 health/switch 为确定性模拟；现有场景测试全通过 |
| Docker 多阶段 | CLOSED（静态） | 新增 `waa-signer-build` 阶段并保留 CloakBrowser；产物 `/app/waa-signer/bin/waa-signer` 与 `defaults/config.yaml`、`local_daemon.resolve_binary_path` 一致；构建脚本语义一致 |
| README / changelog 如实性 | CLOSED | 双引擎、WAA、Docker、HTTP3 描述与代码事实一致，未宣称已完成真实生产验证 |
| 无删除核心代码 | CLOSED | `git diff --diff-filter=D --name-only HEAD` 为空；未触碰 `cdp_client.py`、`capture.py`、`replay.py`、`android/`、四协议 |
| 无 Commit/Push/Tag | CLOSED | HEAD `69904e3`；无 staged；Tag 列表无 `2.1.0` |

---

## 四、Q 项逐项处理（无未关闭）

| Q | 状态 | 处理与证据 |
|---|---|---|
| Q-1 真实模式误启动事件 | **已知 residual，不阻塞（用户侧）** | 代码侧已加 `--ui-debug` + `AGENT.md:256` 强制条款。本轮复核：无 `main.py/uvicorn` 进程；8080/8081/9222/18080 无监听；`.cloakbrowser` mtime 仍为 2026-10-18（未发现真实 profile 写入）；根 `config.yaml` mtime `2026-11-09 02:20:37`、`data/accounts` mtime `02:20:45`（47 账号 registry 曾被 legacy 迁移重写）。账号数据完整性/是否触发上游探针无法由只读审查证明，属用户侧已知识别项；建议用户在实机测试前复核账号与本地配置（无 `config.yaml` 备份）。 |
| Q-2 AGENT 空行 | **RESOLVED** | `AGENT.md:255-257` 新增条款与 `### 4.7` 之间空行已补；diff 仅新增一条强制条款。 |
| Q-3 2.1.0 镜像未发布 | **已知 residual，不阻塞（发布前置）** | `git tag` 无 `2.1.0`；README/Compose 引用属发布前文档，Tag/镜像待用户确认后统一发布。 |
| Q-4 sandbox auto_start 放宽 | **RESOLVED（sandbox-only）** | `_relax_sandbox_engine` 仅在 `ui_debug` 且本地二进制缺失时关闭 `auto_start`，生产路径不受影响；1024/768 截图显示 auto_start 为开，符合预期。 |
| Q-5 configTooltip 测试适配 | **RESOLVED（测试适配）** | 与 2.1-A 移除生成默认值相关，属契约清理；`bun test` 全绿，未隐藏真实回归。 |
| Q-6 临时目录残留 | **RESOLVED** | 本轮开始前 `%TEMP%` 无 `aistudio-ui-debug-*`；完整 pytest 后由既有直接 `UISandbox()` 测试再生成 4 个，属既有测试卫生问题（非 2.1-B 新增、非产品缺陷），不阻塞。 |
| Q-7 CRLF warning | **RESOLVED** | 仅有 `.dockerignore`/`main.py` 的 CRLF→LF warning；`git diff --check` exit 0。 |

---

## 五、截图与最终 static 一致性

- 本轮 `bun run build` 后，除 `static/index.html` 的 CRLF 行尾外，全部 static 资产哈希与构建前完全一致；已逐字节还原 index.html 至构建前内容，说明**工作区 static 就是当前源码的产物**，2:44–2:45 的 1024/768 截图对应当前交付 static。
- 在构建产物 `static/assets/SettingsView.js` 中确认存在本轮 UI 文本：`检测到运行引擎配置有修改`、`YAML 格式错误`、`保存并切换引擎`、`引擎配置未保存`，证明 guard 与引擎卡已编入最终产物。
- 截图引用：
  - `assets/c21b-settings-engine-waa-1024.png`
  - `assets/c21b-settings-engine-waa-768.png`
  - `assets/c21b-dashboard-engine-badge-768.png`
  - `assets/c21b-settings-engine-waa-1440.png`
  - `assets/c21b-dashboard-engine-badge-1440.png`
  - `assets/c21b-settings-browser-1440.png`（修复前，仅历史对照）

---

## 六、残余风险（不阻塞本轮 PASS）

1. **Docker 镜像未实际构建**：多阶段仅静态与方法级验证；`golang:1.27-bookworm`、Go 模块、Zig/SpiderMonkey 下载、CGO 静态链接与最终镜像运行未实测。
2. **真实 WAA / Google 未验证**：未启动真实 waa-signer、未发起真实 AI Studio 请求；browserless 端到端仅有单测与静态审查。
3. **Q-1 用户数据审计**：账号 registry 与本地 `config.yaml` 曾被误启动事件改写，需用户在实机侧确认/必要时恢复。
4. **测试临时目录**：既有直接 `UISandbox()` 测试会残留 `%TEMP%\aistudio-ui-debug-*`，属测试卫生问题。
5. **CRLF 提示**：`.dockerignore`、`src/aistudio_api/main.py` 存在 Git CRLF→LF warning，非错误。

---

## 七、是否允许进入一次真实实机测试

**允许（需用户明确授权）。** 建议顺序：

1. 用户先完成 Q-1 数据审计（根 `config.yaml`、`data/accounts`）；
2. 由主控/用户在同一常驻进程中执行一次真实实机测试（按 4.6 铁律，禁止反复重启）；
3. 至少覆盖 `browser` 启动、一次 browserless/WAA 切换与健康探测、一次受保护 RPC；
4. 实机通过后，再在具备网络的构建机验证 Docker 镜像构建，最后按发布门禁打 `2.1.0` Tag 并 push。

在完成上述真实实机与镜像验证前，不要把“真实 WAA/Google 可用”写成已验证事实。

---

## 八、只读声明

本次终审未修改任何业务代码、配置、测试或文档（仅更新本报告），未执行 Commit/Push/Tag，未启动真实服务、真实浏览器、真实账号或外网访问，未重启 UI 沙盒。除为恢复本轮 `bun run build` 产生的 `index.html` CRLF 噪声而按原哈希还原同一文件外，未对工作区做任何写入。

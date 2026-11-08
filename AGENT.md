# 智能体协作与开发规范 (AGENT.md)

本文档面向所有参与本项目的代码代理（Coding Agents）与开发者，明确核心架构共识、运行环境约束与代码规范。

适用范围：本仓库主工作区。主工作区即唯一常态开发、审查与交付工作区，所有 Worker/Reviewer 直接在同一工作树内读写、测试与出报告。行为权威是 `docs/dev/rebuild/06-decision-log.md` 全文与 `docs/dev/rebuild/08-09-decision-record.md` 的 D-01..D-14；两者冲突时以 `08-09` 为准。本文只固化工程口径，不新增决策。

历史 `docs/dev/rebuild/` 与旧 `rebuild` 分支只作决策依据与只读失败样本；严禁把旧分支整树 merge、checkout 或覆盖当前代码。

---

## 目录

- [1. 核心架构与设计原则](#1-核心架构与设计原则)
- [2. 运行时与环境约束 (Termux)](#2-运行时与环境约束-termux)
- [3. 代码质量与类型规范](#3-代码质量与类型规范)
- [4. 实例、池与并发安全（核心）](#4-实例池与并发安全核心)
- [5. 路由、错误与重试](#5-路由错误与重试)
- [6. 账号、Cookie、预设与鉴权](#6-账号cookie预设与鉴权)
- [7. 保活、日志与协议面](#7-保活日志与协议面)
- [8. 配置口径](#8-配置口径)
- [9. 代码结构索引](#9-代码结构索引)
- [10. 智能体工作流与发布纪律](#10-智能体工作流与发布纪律)

---

## 1. 核心架构与设计原则

`aistudio-api` 是 Google AI Studio 反向代理网关。

- **分层边界（硬约束）**：`api` 只做协议校验、鉴权与响应渲染；`application` 编排用例，严禁 import `fastapi` 或 `aistudio_api.api`；`domain` 只放纯模型与错误契约；`infrastructure` 提供浏览器、CDP、存储等适配器，严禁反向 import `aistudio_api.application`。依赖方向固定为 `api → application → domain ← infrastructure`。
- **协议面**：原生 Gemini API（`/v1beta/...`）、OpenAI Chat Completions（`/v1/chat/completions`、`/v1/models`）、Claude Messages（`/v1/messages`）与 OpenAI Responses（`/v1/responses`）四种协议面均已完成并默认启用；OpenAI/Claude/Responses 可通过 `features.formats` 关闭。所有协议请求归一到协议中立核心，响应在格式边界逆变换；核心生成管线不得散落协议判断。
- **轻量 CDP 驱动**：使用纯 Python 异步 WebSocket 直连 Chrome DevTools Protocol（CDP），不依赖 Node.js、Playwright 或 Selenium。
- **浏览器内 XHR Replay**：在 MakerSuite 页面上下文中执行携带 `withCredentials = true` 的异步 XHR 请求，天然复用完整的 Cookie 会话、BotGuard 快照与环境指纹。
- **代码即事实标准（Code as Ground Truth）**：通过全量测试验证的代码是系统的事实标准。当技术规范文档与真实代码逻辑出现分歧时以代码为准，并在重构过程中同步更新文档；但不得用“代码如此”为违反 `06`/`08-09` 的旧实现辩护。
- **架构事实优先（Fact & Architecture over Brittle Tests）**：保持架构整洁与低耦合。避免为了迁就耦合了私有变量的脆弱 mock 测试而妥协生产设计；遇冲突时应重构测试以对齐公共接口契约，消除抽象泄露。
- **协议索引常量化（Specification Constants & No Magic Numbers）**：Protobuf-over-JSON 逆向数组索引统一提取为具备明确语义的规范枚举或常量（如 `wire_spec.py`），杜绝裸数字散落业务逻辑中。

## 2. 运行时与环境约束 (Termux)

Python 工具链运行在 Termux 宿主（通过 `uv` 管理虚拟环境）；Chromium（CloakBrowser）由于 glibc/bionic ABI 差异，运行在 `proot-distro` Linux 容器中。CDP 通过内置的 `cdp_client.py` 经由 WebSocket 直连。

### 2.1 环境准备与启动流程

```bash
# 1. 宿主依赖环境配置与同步
bash scripts/setup-env.sh
uv sync --extra dev   # 开发/测试/lint 必须带 dev extra

# 2. 准备受控浏览器运行时（仅首次执行）
bash scripts/setup-browser.sh

# 3. 启动服务
uv run python3 main.py server --port 8080
```

> [!NOTE]
> - `uv` 会解析 `uv.lock` 中的跨平台镜像索引与预编译 wheel，避免在 Termux 环境因编译 Rust 消耗过多内存导致 OOM。
> - `scripts/cloakbrowser_termux/run-chrome.sh` 是 Termux 下的 Chromium 包装入口，内部调用 `proot-distro login aistudio-api -- /opt/cloakbrowser/chrome "$@"` 并自动绑定路径。
> - `uv run pytest` 不会自动安装 dev extra；若项目 venv 未装 pytest/pytest-asyncio，`uv run` 可能回落到系统 pytest。开发与验收一律先 `uv sync --extra dev`。

### 2.2 前端构建与管理

- 前端位于 `web/` 目录，基于 Vite + Vue 3 + TypeScript + Tailwind CSS。
- 依赖管理与构建使用 `bun`（`bun run dev`、`bun run build`、`bun run lint`、`bun run type-check`）。
- 生产构建产物直接输出至 `src/aistudio_api/static/`，由 FastAPI 静态托管。

### 2.3 Android 伴侣端 (android/)

- 源码位于 `android/` 目录，基于 Jetpack Compose + Material 3 构建，用于移动端多账号 Cookie 提取。
- 包含多 Profile 隔离、按需启动 WebView（不常驻内存）以及 Bitwarden 自动填充适配。
- 代码规范检查使用 `ktlint`，规则配置于 `android/.editorconfig`。
- 检查命令：`ktlint "android/app/src/main/java/**/*.kt"`。

### 2.4 常见排错

| 现象 | 原因 | 处理方案 |
|---|---|---|
| `[Errno 98] address already in use` | 端口被占用或上一次服务未完全退出 | 执行 `pkill -f 'main.py server'` 后重试，或指定 `--port <other_port>` |
| `Chromium CDP endpoint on port N not ready` | proot 容器未启动或缺少 apt 依赖 | 重新运行 `bash scripts/setup-browser.sh` |
| `proot-distro login: container 'aistudio-api' is missing` | 容器尚未创建或被改名 | 运行安装脚本创建容器，或通过 `AISTUDIO_PROOT_NAME` 指定现有容器 |
| `pydantic-core` 报 `GLIBC_X.Y not found` | 使用了系统 pip 安装而非 `uv` | 清理 `.venv/` 后重新执行 `uv sync --extra dev` |
| pytest 报 `Unknown config option: asyncio_default_fixture_loop_scope` | 项目 venv 未装 dev extra，回落到系统 pytest | `uv sync --extra dev` 后重跑 |
| Windows 报 `UnicodeDecodeError: 'gbk' codec...` | 本机 locale 非 UTF-8，fixture 按 UTF-8 存储 | 设置 `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'` 后重跑 |
| Windows `bun.CMD` 报 `\n was unexpected at this time`，或 `/opt/cloakbrowser` 用例失败 | 上游测试的平台适配问题，不是业务代码回归 | 以 Linux/Termux 结果为准；不得为过测试修改契约 |

### 2.5 临时文件与目录规范 (Termux)

- **避免硬编码 `/tmp`**：Android Termux 环境中不存在系统级 `/tmp` 目录。在代码、测试、脚本或临时分析中严禁硬编码 `/tmp/`，否则会引发 `FileNotFoundError: [Errno 2] No such file or directory: '/tmp'`。
- **统一使用 `tempfile` / `$TMPDIR`**：所有临时目录或文件操作必须通过 `tempfile.gettempdir()` 或 `tempfile.mkdtemp()` 创建（自动安全解析至 `$TMPDIR` 即 `/data/data/com.termux/files/usr/tmp`），并在操作完成后通过 `try...finally` 与 `shutil.rmtree` 及时清理。

---

## 3. 代码质量与类型规范

本项目启用了严格的静态类型检查与代码质量工具链。

### 3.1 类型安全要求

- **避免 `Any` 滥用**：在公共接口、领域模型与核心流程中避免无约束的 `Any`。
- **未知数据收窄**：对于动态解析的数据，使用 `object` 配合 `isinstance` 进行类型收窄。
- **结构化定义**：数据结构使用具名 dataclass（如 `NormalizedGeminiRequest`、`AccountMeta`）或明确泛型类型标注（如 `dict[str, object]`）。

### 3.2 Ruff 代码风格与规范配置

项目采用 Ruff 作为统一的 Python 代码风格与 Lint 工具，并在 `pyproject.toml` 中开启了严格的规则检查：

- **激活规则集**：
  - `E` / `W` (pycodestyle 错误与警告)
  - `F` (Pyflakes 代码逻辑错误)
  - `I` (isort 自动 import 排序)
  - `B` (flake8-bugbear 常见陷阱与 Bug 预防)
  - `C4` (flake8-comprehensions 列表推导式优化)
  - `UP` (pyupgrade Python 3.11+ 语法升级)
  - `SIM` (flake8-simplify 代码精简，强制 `contextlib.suppress` 替代 `try-except-pass`)
  - `PTH` (flake8-use-pathlib 规范使用 Pathlib)
  - `RUF` (Ruff 专有规则检查)
  - `ASYNC` (flake8-async 异步编程反模式检查，如 `asyncio.Event` 替代轮询睡眠)
  - `PIE` (flake8-pie 冗余代码消除)
  - `Q` / `RSE` / `RET` (引号、raise 括号与返回值规范)
  - `A` (内建函数命名防覆盖)
  - `YTT` (flake8-2020 版本兼容性检查)
- **全局豁免（均有明确理由）**：
  - `B008`：FastAPI `Depends()` 注入参数的标准模式
  - `RUF001` / `RUF002` / `RUF003`：注释与文档字符串中的中文全角标点（项目文档语言为中文）
  - `ASYNC109` / `ASYNC230` / `ASYNC240`：异步函数中的超时参数与本地文件访问（单机自托管场景）
  - `E501`：行宽由 formatter 统一处理
- **按文件豁免**：`main.py` 豁免 `E402`（启动时需先注入 `sys.path`）；`tests/**` 豁免动态特性规则。
- **检查与格式化指令**：
  ```bash
  uv run ruff check .
  uv run ruff check . --fix
  uv run ruff format .
  ```

### 3.3 智能体工具调用与开发工作守则

为保障代码修改的准确性、交互效率并最大化利用大上下文模型能力，智能体与开发者必须严格遵循以下高优先级操作准则：

1. **优先读取完整文件，严禁滥用范围截断与 raw（Full Reads Only）**：
   - 查看代码、测试及文档文件时，**一律使用完整的相对文件路径读取完整文件**。
   - **严禁无端添加行号范围选择器（如 `:1-50`）或 `:raw`**，避免局部切片截断导致上下文断裂、误判函数边界，或由于行号漂移造成后续 `edit` 命中失败被拦截。
2. **批量并发读取（Batch Parallel Reads）**：
   - 涉及多个独立文件、测试文件或模块的调研比对时，在单个响应轮次中同时并发发起多个 `read` 调用。
   - 避免逐个串行读取导致多轮调用消耗上下文与拖慢交互。
3. **严禁滥用 `eval` 执行文件查看（No Eval Abuse）**：
   - 查看文件内容、定位符号与阅读代码必须使用专业的 `read` / `grep` 工具，**严禁使用 `eval` 编写 Python 文件读取脚本来替代 `read`**。
   - `eval` 仅限用于必要的复杂内存原型运算、即时逻辑求值或重现动态执行结果，非必要绝不滥用。
4. **统一强制使用 `uv` 工具链（Enforce uv）**：
   - 执行 Python 脚本、测试套件（`uv run pytest`）、代码检查（`uv run ruff check .`）与包同步（`uv sync --extra dev`）**一律通过 `uv` 调用**。
   - 严禁调用系统裸 `python`、`python3` 或 `pip`，确保所有依赖与环境均在受控的虚拟环境内运行。
5. **任务清单 (todo) 保持敏捷与批量操作**：
   - 待办事项完成时，**允许并鼓励一次性全部批量划掉**，严禁机械地一轮交互只标记一个任务。
   - 专注推动实质性工程进展，避免频繁产生仅包含待办状态翻转的无效上下文。
6. **精炼克制（No Bloat）**：
   - 方案设计、代码修改与文档更新直击痛点。
   - 避免引入形式主义的胶水层、过度抽象与冗余样板代码，保持代码与文档紧凑精炼。
7. **交付前全量审查完整 diff（Review Full Diff）**：
   - 每次提交或报告完成前，**必须完整查看整个 `git diff`（不带行数折叠与省略）**，逐行确认所有改动准确无误、没有残留调试代码或意外破损。
   - 确保 `uv run ruff check .`、`uv run pyright src tests` 和 `uv run pytest` 全部通过后再行提交；无法通过的必须给出原因、复现命令与平台差异证据。
8. **微创编辑优先与 Diff 审计守则（Prefer Edit & Mandatory Diff Inspection）**：
   - 局部功能调整或代码重构**一律优先使用精准微创的 `edit`**，避免全文件重写引入非预期的代码格式漂移与无关变动。
   - 仅在新建文件、大型嵌入脚本提取（`src/.../js/*.js`）或结构彻底颠覆时使用 `write`。**使用 `write` 进行文件覆盖后，必须在第一时间执行 `git diff` 进行全量逐行审计**，确保无意外功能遗漏、截断或回退。
9. **零 Shim 容忍与干净重构（Clean Cutover Refactoring）**：
   - 重构内部函数或类方法签名时，必须实施**一步到位的原子切换（Atomic Cutover）**。
   - **严禁保留 `@deprecated` 向下兼容存根、无意义的历史 shim 别名包装或 `_unused_*` 废弃形参**；全库一次性完整迁移所有调用方和对应测试断言。
10. **测试与契约纪律**：
    - 禁止为通过测试修改测试/配置/契约；测试失败必须区分“业务回归”与“本机平台差异”，并给出复现命令。
    - 历史蓝绿、lease、同账号双页等测试只作行为参考，按 `06-test-disposition.csv` 处置，不得作为当前契约。

### 3.4 自动化检查命令与执行守则

在提交代码改动前，必须确保以下工具链检查通过：

| 工具 / 阶段 | 命令 | 判定标准 |
|---|---|---|
| **Python 代码风格与 Lint** | `uv run ruff check .` | 0 errors |
| **Python 代码格式化** | `uv run ruff format --check .` | 全部文件已格式化 |
| **Python 类型检查** | `uv run pyright src tests` | 0 errors |
| **Python 单元测试** | `uv sync --extra dev; uv run pytest` | Linux/Termux 全绿；Windows 本机仅允许 2.4 所列平台差异；新增失败必须解释 |
| **浏览器 JS 语法校验** | `for f in src/aistudio_api/infrastructure/browser/js/*.js; do bun build "$f" --no-bundle >/dev/null; done` | 0 errors |
| **前端代码规范** | `cd web && bun run lint` | 0 errors, 0 warnings |
| **前端类型检查** | `cd web && bun run type-check` | 0 errors |
| **前端生产构建** | `cd web && bun run build` | 构建成功并更新 static 产物 |
| **Android 静态检查** | `cd android && ./gradlew :app:lintDebug :app:ktlintCheck` 或 `ktlint "android/app/src/main/java/**/*.kt"` | 0 errors |

> [!NOTE]
> **跨平台原生二进制依赖与开发工具**：
> - `pydantic-core` 等依赖在 PyPI 上无预编译 Android wheel，而 Termux 的 TUR 源无桌面端 wheel。因此 `uv.lock` 纳入 `.gitignore`：桌面端直接从 PyPI 安装对应 wheel；Termux 端通过 `setup-env.sh` 生成本地 `uv.toml` 使用 TUR 源，避免不同平台的 wheel 源锁定冲突，各平台均直接使用二进制 wheel 避免源码编译。
> - `ruff` 作为开发阶段的 Lint 工具，在 `pyproject.toml` 的 dev 依赖中配置了平台标记 `ruff>=0.8.0; sys_platform != 'android'`；桌面平台（Linux / macOS / Windows）执行 `uv sync --extra dev` 时直接自 PyPI 下载预编译 wheel。而在 Android Termux 环境下，TUR 并未打包 ruff 的 PyPI wheel，开发者可通过 Termux 原生包管理器 `pkg install -y ruff` 直接获得编译好的 aarch64 native 二进制，`setup-env.sh` / `setup-browser.sh` 会自动建立 `.venv/bin/ruff` 软链以便在虚拟环境中直接调用，普通用户生产运行无需安装。

> [!TIP]
> **避免无效重跑**：若在当前交互轮次中未发生代码或配置文件的实质性变动，无需重复执行全量类型与测试套件检查。

---

## 4. 实例、池与并发安全（核心）

### 4.1 实例定义（不可再改的边界）

- **1 实例 = 1 账号 + 1 BrowserContext + 1 Page + 1 surface（chat 或 build）**。
- 同一账号的 chat/build 不并存；需要切换就关闭当前实例再重建，或换账号。
- 实例是调度与用量单位；Context/Page 是实现细节，不作为用户可配置项。
- 实例配置使用 `count/surface/(only_tier|min_tier|max_tier)`；至少一个 tier 选择器；全局只做总上限。
- 容量按实例数计算，不再有 reservation 计数。

#### 4.1.1 实例 Tier 选择器（only_tier / min_tier / max_tier）

实例 tier 选择器定义账号可落入的闭区间；实现事实在 `domain/model_access.py` 与 `infrastructure/gateway/model_defaults.py`。

- **至少一个选择器**：`only_tier`/`min_tier`/`max_tier` 至少一个非空；`None`/空字符串视为未提供；显式提供的非空值必须是字符串，非字符串（如数字/列表）或未知 tier 名称一律 fail-fast（`model_access.py:140-160,179-182`、`model_defaults.py:317-448`）。未知/非字符串不得静默忽略，否则槽位会漂移到非预期 lane。
- **`only_tier` 精确钉住**：`Free` 归一 `Free..Free`；`Ultra` 展开 `Ultra..Ultra_20X`；`Plus`/`Pro` 保持精确。`only_tier` 不得与不同的 `min_tier/max_tier` 同时配置；允许标准化后的同区间写法（如 `only=Free,min=Free,max=Free`）（`model_access.py:184-199`）。
- **`min_tier` 单独**：`Free` → `Free..Free`；付费 tier → `min..Ultra_20X`（`model_access.py:211-214`）。
- **`max_tier` 单独**：`Free` → `Free..Free`；付费上限下界默认 `Pro`，不能低于 Pro；确需 Plus 槽位时显式 `only_tier: Plus`（`model_access.py:201-209`）。
- **`min_tier` + `max_tier`**：形成闭区间；要求 `rank(min) <= rank(max)`；Free 只能 `Free..Free`，不得与付费混合；付费区间 `min` 不能低于 Plus、`max` 不能低于 Pro（`model_access.py:216-232`）。
- **Lane 规则**：Free 选择器只落 Free lane；任何付费选择器落 paid lane；账号 tier 必须先匹配 lane，再满足 `min..max`（`model_access.py:235-240`、`instance_pool.py:109-114,182-191`）。`InstancePool.add_slot` 与 `InstanceManager.reassign` 都拒绝跨 lane（`instance_pool.py:409-446`、`instance_manager.py:424-478`）。
- **归一化与合并**：所有 Free 选择器归一为 `only=Free,min=Free,max=Free`，使 `only_tier: Free` 与旧 `min_tier: Free` 合并 count；paid `only_tier` 单独成键；`min/max` 仅在上界显式配置时把上界纳入合并键（`model_defaults.py:401-431`）。
- **运行时契约**：`InstanceSpec.from_mapping` 归一 spec，`spec.selector` 生成闭区间，`accepts_tier` 校验账号（`instance_pool.py:72-114`）；实例 API 返回 `only_tier/min_tier/max_tier`，前端类型与页面已使用这些字段（`domain/instances.py:20-35`、`instance_manager.py:1241-1253`）。

### 4.2 Free/Pro 多实例池（核心灵魂）

- 池按账号 tier 分层：Free 池与 Pro/Ultra/Plus/Paid 池；Free 模型只能命中 Free 实例；paid 模型只能命中 Pro 及以上实例。
- Free 账号实例与 Pro 账号实例可以同时在线，各自独立 BrowserContext/Page/Cookie/模板/在途计数；**严禁 Free 模型占用或消耗 Pro 账号配额**。
- 候选包含 SLEEPING/UNSTARTED 实例；消息到达时唤醒所有合格休眠/未启动实例，不局限于存活实例。
- 容量按实例数计算；不自动新开实例；容量满按路由/切号/503 处理。
- 实例数量、surface、tier 区间（`only_tier`/`min_tier`/`max_tier`）只在实例配置设置；不恢复策略组、tag、quota group、affinity 打分。

### 4.3 唯一并发原语：`active_requests`

- 只保留 `active_requests` 计数/句柄；成功、失败、取消、断连、流关闭都在 `finally` 释放。
- 取消 lease / reservation / 30s/15s 预算 / pinned / defer / slot COOLDOWN / retire_idle / `_runtime_occupancy` / 同账号双页 / 后台自动 rebind / 自动 rebalance。
- 手动关闭/改绑在有 `active_requests` 时仍可拒绝；禁用账号/应用预设的 rebind 不得直接拒绝（见 4.4）。
- 不新增第二套并发账本；统计/日志只消费事件，不参与准入。

### 4.4 切换与 rebind（D-05）

- 控制面只负责登录与保活，不是请求实例，不占池容量；控制面保持单页，不做蓝绿。
- 账号有活动实例时，Cookie 回写走实例页；否则允许独立控制页。
- 禁用账号/应用预设触发同一控制面 rebind：`RETIRING` → 停止新请求 → 有界等待 `active_requests==0` 或超时强制关闭 → 关闭旧 Page/Context → 切换账号/预设。期间不新建新页；不得无界等待；失败必须显式返回，不得让 registry/UI 与浏览器会话不一致。
- 手动显式关闭旧实例再重建允许；后台自动 rebind、pinned、自动 rebalance 丢弃。
- 禁止先建新页再 drain（blue-green）；同一账号最多 1 个 active Page。

### 4.5 Page/Context 清理（D-10/D-11）

- `connect_page`/prune 关闭所有非当前请求、非当前活跃账号的 Page/Context；不允许页面/context 泄露。
- 只允许一个明确处于 drain 的旧页，且有界等待超时后强制关闭；drain 只发生在旧 Page/Context 上，期间不新建新页；不得把“1 active + 1 drain”当页预算例外。
- 关闭即释放实例；失败/stale 才手动重启，禁止自动 stale 复活；idle browser 自动回收默认关闭。

### 4.6 实机防风控铁律

- **严禁任何 Worker / 子 Agent 私自启动真实服务（`main.py server`）或拉起真实浏览器进程！** 每次冷启动均会触发 BotGuard 探针，短时间重复启停会导致账号被 Google 风控封禁。即使获批执行实机测试，全流程也只允许由主控或用户在单次常驻进程中完成，严禁反复重启服务。
- 多实例池启用时，启动预热只允许由 `InstanceManager.startup()` 在池会话（`PooledSession`）上完成；控制面 `client.session` 不得在启动时自动开页/跑 BotGuard。控制面仅在按需场景（无活跃实例、手动导入 Cookie、保活）打开，且保持单页。
- 离线开发/回归默认 mock/fixture，不得以“验证启动”为名启动真实服务、真实浏览器或发起网络请求。
- **前端 UI Debug 沙盒例外（强制规范）**：严禁在默认生产模式（`ui_debug=false`）下启动服务或拉起真实浏览器；凡涉及前端 UI 变更，Worker 与 Reviewer 必须在强制注入环境变量 `AISTUDIO_UI_DEBUG=1`（开启 `diagnostics.ui_debug=true` 的纯内存/临时目录隔离沙盒，100% 阻断真实浏览器与外网请求）的前提下，在 `127.0.0.1:<随机空闲端口>` 启动临时沙盒服务，使用 Playwright 等浏览器工具在多分辨率（`1440x900` / `1024x900` / `768x900`）下实测页面交互与检查样式；测试结束后必须在 `finally` 中立即关闭临时服务进程，并确认端口关闭、无进程残留、临时目录已清理。

### 4.7 浏览器进程与 CDP（D-02/D-03）

- 保留 pipe watchdog，只负责父进程死亡后的孤儿 Chromium 清理；不得据此做账号 stale 复活、自动 rebind 或自动重启浏览器会话。
- 只保留 WebSocket ping；不恢复 `set_liveness_checker` 与 `Runtime.evaluate` 轮询；半开连接由命令超时与断线失败兜底；页面崩溃/断线立即失败 pending futures。
- 页面内 JavaScript 交互使用局部闭包 Promise 返回数据，不在 `window` 上遗留全局共享状态。
- 保留 CloakBrowser 低内存/伪装参数：`--renderer-process-limit=1`、`--in-process-gpu`、128MB V8 限制、`--use-gl=angle --use-angle=swiftshader`、`--mute-audio`、`--fingerprint-platform=windows`、`--fingerprint-timezone`；移除 `--disable-audio` 与 `--disable-software-rasterizer`。
- 不访问真实 Google/真实账号/真实 IP，除非用户单独授权；默认 mock/fake CDP/fixture。

### 4.8 禁止恢复清单

- 旧 lease/租约、reservation、pinned、defer、同账号双页、后台自动 rebind、自动 rebalance、策略组 `groups`、`channel_registry`、账号 tag 路由、quota group、`ensure_active_account`/`try_switch_account` 全局回退、死熔断器。
- 发现旧 fork 代码含上述机制时，只能按 `06`/`08-09` 改写，不得原样搬运。

---

## 5. 路由、错误与重试

- **Free/Pro 分流**：Free 模型走 Free 池；paid 模型必须 Pro 及以上，且先 chat；实例 tier 区间只决定账号能否落槽，不改变模型准入。
- **Paid 模型 tier 不足（D-04/D-06）**：领域错误 → API 层按 OpenAI/Gemini/Claude/Responses 映射 403；错误体含 `tier/model/required_tier/current_tier`；不切号、不自动降级到 free、不得用 503 表达 tier 不足。
- **429**：先同账号重试 → 唤醒/切现有池内实例 → 保存消息后切换再重试 → 耗尽 503 + 等待/管理页提示；已出正文不切号。
- **鉴权 403**：不加硬冷却锁（`auth_cooldown=0.0`）；首次 403 重新签名并原位重试，第二次 403 才切换到健康账号；`auth_errors` 只累加计数，调度准入不读取，不存在动态避让；Paid 模型 `TierRequiredError` 403 不切号。
- 连续切号上限保留；丢弃全局回退与死熔断器；不请求期切号处理 403。
- **`quota_scope`（D-01）**：默认 `account`（chat/build 共用额度），可选 `account_surface`（分开）；只影响 `AccountStats` 记账键与 `/stats`/配额展示/429 冷却口径；不新增实例/Context/Page；不请求期自动改口径。
- 上游瞬态错误：先重试，再路由，再重试；现有实例都试过或触发连续切号上限后报错。
- 失败/stale 手动重启；不自动换面/换组；拒绝时显式返回 `surface_*` 错误。

---

## 6. 账号、Cookie、预设与鉴权

### 6.1 账号与 Cookie

- 注册表/`auth.json`/`meta.json`/`presets.json` 等持久化必须用 `account_store._atomic_write_json`（临时文件 + `os.replace`）。
- Cookie 跨域保真：不得把 `accounts.google.com` 的 domain 压平成 `.google.com`；保留 LSID/GAPS/ACCOUNT_CHOOSER 等关键项；导入/解析/注入/回写/探活/删除/重复标记/身份校验保留。
- 外部导入过滤旧设备/跨 IP 易腐败凭据（`OSID`、`__Secure-OSID`、`SIDCC`、`_ga` 等），保留核心认证项（`SID`、`SAPISID`、`1PSIDTS`）；浏览器访问 AI Studio 时自动协商有效 OSID。
- tier 以上游原生解析为准，fork 只保存/刷新；24h 调度归 P6。

### 6.2 分组与预设

- 分组 CRUD + 分组过滤 UI 保留；`AccountPreset` 存 `data/accounts/presets.json`。
- **D-08**：`_load_presets` 与 `_save_presets` 不得在非重入锁内互相调用；写失败必须上报且不得留下半写文件；只读目录/删除文件场景不得死锁；不得静默吞异常换“启动成功”。
- **D-13**：不实现 `notes` 字段/API；删除 `updateNotes`、`batchToggleGroup` 及后端端点；不保留 notes 列/前端类型。

### 6.3 API 鉴权（D-14）

- 新增 `security.api_allow_anonymous`，默认 false；空 API Key ≠ 开放。
- 管理页只由 `AISTUDIO_WEB_PASSWORD` 控制：空 = 免密，不报错；管理页不被 API Key 锁死。
- API Key 与 Web 密码互不复用；不引入 IP/CIDR/设备白名单/“本机-局域网模式”；CF Tunnel 绑定逻辑不变。
- API Key 只显示掩码；控制台登录用服务端会话，不用 localStorage token。

---

## 7. 保活、日志与协议面

### 7.1 保活（P6）

- 保留 keepalive 服务、24h auth update、24h tier/email 刷新；24h Cookie 回写覆盖池内实例与 build-only，按逐 slot 实际成功记账。
- 保留 429 额度冷却（60/300/600）与美西午夜跨天重置；手动切号/手动触发保活/状态查询入口保留。
- 删除后台 auto_switch/rebalance/legacy rotator 回退；请求驱动 429 切号归 P2；手动切号不走蓝绿。
- `active_activity` 可选（默认关；上游已无 idle 回收，置 disable）；idle browser 自动回收默认关闭；消息唤醒 SLEEPING/UNSTARTED；失败/stale 手动重启。
- job 锁：单全局串行；busy 跳过且不推进 `next_due`；失败按 job 短重试。

### 7.2 日志（P7）

- 存储：ring 5000 + 每日文件 7 天归档；日志页 SSE；支持级别过滤、搜索、暂停、1/3/7 天导出。
- dump 只落 `safe_*`，默认关闭；禁止明文 headers/raw_body/cookie/token/API key/authorization。
- root DEBUG 保留，但必须按命名空间/三方库过滤 WARP/tunnel/CDP/websockets/httpx 噪音。
- 统计后端单一事件账本：request/attempt/usage/error/latency/account/model/surface/paid；client 与 attempt 分区显示，绝不混算成功率；统计页后置。

### 7.3 协议与生成

- OpenAI Chat、Claude Messages、OpenAI Responses 与 Gemini 四协议已完成并默认启用；不再按阶段先后排序。
- ATG 保留，默认 off，按协议通过 `mode_by_format` 开启；`text_model_pattern` / `non_text_model_pattern` 仅用于 text/non_text 过滤；tool_calls、thinking、images、usage、response_id、thought_signature 全部保留。
- chat 继续 capture/replay + wire/JSPB，不改成直接 HTTP。
- 账号切换、429 限流或 403 鉴权重试时清理模板缓存（`client.clear_templates()`），避免跨账号复用；快照签名按每次请求哈希实时生成，不跨请求共享；初次握手截获基线模板后，任意目标模型通过 `capture_template(model)` 纯内存适配。
- **PG-01..PG-04 是工具调用硬门槛**：functionCall 缺 thoughtSignature 兜底、首事件前真实 HTTP 4xx/5xx、finish-only/工具流终止兜底、functionResponse/functionCall 强制 Struct + 脱敏 `[toolshape]` 摘要。
- countTokens：默认把 system/tools/content 统一打包字符串计数；官方 key 仅用于精确 countTokens，不得生成/回显/进日志。
- 流式首事件统一预读，尽量保留真实 HTTP 状态；已出正文不切号；非流式与同账号空响应重试保留。

---

## 8. 配置口径

- 单一 schema：YAML + env + 默认值；实例配置 `count/surface/(only_tier|min_tier|max_tier)`；至少一个 tier 选择器，Free/paid lane 不混合；全局只做总上限。`defaults/config.schema.json` 覆盖 `defaults/config.yaml` 的顶层键；跨字段语义仍由 `domain/model_access` 与 `model_defaults` 在运行期校验。
- **D-07**：`_load_yaml_config` 不得浅替换 `rotator` 块；缺失键用默认值深合并；启动时校验类型/必填；请求路径不得抛 `KeyError`；非关键配置回退默认必须显式记录。
- 删除 `channel_registry`，只保留 `surface=chat/build`；删除旧 groups/channel/打分配置；保留 upstream rotator 的账号状态与 429 冷却能力。
- 保留热更新与原子写回；legacy 配置键一次性迁移后删除；关键配置 fail-fast，非关键配置回退默认。
- **D-12**：新设置按现有 UI（`SettingsView`/`ApiKeyManagerCard` 等）增量承载，不重构 Web 布局/导航；`hash_include_code` 是诊断开关，不进普通用户 UI。
- **配置跟踪（已迁移）**：根 `config.yaml` 是本地运行时配置，untracked + gitignored（`.gitignore:15:/config.yaml`），不得入库；`defaults/config.yaml` 是唯一 tracked 的分发默认。`git ls-files config.yaml` 为空，与 Q-04/changelog 一致。
- **B1（P9-pre）迁移**：默认配置以 `defaults/config.yaml` 为 tracked 基准；根 `config.yaml` 已从版本控制移除并加入 ignore；features 变更只提交 `defaults/config.yaml`，根运行态内容不提交。

---

## 9. 代码结构索引

当前主仓结构：

```text
aistudio-api/
├── web/                           # Web 管理控制台 (Vite + Vue 3 + TS + Tailwind)
│   ├── src/
│   │   ├── api/                   # REST 请求封装
│   │   ├── types/                 # TypeScript 类型契约
│   │   ├── stores/                # Pinia 状态管理
│   │   ├── components/            # UI 与业务解耦组件
│   │   ├── views/                 # 控制面板 / 账号 / 系统与模型配置 / 日志
│   │   └── router/                # Vue Router 路由与守卫
│   └── vite.config.ts             # 构建配置 (输出至 src/aistudio_api/static)
├── android/                       # Android 伴侣端：多资料凭据提取器
├── src/aistudio_api/
│   ├── api/                       # FastAPI 接口层与协议适配
│   │   ├── app.py                 # 应用生命周期、异常映射与静态托管
│   │   ├── dependencies.py        # API Key / Web 会话鉴权注入
│   │   ├── routes_gemini.py       # /v1beta 生成与 countTokens
│   │   ├── routes_openai.py       # /v1/chat/completions 与 /v1/models
│   │   ├── routes_claude.py       # /v1/messages 与 count_tokens
│   │   ├── routes_responses.py    # /v1/responses 与 input_tokens
│   │   ├── routes_models.py       # 模型发现
│   │   ├── routes_accounts.py     # 账号管理与探活
│   │   ├── routes_instances.py    # 实例槽位生命周期
│   │   ├── routes_keepalive.py    # 保活状态与手动触发
│   │   ├── routes_system.py       # 统计、配置、API Key 与热重载
│   │   ├── routes_logs.py         # 日志 SSE 与导出
│   │   ├── routes_debug*.py       # UI Debug 沙盒接口
│   │   ├── sse_keepalive.py       # 首事件预读与 SSE 心跳
│   │   ├── ui_sandbox.py          # 纯离线 UI 调试沙盒
│   │   ├── format_errors.py       # 四协议错误渲染
│   │   └── state.py               # 运行时全局单例容器
│   ├── application/               # 业务应用层
│   │   ├── generation_service.py  # 协议中立生成主链
│   │   ├── routing_pipeline.py    # 候选、重试、切号与 active_requests
│   │   ├── routing_eligibility.py # surface / lane / tier 准入
│   │   ├── routing_safety.py      # 单请求切号预算
│   │   ├── instance_manager.py    # 实例生命周期与换绑
│   │   ├── account_keepalive.py   # 24h Cookie / 元数据保活
│   │   ├── account_rotator.py     # 账号统计、429 冷却与跨天重置
│   │   ├── account_service.py     # 账号存储与激活协调
│   │   ├── chat_service.py        # 请求规范化与多模态处理
│   │   ├── signature_cache.py     # PG-01 思维签名缓存
│   │   ├── token_counter.py       # countTokens 打包与上游调用
│   │   ├── contracts/             # 四协议请求/响应 DTO
│   │   ├── format_converters/     # OpenAI / Claude / Responses 转换
│   │   └── atg/                   # 防截断合成工具网关
│   ├── domain/                    # 纯净领域模型与异常体系
│   │   ├── errors.py              # 业务异常定义
│   │   ├── instances.py           # Instance / InstanceState
│   │   ├── model_access.py        # tier 选择器与模型准入
│   │   └── models.py              # 领域数据结构
│   ├── infrastructure/            # 基础设施层
│   │   ├── account/               # Cookie 解析、凭据持久化与元数据刷新
│   │   ├── browser/               # CDP 客户端、CloakBrowser 进程与指纹
│   │   ├── gateway/               # 会话/池/捕获/重放/Wire/模型发现
│   │   └── utils/                 # 统一日志、广播与通用工具
├── config.yaml                    # 本地运行时配置 (untracked + gitignored；不入库)
├── defaults/config.yaml           # 单一分发默认配置 (tracked)
├── main.py                        # 本地统一启动入口
└── tests/                         # 单元测试套件
    ├── conftest.py                # 气密性隔离配置
    ├── fixtures/                  # 真实 Protobuf JSON 请求与响应报文
    └── unit/                      # 按职责划分的单元测试模块
```

---

## 10. 智能体工作流与发布纪律

### 10.1 角色与报告

- **主 agent = integrator**：唯一提交权；负责顺序、分派、回收报告、验收；不得把多个 domain 打包给少量 agent。
- **worker**：每次只接一个 `Dxx-NNN` 或一个明确文件域；在主工作区直接修改并跑通 `uv run pytest`；不得改 `05/06/08-09`/架构文档；报告必须含改动文件、测试项、预期、实际、命令与结果。
- **reviewer**：只读；对照 `06`/`08-09` 检查实际 diff 与测试结果；报告写 `domains/Px/reviews/REV-<Step>.md`；必须含 PASS/CONDITIONAL/FAIL、file:line、pytest 实跑、只读声明。
- **前端 Review 截图归档（强制）**：凡涉及前端 UI 的审查步骤，Reviewer 必须在 `docs/dev/rebuild/domains/<Domain>/reviews/assets/` 保留 1~2 张最具代表性的完整页面实测截图，并在对应 `REV-*.md` 中引用，便于后续直观追溯 UI 样貌。
- **复杂任务并行拆分（强制）**：当单步任务涉及多个独立页面或跨模块改动时，主控 Integrator 必须按文件域拆分为多个独立的并行子 Agent（`task`）执行；每个子 Agent 只读取/修改自己职责范围内的少量文件，严禁单 Agent 同时加载全站导致上下文膨胀、质量下降。
- worker/reviewer 报告必须原样交用户；用户审计前主 agent 不直接改代码/文档，不自行派下一步。

### 10.2 每步闭环

- 开工前主 agent 用 10 行以内大白话说明：改哪几个文件、实现什么效果、跑哪几个测试。
- 先写测试项 + 预期，再实现；实现后报实际；不得预填证据。
- 跑 `uv run pytest`；必要时跑 ruff/pyright；全量审查 `git diff`。
- reviewer 独立审；报告落盘；用户审计确认后才 commit；单步问题只做当前步骤内局部返工，严禁推翻整体计划或重写 `05/06/08-09`。
- **UI 前置设计确认（强制）**：凡涉及前端 UI 界面、设置卡片、按钮或交互变更的步骤，主 Agent 严禁仅给简短摘要就直接派 Worker 改代码。开工前必须先只读盘点并提交《UI 页面与交互设计清单》，至少包含：①所在页面与卡片位置；②控件类型、中文文案、选项与默认值；③点击后的 API 调用、loading/成功/错误反馈与确认弹窗逻辑；④拟清理的废弃组件/按钮。必须等用户审阅并明确确认后，才准派 Worker 修改 .vue 代码；每次 UI 修改完工必须执行 bun run build 更新 src/aistudio_api/static 产物。
- **UI 验收闭环（强制）**：UI 步骤必须包含 `AISTUDIO_UI_DEBUG=1` 沙盒 + Playwright `1440x900`/`1024x900`/`768x900` 实测；Reviewer 必须归档 1~2 张完整页面截图并在 `REV-*.md` 引用；多页面或跨模块步骤按文件域并行拆分 Worker/Reviewer，禁止单 Agent 一锅炖。

### 10.3 施工阶段顺序（历史追溯）

B0（`AGENT.md`）→ B1（P9-pre）→ B2-1（P1/D02）→ B2-2（P3-D03）→ B2-3（P3-D04 多实例池）→ B2-4（P2/D05 路由）→ B3（P6/D08 保活）→ B4（P7/D09 日志）→ B5a（PG-01..04）→ B5b（P5 最小 OpenAI/Gemini/ATG 闭环）。

该顺序仅用于追溯已完成施工；当前日常工作按单工作区常态增量推进。尚未执行的 C5b 合并、打标与清理流程仍按既定发布口径执行，不因工作区常态化而删除或改写。

### 10.4 硬禁令

- 不预写纸面 PKG；不把台账当施工清单；不把 534 当 MVP。
- 不因单步失败推翻 `05/06/08-09` 或重写整体方案。
- 不 merge/checkout 旧 `rebuild`；不 push，除非用户明确授权本次推送并指定分支/范围；不 rewrite history。
- 不访问真实 Google/真实账号/真实 IP，除非用户单独授权；默认离线 fixture/mock。
- **实机防风控**：严禁 Worker / 子 Agent 私自启动真实服务（`main.py server`）或拉起真实浏览器进程；获批实机测试也只允许主控/用户在单次常驻进程中完成，严禁反复重启（见 4.6）。
- 不为过测试修改测试/配置/契约；不伪造审计、不预填证据、不用文档进度冒充迁移进度。
- 不新增 D-xx/Gate/顺序/范围；需要时停下问用户。
- 不把函数签名/底层实现甩给用户裁决；向用户汇报用大白话，说明功能保留、执行顺序、可运行结果。
- `archive/` 下所有文件（含 `batch-drafts-20261029/`）均为历史留底，严禁作为施工依据。

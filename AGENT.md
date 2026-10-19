# 智能体协作与开发规范 (AGENT.md)

本文档面向所有参与本项目的代码代理（Coding Agents）与开发者，明确核心架构共识、运行环境约束与代码规范。

---

## 目录

- [1. 核心架构与设计原则](#1-核心架构与设计原则)
- [2. 运行时与环境约束 (Termux)](#2-运行时与环境约束-termux)
- [3. 代码质量与类型规范](#3-代码质量与类型规范)
- [4. 并发安全与多账号守则](#4-并发安全与多账号守则)
- [5. 代码结构索引](#5-代码结构索引)
- [6. 功能开关规范 (features)](#6-功能开关规范-features)

---

## 1. 核心架构与设计原则

`aistudio-api` 是 Google AI Studio 反向代理网关，提供原生 **Gemini API 兼容接口**（`/v1beta/...`）。

- **协议面分层**：原生 Gemini API（`/v1beta/...`）是第一协议面；WP8 起同时提供 OpenAI Chat Completions（`/v1/chat/completions`、`/v1/models`）与 Anthropic Messages（`/v1/messages`）兼容面。所有请求统一归一到 `GeminiGenerateContentRequest`，响应在格式边界逆变换；核心生成管线不得散落协议判断。
- **轻量 CDP 驱动**：使用纯 Python 异步 WebSocket 直连 Chrome DevTools Protocol（CDP），不依赖 Node.js、Playwright 或 Selenium。
- **浏览器内 XHR Replay**：在 MakerSuite 页面上下文中执行携带 `withCredentials = true` 的异步 XHR 请求，天然复用完整的 Cookie 会话、BotGuard 快照与环境指纹。
- **代码即事实标准（Code as Ground Truth）**：通过全量测试验证的代码是系统的事实标准。当技术规范文档与真实代码逻辑出现分歧时以代码为准，并在重构过程中同步更新文档。
- **架构事实优先（Fact & Architecture over Brittle Tests）**：保持架构整洁与低耦合。避免为了迁就耦合了私有变量的脆弱 mock 测试而妥协生产设计；遇冲突时应重构测试以对齐公共接口契约，消除抽象泄露。
- **协议索引常量化（Specification Constants & No Magic Numbers）**：Protobuf-over-JSON 逆向数组索引统一提取为具备明确语义的规范枚举或常量（如 `wire_spec.py`），杜绝裸数字散落业务逻辑中。

## 2. 运行时与环境约束 (Termux)

Python 工具链运行在 Termux 宿主（通过 `uv` 管理虚拟环境）；Chromium（CloakBrowser）由于 glibc/bionic ABI 差异，运行在 `proot-distro` Linux 容器中。CDP 通过内置的 `cdp_client.py` 经由 WebSocket 直连。

### 2.1 环境准备与启动流程

```bash
# 1. 宿主依赖环境配置与同步
bash scripts/setup-env.sh
uv sync

# 2. 准备受控浏览器运行时（仅首次执行）
bash scripts/setup-browser.sh

# 3. 启动服务
uv run python3 main.py server --port 8080
```

> [!NOTE]
> - `uv` 会解析 `uv.lock` 中的跨平台镜像索引与预编译 wheel，避免在 Termux 环境因编译 Rust 消耗过多内存导致 OOM。
> - `scripts/cloakbrowser_termux/run-chrome.sh` 是 Termux 下的 Chromium 包装入口，内部调用 `proot-distro login aistudio-api -- /opt/cloakbrowser/chrome "$@"` 并自动绑定路径。

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
| `pydantic-core` 报 `GLIBC_X.Y not found` | 使用了系统 pip 安装而非 `uv` | 清理 `.venv/` 后重新执行 `uv sync` |

### 2.5 临时文件与目录规范 (Termux)

- **避免硬编码 `/tmp`**：Android Termux 环境中不存在系统级 `/tmp` 目录。在代码、测试、脚本或临时分析中严禁硬编码 `/tmp/`，否则会引发 `FileNotFoundError: [Errno 2] No such file or directory: '/tmp'`。
- **统一使用 `tempfile` / `$TMPDIR`**：所有临时目录或文件操作必须通过 `tempfile.gettempdir()` 或 `tempfile.mkdtemp()` 创建（自动安全解析至 `$TMPDIR` 即 `/data/data/com.termux/files/usr/tmp`），并在操作完成后通过 `try...finally` 与 `shutil.rmtree` 及时清理。
---

## 3. 代码质量与类型规范

本项目启用了严格的静态类型检查与代码质量工具链：

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
- **按文件豁免**：`main.py` 豁免 `E402`（启动时需先注入 `sys.path`）；`tests/**` 豁免动态殊性规则
- **检查与格式化指令**：
  ```bash
  # 代码风格与 Lint 检查
  ruff check .
  # 自动格式化与 import 排序修复
  ruff check . --fix
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
   - 执行 Python 脚本、测试套件（`uv run pytest`）、代码检查（`uv run ruff check .`）与包同步（`uv sync`）**一律通过 `uv` 调用**。
   - 严禁调用系统裸 `python`、`python3` 或 `pip`，确保所有依赖与环境均在受控的虚拟环境内运行。
5. **任务清单 (todo) 保持敏捷与批量操作**：
   - 待办事项完成时，**允许并鼓励一次性全部批量划掉**，严禁机械地一轮交互只标记一个任务。
   - 专注推动实质性工程进展，避免频繁产生仅包含待办状态翻转的无效上下文。
6. **精炼克制（No Bloat）**：
   - 方案设计、代码修改与文档更新直击痛点。
   - 避免引入形式主义的胶水层、过度抽象与冗余样板代码，保持代码与文档紧凑精炼。
7. **交付前全量审查完整 diff（Review Full Diff）**：
   - 每次提交或报告完成前，**必须完整查看整个 `git diff`（不带行数折叠与省略）**，逐行确认所有改动准确无误、没有残留调试代码或意外破损。
   - 确保 `uv run ruff check .`、`bun x pyright` 和 `uv run pytest` 全部零报错通过后再行提交。
8. **微创编辑优先与 Diff 审计守则（Prefer Edit & Mandatory Diff Inspection）**：
   - 局部功能调整或代码重构**一律优先使用精准微创的 `edit`**，避免全文件重写引入非预期的代码格式漂移与无关变动。
   - 仅在新建文件、大型嵌入脚本提取（`src/.../js/*.js`）或结构彻底颠覆时使用 `write`。**使用 `write` 进行文件覆盖后，必须在第一时间执行 `git diff` 进行全量逐行审计**，确保无意外功能遗漏、截断或回退。
9. **零 Shim 容忍与干净重构（Clean Cutover Refactoring）**：
   - 重构内部函数或类方法签名时，必须实施**一步到位的原子切换（Atomic Cutover）**。
   - **严禁保留 `@deprecated` 向下兼容存根、无意义的历史 shim 别名包装或 `_unused_*` 废弃形参**；全库一次性完整迁移所有调用方和对应测试断言。

### 3.4 自动化检查命令与执行守则

在提交代码改动前，必须确保以下工具链检查通过：

| 工具 / 阶段 | 命令 | 判定标准 |
|---|---|---|
| **Python 代码风格与 Lint** | `uv run ruff check .` | 0 errors |
| **Python 代码格式化** | `uv run ruff format --check .` | 155 files already formatted |
| **Python 类型检查** | `bun x pyright src tests` | 0 errors |
| **Python 单元测试** | `uv run pytest` | 全部通过 (571 passed) |
| **浏览器 JS 语法校验** | `for f in src/aistudio_api/infrastructure/browser/js/*.js; do bun build "$f" --no-bundle >/dev/null; done` | 0 errors |
| **前端代码规范** | `cd web && bun run lint` | 0 errors, 0 warnings |
| **前端类型检查** | `cd web && bun run type-check` | 0 errors |
| **前端生产构建** | `cd web && bun run build` | 构建成功并更新 static 产物 |
| **Android 静态检查 (Lint & 代码规范)** | `cd android && ./gradlew :app:lintDebug :app:ktlintCheck` 或 `ktlint "android/app/src/main/java/**/*.kt"` | 0 errors |
> [!NOTE]
> **跨平台原生二进制依赖与开发工具**：
> - `pydantic-core` 等依赖在 PyPI 上无预编译 Android wheel，而 Termux 的 TUR 源无桌面端 wheel。因此 `uv.lock` 纳入 `.gitignore`：桌面端直接从 PyPI 安装对应 wheel；Termux 端通过 `setup-env.sh` 生成本地 `uv.toml` 使用 TUR 源，避免不同平台的 wheel 源锁定冲突，各平台均直接使用二进制 wheel 避免源码编译。
> - `ruff` 作为开发阶段的 Lint 工具，在 `pyproject.toml` 的 dev 依赖中配置了平台标记 `ruff>=0.8.0; sys_platform != 'android'`；桌面平台（Linux / macOS / Windows）执行 `uv sync --extra dev` 时直接自 PyPI 下载预编译 wheel。而在 Android Termux 环境下，TUR 并未打包 ruff 的 PyPI wheel，开发者可通过 Termux 原生包管理器 `pkg install -y ruff` 直接获得编译好的 aarch64 native 二进制，`setup-env.sh` / `setup-browser.sh` 会自动建立 `.venv/bin/ruff` 软链以便在虚拟环境中直接调用，普通用户生产运行无需安装。

> [!TIP]
> **避免无效重跑**：若在当前交互轮次中未发生代码或配置文件的实质性变动，无需重复执行全量类型与测试套件检查。

## 4. 并发安全与多账号守则

> [!IMPORTANT]
> 1. **原子化文件持久化**：所有持久化文件（`registry.json`、`auth.json`、`meta.json` 等）操作必须使用 `account_store._atomic_write_json`（写入临时文件后通过 `os.replace` 原子替换），防止并发写入截断。
2. **蓝绿会话平滑热切与就绪门机制**：切换账号时，`BrowserSession` 在后台创建独立新标签页，注入 Cookie、完成页面导航与 BotGuard 握手验证；验证通过后原子翻转 `SessionReadinessGate` 并将新上下文设为就绪。旧会话上下文加入退休队列，优雅等待在途流式请求排干（`decr_in_flight()` 触发 `drain_event`）后再行关闭，保障流式请求平滑过渡零中断。
3. **缓存与模板隔离**：账号切换、429 限流或 403 鉴权重试时，调用 `client.clear_templates()`，避免跨账号复用请求模板。快照签名按每次请求哈希实时生成，绝不跨请求共享。初次握手截获基线模板后，任意目标模型通过 `capture_template(model)` 纯内存动态适配参数。
4. **鉴权故障快速隔离与自愈**：当遇到 `The caller does not have permission` (403) 时，立即将当前账号置入 `auth_cooldown` 并快速故障转移至健康账号；单账号或备用号耗尽时自动触发在位强制刷新与 BotGuard 重握手自愈。
5. **无全局 DOM 污染**：页面内 JavaScript 交互使用局部闭包 `Promise` 返回数据，不在 `window` 对象上遗留全局共享状态。
6. **Cookie 智能精简与天然协商**：外部导入 Cookie 时自动过滤旧设备或跨 IP 绑定的易腐败凭据（`OSID`、`__Secure-OSID`、`SIDCC` 及 `_ga` 等追踪标记），保留核心认证项（`SID`、`SAPISID`、`1PSIDTS`）。浏览器访问 AI Studio 时自动协商出绑定当前网络/TLS 的全新有效 `OSID`，杜绝 403 权限拒绝。
7. **过期凭据排除调度**：账号信息刷新（`POST /accounts/refresh`，直连 `GetAiStudioBenefitTier`）遇 401 即落 `expired=true` 并自动排除出调度池，直至刷新成功或重新导入 cookies；全部账号过期时显式报错（`credential expired, re-import cookies`），绝不静默跳过；5xx/网络等非权威失败不改动账号状态。
7. **真实环境伪装与低内存协同**：保留 `--renderer-process-limit=1`、`--in-process-gpu` 与 128MB V8 内存限制以保障 Android 低 RAM 运行；移除 `--disable-software-rasterizer` 并启用 `--use-gl=angle --use-angle=swiftshader` 恢复软件 WebGL 上下文，保留 `--mute-audio` 并移除 `--disable-audio` 保护 AudioContext；统一注入 `--fingerprint-platform=windows` 伪装为 Windows 桌面环境，并通过原生 `--fingerprint-timezone` 与 Wire 协议层保持时区/位置严格一致。
8. **账号接口解耦与 SPA 缓存隔离**：对外标准 REST 接口统一位于 `/api/accounts`（保持 `/accounts` 向下兼容），并在 SPA 单页应用与 API 响应中下发 `Vary: Accept` 与 `Cache-Control: no-store`，杜绝浏览器与代理网关发生缓存错乱。
9. **双保活不可互相替代**：定时切号（`auto_switch`，长周期均衡 + 真实页面活动）与自动刷新 Auth（`keepalive_auth_update`，cookies/origins → `auth.json`）必须同时保留；元数据刷新（`keepalive_metadata_refresh`，tier/email/expired → `registry.json`/`meta.json`）只做体检，不能替代凭证回写。三者独立开关。
10. **切号与回写安全**：自动/手动切号共用 `account_orchestrator` 切号锁；切号门槛必须覆盖 chat `session.in_flight_count` 与 build `in_flight_count`；切号时关闭/重建 build 页；切号前保存旧账号 Auth，切后保存新账号 Auth。手动刷新与自动元数据刷新共用同一 `AccountInfoRefresher` 实例和 409 互斥。
11. **配置双写**：本地根 `config.yaml` 是运行时实际配置且不被 git 追踪；分发默认配置是 `defaults/config.yaml`（tracked）。任何 `features` 变更必须两处同步，提交与证据以 `defaults/config.yaml` 为准。

## 5. 代码结构索引

```text
aistudio-api/
├── web/                           # Web 管理控制台 (Vite + Vue 3 + TS + Tailwind)
│   ├── src/
│   │   ├── api/                   # REST 请求封装 (auth, accounts, system, models)
│   │   ├── types/                 # TypeScript 类型契约
│   │   ├── stores/                # Pinia 状态管理
│   │   ├── components/            # UI 与业务解耦组件
│   │   ├── views/                 # 页面路由视图 (Dashboard, Accounts, Settings, Login)
│   │   └── router/                # Vue Router 路由与守卫
│   └── vite.config.ts             # 构建配置 (输出至 src/aistudio_api/static)
├── android/                       # Android 伴侣端：多资料凭据提取器 (Jetpack Compose + Material 3)
│   ├── app/src/main/
│   │   ├── AndroidManifest.xml    # 声明 BROWSABLE / http / https 意图，适配 Bitwarden
│   │   └── java/com/aistudio/extractor/
│   │       ├── MainActivity.kt    # 单 Activity、SAF 导出/备份导入
│   │       ├── data/              # ProfileItem / AccountBundle / ProfileManager
│   │       └── ui/                # Material 3 主题、MainScreen、按需挂载的 LoginScreen
│   ├── build.gradle.kts           # 根构建脚本
│   └── .editorconfig              # ktlint 代码规范配置
├── src/aistudio_api/
│   ├── api/                       # HTTP 接口层 (FastAPI)
│   │   ├── app.py                 # 应用生命周期与静态托管
│   │   ├── dependencies.py        # API Key 与 Token 鉴权注入
│   │   ├── routes_gemini.py       # /v1beta/models/*:generateContent 路由
│   │   ├── routes_models.py       # /v1beta/models 模型发现路由
│   │   ├── routes_openai.py       # /v1/chat/completions 与 /v1/models (WP8)
│   │   ├── routes_claude.py       # /v1/messages (WP8)
│   │   ├── routes_accounts.py     # 账号管理与探活路由
│   │   ├── routes_keepalive.py    # 保活状态查询与手动触发路由
│   │   ├── routes_system.py       # 监控指标、日志配置与 config.yaml 热重载路由
│   │   ├── state.py               # 运行时全局单例容器 (RuntimeState)
│   │   ├── schemas.py             # Gemini API 请求与校验模型
│   │   ├── schemas_openai.py      # OpenAI Chat Completions 请求模型 (WP8)
│   │   ├── schemas_claude.py      # Anthropic Messages 请求模型 (WP8)
│   │   ├── format_errors.py       # 协议面错误形状、路径分流与格式开关 (WP8)
│   │   ├── responses.py           # Gemini API 响应格式化辅助工具
│   │   └── response_models.py     # API Pydantic 响应模型
│   ├── application/               # 业务应用层
│   │   ├── account_rotator.py     # 黏性账号调度器 (按模型记录 429 与太平洋午夜重置)
│   │   ├── account_service.py     # 账号存储与激活上下文协调
│   │   ├── account_keepalive.py   # 双保活调度器 (Auth 回写/元数据刷新/定时切号/轻量活动)
│   │   ├── api_service_gemini.py  # Gemini 协议渲染层 (SSE/响应/错误格式)
│   │   ├── generation_service.py  # 协议无关生成管线 (ATG/重试/切号/流式事件)
│   │   ├── upstream_errors.py     # 上游错误分类与重试/切号处理
│   │   ├── format_converters/     # OpenAI/Claude 请求响应转换、媒体下载、thoughtSignature 缓存
│   │   ├── account_orchestrator.py# _switch_lock 防雪崩切号与故障转移编排
│   │   ├── surface_orchestrator.py# 组策略/面能力/组身份与决策链原语
│   │   ├── channel_registry.py    # 渠道注册表、pg/chat/build/official 前缀解析
│   │   ├── account_routing.py     # 账号级 routing 覆盖归一化 (只收窄)
│   │   ├── routing_safety.py      # 请求级切换/顺延预算 + 滚动熔断
│   │   ├── routing_pipeline.py    # 唯一决策管线 ①能力→②候选→③组→④排序→⑤落地→⑥失败链
│   │   ├── atg/                   # ATG 防截断 (synthetic transport: injector/extractor/stream_extractor/repair/config 等)
│   │   └── chat_service.py        # 消息规范化与多模态数据处理
│   ├── domain/                    # 纯净领域模型与异常体系
│   │   ├── errors.py              # 业务异常定义 (AuthError, UsageLimitExceeded 等)
│   │   └── models.py              # 领域数据结构 (Candidate, ModelOutput 等)
│   ├── infrastructure/            # 基础设施层
│   │   ├── account/               # Cookie 解析、凭据持久化与账号信息刷新 (account_store, cookie_parser, account_info_refresher)
│   │   ├── browser/               # 异步 CDP 客户端与 CloakBrowser 进程管理 (cdp_client, browser_engine)
│   │   └── gateway/               # 会话/就绪门/鉴权、捕获与重放 (capture/replay/transport)、构建面 (build_surface)、模型默认与发现、Wire 编解码与流式管道
│   │       ├── instance_manager.py# 四层预算取最严、槽位计划数、级别 A 落地与 borrow
│   │       └── realm_pool.py      # 跨账号 Realm 池 (独立 BrowserContext/cookie jar, LRU, 在途不回收)
├── config.yaml                    # 本地运行时配置 (git-ignored; 仅本机验证)
├── defaults/config.yaml           # 分发默认配置 (tracked; features 变更的提交基准)
├── main.py                        # 本地统一启动入口
└── tests/                        # 单元测试套件 (模块化轻量架构，全量约 20s)
    ├── conftest.py               # 气密性隔离配置 (monkeypatch AISTUDIO_* 数据目录)
    ├── fixtures/                 # 真实 Protobuf JSON 请求与响应报文
    └── unit/                     # 49 个按职责严格划分的单元测试模块
        ├── test_account_*        # 账号调度、并发轮换防雪崩与凭据导入
        ├── test_api_*            # API 鉴权、响应序列化与模型/系统路由
        ├── test_browser_*        # Chromium 进程看门狗、CDP 会话与页面生命周期
        ├── test_wire_*           # Protobuf-over-JSON 编解码、流式解析与模型规则
        ├── test_format_*         # OpenAI/Claude 请求响应、流式事件与错误形状
        └── test_signature_cache.py # thoughtSignature 缓存复用/持久化/淘汰
```

## 6. 功能开关规范 (features)

本仓库的运行时功能开关统一收敛到 `config.yaml` 顶层 `features:` 段（`config.py` 的 `Settings` 提供同名 `AISTUDIO_*` 环境变量作为启动基线）。

- **落点**：`config.py` 的 `Settings`（`_load_bool_env` / `_load_int_env` / `_load_float_env` / `_load_choice_env` 模式，`AISTUDIO_*` 前缀）；解析函数 `model_defaults.get_features_config()`（仿 `_load_yaml_config` 的 mtime 缓存 + `invalidate_config_cache()`）；对外经 `GET /config` 响应的 `features` 键暴露。
- **优先级**：`config.yaml features.*` > `AISTUDIO_*` 环境变量 > 内置默认值。`features` 段是运行时权威配置，`PUT /config/yaml` 后**热重载立即生效**；环境变量仅在进程启动时读取一次，作为缺省兜底。
- **命名对齐**：YAML 键统一 `snake_case`，语义对齐对面 Ark 的 `.env` 字段（如 `atg.mode`↔`ATG_MODE`、`atg.client_synthetic_guard`↔`ATG_CLIENT_SYNTHETIC_GUARD`、`account_refresh_concurrency`↔`REFRESH_CONCURRENCY`、`auto_switch_interval_hours`↔`AUTO_SWITCH_INTERVAL_HOURS`）。
- **默认值**：编排/fallback/活动类开关默认关闭（`features.fallback.*` 全 `off`，遵循「零自动回落」决策）；`client_synthetic_guard` 默认 `true`（兼容优先）；WP10 `keepalive_auth_update` / `keepalive_metadata_refresh` / `keepalive_metadata_refresh_on_startup` 默认 `true`（账号保活优先，对齐 Ark `ENABLE_AUTH_UPDATE`），`keepalive_active_activity` 默认 `false`；WP8 `features.formats.openai` / `features.formats.claude` 默认 `true`（对齐 Ark 无条件端点注册），`features.formats.media.allow_private_urls` 默认 `false`，`features.atg.mode_by_format` 默认 `inherit`。非法/未知取值一律静默回退安全默认，禁止让新增顶层键破坏既有 YAML 消费方。
- **保活开关（WP10）**：`auto_switch`（长周期切号 + 真实活动）、`keepalive_auth_update`（cookies/origins → `auth.json`）、`keepalive_metadata_refresh`（tier/email/expired → `meta.json`）三者独立；切号前/后、初始化、重连、关闭前均回写 Auth；自动/手动切号共用切号锁，门槛覆盖 chat+build 在途；元数据刷新与 `POST /api/accounts/refresh` 共用同一实例和 409 互斥。结构化读写接口：`GET /api/keepalive`、`POST /api/keepalive/run`、`PUT /config/features`。
- **配置双写**：根 `config.yaml` 是本地运行时配置且被 git-ignore；`defaults/config.yaml` 是分发默认配置（tracked）。任何 `features` 变更必须两处同步；提交、代码评审与证据以 `defaults/config.yaml` 为准。
- **开关文案自解释约束（强制）**：每个开关在 `web/src/components/config/schema.ts` 的 `CONFIG_SCHEMA` 必须提供 `description`（一句话）+ `explanation`（开=什么 / 关=什么 / 何时用 / 失败怎么办）+ `recommendedValue`（`SchemaDictionaryModal` 自动渲染推荐值徽章），前端经 `FeaturesCard.vue` + `FieldHelpTip.vue` 呈现。
- **ATG 防截断（features.atg.*）**：synthetic transport 全链路（wrap 注入合成工具与控制指令 → 上游 → unwrap 还原正文）由 `application/atg/` 承载；`mode=off` 零行为变化；`required` 缺失合成调用显式失败（绝不静默）；运行日志可 grep `[ATG]` 获取注入/跳过原因与解包 stats（syntheticHit/syntheticCount/realToolCount/repaired/conflict）；`client_synthetic_guard` 默认开，客户端自带同类机制时自动跳过本地包装。
- **复杂策略组（WP5 二期，P4.5）**：唯一决策管线在 `application/routing_pipeline.py`，渠道在 ① 固定、链内不换面；组只收窄候选/面/预算/权重，账号级覆盖存 `AccountMeta.routing`（`meta.json`/`registry.json`），不新增文件。`features.channels` 控制渠道启用与渠道预算，`features.routing` 控制 `max_switches`（仅换号/换实例，独立于同账号 `MAX_RETRIES`）、global/Realm 预算、borrow、熔断；`features.slots` 为账号×渠道预留实例位。Realm 模式下一个存活 Realm 计一个全局实例槽；活跃账号优先用默认会话，非活跃账号走独立 BrowserContext（约 +180MB/Realm）。所有拒绝显式返回 `surface_*` 错误，不静默换面/换组。
- **格式面与 thoughtSignature（WP8）**：OpenAI/Claude 请求统一归一到 `GeminiGenerateContentRequest`；历史工具调用优先使用持久化的真实签名（`data/signature_cache.json`，30 天 TTL、4096 条、原子写入），缺失时使用 Ark 同款占位签名 `context_engineering_is_the_way_to_go`。严禁把工具调用降级为不可解析正文，也严禁静默丢弃；Claude thinking 回放不信任外部签名。`features.formats.*` 默认开启，关闭时对应路由显式 403。
- **前端**：`VisualConfigEditor.vue` 挂载 `FeaturesCard.vue`；整页 YAML 全量 `parse/stringify` 保留未知键，`features` 段不被可视化编辑丢弃。

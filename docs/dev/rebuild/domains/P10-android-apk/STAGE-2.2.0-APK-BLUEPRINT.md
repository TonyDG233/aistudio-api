# Stage 2.2.0 独立 Android APK 服务端化完整架构与施工蓝图

- 所属业务域：`P10-android-apk`
- 关联总看板：[`index.md`](index.md)
- 目标发布版本：`2.2.0`（当前代码基线：`2.1.4`，已完成 M1 `REV-2.2.0.1`）

---

## 1. 核心设计共识：100% 复用现有主项目逻辑与 Web 移动端管理台（零重复造轮子）

### 1.1 主逻辑直接引用，严禁用 Kotlin 重写第二套网关
- **Go 签名器（`waa-signer`）原样交叉编译**：
  直接复用仓库现有的 `waa-signer`（Go 1.27 + SpiderMonkey 152 + Firefox 152 uTLS + `camoufoxnative` Windows 桌面指纹库），通过 `waa-signer/build.ps1 -Target android-arm64` / `waa-signer/build.sh --target android-arm64` 交叉编译为 `arm64-v8a` 静态可执行 ELF `android/app/src/main/jniLibs/arm64-v8a/libwaa_signer.so`（已在 M1 完成并验证通过）。
- **Python 网关（`src/aistudio_api`）原样嵌入**：
  通过 Chaquopy（Python 3.11 Android 插件）在 APK 构建期直接打包仓库 `../src/aistudio_api`、`../defaults/config.yaml` 以及已构建好的前端静态资源 `../src/aistudio_api/static/`，在手机本地启动 FastAPI + Uvicorn 监听 `127.0.0.1:8080`。
  - 四协议转换（Gemini / OpenAI Chat / Claude Messages / OpenAI Responses）、PG-01..08 工具签名缓存、ATG 输入/输出双独立开关与图文双轨分流、`/RotateCookies` 自动续期、真 LRU 全池大循环、预防性主动轮换（`proactive_switch`）、429 首击退避切号、`verify-account` 自动熔断隔离与死槽自愈等全部逻辑 **100% 与服务端共用同一份 Python 源码**。
- **Kotlin 仅作为极薄的原生宿主管家**：
  Kotlin 层完全不介入任何路由、协议或签名逻辑，仅负责三件事：
  1. 拉起并守护 `libwaa_signer.so`（`WaaSignerSupervisor.kt`，监听 `127.0.0.1:8081`）与嵌入式 Python FastAPI 网关（`EmbeddedGatewayRunner.kt`，监听 `127.0.0.1:8080`）；
  2. 通过 `MediaSession` + `foregroundServiceType="mediaPlayback"` + 静音 PCM `AudioTrack`（不抢占音频焦点）+ `PARTIAL_WAKE_LOCK` 实现锁屏/切后台免杀常驻；
  3. 提供一次性即用即毁的隔离 `WebView` 登录提取器（`LoginScreen.kt`），抓取跨域 `HttpOnly` 根 Cookie（`SID` / `SAPISID` / `LSID`）后直通写入本地网关 `AccountStore` 并立即销毁 `WebView`。

### 1.2 UI 架构：直接复用主项目 `web/` 移动端界面 + 极简原生宿主顶栏
- **为什么不手搓第二套 Compose 管理台**：
  主项目 `web/`（输出至 `src/aistudio_api/static/`）已完成 `375px` 移动端窄屏防挤压适配与全站术语/按钮统一。APK 启动本地 `127.0.0.1:8080` 后，日常管理（控制面板、账号列表、导入电脑 `.json` 凭据、实例槽位换绑、预防性主动轮换、ATG 配置、实时日志）**直接通过内置全屏管理视图（或系统浏览器打开 `http://127.0.0.1:8080`）展示现成的 Web 移动端界面**。
- **自动同步优势**：
  后续对主项目 `web/` 做的任何 UI 微调，只要执行 `cd web && bun run build` 更新 `src/aistudio_api/static/`，重新打包的 APK 移动端界面即 **100% 自动同步生效**，彻底消除术语割裂。
- **原生 Compose 层仅保留 Web 网页无法替代的 2 个原生能力**：
  1. **原生网关服务控制与保活状态条**（启动/停止本地网关、静音媒体保活开关、电池优化白名单一键跳转、一键复制 `http://127.0.0.1:8080` 地址）；
  2. **手机网页登录提取入口（含常驻 4 条防风控硬核警示卡）**：调用原生 `CookieManager` 抽取 `.google.com`、`aistudio.google.com`、`accounts.google.com` 下的 `HttpOnly` 凭据直通本地网关 `data/accounts/`。

---

## 2. 必须写入 APK 界面的 4 条防风控硬核警示（强制 UI 要求）

在 APK 的「原生宿主控制页 / 账号提取入口」以及「内置 WebView 登录页顶部」，必须常驻展示醒目的 **《账号防风控安全守则》警示卡片**，明确包含以下 4 条铁律：

1. **无系统免密快捷登录说明**：
   第三方 APK 的安全隔离 WebView 受 Android 沙盒限制，无法直接免密继承手机系统 Google Play 已登录账号，需手动输入或配合密码管理器（如 Bitwarden）填充登录。
2. **每日手动登录频次红线（防触发 `verify-account` 硬件验证风控）**：
   同一台设备 / 同一代理 IP 下，**非高信任老号建议每天最多手动登录提取不超过 `2 个` 账号（建议早晚错开）**，高信任老号可酌情操作；严禁短时间内连续清空缓存批量登录多个普通账号，以免触发 Google `verify-account` 二维码设备硬件验证（单台物理手机/iPad 的硬件验证配额在滚动周期内仅约 5 个账号，且 7 天内难以刷新）。
3. **优先导入电脑端 JSON 凭据与本机验证建议**：
   若账号已在电脑端（无痕窗口 + 油猴脚本）导出过独立 `.json` 凭据包，请直接在管理台「账号管理 → 导入凭据」选择 `.json` 文件零风控入库，避免在手机端重复走密码登录流；若该账号原本已登录在本机系统，二次验证时优先选择本机弹出的「Google 提示（点“是”）/ 通行密钥（Passkey）」。
4. **禁止同号双开抢令牌与禁点网页退出登录**：
   账号提取并交由本 APK 反代运行期间，请勿在手机自带浏览器中同时打开 `aistudio.google.com` 网页操作同一账号；在内置登录页看到 AI Studio 界面后直接点右上角「提取并入库」，**切勿点击 Google 网页内的「退出登录（Sign out）」按钮**（否则服务端会当场吊销刚提取的根 Cookie）。

---

## 3. 分阶段详细施工规范（M1 → M4）

### 3.1 M1（已完成 · `REV-2.2.0.1` PASS）：`libwaa_signer.so` 交叉编译与 `WaaSignerSupervisor` 进程守护
- **已交付文件**：
  - `waa-signer/cmd/waa-signer/main.go` & `main_test.go`：新增 `-dns` 参数与 `WAA_DNS_SERVERS` / `AISTUDIO_DNS_SERVERS` 环境变量支持，在 Android 缺失 `/etc/resolv.conf` 时启用自定义纯 Go UDP/TCP 轮询 DNS Resolver；自动探测并注入 `/apex/com.android.conscrypt/cacerts:/system/etc/security/cacerts` 根证书目录。
  - `waa-signer/build.ps1` & `waa-signer/build.sh`：新增 `android-arm64` 构建目标，基于 Zig `aarch64-linux-musl` + `linux-arm64` SpiderMonkey 152 静态库产出 `android/app/src/main/jniLibs/arm64-v8a/libwaa_signer.so`（50.48 MB，64 位 ARM `EM_AARCH64` 静态 ELF，已在 `.gitignore` 中忽略二进制产物）。
  - `android/app/build.gradle.kts`：配置 `ndk.abiFilters += "arm64-v8a"`、`packaging.jniLibs.useLegacyPackaging = true` 与 `keepDebugSymbols += "**/libwaa_signer.so"`。
  - `android/app/src/main/java/com/aistudio/extractor/service/WaaSignerSupervisor.kt` & `WaaSignerSupervisorTest.kt`：实现 `libwaa_signer.so` 子进程启动、环境变量注入（`TMPDIR`、`HOME`、`WAA_STATE_DIR`、`SSL_CERT_DIR`、`WAA_DNS_SERVERS`）、`/health` 轮询探测、`runGeneration` 代际并发防竞态、指数退避重启与优雅销毁（15 项 JUnit 单元测试全绿）。

---

### 3.2 M2（待执行 · 对应审查标号 `REV-2.2.0.8-embedded-python-gateway.md`）：嵌入式 Python FastAPI 网关与 `browserless` 配置锁定

#### 施工前必做第一步
先派发只读 `general` 子代理盘点 `main.py`、`src/aistudio_api/main.py`、`src/aistudio_api/config.py`、`src/aistudio_api/api/app.py`、`pyproject.toml` 的依赖列表与启动入口，向用户汇报 M2 精确改动清单后再派 Worker 施工。

#### 核心实现规范
1. **Gradle 集成 Chaquopy（Python 3.11 运行时）**：
   - 在 `android/build.gradle.kts` 与 `android/app/build.gradle.kts` 引入 Chaquopy 插件（`com.chaquo.python`）；
   - 配置 `defaultConfig.python`：
     - `version = "3.11"`
     - `pip` 安装纯运行期必需依赖：`fastapi`、`uvicorn`、`pydantic`、`httpx`、`socksio`、`pyyaml`、`python-dotenv`、`anyio`、`starlette`（不打包桌面端 `pytest`/`ruff`/`pyright` 开发依赖）；
     - 将 `../src/aistudio_api` 作为 Python 源码集打包进 APK，并将 `../defaults/config.yaml` 同步打入 assets 或 Python 资源目录。
2. **新增 Python 嵌入式启动入口 `src/aistudio_api/android_entry.py`**：
   - 提供 `prepare_android_environment(files_dir: str, cache_dir: str, signer_endpoint: str = "http://127.0.0.1:8081", port: int = 8080) -> dict[str, object]`：
     - 严格遵循 `AGENT.md §2.5`：设置 `os.environ["TMPDIR"] = cache_dir`、`os.environ["AISTUDIO_TMP_DIR"] = cache_dir`（严禁硬编码 `/tmp`）；
     - 设置 `os.environ["AISTUDIO_DATA_DIR"] = f"{files_dir}/data"`、`os.environ["AISTUDIO_CONFIG_PATH"] = f"{files_dir}/data/config.yaml"`；
     - 若 `files_dir/data/config.yaml` 不存在，从内置 `defaults/config.yaml` 初始化，并**强制覆写锁定**：
       - `engine.mode = "browserless"`（在 Android APK 环境硬性禁止切换到 `browser` / CloakBrowser 模式）；
       - `engine.browserless.signer.mode = "remote_http"`（或由 Kotlin `WaaSignerSupervisor` 统一托管 `127.0.0.1:8081`，设置 `auto_start = false`、`endpoint = "http://127.0.0.1:8081"`，避免 Python 重复拉起第二个 `waa-signer` 子进程）；
       - `engine.browserless.signer.max_workers = 2`、`request_timeout_s = 300`、`transport.read_timeout_s = 300`；
       - `features.routing.proactive_switch_mode = "free_only"`、`proactive_switch_threshold = 15`、`switch_backoff_sec = 4.0`。
   - 提供 `run_embedded_server(host: str = "127.0.0.1", port: int = 8080)` 与 `stop_embedded_server()`：在后台线程中通过 `uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))` 启动并支持设置 `server.should_exit = True` 优雅停机。
3. **新增 Kotlin 侧 `EmbeddedGatewayRunner.kt`**：
   - 串联 `WaaSignerSupervisor.start()` → 等待 `127.0.0.1:8081/health` 就绪 → 调用 Chaquopy `android_entry.prepare_android_environment` & `run_embedded_server` → 轮询 `http://127.0.0.1:8080/health` 就绪后将状态置为 `GatewayRunState.RUNNING`。

---

### 3.3 M3（待执行 · 对应审查标号 `REV-2.2.0.9-mediasession-keepalive-service.md`）：`MediaSession` 静音音轨无感后台常驻保活

#### 核心实现规范
1. **`AndroidManifest.xml` 权限与服务声明**：
   - 声明权限：`FOREGROUND_SERVICE`、`FOREGROUND_SERVICE_MEDIA_PLAYBACK`、`WAKE_LOCK`、`POST_NOTIFICATIONS`、`REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`（**严禁声明 `SYSTEM_ALERT_WINDOW` 悬浮窗权限**）；
   - 注册 `<service android:name=".service.GatewayForegroundService" android:exported="false" android:foregroundServiceType="mediaPlayback" />`。
2. **`SilentAudioKeepAlive.kt`（零打扰静音 PCM 循环器）**：
   - 使用 `AudioTrack.Builder()` 创建 `8000 Hz`、`CHANNEL_OUT_MONO`、`ENCODING_PCM_16BIT` 的极低开销音频流，循环写入全 `0` 静音字节缓冲区；
   - **铁律**：**绝不调用 `AudioManager.requestAudioFocus()`**，`AudioAttributes` 设置为 `USAGE_MEDIA` + `CONTENT_TYPE_MUSIC`，确保用户在后台用网易云音乐听歌或用 SillyTavern 播放 TTS 语音时 **100% 不被抢占或压低音量**。
3. **`GatewayForegroundService.kt`（`MediaSession` + `MediaStyle` 常驻通知 + `PARTIAL_WAKE_LOCK`）**：
   - 集成 `MediaSession` 与通知栏控制卡片：展示 `AI Studio 本地网关 · 运行中 (127.0.0.1:8080)`、当前活跃槽位与账号状态，提供「打开管理台」「重启」「停止」快捷按钮；
   - 服务运行期间持有 `PowerManager.PARTIAL_WAKE_LOCK`（带超时续租与 `onDestroy` `finally` 释放保护），防止手机锁屏后 CPU 休眠导致生成断流。

---

### 3.4 M4（待执行 · 对应审查标号 `REV-2.2.0.10-apk-ui-and-account-store-bridge.md`）：WebView 提取直通本地 `AccountStore`、复用 Web 移动端管理台与 4 条防风控警示卡落盘

#### 施工前必做第一步
先派只读 `general` 子代理盘点 `android/app/src/main/java/com/aistudio/extractor/` 现有 UI 组件与 `web/src/` 移动端界面路由，向用户展示 **《APK 宿主壳 + 内嵌 Web 移动端管理台 + 登录页 4 条防风控警示卡》双层 ASCII 线框图**，获用户拍板后再派 Worker 施工。

#### 核心实现规范
1. **凭据直通本地网关 `AccountStore`（`ProfileManager.kt` / `GatewayAccountBridge.kt`）**：
   - 当用户在 `LoginScreen.kt` 完成 Google 登录点击「提取并直通入库」时：
     - 从 `CookieManager.getInstance()` 读取 `https://aistudio.google.com`、`https://accounts.google.com`、`https://www.google.com` 下的完整 Cookie（保留 `SID`、`HSID`、`SSID`、`APISID`、`SAPISID`、`__Secure-1PSID`、`__Secure-3PSID`、`__Secure-1PSIDTS`、`LSID`、`__Host-1PLSID`、`__Host-3PLSID`、`ACCOUNT_CHOOSER` 等，自动过滤 `OSID`、`__Secure-OSID`、`SIDCC` 等易腐败项）；
     - 直接调用本地网关 `POST http://127.0.0.1:8080/api/accounts/import-bundle`（或网关未启动时原子写入 `filesDir/data/accounts/`），自动完成入库、多登子账号探测与实例池热加载；
     - 提取完成后**立即销毁卸载登录 `WebView`**，绝不常驻内存，也绝不用该 `WebView` 发送任何 API 生成请求。
2. **植入常驻《账号防风控安全守则》4 条警示组件（`RiskWarningCard.kt`）**：
   - 在 APK 宿主顶部控制面板与 `LoginScreen.kt` 登录页顶部显著展示本蓝图第 2 节规定的 4 条防风控硬核警示。
3. **本地构建完整 APK 并校验**：
   - 同步将 `android/app/build.gradle.kts` 中的 `versionName` 升级为 `"2.2.0"`；
   - 在本地执行 `cd web && bun run build` + `cd android && .\gradlew.bat :app:ktlintCheck :app:lintDebug :app:testDebugUnitTest :app:assembleDebug` 产出完整可安装的 `app-debug.apk`。

---

## 4. Post-M4 收尾研讨议题（待 M1~M4 全部完工后与用户确认）

1. **真机 ADB 安装与全链路实测**：
   - 是否通过无线/USB ADB 将本地编译好的 APK 安装至手机，实时抓取 `logcat` 验证从「前台服务启动 `libwaa_signer.so` + 嵌入式 FastAPI `127.0.0.1:8080` → 导入/提取账号 → 发送 `/v1/chat/completions` & `/v1beta` 消息生成」的完整闭环。
2. **GitHub Actions 自动构建 APK 工作流**：
   - 是否在 `.github/workflows/` 中新增或扩展 Android Release 工作流（自动执行 `bun run build` → `waa-signer/build.sh --target android-arm64` → `./gradlew assembleRelease` 并作为 GitHub Release Artifact 随 `2.2.0` 标签发布）。

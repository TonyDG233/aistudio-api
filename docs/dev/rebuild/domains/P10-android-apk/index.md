# P10 Android APK 独立服务端化任务计划与看板（Stage 2.2.0）

- 域标识：`P10-android-apk`
- 目标阶段：Stage 2.2.0（前置条件：Stage 2.1.0 双引擎 `waa-signer` / `browserless` 稳定并完成实机验收后启动）
- 关联模块：`android/`、`waa-signer/`、`src/aistudio_api/`（嵌入式网关与配置锁定）

## 1. 域职责边界与架构红线

- **唯一引擎红线**：APK 内**仅启用 `browserless` 引擎**，彻底剥离桌面 Chromium / CloakBrowser、Termux 与 `proot-distro` 依赖，禁止拉起桌面浏览器或任何外部容器进程。
- **不破坏现有双引擎契约**：不修改 2.1.0 已冻结的服务端双引擎与四协议契约；现有 `android/` 多 Profile Cookie 提取器 UI 与能力完整保留。
- **核心方案（路线 A · 桌面指纹原生伪装）**：
  - 将 `waa-signer`（Go 1.27 + SpiderMonkey 152 + Firefox 152 uTLS + `camoufoxnative` Windows 桌面指纹）通过 Android NDK 交叉编译为 `arm64-v8a` 的 `libwaa_signer.so`，放入 `android/app/src/main/jniLibs/arm64-v8a/`。
  - **风控规避动机**：严禁直接用 Android `WebView` 承担推理代理请求链路，彻底规避 `WebView` 自动注入 `X-Requested-With` 与移动端特征触发 Google `verify-account` 移动设备验证风控。
- **网关嵌入**：通过 Chaquopy / 嵌入式 Python 运行 FastAPI 网关，固定监听 `127.0.0.1:8080`（默认离线配置隔离，按需开放局域网开关），直接复用本地 `AccountStore` 与 `BrowserlessSession`。
- **无感后台保活**：采用类音乐播放器式的 Android 前台服务保活架构，不提供悬浮小窗：
  - `MediaSession` + `foregroundServiceType="mediaPlayback"` 前台常驻服务；
  - 静音 PCM `AudioTrack` 持续输出（配置为不抢占系统音频焦点，避免打断音乐/通话）；
  - 持有 `PARTIAL_WAKE_LOCK` 防止息屏后 CPU 休眠中断本地网关与保活调度。

## 2. 阶段任务拆解看板

| 里程碑 | 任务名称 | 核心交付内容与技术要点 | 状态 |
|---|---|---|---|
| **M1** | NDK 交叉编译 `libwaa_signer.so` | 配置 Android NDK（`arm64-v8a`）与 Zig/CGO 工具链，交叉编译 `waa-signer`（Go 1.27 + SpiderMonkey 152 + Firefox 152 uTLS + `camoufoxnative` Windows 指纹）为可执行/可加载 `libwaa_signer.so`，产出放入 `jniLibs/arm64-v8a/` 并补齐本地子进程生命周期守护 | 已完成 PASS（`REV-2.2.0.1` 二轮复审：`P1-1`/`P1-2`/`P2-1`～`P2-4` 与 `Q-1` 全部闭环；遗留 `P2-5`/`Q-2`/`Q-3` 非阻断项转 M2/M3） |
| **M2** | Python 网关嵌入与配置锁定 | 集成 Chaquopy / 嵌入式 Python 运行时至 `android/`，内嵌 `aistudio_api` FastAPI 服务监听 `127.0.0.1:8080`；启动期强制锁定 `engine.type=browserless`，禁用 `browser`/CDP 路径与桌面浏览器探测，复用标准化 `defaults/config.yaml` 与 `data/` 存储目录 | 待开始 |
| **M3** | `MediaSession` 无感保活服务 | 新增 `foregroundServiceType="mediaPlayback"` 前台服务与通知栏媒体控制卡片（启动/停止/端口状态）；实现静音 PCM `AudioTrack`（不申请/不抢占音频焦点）+ `PARTIAL_WAKE_LOCK` 息屏防休眠；不提供悬浮小窗 | 待开始 |
| **M4** | WebView 提取直通本地 `AccountStore` | 打通现有 `android/` 多 Profile WebView 登录提取流与嵌入式网关 `data/accounts/` 的直写通路（原子写 `auth.json`/`meta.json` 并热通知网关重载/清冷却），同时保持 WebView 仅限登录凭据采集，与推理 WAA 指纹链路严格物理隔离 | 待开始 |

### 2.1 实机验证工具链（M2/M4 前置支撑）

| 交付物 | 作用 | 状态 |
|---|---|---|
| `tools/verify_qr_adb.py` | Island 工作资料 + ADB 实机 `verify-account` 全链路：二维码离线解码（PNG/BMP/DIB，纯 Python 兜底）→ 主/工作空间 ANDROID_ID/GSF 对比 → 链接注入目标空间 → 前台窗口/进程归属/logcat 取证 → 完成报告 | 离线代码 `PASS`（`REV-2.2.0.3` 整改复审：门禁全绿、59/59 用例通过、无 `P0`；`P1-1`~`P1-3`、`P2-1`~`P2-8`、`Q-1`~`Q-4` 全部闭环）；**实机绕过尝试已失败**（见 §2.2） |
| `tests/unit/test_verify_qr_adb.py` | 59 个离线用例：解析器、防串味拦截、`--allow-main-user` 基线、内置解码器（RGBA/LA 8/16bit、破坏纠错、旋转、BMP/DIB、解码炸弹）、shell 转义、verify 状态机与 CLI | 通过 |
| `data/tools/island/Island.v6.2.1.apk` | Island 安装包（`3,499,707` 字节，SHA-256 `374e5d23…f611`，被 `.gitignore:12:/data/` 忽略、不入库） | 就绪 |

> **【实机验证结论留档：Island 工作资料绕过设备验证尝试已失败】** 经真机控制变量实测，通过 Android `FLAG_MANAGED_PROFILE`（Island / 壶中界 `User 10`）隔离 `ANDROID_ID` 与独立 KeyMint 密钥以突破 Google AI Studio `verify-account`（`recaptcha.net/qr/`）单设备验证限额的方案**已证实无效**：Google 的 `recaptcha.net/qr/`（DroidGuard / Play Integrity）会检测并直接拒绝第三方受管工作资料（Managed Profile）沙盒环境（返回 `We couldn't verify your account with this device... or doesn't meet device security requirements`），而切回同一台物理手机的原生主空间（`User 0`）则可正常通过验证。因此 `tools/verify_qr_adb.py` 无法用于重置或绕过单台物理手机的验证配额限制，仅保留作为剪贴板二维码离线解码与主空间（`--allow-main-user`）免摄像头 ADB 辅助推送工具留底。

## 3. `reviews/` 审查报告与成果索引

> M1 独立只读审查已产出首份报告并完成二轮复审：`waa-signer` 交叉编译与 `WaaSignerSupervisor` 子进程守护的 `P1`/`P2`/`Q` 问题已按报告逐项整改，二轮复审结论 `PASS`。

- [`REV-2.2.0.1-waa-signer-android-ndk.md`](reviews/REV-2.2.0.1-waa-signer-android-ndk.md) — Stage 2.2.0 M1 独立代码与门禁审查（二轮复审结论 `PASS`；`P1-1`/`P1-2`/`P2-1`/`P2-2`/`P2-3`/`P2-4` 与 `Q-1` 主体全部闭环；新发现非阻断项 `P2-5`、`Q-3` 与顺延 `Q-2` 转 M2/M3）
- [`REV-2.2.0.3-verify-qr-adb-tool.md`](reviews/REV-2.2.0.3-verify-qr-adb-tool.md) — 实机验证工具链独立只读审查（整改后离线复审结论 `PASS`；`ruff`/`ruff format`/`pyright`/`pytest 59 passed`/`git add -N` 后 `git diff --check` 全绿、无 `P0`；`P1-1` RGBA/LA 通道错位、`P1-2` `--allow-main-user` 死路、`P1-3` 无 `u<uid>` 焦点假 PASS，以及 `P2-1`~`P2-8`、`Q-1`~`Q-4` 全部关闭，逐项证据见报告 §7。报告头部已留档：**Island 受管工作资料绕过设备验证的实机尝试已失败**）

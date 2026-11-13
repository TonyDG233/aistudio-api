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
| **M1** | NDK 交叉编译 `libwaa_signer.so` | 配置 Android NDK（`arm64-v8a`）与 Zig/CGO 工具链，交叉编译 `waa-signer`（Go 1.27 + SpiderMonkey 152 + Firefox 152 uTLS + `camoufoxnative` Windows 指纹）为可执行/可加载 `libwaa_signer.so`，产出放入 `jniLibs/arm64-v8a/` 并补齐本地子进程生命周期守护 | 待开始（依赖 2.1.0 稳定） |
| **M2** | Python 网关嵌入与配置锁定 | 集成 Chaquopy / 嵌入式 Python 运行时至 `android/`，内嵌 `aistudio_api` FastAPI 服务监听 `127.0.0.1:8080`；启动期强制锁定 `engine.type=browserless`，禁用 `browser`/CDP 路径与桌面浏览器探测，复用标准化 `defaults/config.yaml` 与 `data/` 存储目录 | 待开始 |
| **M3** | `MediaSession` 无感保活服务 | 新增 `foregroundServiceType="mediaPlayback"` 前台服务与通知栏媒体控制卡片（启动/停止/端口状态）；实现静音 PCM `AudioTrack`（不申请/不抢占音频焦点）+ `PARTIAL_WAKE_LOCK` 息屏防休眠；不提供悬浮小窗 | 待开始 |
| **M4** | WebView 提取直通本地 `AccountStore` | 打通现有 `android/` 多 Profile WebView 登录提取流与嵌入式网关 `data/accounts/` 的直写通路（原子写 `auth.json`/`meta.json` 并热通知网关重载/清冷却），同时保持 WebView 仅限登录凭据采集，与推理 WAA 指纹链路严格物理隔离 | 待开始 |

## 3. `reviews/` 审查报告与成果索引

> 当前处于 Stage 2.2.0 前置规划期，待 M1–M4 逐步落地后，独立只读 Reviewer 报告与实机/APK 截图成果将归档于本域 `reviews/`（当前占位：`reviews/.gitkeep`）。

- （暂无审查报告）

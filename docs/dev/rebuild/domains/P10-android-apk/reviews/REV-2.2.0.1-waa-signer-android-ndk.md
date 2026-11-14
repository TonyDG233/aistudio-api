# REV-2.2.0.1 — Stage 2.2.0 M1 独立代码与门禁审查报告（二轮复审）

- **审查对象**：Stage `2.2.0` M1（`waa-signer` Android `arm64-v8a` 静态交叉编译管线、DNS / CA 根证书兜底、`WaaSignerSupervisor` 进程守护与 Gradle/Manifest 配置）
- **审查日期**：2026-11-14（二轮复审；初审同日完成）
- **复审结论**：**`PASS`**
- **只读声明**：本审查由独立只读 Reviewer 执行，审查全程未修改任何业务代码、构建脚本或测试文件，未启动真实服务（`main.py server`），未拉起真实浏览器 / `libwaa_signer.so` 进程，未连接真实 Google 账号，未执行 `git commit` / `git push` / `webfetch`。唯一写入文件为 `docs/dev/rebuild/domains/P10-android-apk/reviews/REV-2.2.0.1-waa-signer-android-ndk.md` 与 `docs/dev/rebuild/domains/P10-android-apk/index.md` 的 M1 状态行。

---

## 1. 复审范围与产物核验清单

### 1.1 复审文件清单（全部只读）

| 文件 | 复审关注点 |
|---|---|
| `android/app/src/main/java/com/aistudio/extractor/service/WaaSignerSupervisor.kt` | `P1-1`/`P1-2` 代际隔离与锁内状态机、`P2-2`/`P2-4` DNS 归一化 |
| `android/app/src/test/java/com/aistudio/extractor/service/WaaSignerSupervisorTest.kt` | `Q-1` 生命周期并发回归用例 |
| `waa-signer/cmd/waa-signer/main.go` | `P2-2` 空切片守卫与轮询、`P2-4` DNS 解析严格化、DNS/CA 兜底 |
| `waa-signer/cmd/waa-signer/main_test.go` | 新增 DNS 边界与空切片用例 |
| `waa-signer/build.ps1`、`waa-signer/build.sh` | `P2-3` CGO 环境变量残留、双目标构建分支 |
| `android/app/src/main/AndroidManifest.xml`、`android/app/build.gradle.kts` | `P2-1` `extractNativeLibs` 双写告警 |
| `.gitignore` | 交叉编译产物忽略规则 |
| `android/app/src/main/jniLibs/arm64-v8a/libwaa_signer.so` | 构建产物 ELF 架构与静态链接 |

### 1.2 构建产物核验（二轮独立实测）

| 检查项 | 二轮实测结果 | 判定 |
|---|---|---|
| 产物存在性与体积 | 存在，`50,483,968` 字节（~48.14 MiB），`LastWriteTime=2026-11-14 14:29:50` | 通过 |
| ELF64 头前 20 字节 | `7F 45 4C 46 02 01 01 00 00 00 00 00 00 00 00 00 02 00 B7 00`（`ELF64`、小端、`ET_EXEC`、`e_machine=0x00B7` `EM_AARCH64`） | 通过 |
| SHA-256 | `4A3BA8E055D3DCA64F65AA99947031878564B56B2462F012365971CF0DD78C67` | 通过 |
| 产物忽略规则 | `git check-ignore -v` → `.gitignore:60:android/app/src/main/jniLibs/**/*.so` 精确命中 | 通过 |
| `waa-signer/build.ps1` 编码 | 前 3 字节 `EF BB BF`（UTF-8 BOM 保留），`$Target` 默认 `windows-amd64`（`build.ps1:11`） | 通过 |
| `waa-signer/build.sh` 换行符 | CR 字节计数 `0`（纯 LF）；无参仍按宿主机构建，`--target android-arm64` 指向 `jniLibs`（`build.sh:38,80`） | 通过 |

---

## 2. 初审 `P1-1` … `Q-1` 逐项关闭核验

> 结论：**初审 6 项 `P` 问题 + 1 项 `Q` 全部按建议方案落地，无遗留 P0/P1/P2。** 逐条证据如下。

| 编号 | 初审问题摘要 | 二轮复审证据（`file:line`） | 判定 |
|---|---|---|---|
| **P1-1** | `withContext(Dispatchers.IO){pb.start()}` 取消窗口导致孤儿原生进程占用 8081 端口 | `WaaSignerSupervisor.kt:278-296`：进程创建已改为在 `synchronized(lifecycleLock)` 内同步执行 `startProcess(...)`，并在同一临界区把返回值写入局部 `process` 与 `currentProcess`，`withContext` 包裹已彻底移除（全文再无 `withContext(Dispatchers.IO)` 包裹进程创建，仅 `queryAndParseHealth:406` 保留健康 HTTP 查询）；锁内先复核 `generation != runGeneration \|\| !shouldRun`（`:281`），代际失效则不启动；`:293-296` 在锁外再次复核并主动 `destroyProcess`；`stop()` 在 `:150-167` 锁内摘除 `currentProcess` 后于锁外销毁；`:329-341` 的 `finally` 对本地 `process` 兜底销毁（`isAlive` 判定避免重复杀）。 | **已关闭** |
| **P1-2** | `stop()` 与旧循环异步竞态导致 `_status` 回退 / `shouldRun` 被旧协程误置 `false` | 引入 `runGeneration` 代际栅栏（`:121,130-131,151`）：`start()` 与 `stop()` 均在 `lifecycleLock` 内自增；`isCurrentGeneration(:169-172)`、`updateStatusIfCurrent(:174-184)`、`failAndStopIfCurrent(:186-207)` 三个入口全部在锁内校验 `generation == runGeneration && shouldRun` 后才写 `_status`/`shouldRun`。全文 `_status.value` 写入点仅 `:133`（`start` 锁内）、`:157`（`stop` 锁内）、`:197`（`failAndStopIfCurrent` 锁内）；`shouldRun` 写入点仅 `:132`、`:152`、`:195`（均在锁内）。健康检查返回后经 `:389` 的 `updateStatusIfCurrent` 落盘，旧代际无法复活 `RUNNING`；循环所有 `delay`/`waitFor` 之后均在 `:343`、`:270`、`:299`、`:372` 复核代际后 `break`。 | **已关闭** |
| **P2-1** | 源码 Manifest 硬编码 `android:extractNativeLibs="true"` 与 AGP 注入重复，触发合并告警 | `android/app/src/main/AndroidManifest.xml` 全文（1-39 行）已无 `android:extractNativeLibs`；`android/app/build.gradle.kts:56-61` 保留 `packaging { jniLibs { useLegacyPackaging = true } }`。二轮 `--rerun-tasks` 实跑 `:app:processDebugMainManifest` 无任何 `extractNativeLibs should not be specified` 告警；合并后产物 `merged_manifests/.../AndroidManifest.xml:24` 由 AGP 自动注入 `android:extractNativeLibs="true"`，行为等价且无告警。 | **已关闭** |
| **P2-2** | `newCustomDNSResolver` 空切片除零 panic（错误分支不可达）+ 无条件追加公共 DNS 造成 UDP 轮询黑洞 | Go 侧 `main.go:1022-1051`：`Dial` 首行 `:1027-1029` 已加 `if len(servers) == 0 { return nil, errors.New("no DNS servers configured") }`，取模改为 `int((rr.Add(1) - 1) % uint64(len(servers)))`（`:1034`），后续按 `(start+i)%len(servers)` 逐台失败降级（`:1037-1047`）；`main_test.go:220-235` 新增 `nil` 与空切片双例断言 `no DNS servers configured` 且 `conn == nil`。Android 侧 `WaaSignerSupervisor.kt:571-591` 已改为：仅当系统 DNS 全部为空/非法（`formatted.isEmpty()`）时才回退 `DEFAULT_FALLBACK_DNS`（`:581-590`），系统 DNS 非空时**不再追加**公共 DNS，轮询起点落在系统 DNS 上。 | **已关闭** |
| **P2-3** | `build.ps1` 同会话切换目标时 `CGO_CPPFLAGS` 残留污染 | `waa-signer/build.ps1:458-464`：`android-arm64` 分支设 `CGO_CPPFLAGS` 并 `Remove-Item Env:CGO_CXXFLAGS`（`:459-460`）；`else`（`windows-amd64`）分支设 `CGO_CXXFLAGS` 并新增 `Remove-Item Env:CGO_CPPFLAGS -ErrorAction SilentlyContinue`（`:462-463`），双向对称清理，同会话切换不再残留。`build.sh:205` 同步补 `CGO_CPPFLAGS`，并新增 `libjsrust.a.orig` 一次性备份（`build.sh:181-182`）保证重复构建幂等。 | **已关闭** |
| **P2-4** | `parseDNSServers` 对 `http://8.8.8.8` 型畸形串与 IPv6 Zone ID 处理不严格 | Go 侧 `main.go:946-999`：入口先以 `/`、空白、控制字符整条丢弃（`:960-964`），`parseDNSPort` 强制纯数字且 `1..65535`（`:878-892`），`normalizeDNSHost` 剥离 `%zone` 且要求剩余部分为 IPv6 字面量（`:929-936`），另以 `isValidHostname` 做 RFC 越界校验（`:894-922`）。Android 侧 `WaaSignerSupervisor.kt:593-694`：`normalizeDnsEndpoint` 拒绝含 `/`、空白、控制字符（`:595-600`），方括号分支 `substring(1, closeIdx).substringBefore('%')`（`:606`）与非方括号分支 `substringBefore('%')`（`:627,636`）均剥离 Zone ID，端口统一走 `parseValidPort`（`:688-694`，纯数字 + `1..65535`），并新增 `isValidIpv4Literal`/`isValidIpv6Literal` 严格校验。单测覆盖 `http://8.8.8.8`、`https://...:53`、`:53`、`8.8.8.8:0/99999/-1/+53/abc`、`[bad::ip]:53`、`[fe80::1%wlan0]:53` 等（`WaaSignerSupervisorTest.kt:53-80`、`main_test.go:38-57`）。 | **已关闭** |
| **Q-1** | 实例生命周期（`start`/`stop` 并发、启动超时强杀、连败熔断）缺少自动化覆盖 | 已注入 `processStarter` / `healthQueryOverride` / 目录覆盖等测试缝（`WaaSignerSupervisor.kt:57-59,81-104`），并新增 4 个生命周期用例：`WaaSignerSupervisorTest.kt:226-250`（二进制缺失 → `ERROR` + `lastError`）、`:252-295`（spawn 期间 `stop()` → 进程必被销毁且终态 `STOPPED`，直接回归 `P1-1`）、`:297-343`（陈旧健康查询返回后不得复活 `RUNNING`，回归 `P1-2` 状态回退）、`:345-409`（`stop→start` 快速切换时旧代际进程销毁、新代际 `RUNNING` 不被旧代际覆盖）。全套 `13` 用例实跑 0 失败。**残余**：15s 启动超时强杀（`HEALTH_POLL_MAX_WAIT_MS`）与 5 次连败熔断（`MAX_CONSECUTIVE_FAILURES`）两条路径仍无自动化用例（时间/次数常量不可注入），记为 `Q-3` 非阻断观察项。 | **主体关闭（残余见 `Q-3`）** |

---

## 3. 全量问题清单（P0 / P1 / P2 / Q / N）

### P0（阻断性缺陷）
- **无。**

### P1（高优先级缺陷）
- **无。** 初审 `P1-1`、`P1-2` 均已在源码层闭环并有针对性回归用例（详见 §2）。

### P2（中优先级缺陷）
- 初审 `P2-1`、`P2-2`、`P2-3`、`P2-4` 全部关闭（详见 §2），**无遗留**。
- **`P2-5`（二轮新发现 → 已关闭）**：`startProcess` 在持有 `lifecycleLock` 期间执行 `ProcessBuilder.start()`，使 `stop()` 被 spawn 阻塞。
  - **位置（修复前）**：`android/app/src/main/java/com/aistudio/extractor/service/WaaSignerSupervisor.kt:278-289`（锁内 spawn）、`:147-167`（`stop()` 抢同一把锁）。
  - **实证（修复前）**：`stop immediately after start during process spawn destroys started process and stays STOPPED` 用例耗时 **`3.108s`**（其余 12 例均 ≤ `0.17s`），即 `stop()` 被锁 + `processStarter` 的 3s latсh 超时全程阻塞，说明该路径确实存在"调用方被 spawn 时长阻塞"。生产环境 `pb.start()` 通常为毫秒级，故**不构成功能缺陷、不阻断 M1**；但若 spawn 在内存压力下变慢，前台 Service / UI 线程调用 `stop()` 会放大已有的 `N-1` 阻塞面（`destroyProcess` 另有最多 3s 优雅等待）。
  - **关闭证据（M1 收口，2026-11-14）**：`WaaSignerSupervisor.kt:294-316` 已将 spawn 移出 `lifecycleLock`——先 `isCurrentGeneration` 锁内预检（`:295-297`），锁外 `withContext(NonCancellable) { startProcess(command, stateDir, env) }` 确保协程取消时句柄不丢失（`:299-302`），紧接着 `synchronized(lifecycleLock)` 复核代际并原子登记 `process/currentProcess`（`:303-312`），代际失效则锁外 `destroyProcess(startedProcess)` 后退出（`:313-316`）。回归用例 `stop immediately after start during process spawn destroys started process and stays STOPPED` 耗时由 **`3.108s`** 降至 **`0.108s`**，`stop()` 不再被 spawn 阻塞，且 spawn 成功的进程仍必被回收。**已关闭。**

### Q（疑问 / 验证覆盖缺口）
- **`Q-1`**：主体关闭（`start`/`stop` 并发与代际竞态已覆盖）；残余 `Q-3` 已关闭。
- **`Q-2`（顺延，原样保留）**：`libwaa_signer.so` 在真实 Android 设备上的 `execve()`、DNS 解析与 `/apex/com.android.conscrypt/cacerts` TLS 握手实效，仍待 M2/M3 APK 联调阶段真机闭环；本轮仅验证了 ELF `EM_AARCH64` 静态产物与离线单测。
- **`Q-3`（二轮新增 → 已关闭）**：启动超时强杀与连败熔断两条守护路径缺少自动化用例。
  - **关闭证据（M1 收口，2026-11-14）**：`WaaSignerSupervisor` 主构造函数已将超时/退避/熔断参数改为可注入默认参数：`startupTimeoutMs: Long = DEFAULT_STARTUP_TIMEOUT_MS`、`healthPollIntervalMs: Long = HEALTH_POLL_INTERVAL_MS`、`initialBackoffMs: Long = INITIAL_BACKOFF_MS`、`maxRestartAttempts: Int = MAX_RESTART_ATTEMPTS`、`healthRequester: (String) -> Pair<Int, String>?`（`WaaSignerSupervisor.kt:61-65,98-100`），默认值与原 `15s / 250ms / 1s / 5` 行为完全一致；健康轮询改由可注入的 `healthRequester` 传输层承载（`:577-599`），超时/退避/熔断在循环内读取实例参数（`:332,368-378,400-415`）。新增 2 个短时单测：`startup timeout destroys spawned process and triggers restart`（`WaaSignerSupervisorTest.kt:412-453`，`startupTimeoutMs = 50L` + `healthRequester` 恒 `null`，实测 `0.088s`，断言超时进程被销毁并触发第 2 次 spawn）与 `consecutive failures reaching maxRestartAttempts stop retrying and enter ERROR`（`:455-495`，`maxRestartAttempts = 2`、`initialBackoffMs = 10L`，实测 `0.219s`，断言 3 次 spawn、`restartCount = 2`、终态 `SignerRunState.ERROR`）。**已关闭。**

### N（提示 / 良好实践与现状备注）
1. **`N-1`（保留）**：`stop()` 内部同步执行 `destroyProcess`，最多阻塞 `3000ms`（`STOP_GRACE_WAIT_MS`，`:490-506`）；叠加 `P2-5` 的 spawn 锁，强烈建议 M3 前台 Service 一律从 `Dispatchers.IO` 调用 `stop()`，避免主线程 ANR / StrictMode 违规。
2. **`N-2`（复核一致）**：二轮 `--rerun-tasks` 实跑 `:app:lintDebug` 仍为 `0 errors, 9 warnings`：`ChromeOsAbiSupport` 1 处（`build.gradle.kts:22`，符合仅打包 `arm64-v8a` 决策）+ `LoginScreen.kt:225` `ObsoleteSdkInt` 1 处 + `colors.xml:3-9` `UnusedResources` 7 处，全部为仓库既有代码，非 M1 引入；`compileDebugKotlin` 的 3 条 deprecation warning 亦全部来自既有 `ProfileCard.kt:111` / `LoginScreen.kt:121,233`。
3. **`N-3`（保留）**：未传 `-proxy` 且无代理环境变量时仍会 `discoverProxy()` 探测 `127.0.0.1:7890` / `127.0.0.1:10808`（`main.go:1120`），安卓端可复用本地代理，冷启动最多额外 ~100ms。
4. **`N-4`（复核一致）**：`resolveRuntimeDNSServers`（`main.go:1006-1020`）与 `resolveAndroidCertDir`（`main.go:1053-1073`）对 `windows`/`darwin` 直接跳过，对存在非空 `/etc/resolv.conf` 或 `/etc/ssl/certs/ca-certificates.crt` 的标准 Linux/Docker 环境保持无侵入，桌面端与容器版行为未破坏；`configureRuntimeNetwork`（`:1075-1093`）仅在显式 `-dns`/`WAA_DNS_SERVERS`/`resolv.conf` 缺失时启用兜底。
5. **`N-5`（二轮新增，提示）**：DNS 主机名校验在两侧存在策略差异——Go `parseDNSServers` 接受合法主机名（`dns.google`，`main.go:894-922`），Android `normalizeDnsEndpoint` 只接受 IP 字面量（`WaaSignerSupervisor.kt:620-639`）。因 Android 系统 DNS 来源（`ConnectivityManager.getLinkProperties().dnsServers.hostAddress`）恒为 IP 字面量，实际不影响链路；若后续要支持自定义域名 DNS，Android 侧需同步放开。

---

## 4. 必跑门禁命令实跑记录（二轮独立执行）

### 4.1 Android Gradle 三合一门禁（强制无缓存重跑）

- **命令**：`.\gradlew.bat :app:ktlintCheck :app:testDebugUnitTest :app:lintDebug --console=plain`（工作目录 `android/`）
- **首跑结果**：`BUILD SUCCESSFUL in 1s`，`39 actionable tasks: 1 executed, 38 up-to-date`（全部为增量命中，证据强度不足）。
- **二轮强制重跑**：追加 `--rerun-tasks` 复核 → **`BUILD SUCCESSFUL in 30s`，`39 actionable tasks: 39 executed`**
  - `:app:ktlintCheck`：通过（`runKtlintCheckOverMainSourceSet` / `OverTestSourceSet` 均实际执行，0 errors）。
  - `:app:testDebugUnitTest`：通过。`TEST-com.aistudio.extractor.service.WaaSignerSupervisorTest.xml` 实测 `tests=13 failures=0 errors=0 skipped=0`。
  - `:app:lintDebug`：通过，`lint-results-debug.txt` 末行 `0 errors, 9 warnings`（构成见 `N-2`）。
  - `:app:processDebugMainManifest`：**无** `extractNativeLibs should not be specified` 告警（`P2-1` 实证关闭）。
  - `:app:compileDebugKotlin`：仅 3 条既有 deprecation warning，无新增告警。

### 4.2 `waa-signer` Go 单元测试（`-count=1` 无缓存全量）

- **环境**：`CGO_ENABLED=1`、`GOOS=windows`、`GOARCH=amd64`、`CC="zig cc -target x86_64-windows-gnu"`、`CXX="zig c++ -target x86_64-windows-gnu"`（内置 `.deps/zig/zig-windows-x86_64-0.13.0`）、`windows-amd64` SpiderMonkey 152 静态库；`go version go1.26.4 windows/amd64`。
- **命令**：`go test -count=1 ./...`（工作目录 `waa-signer/`）
- **结果**：**全部 PASS，0 失败**
  - `cmd/waa-signer`：`ok ... 0.204s`，7 个测试函数全部 PASS（`TestParseDNSServers` 含 8 个子用例、`TestFirstNonEmptyEnv`、`TestResolveRuntimeDNSServers`、`TestResolveAndroidCertDir`、`TestNewCustomDNSResolverFallbackAndRoundRobin`、`TestNewCustomDNSResolverEmptyServersNoPanic`、`TestConfigureRuntimeNetworkSetsDefaultResolver`）。
  - `internal/poolstate` `0.102s`、`internal/signerapi` `0.116s`、`internal/upstreamguard` `0.090s` 全 PASS；其余包 `no test files`。
- **`go vet ./cmd/...`**：无输出（0 告警）。

### 4.3 构建产物与空白符门禁

- **ELF 头校验**：`head=7F 45 4C 46 02 01 01 00 00 00 00 00 00 00 00 00 02 00 B7 00`，`e_machine=0x00B7`（`EM_AARCH64`），文件 `50,483,968` 字节。
- **`git diff --check`**（仓库根）：退出码 `0`，无行尾空白 / 冲突标记错误。
- **`git check-ignore -v android/app/src/main/jniLibs/arm64-v8a/libwaa_signer.so`**：命中 `.gitignore:60`。
- **说明**：本次 `git status` 中另有 `src/aistudio_api/**`、`tests/unit/**` 等改动，属此前会话的其他域工作，不在本次 M1 复审白名单内，本报告不予评价且未触碰。

### 4.4 M1 收口复跑（2026-11-14，`P2-5` / `Q-3` 闭环）

- **命令**：`.\gradlew.bat :app:ktlintCheck :app:testDebugUnitTest :app:lintDebug --console=plain`（工作目录 `android/`）
- **结果**：**`BUILD SUCCESSFUL in 22s`，`39 actionable tasks: 15 executed, 24 up-to-date`**
  - `:app:ktlintCheck`：通过（`runKtlintCheckOverMainSourceSet` / `OverTestSourceSet` 实际执行，0 errors）。
  - `:app:testDebugUnitTest`：通过。`TEST-com.aistudio.extractor.service.WaaSignerSupervisorTest.xml` 实测 `tests=15 failures=0 errors=0 skipped=0`（新增 2 例后由 13 增至 15），套件耗时 `0.836s`；`stop immediately after start during process spawn...` 由 `3.108s` 降至 `0.108s`（`P2-5` 实证），新增 `startup timeout...` `0.088s`、`consecutive failures...` `0.219s`。
  - `:app:lintDebug`：通过，`lint-results-debug.txt` 末行 `0 errors, 9 warnings`（构成与 `N-2` 一致，无新增告警）。
- **`git diff --check`**（仓库根）：退出码 `0`，无行尾空白 / 冲突标记错误。

---

## 5. 复审结论与后续动作

- **最终结论：`PASS`。** 初审 `P1-1`、`P1-2`、`P2-1`、`P2-2`、`P2-3`、`P2-4` 与 `Q-1`（主体）全部按建议方案闭环，源码/单测/产物/门禁四方证据一致，`P0`/`P1` 清零，M1 可以验收。**M1 收口轮次（2026-11-14）已进一步闭合 `P2-5` 与 `Q-3`，`P2` 与 `Q` 项均无未决遗留。**
- **不阻断 M1 的遗留（转 M2/M3）**：
  1. ~~`P2-5`：`stop()` 可能被锁内 spawn 阻塞（建议 spawn 移出锁外 + 代际复核登记）。~~ **已关闭（spawn 移出锁外 + `NonCancellable` + 代际复核登记）。**
  2. `Q-2`：真机 `execve()` / DNS / CA 握手实效待 APK 联调验证。
  3. ~~`Q-3`：启动超时强杀与连败熔断缺自动化覆盖，建议注入超时/次数参数后补 2 个短时用例。~~ **已关闭（可注入默认参数 + 2 个短时单测）。**
  4. `N-1`：前台 Service 调用 `stop()` 必须离开主线程（`stop()` 仍同步执行 `destroyProcess`，最多 3s 优雅等待）。
- **后续约束提醒**：M2/M3 涉及真实设备的验证仍须由主控在单次常驻进程中执行，严禁反复重启服务触发 BotGuard 风控。

---

## 附录 A：初审（Round 1）记录留档（原文）

> 以下为同日首轮审查原文，保留问题编号与证据，供追溯；其 `CONDITIONAL` 结论已被本轮 `PASS` 取代。

- **初审结论**：`CONDITIONAL`
- **初审问题清单**：`P0` 无；`P1-1`（`start()`/`stop()` 取消窗口导致孤儿原生进程与 8081 端口死锁）、`P1-2`（`stop()` 与旧循环异步竞态导致 `_status` 回退与 `shouldRun` 误关闭）、`P2-1`（Manifest 双写 `extractNativeLibs` 触发 AGP 合并告警）、`P2-2`（`newCustomDNSResolver` 空切片除零 + UDP 轮询黑洞）、`P2-3`（`build.ps1` `CGO_CPPFLAGS` 残留污染）、`P2-4`（`parseDNSServers` 畸形串与 IPv6 Zone ID 处理不严）、`Q-1`（实例生命周期缺自动化覆盖）、`Q-2`（真机与 Windows 端到端留待后续）、`N-1`..`N-4`（`stop()` 阻塞 3s、lint 9 warnings、代理探测耗时、桌面/Docker 兼容性）。
- **初审门禁基线**：`:app:ktlintCheck :app:testDebugUnitTest :app:lintDebug --rerun-tasks` → `BUILD SUCCESSFUL in 26s`（8 个单测、`0 errors, 9 warnings`）；`go test -count=1 -v ./...` 全 PASS。

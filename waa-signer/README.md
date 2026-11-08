# WAA Signer (Google AI Studio 独立 Go 签名服务)

基于 [Mag1cFall/AIStudio2API](https://github.com/Mag1cFall/AIStudio2API) 核心内核抽离的独立 WAA (Web App Attestation / BotGuard) 证明签名服务。

## 方案特性
1. **独立进程与嵌入式运行时**：基于 SpiderMonkey 152 引擎与 Firefox 152 宿主环境，直接执行 BotGuard 挑战，无需启动完整无头浏览器。
2. **实测性能参考**：
   - 首次冷启动（加载页面与初始化 VM）：约 5-8 秒
   - 二次热复用：约 10-30 毫秒
3. **环境特征匹配**：
   - 提取与当前 VM 会话绑定的 `User-Agent`、`X-AIStudio-Visit-Id`、`X-Goog-Ext-519733851-Bin`、`X-Goog-Api-Key` 等动态头并在响应中返回。
   - 避免外层 HTTP 请求头与 Proof 加密声明的客户端环境不一致导致 403 错误。
4. **接口支持**：
   - `POST /v1/proof/mint`：与 `aistudio-api` 签名客户端接口兼容。
   - `POST /v1/rpc/send`：使用内部 Firefox 152 uTLS / HTTP2 连接池直接代理发送受保护 RPC。
   - `POST /v1/worker/release`：显式释放指定账号或全部空闲 Worker。
---

## 跨平台一键编译

### 前置要求
- **Go 工具链**：本模块 `go.mod` 声明 `go 1.23`（本仓库自身代码的语法基线）。但上游传输依赖当前声明了更高版本：
  - `github.com/Mag1cFall/uquic v0.0.6-firefox.6` → `go 1.27`
  - `github.com/Mag1cFall/utls v1.8.2-firefox.3` → `go 1.27`
  - `golang.org/x/net v0.59.0` → `go 1.26.0`

  实测证据（Go 1.26.4，`GOTOOLCHAIN=local`）：

  ```text
  go: github.com/Mag1cFall/uquic@v0.0.6-firefox.6 requires go >= 1.27
      (running go 1.26.4; GOTOOLCHAIN=local)
  ```

  因此**实际编译必须使用 Go 1.27 工具链**。`go.mod` 已把主 `go` 指令降到 `1.23` 并用独立的 `toolchain go1.27.0` 指令记录实际所需工具链：主指令只表达本模块自身代码的语法基线，无法绕过依赖模块自身的最低版本要求；在保留上述上游固定版本的前提下不存在降级路径。

  构建/测试请显式指定工具链：

  ```bash
  cd waa-signer
  GOTOOLCHAIN=go1.27.0 go test ./internal/poolstate ./internal/upstreamguard ./internal/signerapi
  ```

  补充证据：若使用 `GOFLAGS=-mod=mod` 执行构建，Go 会因依赖的更高版本要求自动把主 `go` 指令改写回 `1.27`。本仓库刻意保留 `go 1.23` + `toolchain go1.27.0` 以同时表达“本模块基线”和“实际构建工具链”，不把依赖要求伪装成本模块要求。请不要把本项目描述为“Go 1.22+ 可直接构建”。
- C/C++ 编译器：强烈推荐 `zig`（免配置支持静态交叉链接），亦可使用 `clang` 或 `gcc` / `MinGW-w64`

### 编译命令

#### Linux / macOS / Android (Termux)
在 `waa-signer/` 目录下执行：
```bash
bash build.sh
```
脚本会自动识别 CPU 架构与操作系统（Linux x64/arm64、macOS arm64/amd64），自动拉取对应的官方静态 `spidermonkey-v152.0.4` 依赖，并产出单一无外部依赖的二进制文件 `bin/waa-signer`。

#### Windows (CMD / PowerShell)
在 `waa-signer\` 目录下直接运行：
```bat
build.bat
```
或在 PowerShell 中执行：
```powershell
.\build.ps1
```
脚本会自动完成依赖下载与编译，产出 `bin\waa-signer.exe`。
> **Windows 编译小贴士**：推荐使用轻量免配置的 Zig 编译器（执行 `winget install zig.zig` 即可一键就绪）。

#### Docker（当前状态）
**主 Dockerfile 目前没有内置 `waa-signer` 的多阶段构建**，`docker build -t aistudio-api .` 不会产出或内置本服务。将 `waa-signer` 纳入主镜像的多阶段构建计划在 **2.1-B** 完成。

在 2.1-B 落地前，请任选一种方式：
1. 按上面的脚本自行编译 `bin/waa-signer`，在容器启动时通过 volume 挂载（或 COPY 到派生镜像）并单独拉起；
2. 独立运行本服务（本机或远程节点），由 `aistudio-api` 通过 `waa.mode=remote` + 显式 endpoint 连接。

> 不存在“主 Dockerfile 已自动构建并内置 waa-signer”的现成能力，请勿依赖不存在的自动化。

---

## 运行与参数说明

```bash
./bin/waa-signer [参数]
```

| 参数 | 环境变量 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `-listen` | `LISTEN_ADDR` / `PORT` | `127.0.0.1:8081` | HTTP 服务监听地址 |
| `-proxy` | `AISTUDIO_PROXY` / `HTTPS_PROXY` | 自动探测 7890/10808 | 上游代理 URL (HTTP/HTTPS/SOCKS5) |
| `-state-dir` | `WAA_STATE_DIR` | 系统临时目录下的 `aistudio_waa_workers` | Worker 指纹与解释器缓存目录 |
| `-locale` | `WAA_LOCALE` | `en-US` | 浏览器环境语言 |
| `-timezone` | `AISTUDIO_TIMEZONE` | `America/Los_Angeles` | 浏览器环境时区 |
| `-max-workers` | `WAA_MAX_WORKERS` | `8` | 同时存活的 Worker 上限；满额时按 LRU 淘汰空闲 Worker |
| `-idle-ttl` | `WAA_IDLE_TTL` | `900s` | 空闲 Worker 保留时长，支持 `900`/`900s`/`15m` |

### 后台守护启动示例
```bash
nohup ./bin/waa-signer -listen "127.0.0.1:8081" -proxy "http://127.0.0.1:7890" > waa-signer.log 2>&1 &
```

---

## HTTP 接口定义

### 1. 探活 `GET /health`
```json
{
  "ok": true,
  "status": "ok",
  "engine": "spidermonkey-152",
  "workers": 1,
  "idleWorkers": 1,
  "maxWorkers": 8,
  "version": "2.1.0",
  "ready": true
}
```

字段语义：
- `ok` / `status`：服务进程与 Worker 池是否仍在正常接受请求；关闭流程中为 `ok=false`、`status="closed"`。
- `workers` / `idleWorkers` / `maxWorkers`：当前占用的 Worker 槽位、完全空闲的 Worker、并发上限。
- `ready`：**服务进程健康且池还能接收新请求**（存在剩余名额或至少一个空闲 Worker）。池已满且全部 Worker 忙时 `ready=false`，但 `ok=true`；`ready` 不表示“至少有一个 Worker 已经预热”。
- 响应绝不包含 Cookie、Authorization 或任何认证材料。

### 2. 签名 `POST /v1/proof/mint`
**请求体 (JSON)**：
```json
{
  "cookies": "SID=...; SAPISID=...; __Secure-1PSID=...",
  "authUser": 0,
  "g1Tier": "TIER1",
  "model": "models/gemini-3.8-flash",
  "prompt": "用户提示词文本",
  "wireContents": [],
  "accountKey": "acct_01JABCDEF"
}
```
- `accountKey` 可选。提供时必须是 1-128 位字母、数字、点、下划线或连字符，且以字母/数字开头；该键会作为 Worker 的稳定身份，用于 mint / rpc / release 对齐，Cookie 轮换不会导致键漂移。未提供时回退为基于 Cookie 的哈希键。
- 非法 `accountKey` 返回 `400`。

**响应体 (JSON)**：
```json
{
  "ok": true,
  "poolState": "hot",
  "accountKey": "acct_01JABCDEF",
  "digest": "7cf1b1f9ef045379fb27a3177f0dd73f671839da3db441cbaae0cac58145d80f",
  "proof": "!enmleR3NAAY187QsFfNCJe3GmEsUzL87AEABEArZ1OshTDyK2...",
  "setCookies": [
    {
      "name": "__Secure-3PSIDTS",
      "value": "...",
      "domain": ".google.com",
      "path": "/",
      "expires": 1790000000,
      "httpOnly": true,
      "secure": true,
      "sameSite": "None"
    }
  ],
  "deletedCookies": [],
  "headers": {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0",
    "Authorization": "SAPISIDHASH ...",
    "Cookie": "...",
    "X-AIStudio-G1-Tier": "TIER1",
    "X-Aistudio-Visit-Id": "v1_...",
    "X-Goog-Api-Key": "AIzaSy...",
    "X-Goog-Authuser": "0",
    "X-Goog-Ext-519733851-Bin": "CAASAUIwATgEQABQBFgDYgJKUHAAeAGQAQCYAQE="
  }
}
```
- `setCookies`：本次交互新增或变化的 Cookie。
- `deletedCookies`：本次交互被删除的 Cookie 墓碑项，`value` 为空、`expires` 为 `0`（Unix 纪元），调用方应按空值或过期时间清理本地副本。
- 服务端普通日志只记录数量，不记录 Cookie 内容。

### 3. 原生连接池转发 `POST /v1/rpc/send`
请求体额外支持 `accountKey`（语义与 mint 相同）。直接复用底层已建立的 Firefox 152 uTLS / HTTP2 / HTTP3 官方连接池向 Google MakerSuiteService 发送原生请求。

安全约束：
- 请求体上限 **32 MiB**，超限返回 `413`。
- `url` 必须为 `https://`，host 必须命中 Google 官方白名单：`google.com`、`*.google.com`、`googleapis.com`、`*.googleapis.com`（含精确域名规则）；拒绝 userinfo、非 443 端口、IP 字面量、任意第三方 URL/SSRF。非法 URL 返回 `400`，错误体不回显 Cookie。
- `url` 省略时默认使用 MakerSuite `GenerateContent` 官方端点。

流式响应在写入状态码前通过 `X-WAA-Set-Cookies` 回传本次交互产生的 Cookie 摘要（`base64url(JSON 数组)`；数组内同时包含更新项与删除墓碑项）；超过 32 KiB 时省略该头并设置 `X-WAA-Set-Cookies-Truncated: true`。同时通过 `X-WAA-Account-Key` 返回实际使用的账户键，可直接用于 `POST /v1/worker/release`。流式响应头与元数据在首字节写出前设置完成。

### 4. 显式释放 `POST /v1/worker/release`
```json
{"accountKey": "acct_01JABCDEF"}
```
或全量释放空闲 Worker：
```json
{"all": true}
```
返回 `{"ok": true, "released": 1, "skipped": 0}`。幂等；没有匹配时返回 `released: 0`；仍有在途 proof/rpc 的 Worker 不会被强制关闭，会计入 `skipped`。

### 5. 强制重建与并发生效规则
热 Worker 签名失败时，调用方可重新请求并由服务端触发 `forceRecreate`：
- 同账号 `inFlight == 0` 时立即关闭旧 Worker 并重建；
- 同账号仍有在途 proof/rpc 时，设置 `recreatePending` / `recreateGeneration`，等待最后一个 lease 释放后再安全重建；
- 整个过程不会中断同账号正在执行的请求，也不会把 Cookie 写入日志。

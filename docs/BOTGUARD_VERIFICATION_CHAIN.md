# Google AI Studio BotGuard 验证机制技术规范

本文档记录 Google AI Studio 官方 Web 客户端与 Google WAA (Web Attestation & Anti-abuse / BotGuard) 服务端的交互协议、数据链路与验证逻辑。

---

## 目录

- [1. 系统架构与交互拓扑](#1-系统架构与交互拓扑)
- [2. 阶段一：页面加载与服务初始化](#2-阶段一页面加载与服务初始化)
- [3. 阶段二：WAA 挑战握手与 Wasm 运行时加载](#3-阶段二waa-挑战握手与-wasm-运行时加载)
- [4. 阶段三：请求内容哈希与快照签名](#4-阶段三请求内容哈希与快照签名)
- [5. 阶段四：Wire 协议组包与浏览器 Fetch/XHR 重放](#5-阶段四wire-协议组包与浏览器-fetchxhr-重放)
- [6. 阶段五：服务端校验与统一报错响应](#6-阶段五服务端校验与统一报错响应)
- [7. 生命周期与会话约束](#7-生命周期与会话约束)
- [8. 核心报文结构参考](#8-核心报文结构参考)
- [9. 池化启动与快照加固（B6 实装）](#9-池化启动与快照加固b6-实装)

---

## 1. 系统架构与交互拓扑

浏览器与 Google 后端服务集群的三方交互拓扑关系：

```mermaid
flowchart TD
    subgraph Browser ["用户浏览器环境 (Client Browser)"]
        UI["Google AI Studio UI<br/>(Angular SPA)"]
        BGSvc["BotGuardService<br/>(WAA 前端容器)"]
        BGWasm["BotGuard Wasm Runtime<br/>(动态加载的沙箱虚拟机)"]
        UI <--> BGSvc
        BGSvc <--> BGWasm
    end

    subgraph GoogleInfra ["Google 后端服务集群 (Google Infrastructure)"]
        MakerSuite["MakerSuite RPC 网关<br/>alkalimakersuite-pa.clients6.google.com"]
        WaaServer["WAA 反作弊网关<br/>waa-pa.clients6.google.com"]
        StaticCDN["Google 静态 CDN<br/>www.google.com/js/bg/..."]
    end

    UI -- "(1) 页面资源 & GenerateContent RPC" --> MakerSuite
    BGSvc -- "(2) 挑战初始化握手 (/Waa/Ping)" --> WaaServer
    BGWasm -- "(3) 下载与执行专属 Wasm 字节码" --> StaticCDN
```

### 完整交互时序

```mermaid
sequenceDiagram
    autonumber
    actor User as 用户 (User)
    participant Browser as AI Studio (Angular)
    participant WaaClient as BotGuardService
    participant WaaServer as WAA 网关 (waa-pa)
    participant StaticCDN as Google CDN (Static)
    participant Gateway as MakerSuite RPC (alkali)

    Note over User,Gateway: 【阶段一 & 二：页面启动与 WAA 挑战握手】
    User->>Browser: 访问 aistudio.google.com/u/0/prompts/new_chat
    Browser->>Browser: Angular 初始化并实例化 BotGuardService
    Browser->>WaaClient: initialize()
    WaaClient->>WaaServer: POST /Waa/Ping (携带 X-Goog-AuthUser 与 SAPISIDHASH)
    WaaServer-->>WaaClient: 下发会话挑战 Nonce 与 Wasm 资源文件名
    WaaClient->>StaticCDN: GET /js/bg/{hash}.js
    StaticCDN-->>WaaClient: 返回动态 BotGuard 虚拟机代码 (绑定 u/0 身份)

    Note over User,Gateway: 【阶段三 & 四：用户提问与动态签名】
    User->>Browser: 输入 Prompt 并点击 Run
    Browser->>Browser: 计算 content_hash = SHA256(Prompt + Images)
    Browser->>WaaClient: snapshot(content_hash)
    WaaClient->>WaaClient: 收集环境/时钟指纹 + HMAC(内容哈希 + 凭据)
    WaaClient-->>Browser: 返回加密快照 Token (!dXaldhL...)
    Browser->>Browser: 组装 Wire 数据包 (body[4] = snapshot)
    Browser->>Gateway: POST /GenerateContent (Fetch credentials='include')
    Note over Gateway: 【阶段五：服务端校验与响应】
    Gateway->>Gateway: 解密 Body[4] 快照，比对 HMAC 内容指纹
    Gateway->>Gateway: 校验快照内嵌 GAIA ID 与请求头 X-Goog-AuthUser
    Gateway-->>Browser: 校验通过，分块流式返回推理响应 (200 OK)
    Browser-->>User: 实时渲染并展示模型生成结果
```

---

## 2. 阶段一：页面加载与服务初始化

1. **页面与路由加载**：
   - 用户访问 `https://aistudio.google.com/prompts/new_chat?model=gemini-3.8-flash`（或子账号路由 `/u/{auth_user}/...`）。
   - HTML 页面引入 `boq-makersuite` JavaScript Bundle。
2. **依赖注入容器初始化**：
   - Angular 根组件启动时，DI 容器解析并实例化 `BotGuardService`（符号随版本变化，如 `_.QC`、`_.Vv`，注入令牌挂载于 root 作用域）：
     ```javascript
     // 结构参考 (类混淆名以实际运行环境为准):
     _.QC = class {
         constructor() {
             this.qb = _.n(_.Aq);
             this.F = !1;
             this.H = new Promise(a => {
                 var b = this;
                 return _.y(function*() {
                     b.qb.la && (yield b.initialize());
                     a();
                 });
             });
         }
     };
     ```
   - 从 Cookie 中读取 `SAPISID`，并根据当前 UNIX 时间戳计算 `SAPISIDHASH`。

---

## 3. 阶段二：WAA 挑战握手与 Wasm 运行时加载

在 `BotGuardService.initialize()` 中，前端与 Google WAA 服务完成握手：

```javascript
initialize() {
    var a = this;
    return _.z(function*() {
        var b = new QVa; // waa-pa 远程 RPC 通信客户端
        a.A = new LUa({
            fetcher: new ura(b, () => a.getMetadata())
        });
        yield a.A.u6a; // 等待 Wasm 运行时握手完成并就绪
    });
}
getMetadata() {
    var a;
    return {
        "X-Goog-Api-Key": "AIzaSyBGb5fGAyC-pRcRU6MUHb__b_vKha71HRE",
        Authorization: (a = _.bq([])) != null ? a : "",
        "X-Goog-AuthUser": this.wb.Bo.toString()
    };
}
```
### 3.1 握手信令交互 (`/Waa/Ping`)

请求发往 `https://waa-pa.clients6.google.com/$rpc/google.internal.waa.v1.Waa/Ping`：

- **请求头示例**：
  ```http
  X-Goog-Api-Key: AIzaSyBGb5fGAyC-pRcRU6MUHb__b_vKha71HRE
  Authorization: SAPISIDHASH <timestamp>_<sha1_hash> ...
  X-Goog-AuthUser: 0
  Content-Type: application/json+protobuf
  ```
- **请求负载**：包含客户端接入 ID（如 `client: "lmnUSbltwc5ULv48iKLX"`）与环境参数。
- **响应载荷**：WAA 服务返回会话挑战 Nonce 与专属 JS/Wasm 资源哈希。

### 3.2 动态加载 Wasm 虚拟机

前端向 Google CDN 请求对应哈希的脚本：

```http
GET https://www.google.com/js/bg/gBetl7I-09yp6c3Nmm4ajwTxhDHStoNbVEOK3L3hfg4.js
```

脚本执行并实例化核心虚拟机。此时虚拟机内部已绑定当前会话的 GAIA ID、Nonce 与 `auth_user` 凭据。

---

## 4. 阶段三：请求内容哈希与快照签名

当用户提交提示词触发请求时，AI Studio 底层执行管线：

```javascript
// 请求组装与签名管线
t = yield _.Nv(requestProto);        // 1. 递归提取 contents 并计算 SHA-256 内容哈希
t = yield _.Dp(this.zc, t);          // 2. 调用快照函数生成 Token
_.l(requestProto, 5, t);             // 3. 将 Token 注入 Protobuf 字段 5 (Wire 数组 body[4])
```

1. **计算内容哈希 (`_.Nv`)**：
   提取请求 `contents` 数组中的所有文本与内联图片数据，通过 `_.rj` 映射为字符串，按顺序以单个空格 `" "` 拼接后计算 SHA-256 Hex 摘要：
   ```javascript
   _.Nv = function(requestProto) {
       return _.z(function*() {
           var b = _.Mv(requestProto).flatMap(c => c.eg()).map(_.rj);
           return _.dv(b.join(" "));
       });
   };

   _.rj = function(part) {
       switch(_.oj(part)) {
           case 2: // text 文本块
               return part.getText();
           case 3: // inline_data 图片数据 (Base64 字符串)
               let b, c;
               return (b = _.pj(part)) == null ? void 0 : (c = b.getData()) == null ? void 0 : c.Xe();
           case 6: // file_data 引用资源
               let d;
               return (d = _.qj(part)) == null ? void 0 : d.getId();
           default: // functionCall / functionResponse 等非文本
               return "";
       }
   };

   _.dv = function(str) {
       var a = (new TextEncoder).encode(str);
       var b = new _.pqa; // SHA-256 算法实现
       b.update(a);
       return maa(b.digest()); // 转换为 16 进制 hex string
   };
   ```

2. **生成 BotGuard 快照 (`_.Pn` / `_.jp` / `_.Dp` / `snapKey`)**：
   前端调用被检测到的专属签名函数（在 `default_MakerSuite` 中动态匹配注册）：
   ```javascript
   // 实测最新版本 (2026-10-27 live AST 验证 - live_m=_b.js):
   _.Pn = function(a, b) {
       return _.y(function*() {
           yield a.H; // 等待 BotGuardService 初始化 Promise
           return a.A
               ? (yield Fxb(a.A), a.A.snapshot({ BFc: { content: b } }))
               : "";
       });
   };
   _.gD = function(a) {
       return _.y(function*() {
           var b = _.Ck(a, _.au, 2).flatMap(c => c.Rg()).map(_.jn);
           return _.VC(b.join(" "));
       });
   };
   // 历史版本混淆符号参考:
   // 2026-09-28: _.jp = (a, b) => a.A.snapshot({ Bcc: { content: b } })
   // 更早版本:   _.Dp = (a, b) => a.A.snapshot({ e9b: { content: b } })
   ```
   > **关键事实说明（严格对齐网页源码 AST 分析）**：
   > 1. `_.Ck(a, _.au, 2)` 仅访问请求 Protobuf 的字段 2（即 `contents` 消息体），**绝不包含**字段 6（`system_instruction`）。
   > 2. `_.jn` 仅对文本 (tag 2)、内联数据 (tag 3)、文件 (tag 6) 提取字符串，其余非文本 Part（函数调用、执行结果等）**统一返回空字符串 `""`**。
   > 3. 数组所有元素以单空格 `" "` 进行 `join`，**不作过滤**，随后计算 SHA-256 Hex 摘要。
   >
   > **符号混淆与动态匹配**：Google 构建版本更新时会重置混淆名（如类名 `_.QC`/`_.nC`、签名函数 `_.Pn`/`_.jp`、参数键 `BFc`/`Bcc` 等），因此系统通过源码特征（`.snapshot`、`content`、`yield`）动态定位函数，避免硬编码混淆符号。
   >
   > **生命周期与状态**：
   > - 快照 Token 与单次请求内容及时间戳绑定，不跨请求复用。
   > - 会话凭据与 Nonce 由服务端动态下发，账号切换时重新初始化。
3. **虚拟机内部处理**：
   - 采集浏览器指纹（屏幕分辨率、语言列表、时钟精度、WebGL 上下文特征等）；
   - 使用握手会话秘钥对 `contentHash` 与环境特征进行加密并计算 HMAC 签名；
   - 生成以 `!` 开头、长度约为 1500~1650 字符的 Base64 快照 Token（例如 `!dXaldhLNAAa1dSU5lXVCmOax...`）。

## 5. 阶段四：Wire 协议组包与浏览器 Fetch/XHR 重放

```json
[
  "models/gemini-3.8-flash",
  [
    [
      [[null, "用户输入的提示词内容"]],
      "user"
    ]
  ],
  [[null, null, 7, 5], [null, null, 8, 5], [null, null, 9, 5], [null, null, 10, 5]],
  [null, null, null, 128, 0.5, 0.8, 16],
  "!dXaldhLNAAa1dSU5lXVCmOax...",
  null,
  null,
  null,
  null,
  null,
  1,
  null,
  null,
  [[null, null, "Asia/Tokyo"]]
]
```

数组索引与 Google Protobuf 字段序号的精确映射（`Index = FieldNumber - 1`）：

| 数组索引 | Proto 字段号 | 字段名称 | 类型与说明 |
|---|---|---|---|
| **`0`** | `Field 1` | `model` | 目标模型名称（如 `"models/gemini-3.8-flash"`、`"models/gemini-3.7-flash"`） |
| **`1`** | `Field 2` | `contents` | 结构化对话轮次与多模态数据数组 |
| **`2`** | `Field 3` | `safety_settings` | 安全审核阈值配置数组 |
| **`3`** | `Field 4` | `generation_config` | 生成控制参数（temperature, topP, topK, maxTokens 等） |
| **`4`** | `Field 5` | `snapshot` | **BotGuard 快照签名 Token（以 `!` 开头）** |
| **`5`** | `Field 6` | `system_instruction` | 系统指令角色与提示词块 |
| **`6`** | `Field 7` | `tools` | 工具声明列表（Google 搜索、代码执行、Google Maps 等） |
| **`7`** | `Field 8` | `evergreen_model_uri` | 动态常青模型 URI（如 `"models/gemini-..."`） |
| **`10`** | `Field 11` | `request_flag` | 请求行为标记（固定值 `1`） |
| **`11`** | `Field 12` | `session_token` | 会话续接令牌（如 `"v1_..."`） |
| **`13`** | `Field 14` | `request_context` | 请求上下文与时区声明（如 `[[null, null, "Asia/Tokyo"]]`） |
### 浏览器内 Fetch/XHR 发送

```http
POST https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GenerateContent
Host: alkalimakersuite-pa.clients6.google.com
Content-Type: application/json+protobuf
X-User-Agent: grpc-web-javascript/0.1
X-Goog-Api-Key: AIzaSyDdP816MREB3SkjZO04QXbjsigfcI0GWOs
X-Goog-AuthUser: 0
Authorization: SAPISIDHASH 1789397237_0093f004...
Origin: https://aistudio.google.com
Referer: https://aistudio.google.com/
Cookie: SID=...; HSID=...; SSID=...; SAPISID=...; __Secure-1PAPISID=...
```

- 设置 `credentials: 'include'`（或 `xhr.withCredentials = true`），自动附加浏览器内存储的完整会话 Cookie。

---

## 6. 阶段五：服务端校验与统一报错响应

服务端网关接收到请求后的审计与校验链路：

```mermaid
flowchart TD
    Req["GenerateContent 请求<br/>(Header + Body)"] --> Step1

    subgraph GatewayValidation ["服务端校验流程"]
        Step1["1. 解密 Body[4] 快照载荷"]
        Step2["2. 校验 HMAC(Body[1] Contents 哈希)"]
        Step3["3. 比对快照 GAIA ID 与 X-Goog-AuthUser 会话"]
        Step4["4. 校验环境与时钟指纹"]

        Step1 -->|解密成功| Step2
        Step2 -->|哈希匹配| Step3
        Step3 -->|身份一致| Step4
    end

    Step1 -->|解密失败| Reject["统一拒绝：HTTP 403<br/>[,[7,'The caller does not have permission']]"]
    Step2 -->|哈希不匹配| Reject
    Step3 -->|身份冲突| Reject
    Step4 -->|指纹异常| Reject
    Step4 -->|校验通过| Accept["HTTP 200 OK<br/>分块流式返回推理数据"]
```

### 6.1 服务端报错特征与网关自愈机制

当 BotGuard 校验失败（快照缺失、快照过期、内容哈希不一致或身份冲突）时，服务端返回 **HTTP 403**，响应体为 JSON 数组 `[,[7,"The caller does not have permission"]]`（对应 gRPC 状态码 7 `PERMISSION_DENIED`）。

网关针对该报错建立两级自愈机制：
1. **403 两阶段自愈与多账号故障转移（Failover）**：首次 403 先清空模板缓存并在同一实例上重新生成签名重试；二次 403 才切换到健康账号。403 不加硬冷却锁（`auth_cooldown=0.0`），仅累加 `auth_errors` 计数；调度准入不读取 `auth_errors`，不存在 `auth_errors * 200.0` 动态避让。
2. **单账号在位重建（In-Place Recovery）**：当无备用账号可用时，网关清空请求模板缓存，重载 Cookie 并通过 `ensure_botguard_service(force_refresh=True)` 重新执行页面导航与 WAA 挑战握手，恢复服务可用性。

---

## 7. 生命周期与会话约束

1. **时效性与内容绑定**：快照 Token 内包含时间戳与挑战 Nonce，且与特定请求的内容哈希强绑定。内容修改后需重新计算哈希并生成新快照。
2. **后台休眠与保活**：当标签页处于后台时，虚拟机可能进入休眠。页面重新切回前台时，`visibilitychange` 事件会触发恢复（`service.A.CE()`）；若已失效则重新执行握手。
3. **多账号身份隔离**：每个子账号（`u/0`, `u/1`...）对应独立的身份上下文与 Wasm 实例，切换账号需通过 `/u/{auth_user}/` 专属路由切换并加载对应账号上下文。

## 8. 核心报文结构参考

### 8.1 SAPISIDHASH 鉴权格式

```text
Authorization: SAPISIDHASH <TS>_<HASH1> SAPISID1PHASH <TS>_<HASH2> SAPISID3PHASH <TS>_<HASH3>
```

计算逻辑：
```text
HASH = SHA1(timestamp + " " + SAPISID_VALUE + " https://aistudio.google.com")
```

网关实现 `calculate_sapisid_hash` 会同时生成三个哈希段（`src/aistudio_api/infrastructure/account/cookie_parser.py:279-297`）：

```text
SAPISIDHASH  <ts>_<sha1(ts SAPISID https://aistudio.google.com)>
SAPISID1PHASH <ts>_<sha1(ts __Secure-1PAPISID https://aistudio.google.com)>
SAPISID3PHASH <ts>_<sha1(ts __Secure-3PAPISID https://aistudio.google.com)>
```

- `SAPISID` 缺失时返回空字符串；1P/3P 缺失时回退到 `SAPISID`（`cookie_parser.py:283-287`）。
- 时间戳取当前 UNIX 秒；`origin` 默认 `https://aistudio.google.com`（`cookie_parser.py:289-296`）。
- 页面内 XHR 仍以 `withCredentials=true` 携带完整 Cookie；Authorization 头是补充而非唯一凭据。

### 8.2 快照签名 Token 特征

| 属性 | 特征 |
|---|---|
| **前缀** | 固定以感叹号 `!` 开头 |
| **编码** | URL-Safe Base64 |
| **长度** | 约 1500 ~ 1650 字符 |
| **内容** | 包含硬件与环境指纹、内容哈希 HMAC 与会话 Nonce |

---

## 9. 池化启动与快照加固（B6 实装）

本章记录目标树在 B6 收口阶段对 BotGuard 链路的工程化加固。原则是：**只允许池化实例启动时触发一次 BotGuard 探针；控制面不重复预热；模板必须来自真实上游请求；输入框与 Hook 必须清理。**

### 9.1 池化启动防重复预热（anti-double-warmup）

启动路径显式分流（`src/aistudio_api/api/app.py:77-120`、`344-357`）：

1. `get_configured_instance_specs()` 非空时 `pool_enabled=True`。
2. `_start_control_warmup(client, pool_enabled=True)` 直接返回 `None`，**不创建控制面预热任务**。
3. `_startup_instances` 只有在拿到非空 `warmup_task` 时才等待控制面预热；随后调用 `InstanceManager.startup()`（`api/app.py:105-120`）。
4. `InstanceManager.startup()` 为每个配置 spec 创建槽，并通过 `InstancePool.activate → PooledSession.bootstrap` 在独立 `BrowserContext` 内打开 Page、注入 Cookie、执行 BotGuard 握手（`instance_manager.py:164-232`、`pooled_session.py:64-114`）。
5. 只有配置中完全没有任何实例（legacy pool-disabled 模式）才允许控制面预热；日志会明确打印“实例池已启用：跳过控制面预热”。

模型发现预热也复用池会话，不新开 Page/BotGuard：`_model_warmup` 等待 `startup_task` 完成后调用 `_refresh_model_catalog_once`，后者只取 `runtime_state.discovery_sessions()`（paid lane 先、Free 后、空闲优先）（`api/app.py:417-442`、`instance_manager.py:628-651`）。

效果：一次冷启动只产生池会话的 BotGuard 探针，避免控制面 + 池会话双重握手触发 Google 风控。

### 9.2 池化会话 SAPISIDHASH 动态计算

模型发现与 HTTP 兜底都按**当前会话账号的 auth file** 动态计算 SAPISIDHASH，不使用硬编码 Authorization：

- 池会话遍历时从 `_session_auth_file` 读取 auth file，解析 `cookies`，调用 `calculate_sapisid_hash(cookies)`（`model_discovery.py:438-446`）。
- 页面 XHR 同时设置 `X-Goog-AuthUser`、`X-AIStudio-G1-Tier` 与动态 `Authorization`（`model_discovery.py:680-689`）。
- HTTP 兜底同样按账号 Cookie 现算 `Authorization`，并带 `X-AIStudio-Visit-Id`、`Origin`、`Referer`（`model_discovery.py:741-758`）。
- `calculate_sapisid_hash` 的实现与三段哈希格式见 §8.1（`cookie_parser.py:279-297`）。

时间戳与 Cookie 每次都重新取值，因此账号切换或 Cookie 刷新后不会复用旧哈希。

### 9.3 Temporary Chat 激活

为避免 bootstrap 探针污染用户历史，`bootstrap_botguard_and_template` 在填充 `"say '1'"` 前调用 `enable_temporary_chat`（`botguard_bootstrap.py:242-252`）：

- 先查找 `Temporary Chat`/`temporary mode`/`开启临时` 按钮并点击；排除 `data use policy` 干扰项（`botguard_bootstrap.py:38-49`）。
- 找不到直接按钮时，打开 `View more actions` 菜单，再查找 `temporary`/`临时` 菜单项（`botguard_bootstrap.py:51-66`）。
- 激活失败只记录 debug 并返回 `False`，不阻断 bootstrap（`botguard_bootstrap.py:71-77`）。

### 9.4 真实模板捕获，拒绝假 fallback

模板必须来自真实上游 `GenerateContent` 请求，禁止使用伪造 fallback 防止假阳性（`botguard_bootstrap.py:199-209`、`365-369`）：

1. 安装页面 Hook 并取得 `snapKey`（`install_page_hooks`，`botguard_bootstrap.py:116-164`）。
2. 订阅 `page.on_request`，只接受 URL 含 `GenerateContent`、不含 `Count`、且 body 非空的第一条请求，记录 URL/headers/body（`botguard_bootstrap.py:225-234`）。
3. 打开 Playground、等待输入框、启用 Temporary Chat、填入探针 prompt、点击 Run 或 Ctrl+Enter（`botguard_bootstrap.py:237-258`）。
4. 若网络事件未捕获，读取页面内 `window.__AISTUDIO__.capturedTemplate` 作为同源真实模板；仍为空则继续短轮询（`botguard_bootstrap.py:314-345`）。
5. 停止生成、清理输入框、执行 DOM GC；若最终仍无真实模板，抛 `RuntimeError("...拒绝使用假 fallback...")`（`botguard_bootstrap.py:347-369`）。

### 9.5 `finally` 输入清理与 Hook 复位

`bootstrap_botguard_and_template` 的 `finally` 无条件执行（`botguard_bootstrap.py:379-386`）：

1. `unsub()` 取消请求订阅，避免后续正常请求被误捕获。
2. 将 `window.__AISTUDIO__.interceptBootstrap` 置回 `false`。
3. 通过 `page.fill("textarea", original_text)` 恢复 bootstrap 前的输入框内容，清除 `"say '1'"` 探针文本。

即使捕获超时或抛异常，输入框与 Hook 也不会残留。

### 9.6 快照生成 5×250ms 重试

池化会话与控制面会话都采用 **5 次尝试、每次间隔 250ms** 的快照重试，拒绝上游 3×300ms 版本：

- 池化：`PooledSession.generate_snapshot` 在 `_snapshot_lock` 内最多 5 次调用 `SNAPSHOT_GENERATE_JS`；成功返回非空 snapshot（正常以 `!` 开头），失败第 5 次后抛 `RuntimeError`（`pooled_session.py:163-188`）。
- 控制面：`BrowserSession.generate_snapshot` 同样 5 次、每次 250ms（`session.py:410-436`）。
- 快照按每次请求的内容哈希重新生成，不跨请求共享；锁保证同一会话不会并发调用签名函数。

### 9.7 D-09 `hash_include_code` 诊断开关

Snapshot content hash 的默认口径包含 `executable_code` 与 `code_execution_result`（`session.py:60-86`）：

- 优先级：`AISTUDIO_HASH_INCLUDE_CODE` 环境变量 > `diagnostics.hash_include_code` 配置 > 默认 `true`（`config.py:257-277`）。
- 关闭时，代码执行 Part 不再进入 hash，回退到上游行为；文本、内联图片、file_id 仍按原顺序参与。
- 该开关仅用于诊断，不进入普通用户 UI；出现“同一输入被路由到错误模型/工具面”等严重异常时，优先怀疑该 hash 并临时回退（`AGENT.md` §4.5、`docs/WIRE_SPECIFICATION.md` §8.3）。

### 9.8 403 自愈与 BotGuard 的关系

BotGuard 校验失败统一返回 HTTP 403 `[,[7,"The caller does not have permission"]]`。网关自愈仍按 §6.1：

- 首次 403：清空模板缓存并在同一实例重新生成签名重试；不加硬冷却锁。
- 第二次 403：切换到健康账号；无备用账号时原位重建（重新导航 + WAA 握手）。
- Paid `TierRequiredError` 403 不触发切号/重建，避免把权限不足误判为 BotGuard 失效。

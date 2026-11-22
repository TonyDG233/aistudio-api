# AI Studio verify-account 二维码与账号验证状态研究报告

> 仓库：`D:\LLM\self_programming\aistudio-api`
> 日期：2026-11-22
> 性质：源码级研究 + 前端方案研究，**不施工**。
> 红线：未修改 `src/`、`web/`、`android/`、`waa-signer/`、`tests/`、`defaults/`；未 `git add/commit`；未访问 VPS；未改动账号文件；未启用/禁用账号；未启动本仓服务。带凭据探测仅使用 1 个低价值 Free 账号，且该账号会话已被上游判定失效；未使用 `acc_61a816d1`、`acc_6803137a`。

## 0. 结论摘要（TL;DR）

1. **二维码不是 AI Studio 的 RPC 返回值。** 它由 Google reCAPTCHA Enterprise 的 bframe 在浏览器内生成：bframe 从挑战数据截取 12 字符短码，拼成 `https://recaptcha.net/qr/<shortcode>`，再由内部 QR 库渲染到 `<canvas class="recaptchaJavascriptChallengeQrCanvas">`。AI Studio 页面只负责 `grecaptcha.enterprise.render/execute`。
2. **纯 HTTP + 已存 Cookies 拿不到二维码。** 匿名访问 `/verify-account` 直接 302 到 Google 登录页；即使有 Cookie，页面也必须在浏览器内执行 reCAPTCHA Enterprise + WAA/BotGuard，bframe 才会下发挑战并生成 QR。直接 GET bframe HTML 只有 sitekey 配置，没有 QR/短码。
3. **扫码结果的权威判据是 `VerifyUser`，不是 `GetUserVerificationStatus`。**
   - 页面流程：`GetUserVerificationStatus`（预检查）→ `grecaptcha.enterprise.execute("verify_user")` → bframe 内扫码 → `mobileToken` → `VerifyUser(token, WAA snapshot)`。
   - `VerifyUser` 返回 1=passed、2=failed、其他=inconclusive。
   - `GetUserVerificationStatus` 返回 1=required、2=verified、3=notRequired，适合做前置/粗查；页面在挑战期间**没有**用它做定时轮询。
4. **前端新增 `verify_required` 可行，而且现有后端已经识别该事件，只是被折叠成了 `session_expired`。** 关键改造点是把 `verification_required` 从 `session_expired` 拆出来，新增持久化字段/API/前端状态/徽章/筛选/按钮/弹窗；二维码展示建议先做“外部 AI Studio 验证页 + 服务端状态查询”的低风险方案。
5. **后台自动取二维码属于高风险能力。** 需要受控浏览器 + 有效登录态 + reCAPTCHA + WAA/BotGuard；可能触发风控、消耗验证次数，且与仓库 §4.6 的防风控铁律冲突。默认不建议实现，必须单独拍板。

---

## 1. 脚本入库位置与脚本机制摘要

### 1.1 入库位置

```text
docs/dev/rebuild/domains/P1-account/references/verify-account-qr.user.js
```

- 来源：用户提供的 `C:\Users\ADMIN\Desktop\fsdownload\油猴脚本.js`（上游脚本，非本仓代码）。
- 原文件未改动；入库副本在文件头增加了 3 行注释说明来源，正文逐字保留。
- 原始文件 SHA256：`7A2D8FFFCA6FE2950E3ADA1A735B036877DD82F9E05C6C665EDDD0E65159C316`（7629 bytes）。
- 入库副本：7903 bytes（含 274 bytes 头注释）。

### 1.2 脚本做了什么

脚本分为两个执行上下文：

1. **reCAPTCHA bframe/fallback iframe 内**（`references/verify-account-qr.user.js:23-58`）
   - 只读 DOM，不修改、不挂 MutationObserver。
   - 依次读取：
     - `#rc-mobile-verification-button` 的 `href`；
     - `canvas.recaptchaJavascriptChallengeQrCanvas` 的 `title`；
     - `.recaptchaJavascriptChallengeQrFallbackText` 的 `textContent`；
   - 用 `/recaptcha\.net\/qr\/([A-Za-z0-9_-]{6,32})/` 提取短码；
   - 每 800ms 轮询一次，发现新短码就 `window.top.postMessage(..., 'https://aistudio.google.com')`。
2. **aistudio.google.com 顶层页面**（`references/verify-account-qr.user.js:61-215`）
   - 校验消息来源是 `recaptcha.net` / `www.google.com`；
   - 在页面底部渲染一个浮动条，展示短码，提供“复制”和“填回中转页”；
   - 中转页是第三方 `https://qr.cuberelay.workers.dev`；
   - 当路径包含 `/verify-account` 时标记 `sawChallengeOnVerify`；
   - 离开 `/verify-account` 后显示“验证似乎已通过，请回中转页点「已通过」”。

### 1.3 脚本机制结论

| 问题 | 结论 |
|---|---|
| 依赖哪些页面上下文 | AI Studio 登录会话 + reCAPTCHA bframe（跨域 iframe） |
| 依赖哪些接口 | 不直接调 AI Studio RPC；依赖 reCAPTCHA bframe 内部协议 |
| 二维码从哪拿 | 不是图片接口；从 bframe 的按钮 href / canvas title / fallback text 提取短码 |
| 轮询/判定逻辑 | 800ms 读短码；1200ms 检查是否离开 `/verify-account`；**没有查询任何验证状态接口** |
| 能否判定扫码通过 | 不能。它只是猜测“离开验证页 = 可能通过”，不是权威状态 |
| 硬编码局限 | 第三方 hub、短码 6-32 位、bframe 路径判断、800/1200ms 固定轮询、依赖 DOM 类名 |
| 哪些只能在浏览器跑 | iframe DOM 读取、`postMessage`、Canvas 所属上下文、剪贴板 |
| 哪些可服务端复用 | 短码 URL 格式、挑战流程认知、`GetUserVerificationStatus` 状态查询；**不能复用 DOM 读取** |

---

## 2. verify-account 页面取证结果

### 2.1 匿名访问的重定向链（E1）

请求：

```text
GET https://aistudio.google.com/verify-account
UA: 桌面 Chrome/142
代理: http://127.0.0.1:10809
```

结果：

```text
302 https://aistudio.google.com/verify-account
    Location: https://accounts.google.com/ServiceLogin?passive=1209600
              &continue=https://aistudio.google.com/verify-account
              &followup=https://aistudio.google.com/verify-account
              &ltmpl=aistudio
302 https://accounts.google.com/ServiceLogin?...
    Location: https://accounts.google.com/v3/signin/identifier?...
              &continue=https://aistudio.google.com/verify-account
              &ltmpl=aistudio&flowName=GlifWebSignIn...
200 https://accounts.google.com/v3/signin/identifier?...
    Content-Type: text/html; charset=utf-8
    size: 178618 bytes (transfer) / 1250130 bytes on disk
```

结论：

- **未登录时 verify-account 不是公开页面**，直接被弹回 Google 登录页。
- 最终 HTML 是登录页，不是 verify-account 页面；因此匿名请求无法直接拿到 verify-account 的 bundle URL。
- 这是本次研究的最重要前提之一：verify-account 必须依赖有效 Google 会话。

### 2.2 带一个低价值 Free 账号的探测（E1，仅 1 次）

本地 `meta.json` 显示该账号 `tier=Free, expired=false`。使用其已存 Cookies 请求：

```text
GET https://aistudio.google.com/verify-account
=> 302 -> ... -> 200 https://accounts.google.com/v3/signin/identifier?...
```

```text
POST .../GetUserVerificationStatus
=> HTTP 401
[16,"Request had invalid authentication credentials. Expected OAuth 2 access token,
 login cookie or other valid authentication credential...",
 [["type.googleapis.com/google.rpc.ErrorInfo",
   ["SESSION_COOKIE_INVALID","googleapis.com",
    [["method","...GetUserVerificationStatus"],
     ["service","alkalimakersuite-pa.googleapis.com"],
     ["cookie","UNKNOWN"]]]]]]
```

结论：

- `meta.expired=false` 不等于会话仍有效；该账号 Cookie 已被上游判定为 `SESSION_COOKIE_INVALID`。
- 本轮没有用有效账号实测 verify-account 页面渲染；页面行为来自公开 bundle 的源码分析。
- 没有尝试第二个账号，避免扩大凭据探测范围。

### 2.3 公开 shell 与 bundle 发现（E1+E2）

虽然 `/verify-account` 被重定向，但 AI Studio 的公开欢迎页可以匿名访问：

```text
GET https://aistudio.google.com/welcome      -> 200
GET https://aistudio.google.com/app/welcome  -> 200
```

公开 shell 引用的脚本：

```text
https://www.gstatic.com/_/mss/boq-makersuite/_/js/k=boq-makersuite.MakerSuite.en_US.rWUj1ypxxEE.2018.O/am=QaSaAAYeHAI/d=1/excm=_b/ed=1/dg=0/wt=2/ujg=1/rs=AMOXD28mipyLK1D24NqS1G8HN1nPDr3-fg/dti=1/m=_b
https://www.gstatic.com/_/mss/boq-makersuite/_/js/k=boq-makersuite.MakerSuite.en_US.rWUj1ypxxEE.2018.O/ck=boq-makersuite.MakerSuite.nBRdAJqKxy0.L.B1.O/am=QaSaAAYeHAI/d=1/excm=_b/excm=_b/ed=1/wt=2/ujg=1/rs=AMOXD28W7REKSLd4dY16EoxPz-fvtzAhKQ/dti=1/m=KJeCXd
```

从 shell 的路由表找到 verify-account 的懒加载模块：

```js
// bundle/shell-1.js:2747
{path:"verify-account",title:_.hn("Verify account"),
 ud:()=>(0,_.Qp)("VI9Wbd").then(()=>_.hs),qA:[()=>_.xj(Bxb)]}
```

下载：

```text
https://www.gstatic.com/_/mss/boq-makersuite/_/js/k=boq-makersuite.MakerSuite.en_US.rWUj1ypxxEE.2018.O/am=QaSaAAYeHAI/d=1/excm=_b/ed=1/dg=0/wt=2/ujg=1/rs=AMOXD28mipyLK1D24NqS1G8HN1nPDr3-fg/dti=1/m=VI9Wbd
=> 200, 647479 bytes transfer (compressed), 2176129 bytes on disk
```

### 2.4 bundle 规模与下载统计

| 文件 | 传输大小 | 落盘大小 | SHA256 前 16 位 | 说明 |
|---|---:|---:|---|---|
| `bundle/shell-1.js` | 643142 | 2162009 | `58ECF2C187F27789` | AI Studio 公开 shell（m=_b） |
| `bundle/shell-2.js` | 21930 | 129239 | `A7D61739EAC60D45` | 公开 shell 的 KJeCXd 模块 |
| `bundle/mod-VI9Wbd.js` | 647479 | 2176129 | `29C2DB954F92756A` | verify-account 懒加载模块 |
| `bundle/recaptcha/enterprise.js` | 1104 | 1790 | `0F4ED8996D4BFA4A` | reCAPTCHA enterprise loader |
| `bundle/recaptcha/recaptcha__en.js` | 359371 | 860942 | `1B1E6C3057EAF0E9` | reCAPTCHA 4i3aN5wAweL7i3ADI7BSX4B- |
| `bundle/recaptcha/bframe-1.html` | 2220 | 17100 | `7617201C7A8CD4A8` | enterprise bframe 初始 HTML |
| `bundle/recaptcha/bframe-2.html` | 28539 | 54819 | `04749AEC5FE8AE9F` | enterprise anchor 初始 HTML |
| `page-welcome.html` | 12962 | 66534 | `5DE795DF2C5338B0` | 公开 welcome shell |
| `page-app-welcome.html` | 12975 | 66569 | `0F16D507B749DCFE` | 公开 app/welcome shell |
| `manifest.json` | 1195 | 1195 | `098E7F9BEBAAA083` | AI Studio PWA manifest |

说明：

- 传输大小是 HTTP 压缩后的 `size_download`；落盘大小是解压后的 UTF-8 文件大小。
- 匿名 verify-account 最终落在 Google 登录页；该登录页未保留到研究目录，避免保存页面内的匿名反 CSRF 令牌。
- 未保存任何 Cookie/Authorization 头；带凭据探测的响应页面已在临时目录删除。
- 本轮下载的是“公开 shell 全部 script 引用 + verify-account 路由懒加载模块 + reCAPTCHA 运行所需公开文件”，不是 AI Studio 全部业务模块（后者体量很大且与 verify-account 无直接关系）。

---

## 3. 二维码获取链路

### 3.1 页面侧：reCAPTCHA Enterprise

`bundle/mod-VI9Wbd.js:3383`：

```js
ejc=function(a){return _.x(function*(){
  yield _.Fp((0,_.wd)`https://www.google.com/recaptcha/enterprise.js?render=explicit&trustedtypes=true`);
  var b=a.window.grecaptcha;
  if(b==null||!b.enterprise)throw Error("Ff");
  yield new Promise(c=>{b.enterprise.ready(c)});
  return b
})};
gjc=class{
  render(a,b){...enterprise.render(a,{sitekey:b,size:"invisible"})}
  execute(a,b){...enterprise.execute(a,{action:b})}
  reset(a){...enterprise.reset(a)}
};
```

`bundle/mod-VI9Wbd.js:3387`：

```js
this.H=_.m(_.ir).agd;
```

sitekey 来自公开页面配置（`page-welcome.html` 内联）：

```text
"z6uGHf":"6LfMhuItAAAAAPMRs5Icz9eJnvIjRlxrxvJQ6ABh"
```

`bundle/mod-VI9Wbd.js:3400`：

```js
this.subject={
  Wxc:"verify_user",
  Jia:()=>this.Jia(),
  verify:a=>avj(this.A,a)
}
```

结论（E2）：

- AI Studio 使用 reCAPTCHA Enterprise，`size:"invisible"`，`action:"verify_user"`。
- sitekey 是公开前端常量，不是账号凭据。
- `enterprise.js` 只是 loader；实际挑战运行在 `recaptcha__en.js` + bframe 中。

### 3.2 二维码 DOM 与 Canvas

`bundle/recaptcha/recaptcha__en.js:75-77`：

```html
<div id='rc-qr'>
  <div class="recaptchaJavascriptChallengeQrOuterContainer">
    <div class="recaptchaJavascriptChallengeQrContainer">
      <div class="recaptchaJavascriptChallengeQrBackground"></div>
      <div class="recaptchaJavascriptChallengeQrInstructionsContainer">
        <div class="recaptchaJavascriptChallengeQrShortInstructionsContainer">
          <h1 class="recaptchaJavascriptChallengeQrShortInstructions">
            Scan to Verify You're Human
          </h1>
        </div>
        ...
      </div>
    </div>
  </div>
  <canvas class="recaptchaJavascriptChallengeQrCanvas"
          tabindex="-1" role="img" aria-label="QR code"></canvas>
  <p class="recaptchaJavascriptChallengeQrFallbackText"></p>
  <a class="recaptchaJavascriptChallengeQrButton"
     id='rc-mobile-verification-button' target="_blank" role="button">
    Click to verify
  </a>
</div>
```

`bundle/recaptcha/recaptcha__en.js:105`：

```text
Scan the QR code with your phone's camera.
This will initialize the verification process on the phone.
After verification, you will pass the challenge.
```

### 3.3 短码 URL 与渲染

`bundle/recaptcha/recaptcha__en.js:1635`：

```js
eE.prototype.tC=function(K,B,m,H,l,L,F,n,J){
  if(!this.W5(...,B))return ...;
  this.url=(n=(F=(this.o=Y[5](7,1,S[32](2,12,B)),
    l=W[20](88,3,S[32](3,12,B))),...),L[1]+this.o),
  ...
  CT.OT.Z(this.url).Tr(this.U),
  "To continue on your mobile device, enter the link "+this.url+" in your mobile browser.",
  this.U).title=m,
  this.J[K]&&(this.J[K].textContent=m),
  ...
```

其中：

```js
// recaptcha__en.js:1635
L=["kaios","https://recaptcha.net/qr/",12]
```

`bundle/recaptcha/recaptcha__en.js:1637`：

```js
... this.U.title=m,
  this.J[K]&&(this[J[K]].textContent=m),
  this.jY()?(x[26](10,"Android")?(
    this.url="intent://recaptcha.net/qr/"+this.o+
      "#Intent;scheme=https;action=android.intent.action.VIEW;end;",
    ...
```

`bundle/recaptcha/recaptcha__en.js:1638`：

```js
this.url="https://appclip.apple.com/id?p=com.google.Recaptcha.VerifyClip&id="+this.o
```

结论（E2 + E3）：

- 二维码内容是 URL：`https://recaptcha.net/qr/<12 字符短码>`。
- 短码从 bframe 的挑战数据中截取（`S[32](2,12,B)`）；具体截取函数未展开（E3）。
- 二维码由内部 QR 库渲染到 `<canvas>`（`CT.OT.Z(this.url).Tr(this.U)`），**不是** SVG/PNG base64/JSON URL 的服务端响应。
- Canvas 的 `title` 和 fallback 文本也包含这个 URL，所以用户脚本能读到短码。
- Android 会改写成 `intent://recaptcha.net/qr/<code>`；iOS 会改写成 App Clip URL。

### 3.4 bframe 初始 HTML 不含二维码（E1）

`bundle/recaptcha/bframe-1.html:259`：

```text
recaptcha.frame.Main.init("[\x22finput\x22,null,[\x22conf\x22,null,
 \x226LfMhuItAAAAAPMRs5Icz9eJnvIjRlxrxvJQ6ABh\x22,0,null,null,0.75,
 null,[21,125,63,73,95,87,41,43,42,83,102,105,109,121],...]]");
```

关键词统计：

```text
recaptcha.net/qr/ => 0
QrCanvas => 0
mobileToken => 0
challenge => 0
payload => 0
```

结论：**直接 GET bframe/anchor HTML 拿不到二维码或短码。** 短码必须等浏览器执行挑战、bframe 收到挑战数据后才生成。

### 3.5 完整链路

```text
用户打开 /verify-account（有效 Google 会话）
  -> AI Studio shell 加载 verify-account 模块
  -> 页面调用 grecaptcha.enterprise.render(container, {sitekey, size:"invisible"})
  -> 页面调用 grecaptcha.enterprise.execute(widget, "verify_user")
  -> reCAPTCHA 在 bframe 内下发挑战（可能是 QR，也可能直接通过/其他挑战）
  -> 如果是 QR 挑战：
       bframe 从挑战数据截取 12 字符短码
       拼成 https://recaptcha.net/qr/<shortcode>
       渲染到 canvas + 设置 canvas.title / fallback text / 按钮 href
  -> 用户手机扫码，在手机完成验证
  -> bframe 内部通过 Punctual 订阅拿到 mobileToken
  -> execute() resolve，返回 token
  -> 页面调用 VerifyUser(token, WAA snapshot)
  -> VerifyUser 返回 1/2/其他
```

---

## 4. 扫码状态判定链路

### 4.1 两个 RPC

`bundle/mod-VI9Wbd.js:1222`：

```js
_.v$a=class extends _.h{constructor(a){super(a)}};
var w$a=class extends _.h{constructor(a){super(a)}tb(){return _.xl(this,1)}};
_.x$a=new _.gv(
  "/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GetUserVerificationStatus",
  _.v$a,w$a,a=>a.serialize(),_.Pc(w$a));

_.Vhb=class extends _.h{constructor(a){super(a)}};
var Whb=class extends _.h{constructor(a){super(a)}};
_.Xhb=new _.gv(
  "/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/VerifyUser",
  _.Vhb,Whb,a=>a.serialize(),_.Pc(Whb));
```

### 4.2 页面如何判定

`bundle/mod-VI9Wbd.js:3385`：

```js
a.state.set("checking");
var c=yield a.subject().Jia();          // GetUserVerificationStatus
if(c===void 0)a.state.set("error");
else if(c==="verified"||c==="notRequired")ijc(a,c);
else{
  a.state.set("challenging");
  a.A.reset(b);
  var d=yield a.A.execute(b,a.subject().Wxc);  // reCAPTCHA execute("verify_user")
  yield a.verify(d)                            // VerifyUser(token)
}
```

`bundle/mod-VI9Wbd.js:3388-3389`：

```js
verify(a){
  b.state.set("verifying");
  var c=yield b.subject().verify(a);   // VerifyUser
  switch(c){
    case 1: ... b.state.set("passed"); b.verified.emit(); break;
    case 2: ... b.state.set("failed"); break;
    default: ... b.state.set("error")
  }
}
```

`bundle/mod-VI9Wbd.js:3400-3401`：

```js
Jia(){
  switch(yield $uj(a.A)){              // GetUserVerificationStatus
    case 1:return"required";
    case 2:return"verified";
    case 3:return"notRequired"
  }
}
```

### 4.3 状态枚举

| 接口 | 字段 | 值 | 语义 |
|---|---|---|---|
| `GetUserVerificationStatus` | response field 1 | 1 | `required` |
| `GetUserVerificationStatus` | response field 1 | 2 | `verified` |
| `GetUserVerificationStatus` | response field 1 | 3 | `notRequired` |
| `VerifyUser` | response field 1 | 1 | `passed` |
| `VerifyUser` | response field 1 | 2 | `failed` |
| `VerifyUser` | response field 1 | 其他 | `inconclusive` / `error` |

### 4.4 页面有没有轮询？

- `GetUserVerificationStatus` 只在 `load()` 和 `retry()` 时调用（`mod-VI9Wbd.js:3388`）。
- verify 组件附近（`mod-VI9Wbd.js:3381-3406`）没有 `setInterval`/`setTimeout` 轮询。
- 挑战期间，页面等待 `grecaptcha.enterprise.execute()` 的 Promise resolve；不是轮询 `GetUserVerificationStatus`。
- bframe 在手机确认后设置 `mobileToken` 并标记 `"r"`（`recaptcha__en.js:1639`）；该过程内部使用 Punctual 订阅端点（`recaptcha__en.js:1205`）。
- `D[19]` 的完整实现未展开，因此“Punctual 订阅能否服务端复刻”属于未证实。

### 4.5 判据结论

- **扫码通过（挑战层）**：`grecaptcha.enterprise.execute()` resolve，得到 token；bframe 内部 `mobileToken` 设置完成。
- **账号验证通过（账号层）**：`VerifyUser` 返回 1。
- **账号当前状态（粗查）**：`GetUserVerificationStatus` 返回 2。
- **失败**：`VerifyUser` 返回 2，或 `execute()` 抛错/取消，或页面 `state="error"`。
- **过期**：二维码/挑战过期由 reCAPTCHA 内部处理；页面会回到 `ready` 或 `error`，没有独立的“过期码”RPC。

> 注意：仅轮询 `GetUserVerificationStatus` 是否能在手机扫码后立即变成 2，属于未证实。静态代码显示账号验证的完成动作是 `VerifyUser`，因此更稳妥的判断是“扫码后必须让浏览器执行 `VerifyUser`，或者由服务端在扫码后调用 `VerifyUser`（需要 token + WAA）”。

---

## 5. 可行性结论与风险（三问三答）

### 问 1：凭据（Cookie/SAPISIDHASH）齐备时，能否服务端直接取二维码？

**结论：不能纯 HTTP 直接取码；必须浏览器内 JS/Canvas 参与。**

证据：

- 匿名 `/verify-account` 302→登录页（E1）。
- 有效会话下页面应可加载（本轮账号会话失效，未实测；路由/鉴权静态代码显示必须登录），但二维码仍由 reCAPTCHA bframe 在浏览器内生成（E2，`recaptcha__en.js:75-77`、`:1635-1638`）。
- bframe 初始 HTML 不含短码（E1，`bframe-1.html:259`，关键词计数为 0）。
- 短码来自挑战数据截取（E2/E3，`S[32](2,12,B)`）。
- 二维码是 Canvas 渲染，不是图片响应（E2，`CT.OT.Z(this.url).Tr(this.U)`）。
- `VerifyUser` 还需要 WAA snapshot（E2，`shell-1.js:1678`、`mod-VI9Wbd.js:3398`）。

可服务端复用的部分：

- 用已存 Cookies + `SAPISIDHASH` 调 `GetUserVerificationStatus`（只读状态查询）。
- 如果拿到短码，可以用任意 QR 库在服务端重新生成二维码图片；但**拿到短码本身**仍需要浏览器上下文。

不可直接复用的部分：

- bframe DOM、canvas title、button href。
- `postMessage` 跨域传递。
- reCAPTCHA Enterprise 的设备/风险判定。
- WAA/BotGuard 运行时。

### 问 2：扫码通过与否能否由接口轮询判定？判据是什么？

**结论：有可轮询的接口，但它不是挑战内的实时判据；权威判据是 `VerifyUser` 返回 1。**

证据：

- `GetUserVerificationStatus` 存在，返回 1/2/3（E2，`mod-VI9Wbd.js:1222`、`:3400-3401`）。
- 页面在挑战期间不轮询它（E2，`mod-VI9Wbd.js:3381-3406` 无定时器）。
- 页面在 `execute()` resolve 后调用 `VerifyUser`，用返回值 1/2 判定 passed/failed（E2，`mod-VI9Wbd.js:3388-3389`）。
- `VerifyUser` 的第二个字段是 WAA snapshot（E2，`shell-1.js:1678`、`mod-VI9Wbd.js:3398`）。

因此：

- **可以**轮询 `GetUserVerificationStatus` 做“账号是否已经 verified”的粗查/后查。
- **不能**仅靠它判断“当前二维码挑战是否通过”；挑战内的通过信号是 `execute()` resolve + `mobileToken`。
- 如果服务端只轮询 `GetUserVerificationStatus`，在用户扫码但浏览器还没执行 `VerifyUser` 时，很可能仍是 `required`（E3，未动态验证）。
- 若要做服务端判定，必须能拿到 reCAPTCHA token 并生成 WAA snapshot，或者让受控浏览器完成 `VerifyUser`。

### 问 3：做成后台能力需要哪些前置？风险在哪？

**前置：**

1. 有效的 Google 登录会话（Cookie + SAPISIDHASH；`meta.expired=false` 不代表会话有效）。
2. 页面域必须是 `https://aistudio.google.com`；Origin/Referer 匹配。
3. `X-Goog-Api-Key`、`X-Goog-AuthUser`、`Content-Type: application/json+protobuf`、`X-User-Agent: grpc-web-javascript/0.1`（E2，`shell-1.js:1288`）。
4. reCAPTCHA Enterprise sitekey（公开）与 `action:"verify_user"`。
5. 方案 B 还需要受控浏览器、BotGuard/WAA 初始化、bframe 跨域读取、Punctual/mobileToken 处理。
6. 若服务端要调 `VerifyUser`，需要 WAA snapshot（`waa-pa.clients6.google.com`）——目前只能在浏览器运行时生成。

**风险：**

| 风险 | 说明 | 证据/依据 |
|---|---|---|
| 触发风控 | 打开 verify-account 本身可能被 Google 视为验证尝试；重复尝试可能加重风控 | 用户背景 + `account_keepalive.py:203-227` 明确不探测 verify-account 锁 |
| 消耗验证次数 | Google 可能对移动设备验证有频率/次数限制；具体额度未在前端暴露 | U |
| BotGuard/WAA 冷启动 | 每次新浏览器上下文都会触发 BotGuard；仓库 §4.6 严禁反复重启真实服务/浏览器 | `AGENT.md §4.6` |
| 账号连坐 | verify-account 是单账号锁，不能像真实 session_expired 一样连坐兄弟号 | `account_orchestrator.py:72-82` |
| 短码泄露 | 短码是一次性挑战凭证；写日志/发给第三方 hub 会扩大暴露面 | 用户脚本第 17 行第三方 hub |
| 第三方依赖 | `qr.cuberelay.workers.dev` 不是本仓可控服务 | `references/verify-account-qr.user.js:17` |
| 状态误判 | 仅轮询 `GetUserVerificationStatus` 可能滞后 | E3/U |
| Pro 号风险 | 用户明确禁止使用两个 Pro 主力号 | 任务红线 |

**建议：**

- 先做“外部验证页 + 服务端状态查询”的低风险方案。
- 不在常规请求路径中自动执行 verify-account 挑战。
- 不把短码、Cookie、Authorization、WAA snapshot 写入日志。
- 后台自动取码必须单独拍板，并限定为低频、显式、可取消的运维动作。

---

## 6. 前端现状盘点与 verify_required 接入方案

完整方案、状态机、接入点清单见 `FRONTEND.md`。这里给出结论与关键 `file:line`。

### 6.1 现状盘点

| 主题 | 现状 | `file:line` |
|---|---|---|
| 前端 Account 类型 | 有 `expired` / `session_expired` / `is_available`，无 `verify_required` | `web/src/types/accounts.ts:3-24` |
| 状态 key | `disabled / expired / cooldown / instance / active / quota / ready` | `web/src/utils/accountStatus.ts:4-11` |
| 状态优先级 | `expired` 与 `session_expired` 合并为 `'expired'` | `web/src/utils/accountStatus.ts:33-35` |
| 状态筛选 | 只有 all/ready/limited/cooldown/expired | `web/src/components/accounts/AccountTable.vue:46`、`:48-59` |
| 筛选 pills | 全部/就绪/配额耗尽/鉴权冷却/登录态失效 | `web/src/components/accounts/AccountTable.vue:419-461` |
| 行徽章 | 已停用/登录态失效/鉴权冷却/实例/当前激活/配额耗尽/就绪 | `web/src/components/accounts/AccountRow.vue:311-360` |
| 恢复按钮 | 仅 `isExpired` 显示；调用 `recoverAccount` | `AccountRow.vue:90-92`、`:365-375`；`AccountTable.vue:312-315` |
| 恢复动作 | refresh + clearCooldown，不查 verify | `web/src/stores/accounts.ts:436-468` |
| 后端响应 | `AccountResponse` 有 `session_expired`，无 `verify_required` | `src/aistudio_api/api/routes_accounts.py:142-164` |
| 后端状态 | `_account_status` 只看 `session_expired` | `routes_accounts.py:102-139` |
| 持久化 | `AccountMeta` 无 `verify_required` | `src/aistudio_api/infrastructure/account/account_store.py:322-345` |
| 运行时统计 | `AccountStats.session_expired` | `src/aistudio_api/application/account_rotator.py:153-171` |
| 错误识别 | 已识别 verify-account 标记 | `src/aistudio_api/domain/errors.py:12-39` |
| 路由事件 | 已单独记录 `verification_required` | `src/aistudio_api/application/routing_pipeline.py:939-958` |
| 事件落库 | 被折叠成 `record_session_expired` | `src/aistudio_api/application/account_orchestrator.py:72-82` |
| 保活 | 明确不探测 verify-account 锁 | `src/aistudio_api/application/account_keepalive.py:203-227` |
| UI Debug 沙盒 | `_AccountSeed` 只有 `session_expired` | `src/aistudio_api/api/ui_sandbox.py:272-283`、`:1946-1953` |

### 6.2 最小改动面

#### 后端

1. `AccountMeta` 增加 `verify_required: bool = False`，持久化到 `meta.json`；`from_dict` 默认 false（`account_store.py:322-345`）。
2. `AccountStats` 增加 `verify_required: bool = False`、`last_verify_required: float = 0.0`；新增 `record_verify_required()` / `clear_verify_required()`（`account_rotator.py:153-171`）。
3. `account_orchestrator.py:72-82` 的 `verification_required` 分支改为写 verify 字段，不再写 `session_expired`。
4. `AccountResponse` 增加 `verify_required: bool = False`（`routes_accounts.py:142-164`）。
5. `_account_status` 增加 `verify_required`，并在 verify 时令 `is_available=false`（`routes_accounts.py:102-139`）。
6. `to_account_response` 透传 `verify_required`（`routes_accounts.py:221-261`）。
7. 新增验证 API：
   - `POST /api/accounts/{id}/verify/session`
   - `GET /api/accounts/{id}/verify/status`
   - `POST /api/accounts/{id}/verify/cancel`
   - 可选 `POST /api/accounts/{id}/verify/complete`
8. `ui_sandbox.py` 增加 `verify_required` 字段/种子。

#### 前端

1. `web/src/types/accounts.ts:3-24` 增加 `verify_required?: boolean`。
2. `web/src/utils/accountStatus.ts:4-11` 增加 `'verify'`；`:25-50` 增加分支（建议顺序：disabled → expired → verify → cooldown → ...）。
3. `web/src/components/accounts/AccountTable.vue:46` 增加 `'verify'`；`:48-59` 增加映射；`:419-461` 增加“需验证”pill。
4. `web/src/components/accounts/AccountRow.vue:311-360` 增加“需扫码验证”徽章；`:363-441` 增加“验证”按钮。
5. 新建 `web/src/components/modals/VerifyAccountModal.vue`。
6. `web/src/views/AccountsView.vue:439-467` 挂载弹窗。
7. `web/src/stores/accounts.ts` 增加 `startVerify` / `fetchVerifyStatus` / `stopVerify`。
8. `web/src/api/accounts.ts` 增加验证 API 封装。

### 6.3 状态机（建议）

```text
normal
  --上游 verify-account 标记--> verify_required
verify_required
  --点击「验证」--> verifying
verifying
  --status=verified / VerifyUser=1--> passed -> 清 verify_required -> normal
  --status=required 且超时/二维码过期--> verify_failed -> 可重试 -> verifying
  --网络/401/上游异常--> verify_error -> 可重试 -> verifying
  --401 SESSION_COOKIE_INVALID--> expired（真实登录失效）
verify_required
  --用户停用--> disabled
```

### 6.4 「验证」按钮交互流

```text
点击「验证」
  -> 打开 VerifyAccountModal（loading）
  -> POST /api/accounts/{id}/verify/session
     方案 A: 返回 external + verify_url
     方案 B: 返回 qr_image + shortcode + expires_at
  -> ready：展示二维码/外部按钮 + 倒计时
  -> 每 2.5s GET /api/accounts/{id}/verify/status
  -> verified：成功态、清锁、刷新列表、停止轮询
  -> required：继续到超时
  -> expired：显示“二维码已过期”+“刷新二维码”
  -> error：显示错误 +“重试”
  -> 取消/关闭：abort 轮询 + cancel 接口
```

loading / 成功 / 失败 / 取消：

- loading：skeleton QR、按钮禁用、可取消。
- 成功：绿色 check、“账号已验证，已恢复调度”、自动关闭或“完成”。
- 失败：红色/琥珀提示、重试/关闭。
- 取消：关闭弹窗、`toast.info("已取消验证")`、账号状态不变。

### 6.5 必须用户拍板

1. 是否允许后端自动拉起真实浏览器取二维码（高风险，默认不做）。
2. 验证弹窗用外部跳转还是内嵌二维码。
3. 是否自动轮询状态，轮询间隔与超时。
4. `verify_required` 是否持久化到 `meta.json`。
5. verify 账号保持 `enabled=true` 但不可调度，还是自动停用。
6. 是否允许“重新导入 Cookies 即清 verify 锁”。
7. 是否展示/复制短码；如何避免日志泄露。
8. 是否允许批量验证/自动修复。
9. 是否对 Pro 账号显示验证入口。
10. 是否接入第三方中转页（不建议）。

---

## 7. ASCII 线框图

完整版见 `FRONTEND.md` 第 6 节。这里保留两张主图。

### 7.1 账号管理页全景

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 账号管理                                             [重置锁定] [油猴抓取脚本] [导入 Cookies] │
├──────────────────────────────────────────────────────────────────────────────┤
│ StatsStrip:  总账号 47 | 可用 39 | 失效 2 | 需验证 5 [NEW] | 429 锁定 1        │
├──────────────────────────────────────────────────────────────────────────────┤
│ KeepaliveBar: 上次保活 12:30 | 下次 13:30 | [立即保活]                        │
├──────────────────────────────────────────────────────────────────────────────┤
│ 账号预设  [白班池] [低延迟] [全 Free]                         [保存当前方案]   │
├──────────────────────────────────────────────────────────────────────────────┤
│ 实例池  [槽位 #1 Free active] [槽位 #2 Free sleeping] ...                     │
├──────────────────────────────────────────────────────────────────────────────┤
│ 分组:  (全部 47)  (default 30)  (备用 17)                                     │
│                                                                              │
│ 搜索 [ 账号名/邮箱/u/0... ]   状态: [全部] [就绪] [配额耗尽] [鉴权冷却]       │
│                                      [登录态失效] [需验证 NEW]                │
├──────────────────────────────────────────────────────────────────────────────┤
│ ▼ cookie_*** (3 个账号)                                    [展开全部] [收起] │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ [开关] u/0 [Free] [default]  a***@***  ID: acc_***                       │ │
│ │ 今日 12 · 累计 345  [成功 300] [429: 2]   [需扫码验证 NEW]               │ │
│ │                                    [验证 NEW] [换槽] [刷新] [删除]        │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ [开关] u/1 [Pro]  [default]  b***@***  ID: acc_***                       │ │
│ │ 今日 0 · 累计 88  [成功 88] [429: 0]    [登录态失效]                     │ │
│ │                                    [恢复] [换槽] [刷新] [删除]            │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘
```

对应真实结构：

- 顶部动作栏：`AccountsView.vue:241-279`
- StatsStrip：`AccountsView.vue:281-282`
- KeepaliveBar：`AccountsView.vue:284-285`
- 预设：`AccountsView.vue:287-335`
- 实例池：`AccountsView.vue:337-341`
- 分组 tabs：`AccountsView.vue:343-375`
- 状态筛选：`AccountTable.vue:405-462`
- 账号行：`AccountRow.vue:151-452`
- 新徽章：`AccountRow.vue:311-360` 分支
- 新按钮：`AccountRow.vue:363-441` 按钮组
- 新筛选：`AccountTable.vue:419-461`

### 7.2 「扫码验证」弹窗（方案 B：内嵌二维码）

```text
┌──────────────────────────────────────────────────────────────────────┐
│ 扫码验证账号                                                   [×]   │
│ 账号: a***@*** · u/0 · Free · ID: acc_***                            │
├──────────────────────────────────────────────────────────────────────┤
│ 状态: ● 等待扫码        倒计时 04:32        轮询中 ⟳                │
│                                                                      │
│                  ┌────────────────────────────┐                      │
│                  │                            │                      │
│                  │          [ QR CODE ]       │                      │
│                  │                            │                      │
│                  └────────────────────────────┘                      │
│                                                                      │
│  用手机相机扫描二维码，并按手机上的提示完成验证。                    │
│  二维码由 Google reCAPTCHA 提供；本弹窗只负责展示与状态回传。        │
│                                                                      │
│  ▸ 短码（可选，默认折叠）  ABCD-EFGH  [复制]                         │
│                                                                      │
│  ⓘ 验证成功后账号会自动恢复调度；二维码过期后请点“刷新二维码”。     │
├──────────────────────────────────────────────────────────────────────┤
│ [刷新二维码]              [我已扫码，检查状态]              [取消]   │
└──────────────────────────────────────────────────────────────────────┘
```

方案 A（外部验证页，推荐先做）：

```text
┌──────────────────────────────────────────────────────────────────────┐
│ 验证账号                                                       [×]   │
│ 账号: a***@*** · u/0 · Free · ID: acc_***                            │
├──────────────────────────────────────────────────────────────────────┤
│ 该账号被 Google 要求完成移动设备验证。                                │
│ 二维码只能在浏览器内的 AI Studio 验证页生成，本系统不会在后台         │
│ 自动拉起浏览器。请点击下方按钮，在新标签页完成扫码。                  │
│                                                                      │
│ 状态: ○ 等待验证        检查中 ⟳      最近检查: 12:30:05             │
│                                                                      │
│ ① 打开验证页  ② 手机扫码  ③ 回到本弹窗点“检查状态”                  │
├──────────────────────────────────────────────────────────────────────┤
│ [打开验证页]              [检查状态]                        [取消]   │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 8. 已实证 / 未证实 / 无法确认

### 8.1 已实证（E1/E2）

1. 匿名 `/verify-account` 302→Google 登录页，最终 200 登录页（E1）。
2. AI Studio 公开 `/welcome` 200，公开 shell 引用 2 个 gstatic 模块（E1）。
3. `verify-account` 路由懒加载 `VI9Wbd`，组件 `_.hs`（E2，`shell-1.js:2747`）。
4. verify 页面使用 `GetUserVerificationStatus`、`VerifyUser`、reCAPTCHA Enterprise `verify_user`（E2）。
5. `VerifyUser` 第二字段来自 WAA snapshot（E2，`shell-1.js:1678`）。
6. 二维码是 bframe 内 Canvas，内容为 `https://recaptcha.net/qr/<shortcode>`（E2，`recaptcha__en.js:75-77`、`:1635-1638`）。
7. bframe 初始 HTML 不含 QR/短码（E1，`bframe-1.html`）。
8. 匿名 RPC 401 `CREDENTIALS_MISSING`；失效 Cookie 401 `SESSION_COOKIE_INVALID`（E1）。
9. 当前前端把 `expired` 与 `session_expired` 合并，后端把 `verification_required` 折叠成 `session_expired`（E2）。
10. 现有“恢复”按钮只做 refresh + clearCooldown（E2）。

### 8.2 未证实（E3，有代码依据但未动态验证）

1. 有效已存 Cookies 下，`/verify-account` 是否总能加载并渲染验证页（本轮账号会话失效，未实测）。
2. 手机扫码后，`GetUserVerificationStatus` 是否会在浏览器 `VerifyUser` 之前变成 2。
3. `S[32](2,12,B)` 的短码是否完全由挑战数据派生，还是含随机成分。
4. `D[19]` / Punctual 订阅的完整协议与可复刻性。
5. 扫码后 `mobileToken` 到 `VerifyUser` 之间是否存在服务端自动完成路径。
6. `VerifyUser` 返回 1 后，`GetUserVerificationStatus` 是否有传播延迟。
7. 二维码过期时间、验证尝试次数上限、频率限制。

### 8.3 无法确认（U）

1. 服务端纯 HTTP 能否通过模拟 bframe/Punctual 稳定拿到短码和 mobileToken。
2. 后台自动取码是否一定会触发更严格的风控或消耗验证次数。
3. 不同地区/账号状态下，reCAPTCHA 是否一定给 QR 挑战（也可能直接通过或给其他挑战）。
4. Google 是否对 `GetUserVerificationStatus` 有隐藏频控。
5. 第三方 hub 的行为与安全性（本报告不建议使用）。

---

## 9. 产物与复现

### 9.1 落盘路径

```text
docs/dev/rebuild/domains/P1-account/references/verify-account-qr.user.js
docs/dev/rebuild/domains/P1-account/research/verify-account/REPORT.md
docs/dev/rebuild/domains/P1-account/research/verify-account/EVIDENCE.md
docs/dev/rebuild/domains/P1-account/research/verify-account/FRONTEND.md
docs/dev/rebuild/domains/P1-account/research/verify-account/page-welcome.html
docs/dev/rebuild/domains/P1-account/research/verify-account/page-app-welcome.html
docs/dev/rebuild/domains/P1-account/research/verify-account/manifest.json
docs/dev/rebuild/domains/P1-account/research/verify-account/bundle/shell-1.js
docs/dev/rebuild/domains/P1-account/research/verify-account/bundle/shell-2.js
docs/dev/rebuild/domains/P1-account/research/verify-account/bundle/mod-VI9Wbd.js
docs/dev/rebuild/domains/P1-account/research/verify-account/bundle/recaptcha/enterprise.js
docs/dev/rebuild/domains/P1-account/research/verify-account/bundle/recaptcha/recaptcha__en.js
docs/dev/rebuild/domains/P1-account/research/verify-account/bundle/recaptcha/bframe-1.html
docs/dev/rebuild/domains/P1-account/research/verify-account/bundle/recaptcha/bframe-2.html
```

### 9.2 复现命令

见 `EVIDENCE.md` 第 10 节。所有命令走本地代理 `http://127.0.0.1:10809`；未带账号凭据的请求为匿名请求。

### 9.3 脱敏声明

- 报告、证据文件、文件名均未包含 Cookie 值、Authorization、SID、完整邮箱。
- 带凭据探测的账号 ID、邮箱、Cookie 均未写入研究目录。
- 公开 sitekey 是前端公开常量，已按原值记录；公开 API key 一律脱敏为 `AIzaSy...`。

---

## 10. 最终建议

1. **先做状态拆分，不自动取码。** 把 `verification_required` 从 `session_expired` 拆成 `verify_required`，前端加徽章/筛选/按钮/弹窗；这是低风险、可验收的第一步。
2. **二维码展示优先方案 A。** 弹出“外部验证页 + 状态查询”，不启动后端浏览器；用户体验略差，但风险最低。
3. **方案 B 作为受控实验。** 只有在用户明确要求“后台自动取码”时，才在独立脚本/独立浏览器上下文中实验，且必须遵守 §4.6、不得反复重启、不得使用 Pro 主力号。
4. **不要用 `GetUserVerificationStatus` 冒充挑战实时状态。** 它的语义是账号验证状态；权威判据是 `VerifyUser`。
5. **不要把短码/QR 内容写入日志。** 短码是一次性挑战凭证。
6. **不要接入第三方 hub。** `qr.cuberelay.workers.dev` 不是本仓可控服务，且会把短码发给第三方。

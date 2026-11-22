# verify-account 证据链（源码片段与探测记录）

本文件只记录可复核证据，不重复完整方案。主报告见 `REPORT.md`，前端方案见 `FRONTEND.md`。

## 0. 证据等级

| 等级 | 含义 |
|---|---|
| E1 | 本轮直接网络探测/下载得到的事实（状态码、重定向、响应体） |
| E2 | 本轮下载的公开 bundle 中可直接读到的源码事实 |
| E3 | 基于 E2 的相邻代码推断，未做动态验证 |
| U | 当前无法确认，明确保留 |

## 1. 页面探测记录（E1）

### 1.1 匿名访问 verify-account

请求：

```text
GET https://aistudio.google.com/verify-account
UA: Chrome/142 桌面
```

结果：

```text
302 https://aistudio.google.com/verify-account
Location: https://accounts.google.com/ServiceLogin?passive=1209600&continue=https://aistudio.google.com/verify-account&followup=https://aistudio.google.com/verify-account&ltmpl=aistudio
302 https://accounts.google.com/ServiceLogin?...
Location: https://accounts.google.com/v3/signin/identifier?continue=https://aistudio.google.com/verify-account&followup=https://aistudio.google.com/verify-account&ltmpl=aistudio&flowName=GlifWebSignIn...
200 https://accounts.google.com/v3/signin/identifier?...
Content-Type: text/html; charset=utf-8
```

结论：未登录时 verify-account 不是独立公开页面，直接被弹回 Google 登录页。最终 HTML 是登录页，不是 verify-account 页面。

### 1.2 公开 AI Studio shell

```text
GET https://aistudio.google.com/welcome  -> 200, 12962 bytes (transfer), text/html
GET https://aistudio.google.com/app/welcome -> 200, 12975 bytes (transfer)
```

公开 shell 的 script 引用：

```text
https://www.gstatic.com/_/mss/boq-makersuite/_/js/k=boq-makersuite.MakerSuite.en_US.rWUj1ypxxEE.2018.O/am=QaSaAAYeHAI/d=1/excm=_b/ed=1/dg=0/wt=2/ujg=1/rs=AMOXD28mipyLK1D24NqS1G8HN1nPDr3-fg/dti=1/m=_b
https://www.gstatic.com/_/mss/boq-makersuite/_/js/k=boq-makersuite.MakerSuite.en_US.rWUj1ypxxEE.2018.O/ck=boq-makersuite.MakerSuite.nBRdAJqKxy0.L.B1.O/am=QaSaAAYeHAI/d=1/excm=_b/excm=_b/ed=1/wt=2/ujg=1/rs=AMOXD28W7REKSLd4dY16EoxPz-fvtzAhKQ/dti=1/m=KJeCXd
```

### 1.3 带一个低价值 Free 账号的探测（仅 1 次，账号 ID 脱敏）

本地 `meta.json` 显示该账号 `tier=Free, expired=false`，但使用其已存 Cookies 请求：

```text
GET https://aistudio.google.com/verify-account
=> 302 -> ... -> 200 https://accounts.google.com/v3/signin/identifier?...
```

```text
POST https://aistudio.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GetUserVerificationStatus
Content-Type: application/json+protobuf
X-User-Agent: grpc-web-javascript/0.1
X-Goog-Api-Key: AIzaSy...（公开前端常量，已脱敏）
X-Goog-AuthUser: 0
Authorization: SAPISIDHASH <ts>_<sha1>
Cookie: <已脱敏>
Body: []

=> HTTP 401
[16,"Request had invalid authentication credentials. Expected OAuth 2 access token, login cookie or other valid authentication credential...",
 [["type.googleapis.com/google.rpc.ErrorInfo",["SESSION_COOKIE_INVALID","googleapis.com",
   [["method","...GetUserVerificationStatus"],["service","alkalimakersuite-pa.googleapis.com"],["cookie","UNKNOWN"]]]]]]
```

结论：`meta.expired=false` 不等于会话仍有效；该账号 Cookie 已被上游判定为 `SESSION_COOKIE_INVALID`。因此本轮没有用有效账号实测 verify-account 页面渲染，只做了静态源码研究。

### 1.4 匿名 RPC 探测

```text
POST .../GetUserVerificationStatus
X-Goog-Api-Key: AIzaSy...（公开前端常量，已脱敏）
无 Cookie / 无 Authorization
Body: []

=> HTTP 401
[16,"API keys are not supported by this API. Expected OAuth2 access token or other authentication credentials that assert a principal...",
 [["type.googleapis.com/google.rpc.ErrorInfo",["CREDENTIALS_MISSING","googleapis.com",...
```

结论：该 RPC 不接受裸 API key，必须有 Google 登录主体（Cookie/OAuth）。

## 2. AI Studio 前端路由与模块（E2）

文件：`bundle/shell-1.js`

```js
// shell-1.js:2747
{path:"verify-account",title:_.hn("Verify account"),
 ud:()=>(0,_.Qp)("VI9Wbd").then(()=>_.hs),qA:[()=>_.xj(Bxb)]}
```

说明：`/verify-account` 路由懒加载模块 `VI9Wbd`，组件命名空间为 `_.hs`，守卫为 `Bxb`。

```js
// shell-1.js:1926（错误面板里的引导链接）
... actions:[{link:(0,_.wd)`/${"verify-account"}`,text:"Verify your account"}] ...
```

下载的懒加载模块：

```text
https://www.gstatic.com/_/mss/boq-makersuite/_/js/k=boq-makersuite.MakerSuite.en_US.rWUj1ypxxEE.2018.O/am=QaSaAAYeHAI/d=1/excm=_b/ed=1/dg=0/wt=2/ujg=1/rs=AMOXD28mipyLK1D24NqS1G8HN1nPDr3-fg/dti=1/m=VI9Wbd
=> 200, 647479 bytes transfer (compressed), 2176129 bytes on disk
```

## 3. verify-account 组件状态机（E2）

文件：`bundle/mod-VI9Wbd.js`

### 3.1 页面组件与 subject

```js
// mod-VI9Wbd.js:3400
_.hs=class{constructor(){
  ...
  this.Crb="/docs/verification#troubleshoot";
  ...
  this.subject={
    Wxc:"verify_user",
    Jia:()=>this.Jia(),
    verify:a=>avj(this.A,a)
  }
}
...
Jia(){var a=this;return _.x(function*(){
  switch(yield $uj(a.A)){
    case 1:return"required";
    case 2:return"verified";
// mod-VI9Wbd.js:3401
    case 3:return"notRequired"
  }
})}
```

状态枚举（`GetUserVerificationStatus` 响应字段 1）：

| 值 | 页面语义 |
|---|---|
| 1 | `required` |
| 2 | `verified` |
| 3 | `notRequired` |

### 3.2 挑战组件状态机

```js
// mod-VI9Wbd.js:3385
jjc=function(a,b){_.x(function*(){
  a.state.set("checking");
  var c=yield a.subject().Jia();
  if(c===void 0)a.state.set("error");
  else if(c==="verified"||c==="notRequired")ijc(a,c);
  else{
    iK(a,"Verification Challenge Started");
    a.state.set("challenging");
    a.A.reset(b);
    try{
      var d=yield a.A.execute(b,a.subject().Wxc)
    }catch(e){
      if(e instanceof djc){
        iK(a,"Verification Challenge Cancelled");
        _.PA(a.veLoggingService,370708);
        a.state.set("ready");
        return
      }
      ...
      a.state.set("error");
      return
    }
    yield a.verify(d)
  }
})};
```

```js
// mod-VI9Wbd.js:3386
kjc=function(a){return _.x(function*(){
  a.state.set("loading");
  try{
    a.widgetId=yield a.A.render(a.Xxc().nativeElement,a.H),
    a.state.set("ready")
  }catch(b){
    ...
    a.state.set("error")
  }
})};
ijc=function(a,b){
  b==="verified"?(iK(a,"Verification Challenge Already Verified"),a.state.set("passed"),a.verified.emit()):
  (iK(a,"Verification Challenge Not Required"),a.state.set("notRequired"),a.notRequired.emit())
};
```

```js
// mod-VI9Wbd.js:3388
load(){... var b=yield a.subject().Jia();
  b==="verified"||b==="notRequired"?ijc(a,b):yield kjc(a)}
verify(a){... b.state.set("verifying");
  try{var c=yield b.subject().verify(a)}catch(d){b.state.set("error");return}
  switch(c){
    case 1:iK(b,"Verification Challenge Passed"); ... b.state.set("passed"); b.verified.emit(); break;
    case 2:iK(b,"Verification Challenge Failed"); ... b.state.set("failed"); break;
    default:iK(b,"Verification Challenge Inconclusive"), ... b.state.set("error")
  }
}
```

结论：组件状态至少包含：

```text
loading -> ready -> checking -> challenging -> verifying -> passed
                                                     |-> failed
                                                     |-> error
       -> passed / notRequired（预检查已满足）
```

### 3.3 页面文案

```js
// mod-VI9Wbd.js:3402-3403
" Verify your account "
" Signed in as <account>  Switch account "
" Go to AI Studio "
" Your account was verified. You can now continue using AI Studio with this account. "
" No verification needed. ... "
" We couldn't verify your account with this device. This usually happens if your device has already been used for verification recently, or doesn't meet device security requirements. Try a different mobile device. "
" Something went wrong while verifying your device. Check your connection or browser extensions (such as ad blockers) and try again. "
" Unusual activity was detected on your account. Account verification is required to restore access to AI Studio. Verify using a mobile device (you may be asked to scan a QR code with your phone). "
```

## 4. 两个 RPC（E2）

### 4.1 RPC 定义

```js
// mod-VI9Wbd.js:1222
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

### 4.2 调用与请求构造

```js
// mod-VI9Wbd.js:3398
$uj=function(a){return _.x(function*(){
  try{
    var b=a.Ja,c=new _.v$a;
    return(yield _.xz(b.A,
      b.F+"/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GetUserVerificationStatus",
      c,{},_.x$a)).tb()
  }catch(d){}
})};

avj=function(a,b){return _.x(function*(){
  var c=new _.Vhb;
  var d=(new _.Lic).setToken(b);
  c=_.Cn(c,1,Yuj,d);
  d=yield Zuj(c);
  d=yield _.Vo(a.sd,d);
  _.Kc(c,2,d);
  d=a.Ja;
  c=yield _.xz(d.A,
    d.F+"/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/VerifyUser",
    c,{},_.Xhb);
  return _.xl(c,1)
})};
```

```js
// mod-VI9Wbd.js:3399
bvj=class{constructor(){this.Ja=_.m(_.zz);this.sd=_.m(_.TB)}};
```

请求形状（E3，按字段号）：

```text
VerifyUserRequest:
  field 1: License/Token message { field 1: token }
  field 2: WAA snapshot string（由 _.Vo(_.TB, ...) 生成）

VerifyUserResponse:
  field 1: enum（1=passed, 2=failed, 其他=inconclusive）
```

### 4.3 鉴权头与 SAPISIDHASH（E2）

```js
// shell-1.js:1288
_.xz=function(a,b,c,d,e,f={}){return Ejb(a,b,c,d,e,f)};
Bjb=function(a,b,c,d){
  b["Content-Type"]="application/json+protobuf";
  b["X-User-Agent"]="grpc-web-javascript/0.1";
  var e=b.Authorization;
  if(e&&Yhb.has(e.split(" ")[0])||a.withCredentials)c.I=!0;
  ...
};
```

```js
// shell-1.js:1625
var Pqb=function(a,b,c){
  var d=String(_.ia==null?void 0:_.ia.location.href);
  return d&&a&&b?[b,Oqb(Nqb(d),a,c||null)].join(" "):null
};
Oqb=function(a,b,c){
  ...
  c=Math.floor((new Date).getTime()/1E3);
  e=f.length==0?[c,b,a]:[f.join(":"),c,b,a];
  ...
  a=Qqb(e.join(" "));
  a=[c,a];
  ...
  return a.join("_")
};
```

```js
// shell-1.js:1630-1632
Zqb=function(a,b){
  var c=a?_.ia.__SAPISID:_.ia.__APISID;
  c||typeof document==="undefined"||(c=new zAa,c=c.get(a?"SAPISID":"APISID")||c.get("__Secure-3PAPISID"));
  return c?Pqb(c,a?"SAPISIDHASH":"APISIDHASH",b):null
};
_.Sp=function(a){...}
```

结论：`Authorization: SAPISIDHASH <unix_ts>_<sha1(ts + " " + SAPISID + " " + origin)>`，origin 取当前页面；同一套逻辑可在服务端用已存 Cookies 复现。

## 5. WAA（VerifyUser 的第二字段）（E2）

```js
// shell-1.js:1678
var ltb=new _.gv("/google.internal.waa.v1.Waa/Create",Vsb,ktb,a=>a.serialize(),_.Pc(ktb));
var mtb=new _.gv("/google.internal.waa.v1.Waa/Ping",Zsb,Ov,a=>a.serialize(),Pv);
var ntb=function(){
  this.A=new _.yz({format:"jspb"});
  this.F="https://waa-pa.clients6.google.com".replace(/\/+$/,"")
};
ntb.prototype.create=function(a,b,c){return _.xz(this.A,this.F+"/$rpc/google.internal.waa.v1.Waa/Create",a,b||{},ltb,c)};
ntb.prototype.ping=function(a,b,c){return _.xz(this.A,this.F+"/$rpc/google.internal.waa.v1.Waa/Ping",a,b||{},mtb,c)};
_.Vo=function(a,b){return _.x(function*(){
  yield a.H;
  return a.A?(yield gtb(a.A),a.A.snapshot({Dmd:{content:b}})):""
})};
```

```js
// shell-1.js:1682
_.TB=class{constructor(){this.pb=_.m(_.ir);_.m(CB);...}
  initialize(){var a=this;return _.x(function*(){
    var b=new ntb;
    a.A=new itb({fetcher:new eBa(b,()=>a.getMetadata())});
    yield a.A.mSb
  })}
  getMetadata(){return {
    "X-Goog-Api-Key":"AIzaSy...（公开前端常量，已脱敏）",
    Authorization: (a=_.Sp([]))!=null?a:"",
    "X-Goog-AuthUser":this.pb.kk.toString()
  }}
}
```

结论：`VerifyUser` 的第二个字段是 WAA（Web Access Attestation）快照，依赖浏览器内 WAA/BotGuard 运行时；不是简单的 Cookie 计算。

## 6. reCAPTCHA Enterprise 与二维码（E2）

### 6.1 AI Studio 侧调用

```js
// mod-VI9Wbd.js:3383
ejc=function(a){return _.x(function*(){
  yield _.Fp((0,_.wd)`https://www.google.com/recaptcha/enterprise.js?render=explicit&trustedtypes=true`);
  var b=a.window.grecaptcha;
  if(b==null||!b.enterprise)throw Error("Ff");
  yield new Promise(c=>{b.enterprise.ready(c)});
  return b
})};
gjc=class{
  render(a,b){... return ...enterprise.render(a,{sitekey:b,size:"invisible"})}
  execute(a,b){... var e=yield d.enterprise.execute(a,{action:b}); if(!e)throw Error("Ef"); return e}
  reset(a){...enterprise.reset(a)}
};
```

```js
// mod-VI9Wbd.js:3387
this.H=_.m(_.ir).agd;
```

sitekey 来自公开页面配置 `z6uGHf`：

```text
page-welcome.html 内联配置:
"z6uGHf":"6LfMhuItAAAAAPMRs5Icz9eJnvIjRlxrxvJQ6ABh"
```

结论：AI Studio 使用 reCAPTCHA Enterprise，`size:"invisible"`，`action:"verify_user"`，sitekey 为公开常量。

### 6.2 二维码 DOM 与 Canvas

```js
// recaptcha__en.js:75-77
"<div id='rc-qr'>
  <div class=\"recaptchaJavascriptChallengeQrOuterContainer\">
   <div class=\"recaptchaJavascriptChallengeQrContainer\">
    <div class=\"recaptchaJavascriptChallengeQrBackground\"></div>
    <div class=\"recaptchaJavascriptChallengeQrInstructionsContainer\">
     <div class=\"recaptchaJavascriptChallengeQrShortInstructionsContainer\">
      <h1 class=\"recaptchaJavascriptChallengeQrShortInstructions\">"
...
"Scan this QR code with your mobile device to verify you are human. ..."
...
<canvas class=\"recaptchaJavascriptChallengeQrCanvas\" tabindex=\"-1\" role=\"img\" aria-label=\"QR code\"></canvas>
<p class=\"recaptchaJavascriptChallengeQrFallbackText\"></p>
<a class=\"recaptchaJavascriptChallengeQrButton\" id='rc-mobile-verification-button' target=\"_blank\" role=\"button\">Click to verify</a>
```

```js
// recaptcha__en.js:105
"Scan the QR code with your phone's camera. This will initialize the verification process on the phone. After verification, you will pass the challenge."
```

### 6.3 短码 URL 与 Canvas 渲染

```js
// recaptcha__en.js:1635
eE.prototype.tC=function(K,B,m,H,l,L,F,n,J){
  if(!this.W5(... ,B))return ...;
  this.url=(n=(F=(this.o=Y[5](7,1,S[32](2,12,B)),
    l=W[20](88,3,S[32](3,12,B))),...),L[1]+this.o),
  ...
  CT.OT.Z(this.url).Tr(this.U),
  "To continue on your mobile device, enter the link "+this.url+" in your mobile browser.",
  this.U).title=m,
  this.J[K]&&(this.J[K].textContent=m),
  this.jY()?(x[26](10,"Android")?(
    this.url="intent://recaptcha.net/qr/"+this.o+"#Intent;scheme=https;action=android.intent.action.VIEW;end;",
    ...
  ):...
```

其中：

```js
// recaptcha__en.js:1635
L=["kaios","https://recaptcha.net/qr/",12]
```

```js
// recaptcha__en.js:1638（iOS App Clip）
this.url="https://appclip.apple.com/id?p=com.google.Recaptcha.VerifyClip&id="+this.o
```

结论：二维码内容不是服务端返回的图片，而是短码 URL：

```text
https://recaptcha.net/qr/<12 字符短码>
```

`<12 字符短码>` 由 bframe 从挑战数据中截取（`S[32](2,12,B)`），具体截取函数未展开（E3）。Canvas 由内部 QR 库渲染（`CT.OT.Z(url).Tr(canvas)`）。

### 6.4 bframe 初始 HTML 不含二维码

```text
bundle/recaptcha/bframe-1.html:259
recaptcha.frame.Main.init("[\x22finput\x22,null,[\x22conf\x22,null,\x226LfMhuItAAAAAPMRs5Icz9eJnvIjRlxrxvJQ6ABh\x22,0,null,null,0.75,null,[21,125,63,73,95,87,41,43,42,83,102,105,109,121],...]]");
```

对 `bframe-1.html` 关键词统计：

```text
recaptcha.net/qr/ => 0
QrCanvas => 0
mobileToken => 0
challenge => 0
payload => 0
```

结论：直接 GET bframe/anchor HTML 拿不到二维码或短码；短码在挑战下发后才在浏览器内生成。

### 6.5 扫码完成与 mobileToken

```js
// recaptcha__en.js:1639
...(...,24).then(function(k){
  H.mobileToken=k;
  t[31](57,H,"r")
}).catch(function(){
  return(t[31](8,H,"n"),M)[15](5)
});
eE.prototype.UR=function(){this.response.mobileToken=this.mobileToken};
```

```js
// recaptcha__en.js:1205
HP={
  H0:new Wd("https://signaler-pa.clients6.google.com/punctual"),
  fR:new Wd("https://signaler-pa.googleapis.com/punctual"),
  v0:!1
}
...
```

结论（E2+E3）：bframe 在手机扫码/确认后通过内部 Punctual 订阅通道拿到 `mobileToken`，随后把挑战标记为 `"r"`（ready/response）。`D[19]` 的完整实现未展开，因此“服务端是否可稳定复刻 Punctual 订阅”属于 U。

## 7. 用户脚本机制（E2，本地副本）

文件：`references/verify-account-qr.user.js`

```js
// lines 17-20
const HUB_ORIGIN = "https://qr.cuberelay.workers.dev";
const SHORTCODE_REGEX = /recaptcha\.net\/qr\/([A-Za-z0-9_-]{6,32})/;
const STRICT_CODE_REGEX = /^[A-Za-z0-9_-]{6,32}$/;
const MSG_TYPE = '__qr_hub_shortcode_v1';
```

```js
// lines 23-58：仅在 bframe/fallback iframe 内运行，只读 DOM
if (location.pathname.includes('/bframe') || location.pathname.includes('/fallback')) {
  function readShortcode() {
    const btn = document.getElementById('rc-mobile-verification-button');
    if (btn && btn.href) { const m = btn.href.match(SHORTCODE_REGEX); if (m) return m[1]; }
    const canvas = document.querySelector('canvas.recaptchaJavascriptChallengeQrCanvas');
    if (canvas && canvas.title) { const m = canvas.title.match(SHORTCODE_REGEX); if (m) return m[1]; }
    const fallback = document.querySelector('.recaptchaJavascriptChallengeQrFallbackText');
    if (fallback && fallback.textContent) { const m = fallback.textContent.match(SHORTCODE_REGEX); if (m) return m[1]; }
    return '';
  }
  function pollAndNotify() {
    const code = readShortcode();
    if (!code || code === lastSent) return;
    lastSent = code;
    window.top.postMessage({ type: MSG_TYPE, code }, 'https://aistudio.google.com');
  }
  pollAndNotify();
  setInterval(pollAndNotify, 800);
  return;
}
```

```js
// lines 88-95：顶层页面只在 verify-account 路径标记“看到过挑战”
function renderTopBar(code) {
  currentCode = code;
  if (location.pathname.includes('/verify-account')) sawChallengeOnVerify = true;
  ...
}
```

```js
// lines 177-207：所谓“过关提醒”只是离开 /verify-account 后显示
function renderReturnReminder() {
  ...
  span.textContent = '验证似乎已通过，请回中转页点「已通过」';
  ...
}
setInterval(() => {
  if (location.pathname.includes('/verify-account')) {
    if (currentCode) sawChallengeOnVerify = true;
    return;
  }
  if (sawChallengeOnVerify && currentCode && !showedReturnHint) {
    renderReturnReminder();
  }
}, 1200);
```

脚本机制结论：

| 能力 | 实现 | 可服务端复用？ |
|---|---|---|
| 读取短码 | 跨域 iframe DOM：button.href / canvas.title / fallback.textContent | 否，必须同源/浏览器上下文 |
| 跨域传递 | `window.top.postMessage(..., 'https://aistudio.google.com')` | 否 |
| 二维码展示 | Canvas 已由 reCAPTCHA 渲染，脚本只取文本短码 | 否，除非重新生成 QR |
| 判定扫码通过 | 仅凭“离开 /verify-account”猜测，未查询任何状态接口 | 不可靠，不可复用 |
| 依赖凭据 | 隐式依赖 AI Studio 登录会话与 reCAPTCHA bframe | 需要有效会话 |
| 硬编码 | 第三方 hub、800/1200ms 轮询、短码 6-32 位、iframe 路径判断 | 不建议 |

## 8. 前端现状（E2，仓库代码）

### 8.1 类型

```ts
// web/src/types/accounts.ts:3-24
export interface Account {
  id: string
  name: string
  email: string | null
  ...
  tier?: string
  tier_checked_at?: string | null
  expired?: boolean
  duplicate_of?: string | null
  enabled: boolean
  group: string
  session_expired?: boolean
  auth_cooldown?: number
  is_available?: boolean
  rebind_required?: boolean
  ...
}
```

### 8.2 状态判定

```ts
// web/src/utils/accountStatus.ts:4-11
export type AccountStatusKey =
  | 'disabled' | 'expired' | 'cooldown' | 'instance' | 'active' | 'quota' | 'ready'
```

```ts
// web/src/utils/accountStatus.ts:25-50
if (account.enabled === false) return { key: 'disabled', ... }
if (account.expired === true || account.session_expired === true) return { key: 'expired', ... }
if (Number(account.auth_cooldown || 0) > 0) return { key: 'cooldown', ... }
...
if (account.is_available === false) return { key: 'quota', ... }
return { key: 'ready', ... }
```

关键事实：前端把 `expired` 与 `session_expired` 合并为同一个 `expired` 徽章，没有 verify 状态。

### 8.3 筛选与徽章

```ts
// web/src/components/accounts/AccountTable.vue:46
const statusFilter = ref<'all' | 'ready' | 'limited' | 'cooldown' | 'expired'>('all')
```

```ts
// web/src/components/accounts/AccountTable.vue:48-59
if (statusFilter.value === 'expired') return status.key === 'expired'
if (statusFilter.value === 'limited') return status.key === 'quota'
if (statusFilter.value === 'cooldown') return status.key === 'cooldown'
return status.key === 'ready'
```

```html
<!-- AccountTable.vue:419-461：状态筛选 pills，当前只有 全部/就绪/配额耗尽/鉴权冷却/登录态失效 -->
```

```html
<!-- AccountRow.vue:311-360：状态 pill 分支 -->
v-if="status.key === 'disabled'" -> 已停用
v-else-if="status.key === 'expired'" -> 登录态失效
v-else-if="status.key === 'cooldown'" -> 鉴权冷却中
v-else-if="status.key === 'instance'" -> 实例 #n ...
v-else-if="status.key === 'active'" -> 当前激活
v-else-if="status.key === 'quota'" -> 配额耗尽
v-else -> 就绪
```

```html
<!-- AccountRow.vue:365-375：恢复按钮，仅在 isExpired 时显示 -->
<button v-if="isExpired" ... @click="emit('clearCooldown')">恢复</button>
```

```ts
// AccountRow.vue:90-92
const isExpired = computed(() => {
  return props.account.expired === true || props.account.session_expired === true
})
```

```html
<!-- AccountTable.vue:573：失效/受阻账号的“恢复”实际调用 recoverAccount -->
@clear-cooldown="acc.session_expired || acc.expired ? handleReverifyAccount(acc) : handleClearAccountCooldown(acc.id)"
```

### 8.4 Store

```ts
// web/src/stores/accounts.ts:436-468
async function recoverAccount(id: string) {
  ...
  const res = await accountsApi.refresh({ account_ids: [id], refresh_email: true })
  ...
  await systemApi.clearCooldown({ account_id: id })
  toast.success('账号登录态已恢复')
  ...
}
```

关键事实：`recoverAccount` 只做“刷新 + 清冷却”，没有 verify-account 二维码或状态查询。

### 8.5 后端响应

```py
# src/aistudio_api/api/routes_accounts.py:142-164
class AccountResponse(BaseModel):
    ...
    expired: bool = False
    ...
    session_expired: bool = False
    auth_cooldown: int = 0
    is_available: bool = True
    quota_exhausted: bool = False
```

```py
# routes_accounts.py:102-139
def _account_status(...):
    ...
    session_expired = bool(stats.session_expired)
    is_available = bool(
        account.enabled and not account.expired and not session_expired and cooldown == 0
    )
    return {"session_expired": session_expired, "auth_cooldown": cooldown, "is_available": is_available, ...}
```

```py
# routes_accounts.py:221-261
def to_account_response(account, *, status=None):
    ...
    session_expired=session_expired,
    auth_cooldown=auth_cooldown,
    is_available=is_available,
    quota_exhausted=quota_exhausted,
```

### 8.6 持久化结构

```py
# src/aistudio_api/infrastructure/account/account_store.py:322-345
@dataclass
class AccountMeta:
    id: str
    name: str
    email: str | None
    created_at: str
    last_used: str | None = None
    auth_user: str = "0"
    cookie_id: str | None = None
    tier: str = "Free"
    tier_checked_at: str | None = None
    last_rotated_at: str | None = None
    expired: bool = False
    duplicate_of: str | None = None
    auth_saved_at: str | None = None
    last_refresh_attempt_at: str | None = None
    last_refresh_error: str | None = None
    enabled: bool = True
    group: str = "default"
```

关键事实：`meta.json` 没有 `verify_required` 字段；`expired` 是持久化字段，`session_expired` 在运行时 `AccountStats` 中。

### 8.7 运行时统计

```py
# src/aistudio_api/application/account_rotator.py:153-171
@dataclass
class AccountStats:
    ...
    auth_cooldown: float = 0.0
    session_expired: bool = False
    last_session_expired: float = 0.0
```

### 8.8 当前把 verify-account 折叠成 session_expired

```py
# src/aistudio_api/domain/errors.py:12-39
_ACCOUNT_VERIFICATION_MARKERS = (
    "aistudio.google.com/verify-account",
    "aistudio.google.com/verify?",
    "must be verified before it can continue using the gemini api",
    "must be verified before it can use the gemini api",
    "account verification required to restore access",
    "free_tier_verification_required",
)
class AuthError(...):
    def __init__(...):
        self.verification_required = is_account_verification_required_message(message)
```

```py
# src/aistudio_api/application/routing_pipeline.py:930-958
if isinstance(exc, SessionExpiredError):
    self._record_error("session_expired", ...)
    return await self._switch_or_raise(..., reason="session_expired")

if isinstance(exc, AuthError):
    if getattr(exc, "verification_required", False):
        self._record_error("verification_required", ...)
        return await self._switch_or_raise(..., reason="verification_required")
```

```py
# src/aistudio_api/application/account_orchestrator.py:72-96
elif event == "verification_required":
    # verify-account is a per-account upstream risk lock...
    rotator.record_session_expired(...)
elif event == "session_expired":
    rotator.record_session_expired(...)
    for sibling_id in _sibling_account_ids(rotator, account_id):
        rotator.record_session_expired(...)
```

关键事实：路由层已经区分 `verification_required` 事件，但落库/运行时统一写入 `session_expired`，因此前端无法区分“登录态失效”与“需扫码验证”。

### 8.9 保活明确回避 verify-account

```py
# src/aistudio_api/application/account_keepalive.py:203-227
"""True for accounts locked by session expiry / verify-account.

Background keepalive must not keep probing a verify-account lock: a
successful ``GetAiStudioBenefitTier`` 200 would otherwise clear the
local ``expired`` flag and silently wash the lock away.
"""
...
return bool(getattr(stats, "session_expired", False))
```

结论：现有系统把 verify-account 视为“不可自动探活”的锁；这支持新增独立状态，而不是继续混在 expired 里。

### 8.10 UI Debug 沙盒

```py
# src/aistudio_api/api/ui_sandbox.py:272-283
@dataclass
class _AccountSeed:
    ...
    expired: bool = False
    group: str = "default"
    cookie_id: str | None = None
    session_expired: bool = False
    auth_cooldown: int = 0
    quota_available: bool = True
```

```py
# ui_sandbox.py:1946-1953
_AccountSeed(
    "acc_pro_session",
    "Pro Session Expired",
    "pro.session@example.com",
    "Pro",
    group="legacy",
    session_expired=True,
),
```

结论：UI 施工时必须同步给 sandbox 增加 `verify_required` 字段和种子账号，否则无法在 `AISTUDIO_UI_DEBUG=1` 下验收新徽章/弹窗。

## 9. 本轮实际下载清单

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

说明：传输大小是 HTTP 压缩后 `size_download`；落盘大小是解压后的 UTF-8 文件大小。匿名 verify-account 最终落在 Google 登录页；该登录页未保留到研究目录，避免保存页面内的匿名反 CSRF 令牌。

## 10. 复现命令（代理、匿名）

```powershell
$env:HTTPS_PROXY='http://127.0.0.1:10809'
$env:HTTP_PROXY='http://127.0.0.1:10809'
$ua='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36'

curl.exe -sS -L --compressed -x http://127.0.0.1:10809 -A $ua `
  -D "$env:TEMP\va-headers.txt" -o "$env:TEMP\va-login.html" `
  -w "%{http_code} %{url_effective} %{num_redirects}`n" `
  https://aistudio.google.com/verify-account
# 探测完成后删除 $env:TEMP\va-headers.txt 与 $env:TEMP\va-login.html；不要保留 Set-Cookie 或登录页令牌。

curl.exe -sS -L --compressed -x http://127.0.0.1:10809 -A $ua `
  -o page-welcome.html https://aistudio.google.com/welcome
```

带凭据探测仅执行一次，未保存响应页面，未写入任何 Cookie/Authorization 到磁盘。

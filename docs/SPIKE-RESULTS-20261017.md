# P0 Spike 阶段性结论（A/B/C 已通过）

> 日期：2026-10-18（会话始于 2026-10-17 晚）· 状态：**Spike A ✅ · B ✅ · C ✅ · D ✅ 全绿**（拦截误伤根因已定位并修复验证，§9）
> 权威证据全部落盘于 `C:\Users\ADMIN\AppData\Local\Temp\kilo\`（见 §7）。若上下文丢失：读本文件即可继续。

---

## 0. 一句话总结

Build 面（app 原生路径 + 直连路径）在本仓纯 Python CDP 架构上**全链路打通**：
预热捕获（`tb` + 请求头，零额度吞没）→ 页内自签（`token = M.Cn(tb, await M.GB(path + " " + jsonText))`）→ 直发 `ProxyStreamedCall`（**HTTP 200，9 帧渐进，189 字节文本**）。
全部卡点根因 = **宿主"真人活动门"（`hasBeenActive`，只认可信输入）** + 两个小坑（跨域 fetch 需 `credentials:"include"`；规格字段需与 Ark 逐字对齐）。

---

## 1. 最终通过标准对照（计划书 §3）

| Spike | 通过标准 | 实测结果 | 证据 |
|---|---|---|---|
| A 凭证保真+宿主可达 | 登录态 + 预览 iframe 存活 | 4/4 全绿（两次复现） | `spikeA-evidence.json` |
| B 签名器捕获 | `signer ready` + `hasToken: true` + warmup 零额度吞没 | ready ✅ / hasToken ✅ / `cnSeen≥1,gbSeen≥1` ✅ / `warmup: "swallowed"` ✅ / 合成 400 完整回传 ✅ | `spikeAppV6/V8-evidence.json` |
| C 直连真流式 | 200 + ≥2 帧渐进 + 可解析 | **200 ✅ / 9 reads·9 frames ✅ / 9518B, 文本 189B ✅**（holder 帧 = REST chunk；接 `wire_parser` 需薄适配，见 §8-7） | `spikeAppV8-evidence.json` (`direct`) |
| D 内存测绘 | 双页 + 并发流 <500MB + boq 白名单 | **全绿**：host+preview+2 并发流峰值 475.6MB < 500MB ✓；boq 未被拦 ✓；整表拦截误伤根因=Monaco（`*vs/editor*`），移除 G_LIB 后全链路复绿 ✓ | `spikeE-nolib-evidence.json` |

---

## 2. 最终工作链路（已验证的完整时序）

```
[Python] ws://127.0.0.1:9998 迷你服务 + 顶帧注入
   ├─ authIndex 应答（requestAuthIndex → authIndexResponse(0)）
   ├─ 签名 hook（spike_signer.js：hook M.Cn/M.GB/XHR；__warmup__ 吞没）
   └─ trusted 输入预热（开门，见 §4）
        ↓
[app]（部署的 scripts/client 同源客户端，零改动）
   ├─ 连我们的 WS（/?authIndex=0）→ 收到规格 → fetch(generativelanguage)
   ├─ 桥：shim bootstrap（宿主 iframe load 后自动送达，含 MessagePort；端口可用= getHostUrl 往返实证）
   └─ 宿主 jCd handler → 过门（Nwc/hasBeenActive）→ 签名（宿主调 M.Cn/M.GB，被 hook 捕获）
        ↓ 原生路径：宿主出站 XHR([path, jsonText, token]) ← 被 hook 吞没（warmup 零额度）
        ↑ 或直连路径（生产用）：页内自签 fetch(ProxyStreamedCall, credentials:"include") → 200 流
```

### 关键请求规格（与 Ark `DirectSignerService.js:1335-1343` 逐字对齐）

```json
{"method":"POST","path":"/v1beta/models/<model>:streamGenerateContent",
 "query_params":{"alt":"sse"},"request_attempt_id":"<id>","request_id":"<id>",
 "streaming_mode":"real", "is_generative":true, "body":"<Gemini REST JSON 原文>"}
```

## 3. 直连调用（Spike C 验收版，生产路径）

```js
// 页内（顶帧）执行；tb 来自 hook 捕获；headers 来自被吞没出站请求的收割（7 项含 authorization）
const token = await M.Cn(S.tb, await M.GB(path + " " + jsonText));
const body = JSON.stringify([path, jsonText, token]);   // 逐字节、键序不可变
const resp = await fetch("https://alkalimakersuite-pa.clients6.google.com/$rpc/…/ProxyStreamedCall",
    { method: "POST", headers: harvested, body, credentials: "include" });   // ← include 必填！
```

- **`credentials:"include"` 是硬坑**：跨域 fetch 默认不带 Cookie → 401 `CREDENTIALS_MISSING`（实测），加上后 200。
- 响应 = holder 三层数组（叶子为转义 REST chunk JSON）；实测 9 帧渐进、`candidates/usageMetadata` 结构（与 `direct-signer-sample.txt` 同构）。
- 出站载荷实证：`rpcBody: ["/v1beta/models/__warmup__:streamGenerateContent", null, "!rK-lr8v…"]`（warmup 无 body → jsonText=null；token 为 `!` 前缀 WAA 串）。

## 4. 根因与开门配方（本夜最大发现）

宿主 handler 在服务 fetch 前执行 `yield _.Nwc(k,{lza:true})` —— **死等 `hasBeenActive()`**（100ms 轮询，静默）。判据（`v4-match-4.js:886-887`）：`navigator.userActivation.hasBeenActive && (可信 mousemove≥30 || 可信 keydown≥5)`，全部只认 `isTrusted` 事件。

**实测有效配方（v6 全绿）**：
1. 45 次 `Input.dispatchMouseEvent(mouseMoved)`（坐标收在顶文档左侧面板内；实测计数 44–45）；
2. **一次真点击** `Input.dispatchMouseEvent(mousePressed+mouseReleased)`（点当前聚焦的 TEXTAREA 中心，无副作用）——**这一步才置位 `userActivation.hasBeenActive`**（实测：仅 mousemove/8×keydown 都不置位；点击后 `hasBeenActive:true, isActive:true`）；
3. `Emulation.setFocusEmulationEnabled(true)`（Playwright 默认行为，建议保持启用）。

> v5 实证：45 移动前 29 计（坐标越界到 iframe 4 次不进顶文档计数）+8 keydown 均未置位；v6 修正坐标+补真点击后一次通过。

## 5. 原生桥路径的一个开放发现（403）

经宿主桥（无 Key 分支 dCd→qCd）发真实模型请求：宿主签名链正常（cnSeen 增长、出站构造正确），但上游返回 **403 PERMISSION_DENIED（"The caller does not have permission"）**——权限在"桥代理层"，与代码链路无关。同类现象：直连路径 200（§3）。**结论：本仓 Build 面主实现用直连路径（§3，Ark 生产同款）；桥路径 403 记入 WP4 开放项**（可能需选 Key 或账号侧授权）。

## 6. 环境与账号（本机实测可用）

- 浏览器 `.cloakbrowser/chromium-146.0.7680.177.4/chrome.exe`；测试 profile `%TEMP%\kilo\spike-profile`；调试 9333；WS 9998；代理 `http://127.0.0.1:10809`（AISTUDIO_PROXY）。
- 账号：`acc_b1a36ccf`（free，odindicola5，Ark:auth-1）——B/C 全绿用它；`acc_46223636`（pro，tonygemini38，Ark:auth-5）。
- **appId 用 `3b85b4ed-f945-422c-ba25-395d1feef528`**（预览宿主 `ais-pre-xrknu3r2…asia-northeast1.run.app`，与 auth-1 cookie 同域；用户可改可重部署）。`56128c84` 为旧实例（宿主 `wk6ozjy…us-east5`，弃）。
- 环境坑：`$env:PYTHONIOENCODING='utf-8'` 必设（否则 GBK 崩）；PowerShell `ConvertTo-Json` 大负载挂死（用 Python）；`el.click()` 不算 trusted。

## 7. 脚本与证据索引（`C:\Users\ADMIN\AppData\Local\Temp\kilo\`）

| 文件 | 用途 | 结论 |
|---|---|---|
| `spike_app_v8.py` | **收官版**：开门+预热吞没+直连 200 | `spikeAppV8-evidence.json`（direct: 200/9帧/9518B/189B） |
| `spike_app_v6.py` | 首次全绿（门+吞没） | `spikeAppV6-evidence.json`（hasToken/warmup=swallowed/400 回条） |
| `spike_app_v7.py` | 桥路径真请求（403 发现） | `spikeAppV7-evidence.json` |
| `spike_app_v5.py` | 门诊断（计数/UA 读数） | `spikeAppV5-evidence.json` |
| `spike_app_v2/v3.py`、`spike_app_probe.py`、`spike_app.py` | 规格对齐/DOM 日志/bootstrap 探针/初版 | 各 evidence JSON |
| `spike_signer.js` | 顶帧 hook（Cn/GB/XHR 收割+吞没） | 由 v5–v8 注入 |
| `spikeA.py` | Spike A（cookie 保真+宿主页） | `spikeA-evidence.json` |

运行模板（cwd=仓库根）：
```powershell
$env:PYTHONIOENCODING='utf-8'
uv run python C:\Users\ADMIN\AppData\Local\Temp\kilo\spike_app_v8.py 'D:\LLM\Ark_AIStudioToAPI\configs\auth\auth-1.json' 3b85b4ed-f945-422c-ba25-395d1feef528
```

## 8. 实测修订（相对计划书）

1. **WP4 主路线修订（重要）**：采用"**原生 app 桥预热捕获 + 直连签名调用**"组合（= Ark 生产模型），不移植 Ark 的 Node 触发链；Python 侧仅 WS 服务 + 注入（约 110 行）。
2. **页面预热新增硬性要求**：可信输入序列（≥30 可信 mousemove + 1 次真点击）——否则宿主"真人活动门"死等（§4）。
3. **直连 fetch 必须 `credentials:"include"`**（跨域 Cookie）——401 坑，实测。
4. **bootstrap 非缺失**（宿主自动送达，探针实证）；宿主端控制器 `jCd/LV.o_`（v4-match-4.js:2071/2252）。
5. 规格字段须与 Ark 逐字对齐（`query_params.alt=sse` 等）；规格送达有 app 自带 DOM 日志可验（`Received request: …`）。
6. 聊天面与 Build 桥面非同一签名链路（勿混用结论）。
7. **Holder 帧为 REST chunk（dict）**；现有 `wire_parser` 消费位置数组协议 → WP4 需薄适配层（REST chunk → 内部块），或直通 REST 语义。
8. 桥代理路径 403（§5）记入 WP4 开放项；直连路径为既定主路径。
9. 测试基准 appId = `3b85b4ed`（§6）。

## 9. Spike D 结果（2026-10-18，`spikeE.py` 消融 + 全量内存，全绿）

**消融实验（blocklist 误伤定位）**：
- `mode=full` 复现：WS 未连接、preview_frames=0；捕获被拦 2 个 .js = `gtm.js`（统计，无害）与 `https://www.gstatic.com/monaco_editor/.../vs/editor/editor.main.js`（命中 `*vs/editor*`）。
- 元凶裁定：**Monaco 编辑器主包被拦导致 apps 页预览 iframe 不挂载**（`*vs/editor*`；同族 `*vs/base*`、`*monaco-editor*`、`*codemirror*`、`*mathjax*`、`*katex*` 一并视为误伤候选）。
- `mode=nolib` 验证（G_LIB 整组不拦、其余照拦）：WS 20s 连接 ✓、preview 挂载 ✓、过门 ✓、warmup 吞没 `hasToken` ✓、直连 200 ✓、2 条并发流均 200 ✓ —— **全链路复绿**。
- **生产结论：Build 面拦截清单 = 媒体/字体/统计/杂项（G_MEDIA+G_TRACK+G_MISC），移除编辑器库组 G_LIB**；`cdp_client.py` 的 `BLOCKED_URL_PATTERNS` 需按面拆分。

**全量内存复测（host+preview+并发流，`--renderer-process-limit=1 --js-flags=--max-old-space-size=128`）**：
- boot 467.5MB → warmup 474.3 → direct 475.6 → 并发流后 473.0（5 进程，无持续增长）。
- **峰值 475.6MB < 500MB ✓（达标，余量约 24MB）**。

**boq 白名单**：`boq-makersuite` 请求出现且未被拦（full/nolib 两次运行一致）✓。

## 10. P1 启动清单（2026-10-18 更新）

1. WP4 主实现照 §2/§3（直连路径 + 开门配方 + `credentials:"include"` + 预热吞没）落进 `src/aistudio_api`；
2. blocklist 按面拆分：Build 面用 §9 修剪版（去 G_LIB），聊天面维持原表；
3. 桥路径 403（§5）作为开放项跟踪（直连为准，不阻塞）。

## 11. 功能移植可行性评估（2026-10-18 定稿）

**总判定：可行（Go）**——P0 四门禁全部通过，方案全链路有可复现证据，剩余为确定工程量而非未知探索。

- 主路线（定案）：纯 Python CDP + app 原样复用 + 迷你 WS(9998) + 顶帧注入（约 110 行）+ 直连签名路径；与 Ark 生产模型同构。WP4 明确否决的 Node 触发链/自研 app/本地服务改造均不需要。
- 残余风险（按序）：① 上游对抗性变化（宿主混淆函数名/真人活动门/规格字段可能随版变更，需探测+显式失败+快速修复）；② 预热时序鲁棒性（活动门只验一条置位路径，生产需读数校验+重试）；③ 账号成本/风控（预热零消耗已锁，真实生成消耗额度需节流/池化）；④ 内存余量约 24MB（475.6/500）偏紧，池化多页需实测调参；⑤ 桥 403（开放项）。
- P1 工程内容：WP4 产品化落码（错误处理/重试/配置/日志）、REST chunk→wire_parser 适配层、blocklist 按面拆分、测试验收。均为确定性工作。

## 12. 内存测绘（2026-10-18，`spikeM.py`，同口径 + 双实例）

口径警告：`WorkingSet` 求和会重复计共享页（虚高）；`Private Working Set`(privWS) 求和 ≈ PSS 代理（去共享重复）。**spikeM 证据 JSON 的 `privws_mb` 字段因 PS 单位误判被 ×1024 放大，真实 MB = 字段值 ÷1024**（`ws_mb`/`commit_mb` 字段正确）。

| 场景（单树=1 浏览器实例，5 进程） | privWS(MB) | WS 求和(MB) | Commit(MB) |
|---|---|---|---|
| A 空载（about:blank） | 75.5 | 281.3 | 112.8 |
| A Build 启动（宿主+预览+app WS） | 195.4 | 477.7 | 247.6 |
| A Build 满载（过门+warmup+直连流后） | 212.4 | 498.7 | 265.7 |
| B 聊天面（/app） | 193.0 | 484.6 | 247.0 |
| B Build 启动（冷缓存首载） | 229.0 | 515.8 | 283.9 |
| **双实例满载（A+B 相加）** | **426.9** | 1002.0 | 528.1 |
| Python 进程（spike 脚本侧） | 28.8 | 47.5 | — |

结论：单实例 Build 面总 PSS 代理 ≈ 241MB（212+29），未超 README 350-450MB 宣称窗口；聊天面（193）与 Build 面（212）在 privWS 口径接近。双实例满载 ≈ 456MB（PSS 实值估计再 +50-100MB 摊薄共享页）。Termux 1GB 空闲建议下双实例仍可行（Linux 口径更低）。此前 §9 的 475.6MB 为 WS 求和虚高口径，不可与 PSS 类比。

Node 层参照（本机现跑进程实测）：每个 Node/Playwright 进程 commit 40-88MB。Ark 类方案每实例 = Node 后端 + Playwright driver + 浏览器（浏览器本体两边同源），Node 层量级约 150-300MB/实例，即本仓相对其的结构性优势所在。

## 13. 对面项目（Ark）A/B 实测：MAX_CONTEXTS=2（2026-10-18）

方法：Ark `.env` 临时 `MAX_CONTEXTS=2` → `npm start`（8869 端口）→ 启动 pool preload 自动登录 2 账号（context #7/#8，均 apps 页 + WS 就绪，`Background preload complete`）→ 稳态后用 `mem_snap_ark.ps1` 双次采样（间隔 45s，结果稳定）。测量口径与 §12 完全一致：进程树逐进程 privWS（PerfFormatted 原值=字节）/WS/Commit 求和。

| 口径 | Ark@2 浏览器树(8 进程) | Ark@2 Node | Ark@2 合计 | 本仓 1 浏览器+PY（等效 2 账号能力） | 本仓 spike 双浏览器+PY（最坏情况） |
|---|---|---|---|---|---|
| **privWS** | 745.2 – 757.6 | 13.0 | **≈ 758 – 771** | 212.4 + 28.8 = **241.2** | 426.9 + 28.8 = 455.7 |
| WS 求和 | 1166.7 – 1243.9 | 47.9 | ≈ 1215 – 1292 | 498.7 + 48.0 = 546.7 | 1002.0 + 48.0 = 1050.0 |
| Commit | 1125.8 – 1126.6 | 22.0 | ≈ 1148 | 265.7 + py | 528.1 + py |

- 宣称"2 账号 ~950MB"核对：privWS 745–758（略低于宣称）；WS 求和 1167–1244、Commit ≈1126（高于宣称）——950 落在多口径区间中部，属合理粗估，非虚标。
- **优化量（同口径 privWS）**：等效 2 账号场景 Ark ≈765（含 Node） vs 本仓 ≈241（单浏览器架构，账号数不增浏览器）→ **节省 ≈520–530MB（-69%）**；即使按最坏情况（本仓也开双浏览器）= 455.7 → 仍节省 ≈310MB（-40%）。
- 结构事实：Ark 的 2 context = 同一个 Camoufox 进程内 2 个全载 apps 页面（其中 #25356 主进程 privWS 380MB、两个页面 content 进程 privWS 145/204MB）；本仓 = 1 浏览器 + 蓝绿标签热切，**足迹随账号数近似平坦**（对面近线性增长：宣称 1→2→3 为 700→950→1100）。
- 现场已还原：Ark `.env` `MAX_CONTEXTS` 恢复 1，Ark 服务已停止，无残留进程。

## 14. 合并场景实测：build+PG 并行单浏览器 + 备用页（2026-10-18，`spikeN.py`）

背景校正（用户指正）："平滑切换"必须**预 warm 备用实例**（本仓现状为切换触发时才后台建页+握手，非瞬时）；且目标形态 = build 面与 PG(chat) 面**并行**（PLAN §WP5：每面一个页面实例）。本实测给出该形态的真实内存与备用页增量（口径同 §12：privWS/WS/Commit 逐进程求和；单浏览器 5 进程共享 1 渲染进程）。

| 阶梯（同 1 浏览器实例） | privWS(MB) | WS求和(MB) | Commit(MB) | privWS 增量 |
|---|---|---|---|---|
| s0 空载 | 77.3 | 287.0 | 115.5 | — |
| s1 +chat 页（/prompts/new_chat） | 200.3 | 496.2 | 251.2 | +123.0（**首頁**，暖引擎） |
| s2 +build 页（apps+预览+WS 已连） | 266.1 | 574.2 | 319.9 | **+65.8** |
| s3 +备用 build 页（第二账号，瞬时切换用） | 336.8 | 650.5 | 395.0 | **+70.7** |

- **修正 §13 表述**：按"对面同等平滑度（瞬时切换）"标准，本仓足迹不是"平坦"，而是 **+约 66–71MB/每预 warm 页**（同浏览器共享引擎后）；但仍远低于对面 per-context（其两页面 content 进程 privWS 145+204≈175/页，另加主进程基座 380）。
- **PLAN "单标签页增量约 20–30MB" 校准**：实测 +66–71MB（首頁 +123MB），WP5"页面实例配额/内存基线"按 **~70MB/实例** 校准（拦截清单开启后或再降，生产复测）。
- **合并 vs 分立（同口径 privWS）**：
  - 合并本仓（s3：chat+build 双面 + build 备用 + Python 28.8）≈ **365.6MB**（1 服务、1 浏览器、1 控制面）；
  - 分立（Ark build@2 = 764 + 本仓 PG 单页栈 = 200.3+28.8 ≈ 229.1）≈ **1000.1MB**（2 服务、2 引擎，且 PG 侧仍无备用）；
  - **省 ≈ 634MB（−63%）**；仅算双面并行无备用（s2 ≈ 295MB）则省 ≈ 705MB（−71%）。
  - 对比 Ark@2 单面（764）：合并"双面 + 备用"（366）省 ≈ 398MB（−52%）——合并后两面全功能 ≈ 对面单面 2 账号的一半。

## 15. 加载时序 + 小鸡模拟（2026-10-18，`spikeO.py`，normal vs chicken）

小鸡定义（`docs/ark-reference/VPS-DEPLOYMENT-NOTES.md`）：1 vCore / 2GB+4GB swap / 剩余磁盘 8.5GB；同机 AstrBot+NapCat 常驻、单核负载 1.3+；反代总 PSS 预算 ≤800MB。模拟方式：全树（浏览器 4–5 进程 + spike Python）**钉核 0 + 同核 60% 占空忙等负载**（`chicken_load.py` 自钉核，每 3s 重钉新子进程）。网络链路未模拟。

| 指标 | Normal | Chicken 模拟 | 倍率 |
|---|---|---|---|
| 浏览器就绪 | 0.9s | 1.1s | 1.2× |
| chat 页 nav→可用（文本框就绪） | 22.3s | 33.2s | 1.5× |
| build 冷建 nav→WS（引擎冷） | 31.2s | 48.7s | 1.6× |
| └ 其中 nav→load（页面装载段） | 7.0s | 35.4s | **5.1×（CPU 敏感段）** |
| 备用页（引擎热）nav→WS | 9.1s | 25.4s | 2.8× |
| 全程（启动→双面+备用全就绪） | ≈67s | ≈111s | 1.7× |
| 内存 privWS（3 页 5 进程） | 315.2MB | 320.8MB | ≈1.0× |
| 全树累计 CPU | 44.7s | 68.1s | 1.5× |

- **体验判定**：小鸡下冷建页 ≈49s、热备用页 ≈25s、全程 ≈1.8 分钟；**备用预热是体验关键**——standby 已在手则切换近瞬时，新建 standby 后台 25s 完成不阻塞在役账号。click→WS 段受网络波动主导（normal 24.2s vs chicken 13.3s 出现倒挂，非 CPU 敏感段）。
- **内存预算**：321MB + Python 29MB ≈ **350MB ≤ 800MB 目标 ✓**（余量 ~450MB）；替换现役 Ark（2 context ≈758–771MB + node）可释放 ~400–500MB 物理内存，缓解其基线 1.2G/1.9G 的 swap 压力。
- **磁盘预算**：CloakBrowser 536MB + 热 profile 36MB（+venv 数百 MB）→ 对 8.5GB 剩余充裕。
- **模拟局限（诚实标注）**：无 cgroup 内存硬限、无慢盘 I/O、网络链路未模拟（真实 VPS 走 WARP）；纯 CPU 维度结论可信。

## 16. P1 前置先行实验摘要（2026-10-18，详见 `PRE-P1-EXPERIMENTS-20261018.md`）

七项全部完成（E1 适配回放 / E2+E7 长驻 / E3 tier 直连 / E4 BrowserContext 隔离 / E5 ATG 白名单 / E6 转换层评估 / E8 上游探测）。关键校准：

- **E4（最重要）**：单浏览器多账号经 **BrowserContext** 隔离成立（SAPISID 逐字节证据、双 WS 齐连）；但独立 context 强制独立渲染进程，**成本 +180MB/账号 context**（比同 context 加页 +66–71MB 贵）。修正 §14：2 账号全栈 ≈ 385+29 ≈ **414MB**（1 服务 1 浏览器），仍比 Ark@2（771）省 ~46%。**WP5 配额基线：单页 ~70MB、跨账号备用 ~180MB/context。**
- **E1**：holder 帧（REST chunk）→ wire 数组薄适配层（~60 行）验证通过，`wire_parser` 零改动。
- **E3**：tier RPC 纯 Python 直连可行（odindicola5→free、tonygemini38→pro），零模型额度。
- **E5**：`wire_codec` allowedFunctionNames 会剔除合成工具（坑复现）；修复=白名单并入合成工具名（已验证）。
- **E8**：三段式探测（MAKERSUITE_MISSING / SIGNER_FUNCS_DRIFT / TB_MISSING）带处置建议；**nK/jK key 属性已漂移（本次均空），须以收割请求头为准**。
- **E2+E7**：16.5 分钟静置后签名链/活动门/预览全存活、直连 200；内存 GC 锯齿无泄漏。
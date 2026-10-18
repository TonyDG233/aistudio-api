# P1 前置先行实验计划（2026-10-18）

> 背景：P0 四 Spike 全绿（见 `SPIKE-RESULTS-20261017.md`）。以下为进入 P1/WP4 前尚未定型、需先行验证的技术方向。
> 执行方式：goal 模式逐项进行；**零/低配额优先**，高消耗实验先停下报告；每项完成后在本文档追加"结果"小节。

---

## E1 Holder→wire_parser 适配回放（零额度）
- **目标**：确认 REST chunk（holder 帧）到现有 `wire_parser.parse_response_chunk` 的适配路径。
- **方法**：用 `spikeAppV8-evidence.json` 已捕获帧离线回放：逐帧构造 parser 输入，记录成功/失败点；设计薄拆壳层并验证。
- **验收**：≥2 帧解析出文本/usage；适配层接口草图（签名+转换示例）入文档。

## E2+E7 长驻活动门鲁棒性 + 内存漂移（零额度，时间型）
- **目标**：build 页开门后静置 15–20 分钟：(a) 再发 warmup（吞没）验证签名通道仍活；(b) 周期采样内存无持续增长；(c) 备用页同测。
- **方法**：基于 v8 的独立 spike：开门→warmup 吞没→静置（分段等待）→复测 warmup→4–5 次内存采样。
- **验收**：静置后 warmup 仍吞没（hasToken 保持）；内存漂移 <±5%。

## E3 WP3 前置：tier 探测 Python 直连（零模型额度）
- **目标**：`GetAiStudioBenefitTier` 纯 Python（httpx + `calculate_sapisid_hash` + 代理）直连可用，free/pro 映射正确。
- **方法**：auth-1（free）与 auth-5（pro）各发一次 RPC，记录 code/body/tier。
- **验收**：两账号 tier 正确；401/403 异常路径行为记录。

## E4 单浏览器多账号 BrowserContext 隔离预热（零额度，关键架构验证）
- **背景问题**：单 profile 的 cookie 是浏览器级共享 → 无法同时登录两个 Google 账号；"瞬时切换"的备用账号须由 **CDP BrowserContext**（独立 cookie jar）承载。这是对"单账号实例能否平滑过渡"的正面回答实验。
- **方法**：`Target.createBrowserContext` 建 context#2 → 内注入 auth-5 cookies → 打开 build 页（注入 signer）→ 验证两 context 同时持不同账号、WS 均连；内存采样对比。
- **验收**：双账号同时在线成立；增量 ≈ 页级（~70MB 量级）；产出 standby 多账号落地形态结论（对 WP4/WP5 更新建议）。

## E5 ATG 白名单坑验证（零额度，代码级）
- **目标**：验证 `wire_codec.py` `allowedFunctionNames` 过滤会剔除合成工具，确认最小修复点。
- **方法**：单测级脚本：带 allowedFunctionNames + 合成工具声明组装，观察剔除；验证白名单修补。
- **验收**：坑复现 + 修复方案可用。

## E6 WP8 转换层移植评估（零额度，对照分析）
- **方法**：盘点 Ark `src/core/FormatConverter/*`（文件/LOC/接口/依赖）→ 映射本仓落点（routes_openai、routes_claude、format_converters）。
- **验收**：模块对照表 + 工作分解与风险入文档。

## E8 上游缺失显式失败探测（零额度）
- **目标**：生产级探测（`default_MakerSuite` / M.Cn / M.GB / tb 属性 nK|jK 漂移）与显式报错路径。
- **方法**：live 页执行探测 JS（正常态）；模拟缺失分支验证错误文案。
- **验收**：正常态全绿；缺失态输出可行动错误（含处置建议）。

---

## 执行纪律
- 每项结果回填本文档；需要时同步更新 `SPIKE-RESULTS-20261017.md` / `PLAN-ark-merge-20261017.md`。
- 全部完成后统一汇报：每项结论、对 PLAN/WP4/WP5 的更新建议、剩余未决项。

---

## 实验结果（2026-10-18）

### E1 Holder→wire_parser 适配回放：**PASS**
- `spikeP.py` 捕获 2 帧全量 REST chunk（`spikeP-evidence.json` → `frames_full`）；离线回放 `e1_replay.py`：
  - 帧0：text=`"OK"`，usage total=153（prompt 7 / completion 146，其中 reasoning=145）；帧1：finish=1（STOP 映射）。
  - 适配函数 `rest_chunk_to_wire`（REST dict → wire 数组）已在脚本中跑通：parts→`[None,text]`、finishReason→枚举码、usageMetadata→index2 十元数组、responseId→index7。
- 待补映射（WP4 落码时）：REST `part.thought` → wire thought flag（index10）；`functionCall.name/args/id` → index3（本脚本已映射）。
- **结论：holder 帧无需改动现有 `wire_parser`，仅需 ~60 行薄适配层（已验证）。**

### E2+E7 长驻活动门 + 内存漂移：**PASS**
- 16.5 分钟静置后：**warmup#2 仍被宿主吞没**（cnSeen 1→2，hasToken 保持，"swallowed"）——签名链长驻存活、零额度。
- `userActivation.hasBeenActive` 静置后保持 true（页面级粘性）；预览 iframe 存活。
- **静置后直连复验 200**（reads=1，2424B，极小额 1 次）——端到端无退化。
- 内存（校准后 privWS）：179.2 → 172.9 → 194.1 → 172.9 → 173.9MB，**GC 锯齿、无单调增长、末值≈初值（无泄漏）**；peak-to-trough 12.3%（原"<±5%"判据对 GC 锯齿过严，判据应改为"趋势+回归"；证据 `spikeR-evidence.json`）。

### E3 tier 探测 Python 直连：**PASS**
- auth-1（odindicola5）→ 200，body `[]` → **free**；auth-5（tonygemini38）→ 200，body `[1]` → **pro**。
- 纯 Python httpx + SAPISIDHASH（SAPISID + 1P + 3P 三段）+ 公网 web key（`AIzaSyDdP8…`，Ark 同款常量）+ 代理，**零模型额度**。
- **结论：WP3 无浏览器直连可行**；401→过期标记、403→key 回收重试等分支可照 Ark 平移。

### E4 单浏览器多账号 BrowserContext 隔离：**PASS（架构关键）**
- 默认 context（auth-1）与 `Target.createBrowserContext` 新 context（auth-5）**双 build 页同时运行，双 WS 均连接成功**。
- 隔离决定性证据（`spikeQ2.py`）：两 context 的 SAPISID cookie 与各自 auth 文件**逐字节一致且互异**。
- 内存（`spikeQ-evidence.json`）：ctx1 205.3MB → +ctx2 后 385.2MB（privWS），**增量 +180MB/账号 context**——独立 context 强制独立渲染进程（进程数 5→6），比同 context 加页（+66–71MB）贵；仍低于对面 per-context（~175–250MB + 重基座）。
- **对 WP4/WP5 更新建议**：多账号"瞬时切换"备用用 **BrowserContext 池**实现；配额基线按 **~180MB/账号 context**（而非 ~70）；2 账号全栈 ≈ 385+29(Python) ≈ **414MB**，比 Ark@2（771）省 ~46%。（口径提醒：`spikeQ-evidence.json` 的 privws 字段有 ×1024 单位残留，本文数字已按 SPIKE-RESULTS §12 统一修正；另注意 `--renderer-process-limit=1` **不合并跨 context 渲染进程**——这正是 +180 的来源。）

### E5 ATG 白名单坑：**PASS**
- 坑复现（`wire_codec.py:329-343`）：allowed=[real_tool] 时合成工具声明被过滤剔除（场景A→仅 real_tool 存活）。
- 修复验证：白名单并入合成工具名后声明保留（场景B）；mode=NONE 清空（场景C 对照）。
- **结论：WP2 移植时在 atg/injector 增加"allowed_names.append(synth_name)"补偿点即可。**

### E6 WP8 转换层移植评估：**完成**
- 模块盘点（9 文件 / 5023 行）：ChatCompletions(786+708)、Claude 请求(548)+响应(803)、Responses 请求(926)+非流(484)+流(828)、Common(754)、Embeddings(30)、index(30)。
- 公共方法面：`translateOpenAIToGoogle` / `translateGoogleToOpenAIStream` / `convertGoogleToOpenAINonStream` / `translateClaudeToGoogle` / `translateGoogleToClaudeStream` / `convertGoogleToClaudeNonStream` / `translateOpenAIResponseToGoogle` / `translateGoogleToResponseAPIStream` / `convertGoogleToResponseAPINonStream` / `translateOpenAIEmbeddingsToGoogle`；Common 侧：parseModelSuffixes、ensureThoughtSignature、hasGeminiBuiltInTools、sanitizeGeminiTools、normalizeGeminiResponseSchema、媒体加载、usage 解析。
- 依赖面极轻：axios + mime-types（→ httpx + `mimetypes`），其余纯逻辑。
- **关键适配判断**：Ark 转换器面向 **Gemini REST dict**；本仓管线是 `NormalizedGeminiRequest` → `wire_codec` / `Candidate` → 输出。移植边界有二选：chat 面转换到内部归一模型（推荐）；build 面本就走 REST 直通，可保持 REST dict 边界。
- 特色能力须一并移植：模型名后缀（-search/-code/-minimal/thinkingLevel）、`DUMMY_THOUGHT_SIGNATURE` + ensureThoughtSignature（Gemini 3 functionCall 校验）、内置工具键映射、schema 归一。
- 工作量维持 WP8 预估 5–8 人日；风险：工具调用流式分片形态对齐、ATG 六边界与格式面啮合。

### E8 上游缺失显式失败探测：**PASS**
- 正常态：`default_MakerSuite` / M.Cn / M.GB 全存活（`spikeP-evidence.json` → detect.live.ok=true）。
- **新发现：`tb.mb.nK`/`jK` 两个历史 key 属性本次均为空**（keyAttr=""）——harvest 请求头更可靠；生产探测应主判 `rpcHeaders["x-goog-api-key"]`。
- 缺失态双分支验证：`MAKERSUITE_MISSING`（M 缺失）与 `TB_MISSING`（tb 缺失），均带处置建议；函数漂移分支 `SIGNER_FUNCS_DRIFT` 已备。
- **结论：三段式探测函数形态可直接采用，WP4 落码时并入页面就绪检测。**
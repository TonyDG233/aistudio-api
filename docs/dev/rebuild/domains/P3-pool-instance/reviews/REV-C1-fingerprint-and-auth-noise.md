# REV-C1 — 浏览器按账号指纹 / `/v1beta/models` 401 噪音 / Tier 单源 独立只读复核

- 复核日期：2026-11-01（UTC+8）
- 目标工作树：`D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`
- 目标分支 / HEAD：`rebuild-upstream` / `0fcc816dc65b38ff4caf702efc95e7b08fcc791f`（未提交改动）
- 主工作树：`D:\LLM\self_programming\aistudio-api`（HEAD `2da21ed`；仅 `docs/dev/rebuild/05-restart-map.md`、`06-decision-log.md` 已修改）
- 审计范围：C 阶段 C1 —— 浏览器按账号指纹加固（P3-F / D03-027..028）、`/v1beta/models` 401 噪音根因、B7-P1 Tier/发现单源收敛
- 审计模式：严格离线、只读；未启动 `main.py server`、浏览器/Chromium、Docker 构建或任何服务端，未发起网络请求
- 代码改动：无；未修改 `src/`、`tests/`、`config/`、`defaults/`、既有文档或配置；未 stage/commit/push
- 唯一写入：本报告的目标树副本与主仓同路径副本（两份字节一致）

## 0. 结论

**Verdict: CONDITIONAL。**

- 全部门禁实跑通过：`pytest` `908 passed`（0 failed / 0 skipped）、`ruff` 0 error、`pyright` 0 error、`bun build` exit 0、`git diff --check` / `git diff --cached --check` exit 0、`git ls-files config.yaml` 为空。
- A（C 路线）、C（指纹）、D（Tier 单源）的证据与任务口径基本一致；B（401 噪音）的核心去重逻辑与 `/health` 切换在 `Dockerfile` 与源码层已落地。
- **存在 1 个 P1 未关闭项**：`docker-compose.yml` 仍以 30s 周期探测认证受保护的 `/v1beta/models`，compose 的 `healthcheck` 会覆盖镜像内已修正的 `Dockerfile` `HEALTHCHECK`；C1 声称的 “HEALTHCHECK 改 `/health`” 未覆盖该部署清单。日志洪水已被 5 分钟去重显著缓解，但根因未完全消除，因此判 CONDITIONAL。
- 另有 2 个 P2、3 个 Q、3 个 N，均为范围/健壮性/测试覆盖/文档项，不改变本次 908 全绿与代码主路径结论。

## 1. 基线、范围与工作树状态

| 项 | 结果 | 证据 |
|---|---|---|
| 目标 HEAD | `0fcc816dc65b38ff4caf702efc95e7b08fcc791f` | `git rev-parse HEAD` |
| 主仓 HEAD | `2da21ed` | `git -C .. log --oneline -5` |
| 目标未暂存修改 | 25 个文件 | `git diff --name-only HEAD` |
| 目标未跟踪文件 | 4 个：`src/aistudio_api/infrastructure/browser/fingerprint.py`、`.../js/fingerprint_spoof.js`、`tests/unit/test_browser_fingerprint.py`、`tests/unit/test_dockerfile_healthcheck.py` | `git status --short` |
| 目标状态条目 | 29 = 25 修改 + 4 未跟踪 | `git status --porcelain` |
| 暂存区 | 空 | `git diff --cached --name-only` |
| 根 `config.yaml` | 忽略/未跟踪 | `git ls-files config.yaml` 为空；`git check-ignore -v config.yaml` → `.gitignore:15:/config.yaml` |
| 提交/推送 | 无 | 复核前后 HEAD 未变；**未提交 / 未 push** |

> 说明：目标树与主仓的 `docs/dev/rebuild/05-restart-map.md`、`06-decision-log.md` 均为未提交修改；两份文件的跨仓 SHA256 一致（见 §6）。

## 2. A. C 阶段路线一致性

| 检查项 | 结论 | 证据 |
|---|---|---|
| 目标树 C 顺序 = C1 指纹+401+B7-P1 → C2 P8/UI → C3 Claude/Responses → C4 统计 → C5 P10/单仓 | **PASS** | `docs/dev/rebuild/05-restart-map.md:36`、`:60-65`；`06-decision-log.md:53`、`:250-257`、`:485-496` |
| 主仓 C 顺序与目标树一致 | **PASS** | 两仓 05 SHA256 均为 `492EE26B41BE1803BCAE1F3CCFF492B2645427EAA201F3E1CFE83FA229464E7C`；06 均为 `8EF50A68DAACF8056768038C92E11B46FF8556A6FB77BFC5E18B601C76ADF4FE` |
| Post-C = browserless 探索 + Build 渠道修复/永久废弃决策，且不阻塞 C1-C5 | **PASS** | `05:37`、`:65`；`06:255`、`:494`、`:496` |
| Build 不再作为 C1 阻塞项 | **PASS** | `05:28`；`06:206-210`、`:239`、`:255`、`:496` |
| 旧顺序 `P4 Build → P8/UI → Claude/Responses → 统计页 → P10` 明确作废/仅作历史 | **PASS** | `05:27`；`06:53`（“旧 … 顺序作废、仅作历史”）、`:257`、`:487` |
| D5 旧顺序已修，无旧顺序残留 | **PASS** | `06:53` 为 C1..C5 + Post-C；全仓 grep 未发现仍以 P4 Build 作为 C1 的现行表述 |
| `05` 仍不超过 100 行 | **PASS** | 目标/主仓均 100 行 |

## 3. B. 401 根因与日志噪音

### 3.1 根因与认证豁免

| 检查项 | 结论 | 证据 |
|---|---|---|
| 镜像 `HEALTHCHECK` 指向认证豁免的 `/health` | **PASS** | `Dockerfile:108-109`：`CMD curl -fsS http://localhost:8080/health \|\| exit 1` |
| `/health` 无需 API Key | **PASS** | `src/aistudio_api/api/routes_system.py:148-150`（`public_router`）；`api/app.py:455` 以无鉴权依赖方式挂载 `system_public_router` |
| `/health` 200 不写访问日志 | **PASS** | `api/app.py:695-697` |
| **`docker-compose.yml` 仍探测 `/v1beta/models`** | **FAIL（P1-1）** | `docker-compose.yml:22-23`：`test: ["CMD", "curl", "-f", "http://localhost:8080/v1beta/models"]`；compose `healthcheck` 覆盖镜像 `HEALTHCHECK` |
| 健康检查回归测试只覆盖 `Dockerfile` | **FAIL（P1-1 关联）** | `tests/unit/test_dockerfile_healthcheck.py:10-21` 只读 `Dockerfile`，不扫描 compose |
| 其他周期性模型探测 | **未发现**（除 P1-1） | 全仓 `*.py/*.yml/*.yaml/*.sh/Dockerfile` grep：仅 `docker-compose.yml:23`、README 示例与测试；`docker-compose.deploy.yml` 无 healthcheck，改用镜像 `HEALTHCHECK` |
| `/v1beta/models`、`/v1/models` 走只读鉴权入口 | **PASS** | `api/routes_models.py:79-83`、`:96-101`；`api/routes_openai.py:169-172`、`:190-193` 均挂 `require_api_key_or_web_session` |
| 生成端点仍走严格 `require_api_key` | **PASS** | `api/app.py:467` 的 `gemini_router`；`api/routes_openai.py:84-87`；`api/dependencies.py:268-282` |

### 3.2 5 分钟 per-(IP,path) 缺 Key 去重

| 检查项 | 结论 | 证据 |
|---|---|---|
| 窗口 300s、状态上限 1024 | **PASS** | `api/dependencies.py:23-24` |
| 首 WARN、窗口内重复 DEBUG（含已抑制次数） | **PASS** | `dependencies.py:76-95`（窗口判定）、`:102-131`（WARN/DEBUG 分支） |
| 按 `(client IP, path)` 隔离 | **PASS** | `dependencies.py:76-80`；测试 `tests/unit/test_api_auth.py:316-331` |
| 只读端点缺 Key 才去重；生成端点不去重 | **PASS** | `dependencies.py:205`（`read_only` 参数）、`:227`/`:243`（仅只读分支传 `dedup_missing_key=read_only`）、`:282`（只读入口传 `read_only=True`）；测试 `test_api_auth.py:333-347` |
| 非法 Key 不去重且不含密钥 | **PASS** | `dependencies.py:257-267`；测试 `test_api_auth.py:349-364` 断言 `wrong-token` 不出现在日志 |
| 线程安全 | **PASS** | 两个共享 dict 的读写均在 `_missing_key_warn_lock` 内（`dependencies.py:79-95`、`:97-99`） |
| 时间逻辑 | **PASS** | 单调时钟 `time.monotonic`（`:26`）；测试 `test_api_auth.py:297-314` 覆盖 299s 仍抑制、+2s 重新 WARN |
| UA ≤80 字符 | **PASS** | `dependencies.py:48-62`、`:109`；测试 `test_api_auth.py:253-277` 断言 80 个 `U` 在、81 个不在 |
| Referer 去 userinfo/query/fragment | **PASS** | `dependencies.py:64-73`；测试 `test_api_auth.py:253-277` 断言 `user:pass`、`topsecret`、`fragment` 均不出现 |
| 无敏感信息泄漏 | **PASS** | 日志仅 client IP、path、reason、截断 UA、净化 Referer；未记录 Authorization/API Key/Cookie |
| 状态清理 | **PASS（有残余风险 Q-1）** | `dependencies.py:88-95` 仅在 `len > 1024` 时清理早于 5 分钟窗口的键 |

## 4. C. 浏览器指纹

### 4.1 确定性、真实性与一致性

| 检查项 | 结论 | 证据 |
|---|---|---|
| 账号归一化 + 确定性 seed | **PASS** | `fingerprint.py:230-252`（`strip().casefold()`；auth file 用“父目录/文件名”身份；SHA-256 namespace seed） |
| 同账号稳定 | **PASS** | `tests/unit/test_browser_fingerprint.py:219-228`（`"  ACCOUNT-a "` 与 `"account-A"` 等价） |
| 不同账号不同指纹 | **PASS** | 同测试 `:226-227`；`test_auth_file_identity_falls_back_to_parent_directory:230-236` |
| Windows 桌面 WebGL 池 | **PASS** | `fingerprint.py:73-109`：NVIDIA/Intel/AMD ANGLE D3D11；`_validate_profile` 强制同家族且含 `Direct3D11`（`:337-348`） |
| 无 SwiftShader/Mesa/llvmpipe | **PASS** | `fingerprint.py:47-53` 禁用词表；`:327-336` 生成即拒绝；`test_profiles_never_expose_software_renderers:239-252` 遍历 512 个账号断言全部不含禁用词 |
| hardware/screen/DPR/canvas/audio/webdriver/chrome/plugins/languages 一致 | **PASS** | `fingerprint.py:111-127`（screen/DPR/CPU/内存/Chrome 主版本/Windows 版本/语言池）、`:140-176`（profile 字段）、`:179-198`（PDF 插件）、`:298-314`（UA/UA-CH）；`js/fingerprint_spoof.js:49-182`（navigator/screen/DPR）、`:185-201`（WebGL）、`:204-263`（canvas）、`:266-311`（audio）、`:314-362`（chrome）、`:365-383`（自动化标记清理） |
| 不启动真实浏览器/不发网络 | **PASS** | 测试以 `_FakePage`/`_FakeConnection` mock CDP（`test_browser_fingerprint.py:33-86`）；语法检查仅用 `bun`/`node`（`:331-352`） |

### 4.2 开关、注入顺序与失败策略

| 检查项 | 结论 | 证据 |
|---|---|---|
| `defaults/config.yaml` 默认 true | **PASS** | `defaults/config.yaml:28-29` |
| JSON Schema 默认 true | **PASS** | `config/config.schema.json:119-131`（`properties.browser.properties.fingerprint_spoof_enabled.default = true`） |
| 加载器默认 true + 非法/缺省回退 true | **PASS** | `config.py:306-332`（异常/缺键/非 bool → `True`） |
| 环境变量覆盖 | **PASS** | `config.py:313-317`：`AISTUDIO_FINGERPRINT_SPOOF` / `AISTUDIO_FINGERPRINT_SPOOF_ENABLED` |
| Settings 暴露开关 | **PASS** | `config.py:343` |
| 显式关闭时零注入（无脚本、无 metrics） | **PASS** | `PooledSession.bootstrap:87-91` 先判开关；`apply_fingerprint:438-445` 关闭时直接返回 `False`；测试 `test_disabled_config_skips_all_injection:162-175` 断言 `page.cdp.calls == []` |
| PooledSession 在页面创建后、Cookie/导航前注入 | **PASS** | `pooled_session.py:81` 建页 → `:87-91` 注入 → `:93-94` cookies/navigate；测试 `test_pooled_session_injects_fingerprint_before_cookies_and_navigation:108-132` 断言事件顺序 `script → metrics → cookies → navigate` 且方法顺序正确 |
| reassign/新账号获得新 payload | **PASS（代码路径）** | `instance_pool.py:421` 先更新 `slot.account_id`，`:478` 传入 `_session_factory(..., account_id=slot.account_id)`；`_bootstrap_runtime:462-497` 重建会话。测试仅在 bootstrap 层覆盖账号差异（`test_same_account_is_stable_and_account_swap_changes_payload:136-158`），池层无专门用例（见 N-1） |
| BrowserSession 切换同样在新页导航前注入 | **PASS** | `session.py:287-298`（建新页 → 注入 → `:300-305` cookies/navigate）；测试 `test_browser_session_rebind_swaps_fingerprint_for_new_account:285-328` 断言两次切换 payload 不同 |
| 脚本注册 fail-closed | **PASS** | `fingerprint.py:447-457` 注册失败抛 `RuntimeError`；`pooled_session.py:110-113` 关闭页面并重新抛出；实例池 dispose context 后重试/失败；`test_apply_fingerprint_disabled_or_failed_registration:256-266` |
| metrics 覆盖 best-effort | **PASS** | `fingerprint.py:459-469` 仅 WARN；`test_metrics_failure_is_logged_but_script_stays_registered:270-281` |
| JS 语法 | **PASS** | `bun build ...\/fingerprint_spoof.js --no-bundle` exit 0；`test_generated_script_parses_with_available_js_runtime:331-352` 未跳过（908 passed / 0 skipped） |
| 测试 mock CDP 且断言插入顺序/payload 差异 | **PASS** | `test_browser_fingerprint.py:33-56`（mock CDP）、`:119-122`（顺序）、`:148-154`（payload 差异） |

## 5. D. Tier 单源

| 检查项 | 结论 | 证据 |
|---|---|---|
| `BENEFIT_TIER_TO_HEADER` 仅在 `domain/model_access.py` | **PASS** | `domain/model_access.py:65-70`；全仓 grep 仅此一处定义 |
| `normalize_tier` / `tier_header_value` 仅在 domain | **PASS** | `domain/model_access.py:105-117`；全仓无第二定义 |
| cookie_parser 重复表/函数已删除 | **PASS** | `cookie_parser.py` 原 `BENEFIT_TIER_TO_HEADER`、`_CANONICAL_TIERS`、`normalize_benefit_tier`、`get_tier_header_value` 已移除（diff `-30` 行）；现存仅 `BENEFIT_TIER_TO_NAME`（int→name）与 `parse_benefit_tier_response` |
| transport 使用 domain primitive | **PASS** | `transport.py:15` import，`:185` 调用 |
| model_discovery 使用 domain primitive | **PASS** | `model_discovery.py:19` import，`:717`、`:764` 调用 |
| account_info_refresher 使用 domain 归一 | **PASS** | `account_info_refresher.py:29` import，`:625-629` 调用 `normalize_tier` |
| account_store / session_auth 使用 domain 归一 | **PASS** | `account_store.py:651`、`:657-659`；`session_auth.py:13`、`:99` |
| 映射精确，Free → None | **PASS** | `model_access.py:65-70`（无 Free 项）；`:110-117`；`tests/unit/test_cookie_import.py:384-392` 覆盖 Free/Pro/Ultra/Ultra_20X/Plus 与小写 |
| Free 显式 tier 不被 Pro 覆盖 | **PASS** | `tests/unit/test_stream_transport.py:47-55` |
| 生成/无效 Key/去重不受 Tier 改动影响 | **PASS** | 全量 908 passed；Tier 变更只涉及映射与归一，未触及鉴权分支 |

## 6. 门禁实跑（独立复跑）

| 命令 | 结果 |
|---|---|
| `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q` | `908 passed in 11.94s`（0 failed / 0 skipped） |
| `uv run ruff check .` | `All checks passed!` |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` |
| `bun build src/aistudio_api/infrastructure/browser/js/fingerprint_spoof.js --no-bundle` | exit `0`（无输出） |
| `git diff --check` | exit `0`（仅有 LF→CRLF 提示，无 whitespace error） |
| `git diff --cached --check` | exit `0` |
| `git ls-files config.yaml` | 空（exit `0`）；`check-ignore` → `.gitignore:15:/config.yaml` |
| `git diff --cached --name-only` | 空 |
| `git rev-parse HEAD` | `0fcc816dc65b38ff4caf702efc95e7b08fcc791f` |

跨仓文档 SHA256：

| 文件 | 目标树 | 主仓 |
|---|---|---|
| `05-restart-map.md` | `492EE26B41BE1803BCAE1F3CCFF492B2645427EAA201F3E1CFE83FA229464E7C` | 同左 |
| `06-decision-log.md` | `8EF50A68DAACF8056768038C92E11B46FF8556A6FB77BFC5E18B601C76ADF4FE` | 同左 |

## 7. 全部未关闭项（P0/P1/P2/Q/N）

### P0

无。

### P1

- **P1-1（未关闭，判 CONDITIONAL 的唯一 P1）**：`docker-compose.yml:22-23` 仍以 30s 间隔探测 `http://localhost:8080/v1beta/models`。Compose 的 `healthcheck` 会覆盖镜像 `Dockerfile:108-109` 已改好的 `HEALTHCHECK`，因此使用 `docker-compose.yml` 部署时仍会周期性产生未带 Key 的 401。新增的 5 分钟去重（`api/dependencies.py:23-132`）已把日志从每 30s 一条 WARN 降为“首条 WARN + 后续 DEBUG”，显著缓解噪音，但 C1 的根因口径 “HEALTHCHECK 改 `/health`” 未覆盖该清单，且 `tests/unit/test_dockerfile_healthcheck.py:10-21` 只扫描 `Dockerfile`，无法捕获。修复方向：把 `docker-compose.yml` healthcheck 改为 `/health`，并把测试扩展到 compose 文件（或断言全仓无 `/v1beta/models` healthcheck）。`docker-compose.deploy.yml` 无 healthcheck，使用镜像 `HEALTHCHECK`，不受此问题影响。

### P2

- **P2-1**：`BrowserSession.import_cookies` 的临时页（`session.py:543-548`）调用 `bootstrap_google_session`（`session_auth.py:428-440`），会先导航 `accounts.google.com` 再导航 AI Studio，但该页由 `create_new_page` 创建后未调用 `apply_fingerprint`，因此该真实导航路径没有指纹注入。该路径属控制面 Cookie 导入，未在 C1 明确要求的 `PooledSession` / `transition_to_account` 两条主路径内；若“按账号指纹加固”应覆盖所有 AI Studio 导航，则这是一个缺口。
- **P2-2**：若 C1 声称完整关闭 B7-P1/B7-06，则“发现单源”尚未完成。当前仅 header 映射/归一单源；`account_info_refresher.py:59` 的 `TIER_BY_CODE = {1: "Pro", 2: "Ultra", 3: "Plus"}` 与 `cookie_parser.py:32` 的 `BENEFIT_TIER_TO_NAME`（含 0→Free）仍是两张 int→tier 表；发现路径仍有多处：`cookie_parser.probe_account_benefit_tier:539`、`model_discovery._fetch_via_page:666`、`_fetch_via_http:739`、`session_auth.extract_account_tier:161`。B7 报告 `REV-B7-architecture-coupling-audit.md:256` 的验收要求“ListModels/BenefitTier 各 1 实现；Tier 表 1 份；paid 判定 1 入口”。按本次审计 D 项的窄口径（仅 `BENEFIT_TIER_TO_HEADER`/归一单源）则 PASS；按 05 的“B7-P1 Tier/发现单源收敛”宽口径则未完全关闭。

### Q（待作者确认/设计取舍）

- **Q-1**：`api/dependencies.py:88-95` 只在 `len(_missing_key_warn_last) > 1024` 时清理早于 5 分钟窗口的键；若 5 分钟窗口内出现大量不同 `(IP,path)`，所有条目都“新鲜”，字典可暂时超过 1024 并随请求增长。条目很小、反向代理下 IP 通常相同，实际影响低；但这不是硬上限，建议硬驱逐/LRU 或在超限时直接跳过记录。
- **Q-2**：`js/fingerprint_spoof.js:139-162` 将 `navigator.plugins`/`mimeTypes` 暴露为普通 Array（无 `PluginArray`/`Plugin`/`MimeType` 原型），`:318` 的 `chrome.runtime` 也近乎空对象；基于 `instanceof`/属性描述符的检测仍可区分。WebGL/canvas/audio 噪声是确定性的且厂商/渲染器同族，整体可用，但“真实 Windows Chrome”保真度是部分的，属残余检测风险。
- **Q-3**：`resolve_fingerprint_spoof_enabled()` 每次 `PooledSession.bootstrap` 都同步读取并解析 YAML（`config.py:318-329`、`pooled_session.py:87`），在事件循环中形成一次小文件阻塞读；建议像其他配置一样按 mtime 缓存。

### N（备注/测试或文档缺口）

- **N-1**：没有 `InstancePool.activate/reassign` 级别的测试断言新 `slot.account_id` 被传入 `PooledSession.bootstrap`。代码路径已核实（`instance_pool.py:421`、`:478`），但现有测试只覆盖 bootstrap 层（`test_browser_fingerprint.py:136-158`）与 BrowserSession 层（`:285-328`）。建议补一个池层用例。
- **N-2**：`.env.example` 未列出 `AISTUDIO_FINGERPRINT_SPOOF` / `AISTUDIO_FINGERPRINT_SPOOF_ENABLED`；加载器已支持（`config.py:313-317`），但运维不易发现。仅文档项。
- **N-3**：`tests/unit/test_dockerfile_healthcheck.py` 的断言范围仅 `Dockerfile`，正是 P1-1 未被捕获的原因；建议与 P1-1 一并扩展。

## 8. 残余风险

- **部署清单差异**：镜像 `HEALTHCHECK` 已正确，`docker-compose.deploy.yml` 无覆盖；但 `docker-compose.yml` 仍保留旧探测（P1-1）。
- **离线边界**：本次严格遵守离线只读，未在真实 Chromium/真实账号/真实 IP 上验证 JS 注入效果；`Page.addScriptToEvaluateOnNewDocument` 与 `Emulation.setDeviceMetricsOverride` 的实际生效、以及 BotGuard 对指纹的接受度，只能由后续获批实机阶段验证（AGENT.md §4.6）。
- **指纹池有限**：GPU 7 种、屏幕 5 种、CPU 4 种、内存 3 种；不同账号虽 seed 不同，但可见属性会在有限集合内碰撞。这是可接受的设计取舍，不是缺陷。
- **metrics best-effort**：`Emulation.setDeviceMetricsOverride` 失败时 JS 仍覆盖 `screen`/DPR/inner/outer，但原生布局视口可能不一致；已按设计降级为 WARN。
- **控制面临时页**：`import_cookies` 的 Google/AI Studio 导航未注入指纹（P2-1），若该路径会被上游检测，需在后续补注入。
- **去重状态上限**：Q-1 的软上限在极端多 IP/多 path 场景下可能短期增长。
- **B7 宽口径**：Tier header 单源已完成，但“发现单源/单张 int→tier 表”仍未完成（P2-2）。

## 9. 报告副本与哈希

| 副本 | 路径 |
|---|---|
| 目标树 | `D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream\docs\dev\rebuild\domains\P3-browser\reviews\REV-C1-fingerprint-and-auth-noise.md` |
| 主仓 | `D:\LLM\self_programming\aistudio-api\docs\dev\rebuild\domains\P3-browser\reviews\REV-C1-fingerprint-and-auth-noise.md` |

两份内容字节一致；SHA256 见交付回复。

**未提交 / 未 push。** 本报告只新增两个副本，未修改源码、测试、配置或既有文档，未执行 `git add` / `git commit` / `git push`。

## 10. 关闭复核（2026-11-01，第二轮独立只读复核）

- 复核对象：目标树 `D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`，HEAD `0fcc816dc65b38ff4caf702efc95e7b08fcc791f`（未提交）；主仓 `D:\LLM\self_programming\aistudio-api`，HEAD `2da21eddf2a9a5f88c4650e623dbfdbcb485b5ef`。
- 边界：严格离线只读；未启动服务/浏览器/Docker/网络；未修改 `src/`、`tests/`、`config/`、`defaults/` 及既有文档；未 stage/commit/push。唯一写入为本报告两份副本的本节追加。
- 目标状态：32 条 = 27 个已跟踪修改 + 5 个未跟踪（本报告 + `fingerprint.py`、`fingerprint_spoof.js`、`test_browser_fingerprint.py`、`test_dockerfile_healthcheck.py`）；暂存区为空。

### 10.1 门禁实跑（独立复跑）

| 命令 | 结果 |
|---|---|
| `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q` | `912 passed in 11.83s`（0 failed / 0 skipped）；`--collect-only -q` 亦 `912 tests collected` |
| `uv run ruff check .` | `All checks passed!`（0 error） |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` |
| `bun build src/aistudio_api/infrastructure/browser/js/fingerprint_spoof.js --no-bundle` | exit `0` |
| `git diff --check` / `git diff --cached --check` | 均 exit `0`（仅 LF→CRLF 提示，无 whitespace error） |
| `git ls-files config.yaml` | 空；暂存区 `git diff --cached --name-only` 为空 |
| `git rev-parse HEAD` | `0fcc816dc65b38ff4caf702efc95e7b08fcc791f`（复核前后未变） |

### 10.2 逐项裁定

| 项 | 裁定 | 证据 |
|---|---|---|
| P1-1 | **RESOLVED** | `docker-compose.yml:23` 已改为 `curl -fsS http://localhost:8080/health`；`Dockerfile:108-109` 同为 `/health`；`docker-compose.deploy.yml` 无 healthcheck（沿用镜像 `HEALTHCHECK`）。全仓 `*.yml/*.yaml/Dockerfile*/*.sh/*.ps1` 扫描无 `/v1beta/models` 或 `/v1/models` 探测。回归测试扩展至三份清单并解析 compose：`tests/unit/test_dockerfile_healthcheck.py:12-13`、`:38-52`、`:55-64`。 |
| P2-1 | **RESOLVED** | `BrowserSession.import_cookies` 在临时页创建后、`set_cookies` 与 `bootstrap_google_session`（内部先 `accounts.google.com` 再 AI Studio 导航，`session_auth.py:428-442`）之前注入指纹：`session.py:543-554`（身份取 `target_auth`，`:549`）。测试 `tests/unit/test_browser_fingerprint.py:368-416` 断言事件顺序 `fingerprint.script → fingerprint.metrics → cookies.set → bootstrap.google → cookies.save`，且 `page.cdp.calls[0]` 的 `source` 等于 `build_fingerprint_script(build_fingerprint_profile(auth_file=target_auth))`。 |
| P2-2 | **PARTIAL / REMAINS** | int→tier 表已单源：`domain/model_access.py:66-70`（`BENEFIT_TIER_BY_CODE`）、`:117-127`（`benefit_tier_from_code`）；`cookie_parser.py:49` 与 `account_info_refresher.py:627-631` 已改用；`account_info_refresher.py:59` 的 `TIER_BY_CODE` 已删除。**但发现实现仍重复**：ListModels 两份（`model_discovery.py:666` page XHR、`:739` HTTP）；BenefitTier 多份（`cookie_parser.py:532`、`session_auth.py:161`、`account_info_refresher.py:532`、`:544`）。B7-P1“各 1 实现”宽口径未闭合。 |
| N-1 | **RESOLVED** | 池层 reassign 用例：`tests/unit/test_browser_fingerprint.py:420-489` 断言 `activate(account-A)` → `activate(account-B)` 后 factory 依次收到新 `account_id`/`auth_file`，两页注入脚本分别对应各自账号且不同；代码路径 `instance_pool.py:421`、`:472-478`。 |
| N-2 | **RESOLVED** | `.env.example:19-20` 新增 `AISTUDIO_FINGERPRINT_SPOOF_ENABLED=1`；加载器同时支持 `AISTUDIO_FINGERPRINT_SPOOF`（`config.py:315`）。 |
| N-3 | **RESOLVED** | 健康检查测试扫描 `Dockerfile`、`docker-compose.yml`、`docker-compose.deploy.yml`，并断言禁止 `/v1beta/models`、`/v1/models`：`tests/unit/test_dockerfile_healthcheck.py:12-13`、`:55-64`。 |
| Q-1 | **REMAIN** | `api/dependencies.py:88-93` 仍为软上限：仅 `len > 1024` 时清理早于 300s 的键；窗口内大量不同 `(IP,path)` 仍可超限增长。 |
| Q-2 | **REMAIN** | `fingerprint_spoof.js:139-162` 仍以普通数组模拟 plugins/mimeTypes（无 `PluginArray`/`Plugin`/`MimeType` 原型）；`:313-314` 的 `window.chrome` 仍为近空对象。 |
| Q-3 | **REMAIN** | `resolve_fingerprint_spoof_enabled()` 每次同步读盘解析 YAML（`config.py:306-329`，读盘在 `:322`）；`pooled_session.py:87` 每次 bootstrap 调用，未加 mtime 缓存。 |

### 10.3 全部未关闭项（逐条列出）

**P0**：无。

**P1**：无（P1-1 已关闭）。

**P2（剩余 1 项，部分关闭）**：
- **P2-2（原文）**：若 C1 声称完整关闭 B7-P1/B7-06，则“发现单源”尚未完成。当前仅 header 映射/归一单源；`account_info_refresher.py:59` 的 `TIER_BY_CODE = {1: "Pro", 2: "Ultra", 3: "Plus"}` 与 `cookie_parser.py:32` 的 `BENEFIT_TIER_TO_NAME`（含 0→Free）仍是两张 int→tier 表；发现路径仍有多处：`cookie_parser.probe_account_benefit_tier:539`、`model_discovery._fetch_via_page:666`、`_fetch_via_http:739`、`session_auth.extract_account_tier:161`。B7 报告 `REV-B7-architecture-coupling-audit.md:256` 的验收要求“ListModels/BenefitTier 各 1 实现；Tier 表 1 份；paid 判定 1 入口”。按本次审计 D 项的窄口径（仅 `BENEFIT_TIER_TO_HEADER`/归一单源）则 PASS；按 05 的“B7-P1 Tier/发现单源收敛”宽口径则未完全关闭。
  - **现状更新**：int→tier 两张表已合并为 `domain/model_access.py:66-70` 单表（`account_info_refresher.py:59` 旧表已删除，`cookie_parser.py:32` 旧 `BENEFIT_TIER_TO_NAME` 已删除），故此子项已关闭；**但 ListModels 仍 `model_discovery.py:666` / `:739` 两份，BenefitTier 仍 `cookie_parser.py:532` / `session_auth.py:161` / `account_info_refresher.py:532,544` 多份**，宽口径仍未闭合。

**Q（剩余 3 项）**：
- **Q-1（原文）**：`api/dependencies.py:88-95` 只在 `len(_missing_key_warn_last) > 1024` 时清理早于 5 分钟窗口的键；若 5 分钟窗口内出现大量不同 `(IP,path)`，所有条目都“新鲜”，字典可暂时超过 1024 并随请求增长。条目很小、反向代理下 IP 通常相同，实际影响低；但这不是硬上限，建议硬驱逐/LRU 或在超限时直接跳过记录。**现状**：逻辑未变，现为 `dependencies.py:88-93`。
- **Q-2（原文）**：`js/fingerprint_spoof.js:139-162` 将 `navigator.plugins`/`mimeTypes` 暴露为普通 Array（无 `PluginArray`/`Plugin`/`MimeType` 原型），`:318` 的 `chrome.runtime` 也近乎空对象；基于 `instanceof`/属性描述符的检测仍可区分。WebGL/canvas/audio 噪声是确定性的且厂商/渲染器同族，整体可用，但“真实 Windows Chrome”保真度是部分的，属残余检测风险。**现状**：未变，`chrome` 段现位于 `:313-314`。
- **Q-3（原文）**：`resolve_fingerprint_spoof_enabled()` 每次 `PooledSession.bootstrap` 都同步读取并解析 YAML（`config.py:318-329`、`pooled_session.py:87`），在事件循环中形成一次小文件阻塞读；建议像其他配置一样按 mtime 缓存。**现状**：未变，函数现为 `config.py:306-329`（读盘 `:322`）。

**N**：无（N-1/N-2/N-3 均已关闭）。

### 10.4 最终结论

**Verdict: PASS。**

P1-1、P2-1 已由源码与新增测试双向坐实；N-1/N-2/N-3 已闭环；全部门禁独立复跑通过（912 passed / ruff 0 / pyright 0 / bun exit 0 / diff check 0 / `config.yaml` 未跟踪 / 暂存区空）。残留 P2-2（发现实现仍重复：窄口径已满足、宽口径部分关闭）及 Q-1/Q-2/Q-3 均为非阻塞项，不改变 C1 通过结论；建议在 C2 前合并 ListModels/BenefitTier 的重复实现并处理 Q-1/Q-3。

**未提交 / 未 push。** 本轮仅向目标树与主仓两份报告副本追加本节，未执行 `git add` / `git commit` / `git push`。

## 11. 关闭复核（2026-11-01，第三轮独立只读复核）

- 复核对象：目标树 `D:\LLM\self_programming\aistudio-api\.kilo\worktrees\rebuild-upstream`，HEAD `0fcc816dc65b38ff4caf702efc95e7b08fcc791f`（未提交）；主仓 `D:\LLM\self_programming\aistudio-api`，HEAD `2da21eddf2a9a5f88c4650e623dbfdbcb485b5ef`。
- 边界：严格离线只读；未启动服务/浏览器/Docker/网络；未修改 `src/`、`tests/`、`config/`、`defaults/` 及既有文档；未 stage/commit/push。唯一写入为本报告两份副本的本节追加。
- 目标状态：34 条 = 28 个已跟踪修改 + 6 个未跟踪（本报告、`benefit_tier.py`、`fingerprint.py`、`fingerprint_spoof.js`、`test_browser_fingerprint.py`、`test_dockerfile_healthcheck.py`）；暂存区为空；`config.yaml` 未跟踪。

### 11.1 门禁实跑（独立复跑）

| 命令 | 结果 |
|---|---|
| `$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; uv run pytest -q` | `915 passed in 12.63s`（0 failed / 0 skipped） |
| 定点复核 4 例（Q-1/Q-2/Q-3/P2-2 相关） | `4 passed, 19 deselected in 0.68s`；Q-2 的 bun/node 执行用例未跳过 |
| `uv run ruff check .` | `All checks passed!`（0 error） |
| `uv run pyright src tests` | `0 errors, 0 warnings, 0 informations` |
| `bun build src/aistudio_api/infrastructure/browser/js/fingerprint_spoof.js --no-bundle` | exit `0` |
| `git diff --check` / `git diff --cached --check` | 均 exit `0`（仅 LF→CRLF 提示，无 whitespace error） |
| `git ls-files config.yaml` | 空（exit `0`）；`git diff --cached --name-only` 为空 |
| `git rev-parse HEAD` | `0fcc816dc65b38ff4caf702efc95e7b08fcc791f`（复核前后未变） |

### 11.2 逐项裁定

| 项 | 裁定 | 证据（file:line）与门禁 |
|---|---|---|
| Q-1 | **RESOLVED** | `api/dependencies.py:24` 上限常量 `1024`；`:87-104` 先按 300s 窗口清理陈旧键，再以 `while len(_missing_key_warn_last) >= _MISSING_KEY_STATE_LIMIT` 逐条淘汰**最旧时间戳**（`:95-100`），留出一格后插入（`:102`），并在淘汰时同步清理 `_suppressed`（`:94`、`:100`），因此是硬上限而非 `>` 软上限。测试 `tests/unit/test_api_auth.py:333-346` 写入 1100 个唯一 `(IP,path)`，断言 `len == 1024`、索引 0..75 被淘汰、76..1099 保留。门禁：`pytest` 915 passed / 0 skipped。 |
| Q-2 | **RESOLVED** | `infrastructure/browser/js/fingerprint_spoof.js:94-304` 现从 `window`/`globalThis` 取原生 `Plugin`/`PluginArray`/`MimeType`/`MimeTypeArray` 构造器（`:100-140`），以 `Object.create(nativeProto)` 建立对象（`:142-163`）、`createCollection` 建立带 `length`/索引/`item`/`namedItem`/`refresh`/`Symbol.iterator` 的集合（`:174-230`），插件与 MIME 对象分别继承原生原型（`:241-302`），最终挂到 `navigator.plugins`/`navigator.mimeTypes`（`:303-304`）。`window.chrome` 改为非破坏式合并：保留宿主已有对象与键，仅补齐缺失键（`:455-534`，`fillChromeKey` `:463-493`），仅在原本不存在时才定义 `window.chrome`（`:526-533`）。测试 `tests/unit/test_browser_fingerprint.py:396-517` 不是静态断言，而是用 `shutil.which("bun") or shutil.which("node")` 取真实 JS 运行时、经 `subprocess.run`（`:509-515`）执行脚本，断言 `instanceof PluginArray/MimeTypeArray/Plugin/MimeType`、`item(-1)===null`、`namedItem`、迭代器、插件/MIME 互链与 `window.chrome === nativeChrome`、`runtime.id`/`custom`/`loadTimes` 原样保留、`app`/`csi` 补齐；兜底场景另断言普通对象契约。门禁：该用例未跳过（bun 可用，0 skipped）且通过；`bun build` exit `0`。 |
| Q-3 | **RESOLVED** | `config.py:80-82` 新增 `_FINGERPRINT_SPOOF_CACHE: dict[path, ((mtime_ns,size), enabled)]` 与锁；`config.py:311-362` 每次先动态求值环境变量（`:324-328`，不进缓存），再 `path.stat()` 取 `(st_mtime_ns, st_size)`（`:330-339`），命中缓存直接返回（`:340-343`），`OSError` 时移除缓存并保守返回 `True`（`:331-337`），解析后写回（`:360-361`）。测试 `tests/unit/test_browser_fingerprint.py:217-265` 统计 `yaml.safe_load` 调用：首次 1 次、第二次仍 1 次；`os.utime` 推进 mtime 后 2 次；改写内容后 3 次；`AISTUDIO_FINGERPRINT_SPOOF_ENABLED` 在 `0`/`1` 间切换不增加解析次数（保持 3），同时证明环境变量动态生效。门禁：`pytest` 915 passed / 0 skipped。 |
| P2-2 | **RESOLVED** | 单一 BenefitTier 信封解析器：`infrastructure/account/benefit_tier.py:44-81` `parse_benefit_tier_envelope`；展示策略 `BenefitTierEnvelope.display_tier`（`:31-41`）保留两调用面差异（未知 code 默认 Free，refresher 用 `include_unknown_code=True` 输出 `unknown-<code>`）。三个消费方均已改用：`cookie_parser.py:16-18`、`:35-41`（调用点 `:563`、`:627`）；`session_auth.py:15-17`、`:226`；`account_info_refresher.py:29-31`、`:279`、`:418`。Tier 表 domain 单源：`domain/model_access.py:66-71` `BENEFIT_TIER_BY_CODE` 与 `:120-127` `benefit_tier_from_code`；全仓 grep 无 `TIER_BY_CODE`/`BENEFIT_TIER_TO_NAME` 等其他 int→tier 表。ListModels 按允许口径保留传输分离：`model_discovery.py:666-737`（page XHR）与 `:739-778`（HTTP）共用 `_parse_raw_models`（`:780`），未复制 BenefitTier 解析。行为变体测试：`tests/unit/test_cookie_import.py:377-406`（`[]`/`[null]`/`[,1]`/`)]}'`/bool/字符串/畸形/未知 code 的 Free 与 envelope 策略）与 `tests/unit/test_account_info_refresh.py:111-137`（0/1/2/3/9/null/畸形 → Free/Pro/Ultra/Plus/unknown-9）。门禁：`pytest` 915 passed / 0 skipped；`ruff` 0；`pyright` 0。 |

### 11.3 全部未关闭项（逐条列出）

- **P0**：无。
- **P1**：无。
- **P2**：无（P2-2 已在 §11.2 关闭；§10.3 的 P2-2 描述由本节取代）。
- **Q**：无（Q-1/Q-2/Q-3 均已在 §11.2 关闭）。
- **N**：无。

### 11.4 最终结论

**Verdict: PASS。**

Q-1/Q-2/Q-3/P2-2 均有源码位置与新增/定点测试双向坐实；全部门禁独立复跑通过（915 passed / 0 failed / 0 skipped、ruff 0、pyright 0、bun build exit 0、diff check 0、`config.yaml` 未跟踪、暂存区空、HEAD 未变）。未关闭 P1/P2/Q/N 为 0。

**未提交 / 未 push。** 本轮仅向目标树与主仓两份报告副本追加本节，未执行 `git add` / `git commit` / `git push`。追加后两份副本字节一致，SHA256 见交付回复。

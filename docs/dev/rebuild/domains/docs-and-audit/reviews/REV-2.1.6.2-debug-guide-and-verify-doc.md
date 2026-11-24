# REV-2.1.6.2 — 2.1.6 文档审查（DEBUG_GUIDE / VERIFY_ACCOUNT / AGENT / changelog）

- 审查对象：`docs/DEBUG_GUIDE.md`（新增）、`docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md`（新增）、`AGENT.md`（§4.6.1 文档指针与 verify 实机约束；§10.3 tag/pyproject 同步规则）、`docs/changelog.md`（新增 2.1.6 章节）
- 参照基线：tag `2.1.5`（`c5903ef`）→ 当前工作树（已提交 HEAD `3a72af6` + 未提交改动）
- 审查性质：**只读**；除本文件外未修改任何源码/测试/配置/既有文档
- 事实标准：当前工作树代码；文档与代码冲突时以代码为准
- **结论：FAIL**（两份新文档中仍把已移除开关写成可用逃生口，且隔离调试步骤在真实仓库配置下会拉起实例真实浏览器；另有系统性行号漂移与 changelog 失实条目，均属交付前必须修正）

问题数量：**P0 = 2，P1 = 7，P2 = 10，Q = 4，N = 3**

---

## 一、门禁执行记录

| 门禁 | 命令 | 结果 |
|---|---|---|
| 空白/冲突检查 | `git diff --check` | 无输出，退出码 0 ✅ |
| 路径存在性 | `Test-Path`（见下表） | 引用路径均存在；`waa-signer/bin/waa-signer` 不存在，与 DEBUG_GUIDE §5.3 的“本机缺失”陈述一致 ✅ |
| 已移除开关 | `git grep -n "MINIMAL_FLAGS\|IMPORT_KEEP_OSID"` | 仅命中测试的“已移除”断言：`tests/unit/test_cookie_import.py:742`、`tests/unit/test_verify_account_service.py:3120`；源码 0 命中 ✅ |
| 文档侧开关扫描 | 工作树全文（含 untracked） | `docs/DEBUG_GUIDE.md:77,124,366` 写成“已移除” ✅；`docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md:171,234` 写成“可用” ❌（见 P0-1） |
| pytest | 未执行 | 本次为文档只读审查，无代码改动，按任务要求不跑 pytest |
| 服务/浏览器/外网/ssh/扫码 | 未执行 | 全程离线只读 |

路径存在性明细（`Test-Path`）：

`docs/DEBUG_GUIDE.md` ✅、`docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md` ✅、`AGENT.md` ✅、`docs/changelog.md` ✅、`docs/ARCHITECTURE.md` ✅、`docs/BOTGUARD_VERIFICATION_CHAIN.md` ✅、`docs/dev/rebuild/06-decision-log.md` ✅、`docs/dev/rebuild/08-09-decision-record.md` ✅、`docs/dev/rebuild/domains/P1-account/research/verify-account/EVIDENCE.md` ✅、`docs/dev/rebuild/domains/docs-and-audit/reviews/` ✅、`web/src/components/modals/VerifyAccountModal.vue` ✅、`src/aistudio_api/application/verify_account_service.py` ✅、`src/aistudio_api/api/routes_accounts.py` ✅、`src/aistudio_api/main.py` ✅、`.cloakbrowser` ✅、`waa-signer/bin/waa-signer` ❌（文档已说明本机缺失，非坏引用）。

> 备注：两份新文档仍为 `git status` 的 `??`（untracked）；文件在磁盘上可打开，但尚未入库（与 `REV-2.1.6.1` P1-1 同一问题，本报告 P1-7）。

---

## 二、引用核对表

判定图例：✅ 行号与内容相符；❌ 行号失效或内容不符；⚠️ 部分相符/可接受但建议改。

### A. `docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md`

“实际”列均指当前工作树 `src/aistudio_api/application/verify_account_service.py`（另有标注者除外）。

| 文档行 | 文档引用 | 实际（工作树） | 判定 |
|---|---|---|---|
| 38 | `EVIDENCE.md:147-153` | 147-153 为 `GetUserVerificationStatus` 状态表 | ✅ |
| 65, 226 | `:2622-2692` `confirm_verified` | `:2698-2768` | ❌ 偏移 +76 |
| 82, 213 | `:93-97`、`:97`（45s） | 注释 `:96-99`，常量 `:100` | ❌ |
| 82, 214 | `:101-104` RPC 超时/间隔 | `:104-107` | ❌ |
| 86, 224 | `:1925-1996` 浏览器启动/导航 | `:1960-2025` | ❌ 偏移 +35 |
| 86, 225 | `:1969-1984` `bootstrap_google_session` | 调用 `:2012-2020` | ❌ |
| 87, 217 | `:279-307` `_READ_QR_JS` | `:299-327` | ❌ |
| 87, 229 | `:2805-2808` bframe 帧筛选 | `:2884` | ❌ |
| 88, 215 | `:195` 短码正则 | `:195` | ✅ |
| 88 | `:2858-2861` 拼 QR URL | `:2934-2937` | ❌ |
| 89, 212 | `:92` TTL 300s | `:95` | ❌ |
| 89, 227 | `:2751-2784` `_wait_for_qr` | `:2827-2860` | ❌ 偏移 +76 |
| 90, 226 | `:2683-2692` 数组解析 | `:2763-2768` | ❌ |
| 107 | `:1969-1984` 会话引导 | `:2012-2020` | ❌ |
| 147, 235 | `:3144-3174` `_apply_verify_compat_args` | `:3205-3235` | ❌ 偏移 +61 |
| 147 | `:3145-3163` 注释记录 | `:3208-3223` | ❌ |
| 147, 233 | `:3113` compat 调用点 | `:3189` | ❌ |
| 151-153 | `:3154-3156` | `:3215-3217` | ❌ |
| 154 | `:3157-3158`；`browser_engine.py:185` | `:3218-3219`；`browser_engine.py:185` | ❌ / ✅ |
| 155 | `:3159` | `:3220` | ❌ |
| 156 | `:3160-3162` | `:3221-3223` | ❌ |
| 158 | `:3168`、`:3172-3173` | `:3229`、`:3233-3234` | ❌ |
| 162 | `verify_account_service.py:3126`；`browser_engine.py:165` | `:3126` 是 `_VERIFY_HEADED_ENV` 读取，不是“最小参数保留 site-isolation”；`browser_engine.py:165` 正确 | ❌ / ✅ |
| 163 | `browser_engine.py:156-166`；`:3095-3112` | 前者 ✅；后者实际 `:3171-3189` | ⚠️ |
| 167, 231 | `:3074-3077` 不覆盖 UA | `:3150-3152` | ❌ |
| 171, 234 | `AISTUDIO_VERIFY_MINIMAL_FLAGS=1`；`:3118-3132`；`:193`；`:3042-3051` | 该开关已移除，无对应实现；`:3118-3127` 才是有头开关 | ❌❌（见 P0-1） |
| 180, 222 | `:1598-1605` 会话内 2/3 清锁 | `:1627-1634` | ❌ |
| 180, 223 | `:1641-1658` 清锁落盘 | `:1670-1696` | ❌ |
| 181, 221 | `:1326-1341` 独立检查 2/3 | `:1354-1362` | ❌ |
| 185, 216 | `:199-211` 成功/失败文案 | `:199-211` | ✅ |
| 185, 219 | `:748-761` 页面判定 | `:771-784` | ❌ |
| 186 | `:1491-1505`、`:320-342` | 页面状态日志 `:1512-1533`；`_DESCRIBE_PAGE_JS` `:343-361` | ❌ |
| 190, 236 | `routes_accounts.py:1340-1372`；`verify_account_service.py:1171-1179`；`:3293-3297` | 路由 `:1339-1371`；`check()` `:1199-1250`；status driver `:3354-3358` | ❌ |
| 191 | `:1343-1356` payload | `:1379-1384` | ❌ |
| 192, 240 | `VerifyAccountModal.vue:864-865` | 两按钮语义文案确在 864-865 | ✅ |
| 218 | `:577-586` 状态枚举 | `:597-606` | ❌ |
| 237-239 | `browser_engine.py:148-186`、`:165`、`:185` | 均正确 | ✅ |
| 241 | `test_verify_account_service.py:1235`；`:867,2865` | 45s 断言 `:1242-1252`；site-isolation `:877`、`:2903`、`:3131` | ❌ |

### B. `docs/DEBUG_GUIDE.md`

| 文档行 | 文档引用 | 实际（工作树） | 判定 |
|---|---|---|---|
| 41 | `_PAGE_SNAPSHOT_JS :309-336` | `:329-338` | ❌ |
| 41 | `_read_bframe_qr :2786`、`:2808-2812` | `:2862`、`:2884` | ❌ |
| 57 | `:3144-3174` | `:3205-3235` | ❌ |
| 58, 59 | `_FAILURE_MARKERS :205-209`；`_SUCCESS_MARKERS :199-204` | 失败 `:205-211`；成功 `:199-204` | ⚠️ / ✅ |
| 65 | `confirm_verified :2622-2654` | `:2698-2730` | ❌ |
| 73 | `:1863-1867`、`:3116-3117`、`:2626-2627` | `:1898-1902`、`:3192-3193`、`:2702-2703` | ❌ |
| 74, 114 | `:3042-3051` | `:3118-3127` | ❌ |
| 75, 232 | `:3095-3111`、`:3074-3077`、`:3144-3174` | `:3171-3189`、`:3150-3152`、`:3205-3235` | ❌ |
| 76, 115 | `:1858-1862`、`:1901-1917` | `:1893-1897`、`:1936-1953` | ❌ |
| 89 | `:2002-2016`、`:1998-2000` | `:2038-2064`、`:2034-2036` | ❌ |
| 90 | `:2050-2072`、`NET_TRACE_MAXLEN :110` | `:2098-2120`、`:113` | ❌ |
| 91 | `NET_BODY_URL_TOKENS :142-150`、`:151` | `:145-153`、`:154` | ❌ |
| 92, 117 | `:1763-1765`、`:2112-2136` | `:1803-1805`、`:2160-2184` | ❌ |
| 92, 118 | `AISTUDIO_VERIFY_CAPTURE_DIR :1784-1786` | `:1819-1821` | ❌ |
| 116 | `:1863-1867`、`:3116-3117`、`:2626-2627` | `:1898-1902`、`:3192-3193`、`:2702-2703` | ❌ |
| 119 | `model_defaults.py:411-420`、`:555-560` | `:411-420`、`:555-561` | ✅ |
| 120 | `app.py:114-123` | `:114-123` | ✅ |
| 121 | `config.py:71-72`、`:145-153` | 正确 | ✅ |
| 122 | `config.py:417`、`account_store.py:293-303` | 正确 | ✅ |
| 123 | `exit_pool/config.py:108-125` | 正确 | ✅ |
| 124 | `verify_account_service.py:1768-1770`（原实现） | 工作树该处为 `_timer` 区域；被删代码只存在于 HEAD | ⚠️ 见 P2-9 |
| 125 | `cookie_parser.py:143-148`（原实现） | 工作树该处为 `ACCOUNT_HOST_ONLY_COOKIES` | ⚠️ 见 P2-9 |
| 129 | `config.py:393` | 正确 | ✅ |
| 130 | `config.py:49`、`app.py:1005` | 正确 | ✅ |
| 170 | `routes_accounts.py:1255-1291` | `:1254-1290` | ⚠️ 差 1 行 |
| 171 | `:1294-1322` | `:1293-1321` | ⚠️ 差 1 行 |
| 173 | `:1268-1275` | `:1267-1274` | ⚠️ 差 1 行 |
| 177 | `model_defaults.py:555-561` | 正确 | ✅ |
| 178 | `app.py:440`、`:446-458`、`:105-123`、`:111-122` | 正确 | ✅ |
| 179 | `verify_account_service.py:3066-3069` | 该处为 `_create_isolated_world`；浏览器/CDP 参数在 `:3130-3194` | ❌ |
| 183 | `defaults/config.yaml:28-38`、`:22` | 正确 | ✅ |
| 190 | `main.py:21-25`、`:33-39`、`routes_accounts.py:121-128` | 正确 | ✅ |
| 198 | `:1784-1786` | `:1819-1821` | ❌ |
| 199 | `:1787-1790` | `:1823-1825` | ❌ |
| 200 | `:1802-1811` | `:1837-1845` | ❌ |
| 201 | `:188-190` | `:191-193` | ❌ |
| 203 | `:2138-2208`、`:2230`、`:2266`、`:2277` | `:2238-2256`、`:2278`、`:2264-2270`、`:2278` | ❌ |
| 340 | `:3144-3174` | `:3205-3235` | ❌ |
| 341 | `:3149-3151` | `:3211-3212` | ❌ |
| 344, 353 | `:11-12`；`:1328`；`:1600` | `:11-12` ✅；`:1354`；`:1627` | ⚠️ |
| 345, 354 | `:2628-2631`、`:2680-2681` | `:2702-2707`、`:2746-2758` | ❌ |
| 363 | `:1776-1801` | 全量取证说明注释 `:180-193` | ❌ |
| 366 | 两开关“已移除” | 与代码一致 | ✅ |

### C. AGENT.md / changelog 关键断言

| 位置 | 断言 | 核对结果 |
|---|---|---|
| `AGENT.md:259-262`（§4.6.1） | 指向 `docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md`、`docs/DEBUG_GUIDE.md` | 磁盘存在 ✅ / 未入库 ⚠️（P1-7） |
| `AGENT.md:467-469`（§10.3） | tag 2.1.5 已发布而 pyproject 停在 2.1.4 | 真：`git show 2.1.5:pyproject.toml` = `2.1.4`；`web/package.json`、signer 也是 2.1.4 ✅ |
| `AGENT.md:470` | 打 tag 前用 `git tag --points-at HEAD` 与 `Select-String ... '^version'` 自检 | 两条输出格式不可直接比较，且打 tag 前 `git tag --points-at` 为空 ❌（P2-6） |
| `changelog.md:12` | 全量取证、目录覆盖、默认 `<data_dir>/verify-capture`、上限、默认不注册 | 代码成立（`:1803-1825`、`:191-193`、`:2160-2184`）；默认目录实际由 `auth_file.parents[2]` 推导（见 P2-3） ⚠️ |
| `changelog.md:13` | 诊断日志“按需开启”；401/403 响应体摘要保留 | 未写开关名；`GenerateAccessToken` 401 body 仅 DEBUG（P1-2/P1-4） ❌ |
| `changelog.md:14` | `POST /verify/check`；2/3 清锁；1/缺失 pending；面板入口 | 路由与清锁成立（`:1339-1371`、`:1354-1362`）；“缺失”若指凭据缺失实为 `error`（P2-8） ⚠️ |
| `changelog.md:15` | 两份新文档内容定位 | 描述基本成立 ✅ |
| `changelog.md:19` | 20s→45s | `:100` 成立 ✅ |
| `changelog.md:20` | 有头不再注入固定 UA；**无头保持原有 UA 与参数** | 前半成立；后半失实，两模式均不覆盖 UA（`:3150-3152`，提交 `3a72af6` 删除 `CHROME_UA`） ❌（P1-3） |
| `changelog.md:21` | pyproject 与 waa-signer 版本同步 | 成立；漏写 `web/package.json`（P2-7） ⚠️ |
| `changelog.md:25` | 启动参数修复、iOS 16.4+ App Clip 可用、安卓误杀 | 参数剔除成立（`:3205-3235`）；iOS/安卓为实机/社区结论，见 P1-6 |
| `changelog.md:26` | Cookie 合法化与缺失名单 WARNING | `cdp_client.py:694-706`、`:729-755` 成立 ✅ |
| `changelog.md:27` | 裸探针 region 403 不写假 verify | `exit_arbitration.py:291-300`、`account_info_refresher.py:752-761` 成立 ✅ |

---

## 三、问题清单

### P0（阻断交付）

**P0-1 已移除的 `AISTUDIO_VERIFY_MINIMAL_FLAGS` 仍被写成可用逃生口（直接违反审查硬约束）**

- 位置：`docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md:171`（§5.5 诊断逃生口）、`:234`（§8 引用索引）、`:162`（§5.3 “最小参数诊断模式也显式保留 site-isolation”）。
- 代码证据：工作树无该开关——`VERIFY_DIAGNOSTICS_ENV` 位于 `verify_account_service.py:92`，`MINIMAL_FLAGS_ENV`/`_minimal_flags_enabled` 已被本轮 diff 删除；`git grep` 仅命中测试的“已移除”断言（`tests/unit/test_verify_account_service.py:3120`）。同批删除的 `AISTUDIO_IMPORT_KEEP_OSID` 也无源码残留（`tests/unit/test_cookie_import.py:742-757`）。
- 危害：操作者按 §5.5 设 `AISTUDIO_VERIFY_MINIMAL_FLAGS=1` 不会有任何效果，却会误以为已切到“最小参数档”，从而把“没变化”误读为实验结果；与 `docs/DEBUG_GUIDE.md:77,124,366` 的“已移除”表述自相矛盾。
- 建议：删除 §5.5 该句或改写为“该开关已移除（见 DEBUG_GUIDE §4 表）”；§5.3 删去“最小参数诊断模式显式保留 `--disable-site-isolation-trials`”的过时依据；§8 删除 `:3118-3132`/`:3126` 两行。

**P0-2 本地隔离调试步骤在真实仓库配置下会拉起实例真实浏览器（与“只跑 verify”和非 BotGuard 红线冲突）**

- 位置：`docs/DEBUG_GUIDE.md:140-166`（§5.1 步骤）、`:175-179`（§5.2 声称“不建实例、不预热控制面”的必要组合）。
- 代码证据：本机运行配置 `config.yaml:30-36` 实际有 2 个实例（Free + Pro），`pool_enabled = bool(pool_specs)`（`api/app.py:439-440`）为真；池启用时启动走 `InstanceManager.startup()` 激活池会话（`app.py:446-464`），会真实拉起 Chromium。`AISTUDIO_ALLOW_EMPTY_INSTANCES` 只在 `instances` 原始列表为空时才生效（`model_defaults.py:555-561`），`AISTUDIO_SKIP_CONTROL_WARMUP` 只在 `pool_enabled=False` 的控制面预热分支被读取（`app.py:108-123`）。因此 §5.1 的环境变量组合**不能**阻止实例池冷启动。
- 危害：照文档执行会启动真实浏览器 + BotGuard，正是 AGENT §4.6 禁止的“反复冷启动风控”场景；也会污染 verify 观测。
- 建议：在步骤 2 增加“复制 `defaults/config.yaml` 到临时目录并置 `instances: []`”，显式设置 `AISTUDIO_CONFIG_PATH=<临时配置>`（`config.py:110-126` 支持该变量）；并在 §5.2 说明：若沿用仓库 `config.yaml`（含实例），`SKIP_CONTROL_WARMUP` 是空转，必须先清空实例。

### P1（必须修）

**P1-1 两篇文档的 `file:line` 系统性失效**

- 位置与证据：见第二节 A/B 表。`verify_account_service.py` 的引用几乎全部偏移，且偏移量随文件位置增长（常量区 +3、`check()` +28、`_wait_for_qr`/`confirm_verified` +76、compat +61），说明文档是按较早的工作树行号写成，之后 `AISTUDIO_VERIFY_DIAGNOSTICS` 引入与全量取证/参数剔除改动把行号整体推后。
- 典型：`confirm_verified` 写 `:2622-2692`（实为 `:2698-2768`）、`_read_bframe_qr` 写 `:2786`（实为 `:2862`）、`_apply_verify_compat_args` 写 `:3144-3174`（实为 `:3205-3235`）、`check()` 写 `:1171-1179`（实为 `:1199-1250`）、`_clear_verify_required` 写 `:1641-1658`（实为 `:1670-1696`）、`NET_TRACE_MAXLEN` 写 `:110`（实为 `:113`）。
- 危害：两篇文档开篇均承诺“所有 `file:line` 均可在本仓核对”“以当前 HEAD 代码为事实标准”，现状使该承诺不成立，排障者会被引到无关代码。
- 建议：在文档冻结前用脚本按“符号名 → 当前行号”重刷全部引用；或改为“符号 + 简短摘录”而不写绝对行号，并加一条 CI/文档自检（如 `Select-String` 断言符号出现在声明行号附近）。

**P1-2 本轮新增的 `AISTUDIO_VERIFY_DIAGNOSTICS` 全套文档零覆盖，且 DEBUG_GUIDE 声称“无开关”**

- 位置：`docs/DEBUG_GUIDE.md:87-92`（四层证据表把第 1/2/3 层写成“无开关”）、`:109`（“调用时读取”清单未列该变量）；`docs/changelog.md:13`（“按需开启”但从不写变量名）。
- 代码证据：`VERIFY_DIAGNOSTICS_ENV = "AISTUDIO_VERIFY_DIAGNOSTICS"`（`verify_account_service.py:92`），调用时读取（`:250-259`），`_log_diagnostic()` 仅在开启时落 INFO、否则 DEBUG（`:262-267`）；会话启动会打印开关状态（`:903-906`）。第 2 层 `_dump_net_trace` 整体走 `_log_diagnostic`（`:2635-2680`），即默认在 INFO 级不可见；第 1 层每轮页面状态也走 `_log_diagnostic`（`:1516`）。
- 危害：操作者按文档无法打开“四层证据”，第 2 层网络记录在默认日志级别下根本看不到，直接破坏 DEBUG_GUIDE 的主要用途。
- 建议：§4 表新增一行（默认关；`1/true/yes` 开启；调用时读取；证据 `:92,250-267`）；把四层表的“开关”列改为“`AISTUDIO_VERIFY_DIAGNOSTICS=1` 时 INFO，否则 DEBUG”（全量取证层除外）；changelog 第 13 条写出变量名。

**P1-3 changelog “无头模式保持原有 UA 与参数”失实**

- 位置：`docs/changelog.md:20`；`docs/DEBUG_GUIDE.md:75` 的“有头模式……不覆盖 UA”也暗示无头仍覆盖。
- 代码证据：`_build_verify_chrome_command` 现在 `extra_args = ["--lang=en-US"]`，注释明确 “Never override the UA”（`verify_account_service.py:3149-3153`）；提交 `3a72af6` 删除了 `CHROME_UA` 及 `if not headed: --user-agent=...`。即两种模式都改用内核原生 UA。
- 建议：changelog 改为“有头/无头均不再注入固定 `Chrome/133` UA，统一使用内核原生 UA”；DEBUG_GUIDE §2.5 同步为“两模式均不覆盖 UA”。

**P1-4 changelog “上游 401/403 响应体摘要保留”只对 VerifyUser 非 2xx 成立**

- 位置：`docs/changelog.md:13`。
- 代码证据：`VerifyUser` 非 2xx body 用 `log.info`（`verify_account_service.py:2518-2524`，默认可见）；`GenerateAccessToken` 的 401 body 一律 `_log_diagnostic`（`:2525-2534`，默认 DEBUG）；`_dump_net_trace` 里非 VerifyUser 的 body 也是 `_log_diagnostic`（`:2670-2680`）。因此“401 响应体摘要保留（INFO）”不成立。
- 建议：把 changelog 措辞限定为“非 2xx `VerifyUser` 响应体保留 INFO；其余 RPC 响应体随诊断开关”；或按 changelog 承诺把 `GenerateAccessToken` 401 提为 INFO（需评估日志量）。

**P1-5 changelog 漏写本版本新增/移除的用户可见开关**

- 位置：`docs/changelog.md:10-27`（2.1.6 全节）。
- 代码有但漏写：
  - `AISTUDIO_ALLOW_EMPTY_INSTANCES`（提交 `625fa08`；`model_defaults.py:411-420`、`:555-561`）；
  - `AISTUDIO_SKIP_CONTROL_WARMUP`（工作树 `app.py:114-123`）；
  - 移除 `AISTUDIO_VERIFY_MINIMAL_FLAGS`（工作树 diff；HEAD `:193`/`:1768-1770`）；
  - 移除 `AISTUDIO_IMPORT_KEEP_OSID`（`cookie_parser.py`、`routes_accounts.py` 工作树 diff）；
  - 新增 `AISTUDIO_VERIFY_DIAGNOSTICS`（见 P1-2）。
- 危害：两个“本地调试开关”是 DEBUG_GUIDE §5 的必要前提，却不在发布说明中；两个移除项会让旧脚本/旧文档静默失效。
- 建议：在 Added/Changed 增补 3 条新增、1 条“移除调查期开关”说明。

**P1-6 两篇文档内部对同一事实给出互相矛盾的证据等级**

- (a) `docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md:103,108,201` 声称 `GenerateAccessToken` 401 与最终成败无对应关系、只是旁支；`docs/changelog.md:26` 却把 401 归因于 Cookie 被拒收并宣称已修复。二者不能同时成立，需明确“修复前 401 是 Cookie 拒收的症状/修复后是否仍偶发”。
- (b) `:30,116` 写“同账号同网络实测：iOS 16.4+ App Clip 通过”；`:121-122` 又把同一 App Clip 路径标为“外部社区信息（未在本仓独立复现）”。若确为实机，应删去“未复现”；若为社区信息，应降级 §1/§4.1 的“实测”表述。
- (c) `:342` 与 `AGENT.md:261` 把“安卓扫码失败属已知的上游误杀”写成事实；`:119-122` 却标为社区信息、无来源链接。
- 建议：给每条结论标注统一证据等级（本仓实机 / 本仓单测 / 上游代码 / 社区信息），并在 §8 引用索引补“证据文件或提交号”；三处矛盾择一。

**P1-7 两份新文档仍未入库，AGENT/changelog 的指针在提交后可能悬空**

- 证据：`git status --short` → `?? docs/DEBUG_GUIDE.md`、`?? docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md`；而 `AGENT.md:259-262`、`docs/changelog.md:15` 已把它们当作 2.1.6 交付物。
- 说明：与 `REV-2.1.6.1` P1-1 同一问题，本报告只做文档侧确认。
- 建议：提交时显式 `git add` 两份文件；提交前复查正文无真实账号/Cookie/短码明文。

### P2（建议修）

1. **`%TEMP%` 在 PowerShell 中不展开**：`docs/DEBUG_GUIDE.md:227` 用 `uv run python %TEMP%\verify_summary.py`，但该文档声明“以 Windows PowerShell 为例”；PowerShell 需 `$env:TEMP`（或 `$env:TEMP\verify_summary.py`），照抄会找不到路径。建议改为 `"$env:TEMP\verify_summary.py"`。
2. **API 调用缺少鉴权说明**：`docs/DEBUG_GUIDE.md:158-173` 设置了 `AISTUDIO_WEB_PASSWORD`，随后直接让读者 `POST /accounts/{id}/verify/start`；账号路由挂载了 `require_web_auth`（`app.py:613-615`），设置密码后未带会话令牌会 401（`api/dependencies.py:199-211`）。建议补一句“先登录管理台取得会话 Cookie，或临时用空密码 + 仅本机网络隔离”。
3. **取证默认目录表述不严密**：`docs/DEBUG_GUIDE.md:117,198` 与 changelog:12 写“默认 `<data_dir>/verify-capture`”，实际由 `self._auth_file.parents[2] / "verify-capture"` 推导（`verify_account_service.py:2170-2174`、`:1819-1821`）。若 `AISTUDIO_ACCOUNTS_DIR` 不在 data 根下会偏离；建议写成“默认 `<accounts_dir>/../../verify-capture`（标准布局即 `<data_dir>/verify-capture`）”，与 REV-2.1.6.1 Q-3 呼应。
4. **测试引用错误**：`docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md:241` 的 `:1235` 实为 `:1242-1252`；`:867` 实为 `:877`（另 `:2903`、`:3131` 也有 site-isolation 断言）；`:2865` 是无关的 `proxy=None`。
5. **路由/payload 行号差 1~多行**：`routes_accounts.py:1340-1372` 实为 `1339-1371`；`verify_account_service.py:1343-1356` 实为 `1379-1384`；`:3293-3297` 实为 `3354-3358`（VERIFY §6.3）。
6. **AGENT §10.3 自检命令不可直接执行/比较**：`AGENT.md:470` 的两条命令输出格式不同（`2.1.6` vs `version = "2.1.6"`），打 tag 前 `git tag --points-at HEAD` 为空；且 `Select-String` 仅 PowerShell。建议改为“打 tag 后 `git tag --points-at HEAD` 的输出（去 `v` 前缀）与 `pyproject.toml` 的 `version` 字符串相等；Termux 用 `grep '^version' pyproject.toml`”，并把 `web/package.json`、signer 版本一并纳入口令。
7. **版本一致性覆盖不全**：changelog:21 只提 pyproject 与 signer，实际同时 bump 了 `web/package.json`（`web/package.json:4 = 2.1.6`）；且 `README.md:128`、`README_EN.md:129` 仍示例 `AISTUDIO_IMAGE_TAG=2.1.4`。建议补写并在 README 同步（或说明镜像标签随发布另行更新）。
8. **`verify/check` 的“缺失 pending”口径不精确**：changelog:14 与文档 §6.3 写“1/缺失 pending”，但凭据缺失、无 SAPISID、上游 401/403 命中标记时实际返回 `state=error`（`verify_account_service.py:1256-1348`）。建议区分“upstream_status=None → pending”与“凭据/会话错误 → error”。
9. **“原实现见 :1768-1770 / cookie_parser.py:143-148”无法在工作树复验**：两处指向被删代码，工作树同位置已是 `_timer` 与 `ACCOUNT_HOST_ONLY_COOKIES`；建议改引 HEAD 提交号（如 `3a72af6` / 对应删除提交）与符号名，避免读者按行号查不到。
10. **DEBUG_GUIDE §2.5 的 UA 措辞仍暗示无头覆盖 UA**：`:75` “有头模式……不覆盖 UA”；应与代码统一为“两模式均不覆盖”（同 P1-3）。

### Q（待确认）

- **Q-1 实机时序/令牌长度无落盘证据**：`docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md:77-82,202` 给出 `uvresp` 4729 字符、120、130、`+22s/+24s` 等精确数字，但仓库内无对应 sanitized capture 或截图。建议附一次取证文件哈希/片段（脱敏）或降级为“量级”。
- **Q-2 2.1.5 changelog 缺章**：`git show 2.1.5:docs/changelog.md` 顶部直接是 `[2.1.4]`，2.1.6 却写“在 2.1.5 基础上”；属既有欠账，请确认是否在 2.1.6 一并补 `[2.1.5]` 章或注明“2.1.5 未单独成章”。
- **Q-3 “必须先用 iOS 16.4+ App Clip”是流程约束而非代码约束**：`AGENT.md:261`、`VERIFY_ACCOUNT_DEVICE_VERIFICATION.md:199` 写成硬约束，但代码/接口无任何设备类型判断；请确认这是“操作规范”而非“产品强制”，并考虑在面板文案中提示而非阻断。
- **Q-4 `waa-signer/bin/waa-signer` 缺失是当前机器状态**：DEBUG_GUIDE §5.3 的结论成立（`Test-Path` False），但若读者已安装该二进制，该节“切 browserless 必然失败”不成立；建议限定为“本机当前未安装”。

### N（备注）

- **N-1** 被删代码的行号引用建议一律改提交号 + 符号名（同 P2-9）。
- **N-2** `AGENT.md:470` 的 PowerShell 示例在 Termux（项目主要运行环境）不可用，建议补 POSIX 等价命令。
- **N-3** `docs/DEBUG_GUIDE.md:26` 在“不适用”条目里又指向“本文件第 3 节”，属自引用；建议指向具体外部文档或直接写“见 §3”。

---

## 四、与 REV-2.1.6.1 的关系

- `REV-2.1.6.1` 已就同一工作树判定代码 **CONDITIONAL**，并列出 P1-1（两文档 untracked）、P2-1（网络监听常开）、Q-3（取证根目录假设）。本报告在文档侧独立复核，结论与之一致：P1-7 = 其 P1-1；P2-3 = 其 Q-3；P1-2 与其 P2-1 描述互补（监听常开 ≠ 日志默认可见，DEBUG_GUIDE 需区分“注册监听”和“输出级别”）。
- 本报告新增的阻断项（P0-1、P0-2）均为文档可操作性/红线问题，不要求改代码；但 P0-2 的修复需要在文档中给出“临时配置 + 空实例”的步骤，而非修改服务默认行为。

---

## 五、首轮只读声明（保留原文）

本次审查为只读：除新增本文件 `docs/dev/rebuild/domains/docs-and-audit/reviews/REV-2.1.6.2-debug-guide-and-verify-doc.md` 外，未修改任何源码、测试、配置或既有文档；未执行 `git add`/`commit`/`push`；未启动服务、浏览器、外网、ssh 或扫码。

---

# 复核轮次（R2，2026-11-24T08:21Z）

> 首轮记录保留在上方，本节只追加复核结论。复核方式：重新完整读取四份文件（非只看 diff），并对代码当前工作树逐符号、逐行号重核；不假设 Worker 已改对。

## 六、R2 结论

**CONDITIONAL** — 两个 P0 均已实质关闭，P1 中仅剩“两份文档仍 untracked”的交付门禁；另有 4 个 P2（均为引用/语义精度问题，其中 2 个是 R2 新引入）、3 个 Q、4 个 N。

问题数量：**P0 = 0，P1 = 1，P2 = 4，Q = 3，N = 4**。

首轮 P0/P1/P2 关闭统计：P0 2/2 关闭；P1 6/7 关闭（P1-1 部分关闭、P1-7 未关闭）；P2 9/10 关闭（P2-2 部分关闭）。

## 七、逐条关闭状态

| 编号 | 首轮问题 | R2 状态 | 证据（当前文件行号 + 内容摘要） |
|---|---|---|---|
| P0-1 | 把已移除的 `AISTUDIO_VERIFY_MINIMAL_FLAGS` 写成可用逃生口 | **已关闭** | `docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md:172`：“**已在 2.1.6 移除**，不要再使用”；`DEBUG_GUIDE:79` 同义；`DEBUG_GUIDE:129,423` 同义；`docs/changelog.md:19-22` 新增 Removed 两条。工作树无 `MINIMAL_FLAGS_ENV`/`keep_osid` 实现；`git grep` 仅命中 changelog 的 Removed 文案与测试断言（`test_verify_account_service.py:3213`、`test_cookie_import.py:742`）。`Select-String` 复核 untracked 文档：两处均为“已移除”语义。 |
| P0-2 | 隔离调试步骤仍会拉起实例真实浏览器 | **已关闭** | 见第八节逐条推演。`DEBUG_GUIDE:143-168` 已把“先隔离配置、再隔离数据”写成前置；`:201-210` 同时设 `AISTUDIO_CONFIG_PATH`/`DATA_DIR`/`ACCOUNTS_DIR`；`:148-149` 明确只设 DATA/ACCOUNTS 是错的，且指出目标配置不存在会被模板（含实例）回填。 |
| P1-1 | 两篇文档 `file:line` 系统性失效 | **部分关闭（残留 P2-R1）** | 100% 重核后，VERIFY 索引 35 行与 DEBUG 的 60+ 处引用基本对齐（如 `confirm_verified:2734-2804`、`_read_bframe_qr:2898-2984`、`compat:3241-3271`、`check:1212-1263`、`routes:1339-1371` 均正确）。残留：`DEBUG_GUIDE:43` 与 `VERIFY:187` 把 `qr_canvas`/`verify_button` 归到 `_PAGE_SNAPSHOT_JS:330-363`，实际这两个控件在 `_DESCRIBE_PAGE_JS:344-363`（`_PAGE_SNAPSHOT_JS` 仅 `:330-339`，只返回 url/text）。 |
| P1-2 | `AISTUDIO_VERIFY_DIAGNOSTICS` 零文档 | **已关闭** | `DEBUG_GUIDE:91-94` 四层表已按“默认 DEBUG / 开启 INFO”描述；`:111` 列入调用时读取；`:117` 单列默认值/语义/证据；`changelog:13` 单列该开关。代码：`verify_account_service.py:93`、`:251-260`、`:263-268`、`:916-919`。 |
| P1-3 | “无头保持原有 UA”失实 | **已关闭** | `changelog:27` 改为“有头与无头模式都不再注入固定 `Chrome/133` UA”；`DEBUG_GUIDE:77` 与 `VERIFY:168` 同口径。代码：`:3186-3189` 仅 `--lang=en-US`，无 UA 覆盖。 |
| P1-4 | 401/403 响应体摘要“保留”失实 | **已关闭** | `changelog:13` 明确“`GenerateAccessToken` 的 401 响应体只在开启时出现，不作为成败判据”；`VERIFY:104,109` 同步。代码：非 2xx `VerifyUser` 为 `log.info`（`:2555-2560`），`GenerateAccessToken` 走 `_log_diagnostic`（`:2565-2570`）。 |
| P1-5 | changelog 漏写新增/移除开关 | **已关闭** | `changelog:15-16` 新增 `AISTUDIO_ALLOW_EMPTY_INSTANCES`、`AISTUDIO_SKIP_CONTROL_WARMUP`；`:19-22` Removed 两条；`:13` 诊断开关。 |
| P1-6 | 两文档内部矛盾（401 / iOS 实测 / 安卓误杀） | **已关闭（余 Q-2）** | `VERIFY:7` 定义“本仓实测 vs 社区资料”；`:31,117,120-123,200` 与 `changelog:32`、`AGENT.md:263` 全部按同一证据等级表述；`VERIFY:104,109,202` 与 `changelog:33` 都把 401 定为伴随症状、非判据。残留：`VERIFY:104` “成功与失败会话中都可能出现”仍缺可核对证据（Q-2）。 |
| P1-7 | 两份新文档未入库 | **未关闭** | `git status --short` 仍为 `?? docs/DEBUG_GUIDE.md`、`?? docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md`；而 `AGENT.md:261-262`、`changelog:17` 已引用。属提交前 `git add` 事项，不是文档内容错误。 |
| P2-1 | PowerShell 下 `%TEMP%` 不展开 | **已关闭** | `DEBUG_GUIDE:284` 改为 `$env:TEMP\verify_summary.py`，用法同为 `"$env:TEMP\..."`。 |
| P2-2 | 401/Bearer 说明不准确 | **部分关闭（残留 P2-3）** | 新增了 `AISTUDIO_WEB_PASSWORD` 与 `require_web_auth` 说明（`:135,224`），但“**必须**携带 `Authorization: Bearer`”不准确：`dependencies.py:159-176` 依次接受 `?key=`、`x-goog-api-key`、`x-api-key` 与 Bearer；`:199-212` 才是 401 分支。另 `:135,224` 的挂载范围 `app.py:613-620` 不含日志路由（日志在 `:627-630`）。 |
| P2-3 | 取证目录默认值与代码不符 | **已关闭（余 Q-1）** | 代码新增 `_resolve_capture_data_root`（`:1821-1837`），优先 `resolve_data_dir()`；`_start_full_capture` 在 `:2206` 调用。文档 `DEBUG_GUIDE:121,255`、`changelog:12` 已改为“项目数据根下 verify-capture”；但证据仍只引 `_FullCaptureSink.__init__:1851-1856`，该处只处理 override/相对路径，不体现 `resolve_data_dir()`（Q-1）。 |
| P2-4 | 测试行号引用错误 | **已关闭** | `tests/unit/test_verify_account_service.py:1242-1252`（45s 断言）、`:877` 与 `:2996`（site-isolation）均与 VERIFY `:245-246` 一致。 |
| P2-5 | 路由/payload/driver 行号偏移 | **已关闭** | `routes_accounts.py:1339-1371`、`verify_account_service.py:1384-1397`、`:3390-3394` 与 `VERIFY:191-192`、`:239` 一致。 |
| P2-6 | AGENT 自检命令不可执行/比较 | **已关闭（余 N-1）** | `AGENT.md:469-485` 已改为“打 tag 后自检”，要求去掉可选 `v` 前缀比较，并补 POSIX `grep`；仍只覆盖 pyproject（N-1）。 |
| P2-7 | 版本一致性覆盖不全 | **已关闭** | `changelog:28` 纳入 `web/package.json` 并说明 README 镜像 tag；`web/package.json:4`、`waa-signer/cmd/waa-signer/main.go:42`、`waa-signer/README.md:106` 均 2.1.6。 |
| P2-8 | `verify/check` 的“缺失 pending”口径 | **已关闭** | `changelog:14` 与 `VERIFY:182` 已明确凭据/SAPISID/`session_invalid`/`region_blocked` 返回 `error`；代码 `_perform_check:1265-1382` 逐支路吻合。 |
| P2-9 | 被删代码行号引用不可复验 | **已关闭** | `DEBUG_GUIDE:129-130` 改为“原实现见 2.1.6 之前的提交历史（`git log -S ...`）”，不再指向工作树行号。 |
| P2-10 | UA 措辞暗示无头覆盖 | **已关闭** | `DEBUG_GUIDE:77` 明确“两种模式都不注入固定 UA”；`VERIFY:168` 同义。 |

## 八、P0-2 逻辑推演（只读代码，不启动服务）

1. `DEBUG_GUIDE:202-204` 把 `AISTUDIO_CONFIG_PATH` 指向已存在的 `$debugRoot\cfg\config.yaml`（步骤 1.1 先写入），因此 `resolve_config_file`（`config.py:110-142`）不会走模板复制分支；即使误走，`DEBUG_GUIDE:149` 已警告模板（`defaults/config.yaml:61-67`）带实例。
2. 示例配置 `instances: []`（`:180`）。`_load_yaml_config` 的 `_deep_merge` 对列表整体覆盖（`model_defaults.py:121-131`），空列表会替换默认的两条实例，不会回填。
3. `get_configured_instance_specs()` → `_normalize_instance_specs([])`；`_allow_empty_instances()` 为真时在 `model_defaults.py:555-561` 返回 `[]`。`max_contexts` 默认 2 是合法正整数（`:574-592`），不会阻断。
4. `pool_specs` 为空 → `pool_enabled=False`（`app.py:439-440`）；`_start_control_warmup` 先看 `pool_enabled`，再看 `AISTUDIO_SKIP_CONTROL_WARMUP`（`app.py:105-123`），示例已设 → 返回 `None`，控制页不打开。
5. `_startup_instances` 等待 warmup 后调用 `manager.startup()`（`app.py:126-142`）；实例池零槽位，不会创建请求实例。
6. `engine.mode: browser` → `BrowserEngineRuntime.start()` 只做 `preflight()`（`engine_runtime.py:288-289`，检查可执行文件存在），不启动 Chromium；真正启动只发生在 `create_session`（有槽位时）。
7. 示例 `features.keepalive.auth_update/metadata_refresh/metadata_refresh_on_startup=false`（`:188-191`）；`_run_due` 对 `enabled=false` 的 job 直接 `continue`（`account_keepalive.py:640-659`），启动期 metadata 刷新也被开关拦住（`app.py:563-567`）。
8. 模型发现只使用 READY 池会话（`app.py:145-151`），零槽位时没有会话，不会回退控制页。
9. 结论：按修订后步骤执行，**数据目录隔离 + 配置隔离**共同保证“不创建请求实例、不预热控制面”；只设 `DATA_DIR/ACCOUNTS_DIR` 确实不够，文档已把这点写成显式红线（`:148`）。
10. 残余风险（非 P0-2 原意）：若操作者 shell 里已导出 `AISTUDIO_UI_DEBUG=1`，它会覆盖示例的 `diagnostics.ui_debug: false`（`config.py:314-318`），服务进入纯离线沙盒，verify 结果全是模拟；文档未要求清理继承环境变量（见 P2-4）。同理继承的 `AISTUDIO_VERIFY_PROXY/HEADED/CHROME_PATH` 也可能破坏“一次只改一个变量”的前提。

## 九、全部 `file:line` 复核结果

- 复核方式：对两文档中每一处 `NNN`/`NNN-NNN` 引用逐一打开当前工作树对应文件核对（100%，非抽样），并复核 `AGENT.md`/`changelog` 中引用的路径。
- 全部相符：`VERIFY:83,87,88,89,90,91,108,148,152-159,163,164,168,172,181,182,186,191,192,204,212-246`（除下表一条）；`DEBUG_GUIDE:59-61,67,75-78,91-94,116-135,145-149,224-229,233-236,240,246-247,255-260,397-402,410-411,420`（除下表）。
- 不符清单（完整）：
  1. `VERIFY:187` “`_PAGE_SNAPSHOT_JS`，`:330-363`” → 实际 `_PAGE_SNAPSHOT_JS` 为 `:330-339`（仅 url/text）；`qr_canvas`/`verify_button` 在 `_DESCRIBE_PAGE_JS:344-363`，经 `_describe_page_snapshot` 读取。
  2. `DEBUG_GUIDE:43` 同一条引用（`_PAGE_SNAPSHOT_JS:330-363`）同因不符。
- 证据不足（非行号错误，列 Q）：`DEBUG_GUIDE:121,255` 用 `_FullCaptureSink.__init__:1851-1856` 支撑 `resolve_data_dir()`，该函数本身不解析数据根；解析在 `_resolve_capture_data_root:1821-1837` 与调用点 `:2206`。

## 十、R2 残留问题清单

### P1（提交前必须处理）
- **P1-R1 两份新文档仍未入库**：`git status --short` 显示 `?? docs/DEBUG_GUIDE.md`、`?? docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md`，而 `AGENT.md:261-262`、`changelog:17` 已将其作为 2.1.6 交付物引用。与 `REV-2.1.6.1` P1-1 同源。建议提交时显式 `git add` 并在提交后确认 `git ls-files docs/DEBUG_GUIDE.md docs/VERIFY_ACCOUNT_DEVICE_VERIFICATION.md` 非空。

### P2（建议修）
- **P2-R1 `qr_canvas` 控件来源符号写错**：`VERIFY:187`、`DEBUG_GUIDE:43`。改引 `_DESCRIBE_PAGE_JS:344-363`（`_log_page_state:2074-2100` 调 `_describe_page_snapshot`），`_PAGE_SNAPSHOT_JS` 只用于 url/text。
- **P2-R2 环境变量读取时机分类错误**：`DEBUG_GUIDE:111` 把 `AISTUDIO_ALLOW_EMPTY_INSTANCES`、`AISTUDIO_SKIP_CONTROL_WARMUP` 列为“调用时读取（下次验证会话即生效）”。前者在配置归一化时读取（`model_defaults.py:555-561`），后者只在启动期 `_start_control_warmup` 读取（`app.py:105-123`）；进程内改这两个变量不会在“下次验证会话”生效，必须重启。建议移到“启动前设置”清单。
- **P2-R3 管理 API 鉴权说明过窄 + 挂载范围不全**：`DEBUG_GUIDE:135,224` 写“**必须** `Authorization: Bearer <password>`”；`dependencies.py:159-176` 还接受 `?key=`、`x-goog-api-key`、`x-api-key`。且 `app.py:613-620` 不含日志路由（`:627-630`）。建议改为“管理路由接受 Bearer 密码，也兼容 `?key=`/`x-api-key`；日志路由见 `:627-630`”，并给出一条 PowerShell `Invoke-RestMethod` 示例。
- **P2-R4 未清理继承的 `AISTUDIO_*` 环境变量**：`DEBUG_GUIDE:143-222` 只设置目标变量，未要求清空 shell 中已有的 `AISTUDIO_UI_DEBUG`（会按 `config.py:314-318` 覆盖 `diagnostics.ui_debug: false`，进入沙盒 fake verify）、`AISTUDIO_VERIFY_PROXY`/`HEADED`/`CHROME_PATH`/`FULL_CAPTURE`。建议步骤 2 增加 `Remove-Item Env:AISTUDIO_UI_DEBUG, Env:AISTUDIO_VERIFY_PROXY, ... -ErrorAction SilentlyContinue`，并说明 `ui_debug` 由环境变量优先。

### Q（待确认）
- **Q-R1 取证目录证据引用不完整**：`DEBUG_GUIDE:121,255` 的默认目录结论正确（代码 `_resolve_capture_data_root` 优先 `resolve_data_dir()`），但所引 `_FullCaptureSink.__init__:1851-1856` 不包含该解析；建议补 `:1821-1837`、`:2206`。
- **Q-R2 `GenerateAccessToken` 401 “成功会话也会出现”缺证据**：`VERIFY:104` 的绝对表述未给日志/抓包位置；与 `changelog:33` 的“伴随症状”并不冲突，但“成功会话中也出现”需要一次可核对的样本或改成“可能为旁支噪声”。
- **Q-R3 推进顺序里的“系统浏览器/最小参数”**：`DEBUG_GUIDE:73` 把它们列为“本轮有效的推进顺序”，而 `:79` 又说最小参数开关已移除；建议注明这两步是历史尝试，最小参数档已不可用，避免读者复现不存在的开关。

### N（备注）
- **N-R1** `AGENT.md:468` 仍写“已知历史欠账…须在 2.1.6 一并补齐”，而 `pyproject.toml:7` 已是 2.1.6；建议改为“已在 2.1.6 补齐”以保持时态一致。
- **N-R2** `changelog:14` 的“管理台账号卡片新增「查询账号状态」入口”实为 `VerifyAccountModal.vue:899` 的按钮（从账号卡片进入的弹窗），措辞可更精确。
- **N-R3** `DEBUG_GUIDE:28` 在“不适用”条目里自引“本文件第 3 节”，建议改为“见本文 §3”或直接给外部文档。
- **N-R4** `changelog` 未单列 `cdp_client.py` 的“页面未导航时不再误报缺失 Cookie WARNING”；属 REV-2.1.6.1 N-1 的同一行为，可在 Cookie 条目补半句。

## 十一、R2 门禁与工作树说明

| 门禁 | 命令 | 结果 |
|---|---|---|
| 空白检查 | `git diff --check` | 无输出，退出码 0 ✅ |
| 已移除开关（git 跟踪文件） | `git grep -n "MINIMAL_FLAGS\|IMPORT_KEEP_OSID"` | 仅 `docs/changelog.md:21-22`（Removed 文案）与两处测试“已移除”断言；无源码实现 ✅ |
| 已移除开关（untracked 文档） | `Select-String` 全文 | `DEBUG_GUIDE:79,129,423`、`VERIFY:172` 均为“已移除/不要使用” ✅ |
| 路径存在性 | `Test-Path` 抽查 | 两文档、EVIDENCE、AGENT、changelog、Vue、routes、main、defaults、browser_engine 均存在；`waa-signer/bin/waa-signer` 不存在，与 `DEBUG_GUIDE:241` 的说明一致 ✅ |
| pytest | 未执行 | 按任务要求；本报告只读文档，未改代码。注意：工作树中 `src/` 与 `tests/` 仍有**他人/前序实现**的改动（如 `account_keepalive.py`、`test_account_keepalive.py`，对应 changelog:35 的 auth_update 修复），不是本轮文档审查所为，也不在本轮执行验证范围内 |

只读声明（R2）：本轮仅向本 REV 文件追加本节；未修改两份文档、`AGENT.md`、`changelog.md`、源码、测试或配置；未执行 `git add`/`commit`/`push`；未启动服务/浏览器/外网/ssh/扫码。

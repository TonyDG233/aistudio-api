# D12 Tests / Evidence

基线：`upstream/master`（`4623109`）..`rebuild`；仅 D12；证据缺失标 `未验证`，真实账号/IP 证据标 `不可复跑`。

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 待用户决定 |
|---|---|---|---|---|---|---|---|
| D12 Tests/Evidence | `tests/unit` 全量回归（TDD 自证，非原始行为证明） | `4623109..HEAD`；`tests/unit/*.py`（79 文件）、`tests/conftest.py`；最后测试提交 `8e5e0cb` | 改造 | working | 2026-10-24 实测 `uv run pytest tests/unit -q` = 942 passed/35.94s；vs upstream 新增 54、修改 17；只证明“代码符合当前实现” | keep（仅回归） | 是否作为 rebuild 基线并剔除固化旧语义用例 |
| D12 Tests/Evidence | 测试钩子（强制 429，默认关闭） | `b59d6f3`；`tests/unit/test_testing_hooks.py`（2）、`tests/conftest.py` | 新增 | working | 2 例通过；`AISTUDIO_TEST_HOOKS=1` 才启用；用于离线走 429/换号路径 | port | 是否允许 hook 进入镜像 |
| D12 Tests/Evidence | 协议/多模态 fixtures | `4623109`；`tests/fixtures/test_input.json`、`test_output.json`、`test_image_input.json`、`test_image_output.json` | 原生 | working | 被 format/wire/multimodal 用例引用；无独立断言 | keep | 是否随 rebuild 复制 |
| D12 Tests/Evidence | Browser/CDP/Context/Page 单测簇 | `4623109`、`4586271`、`28c981f`、`f110d51`；`test_browser_session.py`（改造）、`test_cdp_client.py`（原生）、`test_cdp_browser_context.py`（3）、`test_browser_engine.py`、`test_session_blue_green_readiness.py`（3）、`test_browser_scripts_syntax.py` | 改造 | working | 942 内通过；全部 mock CDP，无真实浏览器/导航 | keep（单测） | 是否以真实 CDP 冒烟补位 |
| D12 Tests/Evidence | Build 面单测（38 例） | `1cc634e` 新增；`8dd6782`、`8e5e0cb` 改；`tests/unit/test_build_surface.py` | 新增 | working | 38 通过；覆盖 holder 解码、`ensure_ready` 单飞、signer/native、BUSY/PAGE_CLOSED/TIMEOUT/NO_FRAMES | keep | signer 备用用例是否保留 |
| D12 Tests/Evidence | Build 身份=租约（2 例） | `8e5e0cb`；`tests/unit/test_build_lease_identity.py` | 新增 | working | `:11` lease auth 优先于全局 session；`:31` 无 auth_file fail-closed 503 | keep | fail-closed 是否作为硬约束 |
| D12 Tests/Evidence | Build 双并发 E2E（声称 200） | `8e5e0cb` 提交正文；`docs/dev/rebuild/03-context-and-summary.md:11`；`%TEMP%\kilo\commit-msg-build-fix.txt` | 新增 | unverified | 未验证：仅“两条 200、0 Page.navigate/ERR_ABORTED/429”文字；无 raw SSE/日志/时间戳；自述为非流式、Free、临时配置 | keep（作为验收目标） | 是否重跑为最终镜像 E2E |
| D12 Tests/Evidence | Capacity/租约单测簇 | `79901b0`、`1201a37`、`b59d6f3`、`8e5e0cb`；`test_instance_pool.py`（40）、`test_instance_manager.py`（27）、`test_routing_pipeline.py`（11） | 新增 | working | 942 内通过；断言 `InstanceCapacityError`、`surface_instance_busy`、503、取消/异常释放租约 | keep | 是否接受 503 为唯一容量码 |
| D12 Tests/Evidence | 旧 capacity 429 证据（已被 `8e5e0cb` 反转） | `b59d6f3`、`8e5e0cb`；`docs/dev/evidence/wp5v3-20261019/raw/phase-concurrent-build-free.json`、`docs/dev/evidence/wp5-20261018/f-quota-try1.out` | 改造 | legacy | 记录两组 429 语义：wp5v3“渠道 'build' 实例预算已满”；wp5“surface_quota_exceeded（配额=0）”；`8e5e0cb` 后统一 503，旧断言不可再作契约 | drop（契约） | 是否保留作历史对照 |
| D12 Tests/Evidence | Paid 模型识别单测 | `a10953d`、`1980ada`；`test_model_discovery_paid.py`（2）、`test_model_access.py`（4）、`test_model_catalog.py` | 新增 | working | 942 内通过；ListModels 标 paid；模型 API 不回传 paid；paid 账号门槛独立 | keep | paid 是否作为路由第一维 |
| D12 Tests/Evidence | Paid/前端字段逆向证据 | 无 Git 溯源；相关 `a10953d`、`1980ada`；`%TEMP%\kilo\probe_frontend_paid.py`、`analyze_paid_fields.py`、`chunk_paid_context.txt`、`paid_frontend_matches.txt`、`bundles-current/` | 新增 | partial | 真实 bundle/前端字段来源；离线可读；依赖真实账号/PRO 页面 → 不可复跑 | keep（结论）/drop（脚本） | 是否把结论固化为 fixture |
| D12 Tests/Evidence | Routing 策略单测簇 | `cdb9e15`、`ec35c8d`、`8b6b52e`、`d8bbb22`；`test_routing_eligibility.py`（11）、`test_routing_safety.py`（18）、`test_generation_routing.py`（10）、`test_surface_orchestrator.py`（30）、`test_surface_policy_v2.py`（10） | 新增 | working | 942 内通过；策略组/资格/预算/熔断均有断言 | port（资格+候选）；drop（策略组/打分） | 策略组是否整体废弃 |
| D12 Tests/Evidence | 单实例候选锁死（无号可切→503） | `ec35c8d`；`src/aistudio_api/application/routing_pipeline.py:251-259`；`tests/unit/test_routing_pipeline.py:212` | 新增 | broken | `02:29`；候选只留已有实例账号；用例只测“invalid 实例可路由”，未覆盖 429 无候选失败 | port（修复后） | 是否把“候选不受实例过滤”列为验收 |
| D12 Tests/Evidence | Cookie 单测簇 | `56c1072`（upstream）、`9786b3a`、`7cd6d74`；`test_cookie_import.py`（18）、`test_cookie_persistence.py`（3）、`test_cookie_storage_state.py`（2） | 改造 | working | 942 内通过；跨域双胞胎、OSID、按 (name,domain,path) 合并回写 | keep | cookie 合并语义是否定为契约 |
| D12 Tests/Evidence | Cookie/Header 真机探针 | 无 Git 溯源；相关 `9786b3a`、`7cd6d74`；`%TEMP%\kilo\capture_headers.py`、`compare_page_cookies.py`、`send_after_inject.py`、`inject_missing_cookies.py` | 改造 | partial | 真实 header/cookie 行为；含真实凭据片段，只可读不可复制 → 不可复跑 | keep（结论） | 风控期后是否重跑 |
| D12 Tests/Evidence | 日志单测簇 | `880954f`（upstream，原生 `test_logging.py` 9）、`eed30b6`（`test_logs_routes.py` 14、`test_log_archive.py` 4、`test_log_broadcast.py` 9） | 改造 | working | 942 内通过；脱敏/SSE/归档有断言；订阅上限与丢弃无 E2E | keep | 7 天导出是否保留 |
| D12 Tests/Evidence | 统计/Token 单测簇 | `1adf3c2`、`af3f55c`；`test_stats_routes.py`（2）、`test_model_stats_v2.py`（5）、`test_token_counter.py`（8）、`test_token_count_routes.py` | 新增 | working | 942 内通过；两套计数/format/surface 分桶/countTokens | keep | 统计页 API 是否进第一版 |
| D12 Tests/Evidence | Keepalive 单测簇（auto_switch/auth_update/metadata） | `1f9ba61`；`test_account_keepalive.py`（9）、`test_keepalive_config.py`、`test_account_store_keepalive.py` | 新增 | partial | 942 内通过；auto_switch 仅 mock/in-flight 门槛；`docs/dev/evidence/wp5v3-20261019/README.md:200-206` 明示 live 未重跑 | port（手动/显式刷新） | 自动切号是否进第一版（`05:54` 暂缓） |
| D12 Tests/Evidence | 自动切号真机证据（旧控制面） | `1f9ba61`；`docs/dev/evidence/wp10-20261019/README.md`；`%TEMP%\kilo\wp10_real_auth.py`、`wp6_switch.py`、`wp6-verify\switch-inflight.sse` | 新增 | legacy | 10-19 手动触发 auto_switch 成功；走旧 active/控制面路径；当前 routing 路径未验；`03:12` 称瘫痪 → 不可复跑 | drop（旧路径） | 是否按新架构重做并重验 |
| D12 Tests/Evidence | 24h 保活证据 | `1f9ba61`；`src/aistudio_api/application/account_keepalive.py`；`docs/dev/evidence/wp10-20261019/README.md` | 新增 | unverified | 未验证：无 24h 实耗样本；`02:26`/`03:31` 指 build-only 不回写 Cookie；仅配置/单次回写用例 | port（补 24h 测试） | build-only 回写是否列为必需 |
| D12 Tests/Evidence | ATG/工具调用单测簇（93 例） | `792f4e1`；`tests/unit/test_atg_*.py`（7 文件）、`test_function_call_parser.py`、`test_response_parser.py` | 新增 | partial | 942 内通过；覆盖解析/注入/修复；无准确率指标，`03:12` 工具调用未解 | port（回归） | 是否需 n 次真机准确率基准 |
| D12 Tests/Evidence | 消息超时证据 | `1cc634e`、`8e5e0cb`；`tests/unit/test_build_surface.py:707` | 新增 | unverified | 未验证：仅 Build 流超时单测；无端到端消息超时/重试预算/错误码证据 | port（补 E2E） | 超时阈值与错误码谁定 |
| D12 Tests/Evidence | 最终镜像 E2E | `4623109..HEAD`；无 `tests/e2e`；`docs/dev/rebuild/03-context-and-summary.md:10` | 改造 | unverified | 未验证：仅 Docker workflow `39639126777` 构建成功；镜像内未跑真实请求；本地验证非流式/Free | port（补） | 是否以镜像 E2E 作合并门槛 |
| D12 Tests/Evidence | 旧 WP/规划文档（PLAN/SPIKE/AUDIT/WP5-V3/V4/WP7/UI-CHANNEL/HANDOFF/WP6/INSTANCE-SPEC） | `675d958`（PLAN/SPIKE/AUDIT）、`c79f667`（WP5-V3）、`961ebc6`（WP5-V4/WP7-V2）、`50e57c6`（WP7-UI）、`98f3b91`（UI-CHANNEL）、`3ab5974`（两 HANDOFF）、`1adf3c2`（WP6）、`750dbd8`（INSTANCE-SPEC）；`docs/dev/*.md` | 新增 | legacy | `01` 矛盾表 10 项漂移；`05:140-145` 明确不再作方案；仅 SPIKE 结论可复用 | drop（除 SPIKE 结论） | 是否整体归档到 docs/archive |
| D12 Tests/Evidence | `%TEMP%` spike 系列（A/B/C/D/E/M/N/O/P/Q/R） | 无 Git 溯源；相关 `1cc634e`、`8dd6782`；`%TEMP%\kilo\spike*-evidence.json`、`spike*.py` | 新增 | partial | 原始行为证据：页面/bundle/native/signer/内存；依赖真实 CDP/账号 → 不可复跑 | keep（只读结论） | 是否迁入 docs/dev/evidence（脱敏后） |
| D12 Tests/Evidence | Build 直连日志（25 份） | 无 Git 溯源；相关 `1cc634e`、`8dd6782`；`%TEMP%\kilo\direct-run*.log`、`inst-*.log` | 新增 | legacy | Build 失败路径/协议细节；旧 signer 主路径；不可复跑 | keep（失败模式） | 全量保留或只留摘要 |
| D12 Tests/Evidence | Bundle 逆向资产 | 无 Git 溯源；相关 `1cc634e`、`a10953d`；`%TEMP%\kilo\bundles-current\`、`chunks\`、`probe_bundle*.py`、`probe-current.cjs` | 新增 | partial | 当前 bundle 函数/字段/resource URL；离线可读；bundle 版本会漂移 | port（离线分析） | 是否按版本重建 bundle 快照 |
| D12 Tests/Evidence | Native 9998/signer 探针 | 无 Git 溯源；相关 `8dd6782`、`8e5e0cb`；`%TEMP%\kilo\probe_signer_live.py`、`spikeQ*.py/json` | 新增 | partial | Q4 native 200/2 frames/3.01s；signer headers/tb/selfTest；真实账号 → 不可复跑 | port（native 结论）；drop（signer 默认阻塞） | signer 是否只作备用 |
| D12 Tests/Evidence | 并发/预算/切号旧探针 | 无 Git 溯源；相关 `1201a37`、`b59d6f3`、`1adf3c2`；`%TEMP%\kilo\wp5_budget_race_probe.py`、`wp5_cancel_probe.py`、`wp5v3_parallel_chat.py`、`wp6_coexist.py`、`wp6-stats.json` | 新增 | legacy | 旧架构：同页 2/4 并发、强制 429 切号、统计；真实账号 → 不可复跑 | keep（旧行为 oracle） | 是否作为 rebuild 对照基线 |
| D12 Tests/Evidence | 内存采样 | 无 Git 溯源；相关 `79901b0`、`9786b3a`；`%TEMP%\kilo\mem-dual.csv`、`mem-private-2c.csv` | 新增 | partial | 7 个 chrome 进程；private 约 388–481MB；单点采样，非 1GB 压测 | keep（规模基线） | 是否把 1GB 门槛写死 |
| D12 Tests/Evidence | mt-audit 合并快照 | 无 Git 溯源；`4623109..HEAD`；`%TEMP%\kilo\mt-audit\*.base / *.ours / *.theirs` | 改造 | working | 12 文件 ours/theirs/base 快照，可复核合并取舍 | keep | 是否入库防丢失 |
| D12 Tests/Evidence | wp7p2 账号导入夹具 | 无 Git 溯源；相关 `7cd6d74`、`2944c3c`；`%TEMP%\kilo\wp7p2\accounts.json`、`single-auth.json`、`broken.json`、`profiles.json` | 改造 | partial | UI 导入边界夹具；含真实 auth/cookie → 不可复跑、禁止复制 | drop（凭据）/port（脱敏形状） | 是否重建脱敏夹具 |
| D12 Tests/Evidence | 仓库内 evidence 快照 | `72595f4`（p2/p3）、`e008509`（wp5）、`4035d36`/`c79f667`（wp5b）、`b59d6f3`/`7cd6d74`（wp5v3）、`1adf3c2`（wp6）、`a6247e2`（wp7）、`2944c3c`（wp7-v2）、`c210d07`（wp8）、`1f9ba61`（wp10）、`2b39b53`（ui-p1/wp11-responses）、`af3f55c`（wp11-count）、`a59c885`/`9786b3a`（p5-final）；`docs/dev/evidence/**` | 新增 | partial | SSE/日志/截图/harness；多为旧架构；wp5v3 raw 含并发/切换/轮转 | port（原始证据） | 是否按 domain 重新索引 |
| D12 Tests/Evidence | Ark build-channel 证据 | `66fa05f`；`docs/ark-reference/build-channel/evidence-20261017/*` | 新增 | working | bundle 考古：GenerateContent server-streaming、wire 字段、candidateCount 零命中 | keep | 是否作为协议权威 |
| D12 Tests/Evidence | 提交信息质量门自证 | `8e5e0cb`（942/双并发）、`9de3c8c`（919，见 `%TEMP%` handoff）、`b59d6f3`（774，见 `docs/dev/evidence/wp5v3-20261019/README.md:114`） | 改造 | unverified | 未验证：942 与本次收集一致；双并发无独立工件；自报不得作 E2E 证据 | port（仅线索） | 是否要求每次验收附 raw 工件 |
| D12 Tests/Evidence | `%TEMP%` handoff（20261021） | 无 Git 溯源；HEAD `9de3c8c`；`%TEMP%\kilo\handoff-aistudio-api-20261021-1326.md` | 改造 | partial | 记录 max_contexts=1、cookie fix、919 passed、未提交；含真实账号 ID 与路径 | keep（上下文） | 是否转为正式交接文档 |

## 冲突/缺口

| 缺口 | 现状 | 影响 |
|---|---|---|
| 最终镜像 E2E | 未验证：无 `tests/e2e`；仅 CI 构建成功（`03:10`） | 无法证明镜像内路由/实例/流式 |
| Build 双并发 E2E | 未验证：仅 `8e5e0cb` 正文 + `03:11`；无 raw | 200/0 导航不可复核 |
| 自动切号 | 未验证：当前 routing 路径无 live；旧控制面 live 成功（wp10）；`03:12` 称瘫痪 | 换号可靠性未证 |
| 24h 保活 | 未验证：无 24h 实耗；build-only 不回写（`02:26`） | 24h 后静默失效 |
| 工具调用准确率 | 未验证：仅 93 例解析/注入单测 | 无准确率基线 |
| 消息超时 | 未验证：仅 `test_build_surface.py:707` | 端到端超时/重试未定 |
| 测试固化旧语义 | capacity 用例固化 503/旧实例模型；旧 429 证据在 wp5v3 raw | rebuild 不能直接复用全部用例 |

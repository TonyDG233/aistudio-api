# D07 Build Surface / 9998 / Signer

范围：`src/aistudio_api/infrastructure/gateway/build_surface.py`、`infrastructure/browser/js/build_signer.js`、`client.py` build 分支、`api/app.py` build 错误映射、`instance_pool.py` build 槽位。
基线：`upstream/master..master`（`1cc634e..8e5e0cb`）；两个主文件相对 `upstream/master` 均为纯新增（`git diff --stat upstream/master..master` = 3165 insertions, 0 deletions）。
证据口径：`tests/unit` 单测可复跑；`%TEMP%\kilo` 的实机日志/SSE 只作历史证据，标 `不可复跑`，本次未重跑真实 CDP/账号。
状态口径：`working/partial/broken/unverified/legacy`；无测试且无独立证据 = `unverified`。

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 待用户决定 |
|---|---|---|---|---|---|---|---|
| D07 | Build app_id 解析（config `channels.build.app_url` → `AISTUDIO_APP_URL` → settings → Ark 默认） | `1cc634e`, `da6ea25`; `build_surface.py:421-446`, `model_defaults.py` channels.build.app_url | 新增 | working | `test_build_surface.py:350-393`（env/default/config/裸 id 四例） | keep | 确认 keep |
| D07 | Build 宿主页创建（context 内建页 else 独立新页，`block_assets=True`，建页时注入 realm tag） | `1cc634e`, `28c981f`; `build_surface.py:1044-1056`; `cdp_client.create_page_in_context` | 新增（`create_page_in_context` 亦为 `28c981f` 新增） | working | `test_build_surface.py:397-430`; `test_cdp_browser_context.py:28-45` | port | 确认 port |
| D07 | Build 页面复用快路径（同 auth + 页面存活 + ready + 无 last_error；WS 断开先试原地恢复） | `d4ff844`, `8e5e0cb`; `build_surface.py:1012-1032` | 新增 | partial | 无单测；实机复用命中见 `%TEMP%\kilo\wp6-verify\reuse-run1.sse`、`reuse-run2.sse`（不可复跑） | port | 确认是否保留快路径 |
| D07 | Cookie 注入（`load_cookies_to_page` + 空 auth/注入失败 fail-closed `COOKIES_MISSING`） | `1cc634e`; `build_surface.py:1063-1077`；复用原生 `session_auth.load_cookies_to_page` | 新增（build 调用点）/原生（load_cookies_to_page） | working | `test_build_surface.py:554-561`；`%TEMP%\kilo\p3-build3\acceptance-run1.log` 载入 7 Cookie（不可复跑） | port | — |
| D07 | AUTH_RESPONDER 预注入（`requestAuthIndex` → `authIndex=auth_user`，否则宿主 boot 静默等待） | `1cc634e`; `build_surface.py:110-124`, `:1086-1092` | 新增 | unverified | 无单测；仅 spikeP 配方注释（`build_surface.py:109`） | port | 确认是否需要 authIndex 握手 |
| D07 | Build 资源拦截组（chat 列表去掉编辑库组，Monaco/vs 在 build 放行） | `1cc634e`; `cdp_client.py:26-79`, `build_surface.py:449-457`, `:1059-1061` | 新增 | working | `test_build_signer_assets.py:135-172` | port | — |
| D07 | 前台/focus 与导航 settle（`Page.bringToFront` + `Emulation.setFocusEmulationEnabled` + 180s goto + 2s 稳定） | `1cc634e`, `d4ff844`; `build_surface.py:1098-1114` | 新增 | unverified | 无单测（FakePage 吞掉 CDP send）；仅注释引用 spikeP | port | 确认 focus 仿真是否必需 |
| D07 | 过场弹窗点击器（Continue to the app / Skip / Launch，精确匹配后模糊匹配） | `1cc634e`; `build_surface.py:78-98`, `:1211-1227` | 新增 | unverified | 单测以 `fast_gate` fixture 直接跳过（`test_build_surface.py:221-233`） | port | 确认点击词表是否需维护 |
| D07 | human gate 可信输入（45 次 mouseMoved + 真实 press/release + 8 次 Escape；native 就绪后再过一次门） | `d4ff844`; `build_surface.py:100-105`, `:1361-1421`, `:1167-1168` | 新增 | unverified | 无单测；`p3-build3` 日志最终连上 9998（不可复跑，未隔离门本身） | port | 确认门参数（45/8 次） |
| D07 | 9998 WS sink 与 realm 分桶（`?realm=` 解析、按 realm 归桶、request_id→ws+queue、断连回灌 error） | `1cc634e`, `28c981f`; `build_surface.py:129-226` | 新增 | partial | `test_cdp_browser_context.py:47-70` 仅覆盖 realm 解析与分桶；sink 本体未测；`p3-build3` 日志 `server listening on 127.0.0.1:9998`（不可复跑） | port | 确认单 sink 单端口模型 |
| D07 | realm WS 标记注入（`:9998` URL 补 `?realm=`，`addScriptToEvaluateOnNewDocument`） | `28c981f`; `build_surface.py:907-934`, `:1054` | 新增 | unverified | 无单测 | port | 确认多 realm 共页是否仍需要 |
| D07 | 原生就绪判定（9998 连接即 `native_ready=True`/`ready_state=native`，signer 不作前置） | `8dd6782`; `build_surface.py:1156-1168` | 新增 | working | `test_build_surface.py:397-430`, `:432-501`, `:540-551` | port | — |
| D07 | signer 后台探测（native 就绪后 `create_task`，strict=False，不阻塞 dispatch；重建时取消旧任务） | `8e5e0cb`; `build_surface.py:1174-1176`, `:1190-1209`, `:1041-1042` | 新增 | working | `test_build_surface.py:432-501`（`_await_signer_probe`） | port | — |
| D07 | signer 状态机（installing/harvesting/warmup-captured/ready/failed；ready 需 tb + 自检通过 + 收割 key） | `1cc634e`, `8dd6782`; `build_signer.js:74-163` | 新增 | working | `test_build_signer_assets.py:47-82` | port | 确认状态机是否需简化 |
| D07 | signer 结构发现与自检重试（snapshot+kvc+content 约束；tokenLike H/A/getMetadata；最多 3 次；warmup 复位重试） | `8dd6782`, `1cf3994`; `build_signer.js:166-275`, `:1037-1117` | 新增 | working | `test_build_signer_assets.py:60-129`；`%TEMP%\kilo\probe_signer_live.py` 只读取证快照（不可复跑） | port | 确认是否允许结构发现式 hook |
| D07 | XHR/fetch 收割头白名单与 warmup 本地吞没（`__warmup__` 零上游流量） | `1cc634e`, `8dd6782`; `build_signer.js:55-63`, `:1119-1259` | 新增 | working | `test_build_signer_assets.py:105-113`；`p3-build3` 日志 harvest 6 头（不可复跑） | port | 确认默认 web key 兜底常量去留 |
| D07 | warmup 规格下发（经 9998 向 app 客户端下发 `__warmup__:streamGenerateContent`） | `1cc634e`, `d4ff844`; `build_surface.py:1423-1451` | 新增 | partial | 无单测；`p3-build3` 日志“已下发 warmup 规格”后 1s signer ready（不可复跑） | port | 确认 warmup 是否仍必需 |
| D07 | native dispatch + SSE pump（request_id 关联 9998 会话；headers/chunk/stream_close/error 归一为 chunk/status/done/error） | `8dd6782`; `build_surface.py:819-905` | 新增 | working | `test_build_surface.py:607-634` | port | — |
| D07 | 传输选择（默认 native；`AISTUDIO_BUILD_TRANSPORT=signer/native` 显式开关，仅在目标健康时生效） | `8dd6782`; `build_surface.py:366-377` | 新增 | working | `test_build_surface.py:585-605` | port | 是：是否保留 env 回滚开关 |
| D07 | native→signer 首帧前回退（下发失败或 error 且未出帧；已出帧不回退不重放） | `8dd6782`; `build_surface.py:1664-1679`, `:1693-1700` | 新增 | working | `test_build_surface.py:636-679` | port | — |
| D07 | native 断线重试（请求前无连接先轻量恢复；dispatch 返回 None 再恢复重发；首帧前 error 重试一次） | `1cf3994`; `build_surface.py:1577-1579`, `:1616-1646`, `:1684-1692` | 新增 | working | `test_build_surface.py:792-900` | port | — |
| D07 | 原地恢复 `_recover_warmup`（过门→等 9998→重发 warmup→可选等 signer，不重导航） | `1cf3994`; `build_surface.py:1248-1310` | 新增 | partial | 单测仅以 monkeypatch 替身验证调用次数（`test_build_surface.py:792-900`）；真实恢复路径 `未验证` | port | 确认是否保留“不重导航”策略 |
| D07 | watchdog（5s 轮询 / 断线 10s 宽限 / 后台轻量重连；`_ensure_lock` 持有时跳过，避免并发导航） | `1cf3994`, `8e5e0cb`; `build_surface.py:1312-1359` | 新增 | unverified | 无单测、无本地日志证据；且 `:1314` 无 `_realm_id` 时直接不启动 | port（需补测） | 是：非 realm 实例是否需要 watchdog |
| D07 | `BUILD_BUSY`（页面有在途请求时禁止换账号或重载） | `8e5e0cb`; `build_surface.py:1004-1010` | 新增 | working | `test_build_surface.py:528-537` | port | — |
| D07 | 流式超时（`TIMEOUT`，每次出帧重置 deadline；native 超时可回退 signer 续期一次） | `1cc634e`, `8dd6782`; `build_surface.py:1708-1723`, `:1808-1814` | 新增 | working | `test_build_surface.py:707-713` | port | — |
| D07 | `PAGE_CLOSED`（就绪轮询期、流式循环、queue 等待期三处检测） | `1cc634e`; `build_surface.py:1478-1484`, `:1711-1735` | 新增 | working | `test_build_surface.py:716-720` | port | — |
| D07 | 就绪期错误族（`NAVIGATION_FAILED` / `APP_NOT_CONNECTED` / `SIGNER_FAILED`，均带 stage+处置建议） | `1cc634e`, `da6ea25`; `build_surface.py:1110-1121`, `:1149-1155`, `:1510-1517` | 新增 | working | `test_build_surface.py:397-501`（间接覆盖成功/失败分支）；APP_NOT_CONNECTED 无单测 | port | — |
| D07 | 分发型错误族（`DISPATCH_FAILED` / `NATIVE_WS_MISSING` / `SIGNER_ERROR` / `NATIVE_ERROR`） | `1cc634e`, `8dd6782`; `build_surface.py:1648-1662`, `:1693-1700`, `:1783-1797` | 新增 | working | `test_build_surface.py:659-679`, `:693-704` | port | — |
| D07 | `NO_CONVERTIBLE_FRAMES` 与丢弃帧诊断（dropped 样本进错误体/告警，不静默丢） | `1cc634e`; `build_surface.py:1815-1830`, `:1744-1756` | 新增 | working | `test_build_surface.py:723-737` | port | — |
| D07 | rid 任务队列隔离（`state.task_queues` + binding demux + 满队列反压告警） | `1cc634e`; `build_surface.py:942-973`, `:1604-1606`, `:1831-1837` | 新增 | working | `test_build_surface.py:752-778` | port | — |
| D07 | `abort_stream(rid)` 按任务中止（写 error 事件到该 rid 队列） | `1cc634e`; `build_surface.py:1844-1854` | 新增 | partial | `test_build_surface.py:781-789`；`src` 内无调用方（仅 `transport.py` 同名方法），HTTP 断开不会触发 | drop（除非接 HTTP 断开） | 是：是否需要断开→abort 联动 |
| D07 | Holder 解码（2–3 层嵌套数组 / 裸叶 / 单层转义字面量 → leaf 对象） | `1cc634e`; `build_surface.py:480-530` | 新增 | working | `test_build_surface.py:260-287` | port | — |
| D07 | REST→wire 适配（candidate/part/usage/responseId 索引映射 + finishReason 名称表） | `1cc634e`; `build_surface.py:549-631`, `:235-267` | 新增 | working | `test_build_surface.py:260-320` | port | — |
| D07 | JSPB `convChunk` 兼容分支（位置数组叶 → REST 再转 wire） | `1cc634e`; `build_surface.py:673-754` | 新增 | working | `test_build_surface.py:322-330` | port（仅兼容保留） | 确认是否保留 JSPB 分支 |
| D07 | 上游错误包识别（`error` dict → `UPSTREAM_ERROR`；RPC 记录 → `UPSTREAM_RPC_ERROR`） | `1cc634e`; `build_surface.py:757-790` | 新增 | working | `test_build_surface.py:331-349` | port | — |
| D07 | Build 事件→chat 面事件映射（status/body/thinking/tool_calls/images/usage/finish_reason/response_id/thought_signature） | `1cc634e`; `build_surface.py:1738-1782`, `:1839-1842` | 新增 | working | `test_build_surface.py:569-582`, `:681-691` | port | — |
| D07 | `BuildSurfaceError`→HTTP（可重试集合 503，其余 502，带 code/detail/advice） | `1cc634e`; `app.py:311-334` | 新增 | working | `test_app_error_handlers.py:24-29` | port | — |
| D07 | **身份事实**：build 认证只取 lease（`_build_lease_auth`），执行期包在控制面 `self._session.request_scope()` | `8e5e0cb`; `client.py:158-177`, `:588-599`, `:698-710`; `session.py:146-160` | 新增 | working | `test_build_lease_identity.py:11-40`；`request_scope` 记的是全局 session/`active_context` 计数 | port（须显式标注） | 是：是否改用 `adapter.session.request_scope()` |
| D07 | build 面实例配额（无 lease 时按 active account/group + chat+build 活页数校验 `max_page_instances`） | `ec35c8d`, `d8bbb22`; `client.py:307-360`, `:586-587`, `:696-697` | 新增 | working | `test_rotator_surface_keys.py:237-296`（含 build 分支先配额后就绪） | port（归属 D04/D05 实现） | 确认归属域 |
| D07 | build-only 实例集成（bootstrap chat 页关闭、`build_ready` 需 native/signer 至少一路、并发建页只计 1、失败释放预约） | `da6ea25`, `8e5e0cb`; `instance_pool.py:110-140`, `:1028-1036`, `:1155-1171` | 新增 | working | `test_instance_pool.py:204,230,307,421,482,529,861,890` | port（归 D04 实现） | 确认 build-only 口径 |
| D07 | 非流式 build 收集（`_collect_build_output`：status≥400 抛错，聚合 text/thinking/tools/images/usage） | `ec35c8d`; `client.py:630-666` | 新增 | partial | 无直接单测（`未验证`）；仅流式路径有覆盖 | port | 确认非流式 build 是否保留 |
| D07 | 清理路径（新页未就绪即关闭、finally 取消 pump/清 pending/task_queues、切号与 close 关 surface、slot 关页） | `1cc634e`, `8e5e0cb`; `build_surface.py:1180-1186`, `:1831-1837`; `client.py:226-235`, `:419-424`; `instance_pool.py:142-155` | 新增 | working | `test_instance_pool.py:307`; `test_build_surface.py:569-582`（in_flight 归零） | port | — |
| D07 | signer 脚本缺失降级（只启用原生 9998，记录 `signer_error`，不阻塞渠道） | `8dd6782`; `build_surface.py:1079-1085`, `:1292-1298`, `:1177-1179` | 新增 | working | `test_build_surface.py:540-551` | port | — |
| D07 | 失败现场截图 PNG + 页面 HTML dump（Build 流程失败时保存，供 debug） | 用户要求/参考项目；无 Git 溯源 | 新增 | unverified | 未验证；参考项目实现待移植 | port | 待用户决定 |

## 冲突/缺口

| 编号 | 冲突/缺口 | 影响 | 证据 |
|---|---|---|---|
| G1 | build 身份只取 lease，但执行期 `self._session.request_scope()` 记的是控制面全局 session/`active_context`；页面却建在 lease adapter 的 context（CDP client 也取自 `self._session`） | 跨账号计数与 keepalive busy 判定可能失真；租约身份与控制面身份不一致 | `client.py:158-177`, `:589`, `:699`; `session.py:146-160` |
| G2 | `_count_live_page_instances` 只数全局 chat 页 + 全局 build 页，不数 pool slot 页 | pool 模式配额可能低估；client 级 surface 与 slot surface 可能各建一页 | `client.py:250-263`, `:343-344`; `instance_pool.py:126-140` |
| G3 | 无 realm 时 `_connections()` 回退到模块级 `_APP_WS_CONNS`，且 realm 连接同时写入全局列表 | 非 realm surface 可能认领其他 realm 的 9998 连接 | `build_surface.py:150-162`, `:816-817` |
| G4 | `_recover_warmup` 在 signer 脚本缺失时仍下发 warmup（无页面吞没器） | 可能产生真实上游请求，违反 signer “零上游流量”约定 | `build_surface.py:1288-1298`; `build_signer.js:1119-1160` |
| G5 | watchdog 仅 `_realm_id` 存在时启动，且任务无取消出口；`aborted` 事件分支无任何产生方 | 非 realm 实例断线只能靠请求触发恢复；abort 路径形同虚设 | `build_surface.py:1314-1321`, `:1798-1806`; `:1844-1854` |
| G6 | `abort_stream` 在 `src` 内无调用方；HTTP 断开不联动 build 任务 | 客户端断开后生成继续占用 build 页与配额 | `build_surface.py:1844-1854`; `transport.py:130-137` |
| G7 | 复用条件依赖 `last_error` 为空，但非 strict 探测失败只写 `signer_error`；`last_error` 仅 strict 路径设置 | 复用门槛语义分裂，signer 失败后页面仍被判可复用 | `build_surface.py:1016-1023`, `:1506-1522`, `:1525-1541` |
| G8 | human gate / 过场点击 / focus 仿真 / watchdog / 原地恢复 / sink 本体均无单测，仅有不可复跑实机日志 | 重建后无回归护栏，只能靠真实账号验收 | `test_build_surface.py:221-233`; `%TEMP%\kilo\p3-build3`、`wp5-verify`、`wp6-verify`、`spikeP/Q*-evidence.json` |
| G9 | 默认 app id 为硬编码外部应用，`da6ea25` 换过一次 | 重建时来源/授权/可用性需用户确认 | `build_surface.py:69`; `da6ea25` |

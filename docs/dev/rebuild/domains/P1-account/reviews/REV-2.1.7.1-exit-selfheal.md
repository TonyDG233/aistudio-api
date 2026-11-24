# REV-2.1.7.1 — 出口自愈三路并行改动只读审查

- 审查日期：2026-11-24
- 审查角色：独立只读 Reviewer
- 审查对象（工作区未提交改动，主仓相对 `HEAD=73b1aaa`；管理器相对其本地 git `HEAD=26eb4a9`）：
  1. 主仓 app 侧：`application/{exit_arbitration,account_keepalive,routing_pipeline,instance_manager,verify_account_service}.py`、`infrastructure/account/account_info_refresher.py`、`infrastructure/waa/engine_runtime.py` + 对应 `tests/unit/*`。
  2. 管理器（独立仓，gitignored）：`data/tools/exit-manager/` 全量 diff（`manager/config/state/health/fanout/server/main` + `tests/*`）。
  3. 宿主脚本（gitignored）：`data/tools/vps-exit-scripts/`（`warp-health-inst.sh`、`warp-health-all.sh`、`warp-provision-instance.sh`、`fanout-guard.sh` + `cron.d-warp-health-pool` + `README.md`）。
- 依据：`AGENT.md`、`docs/dev/rebuild/06-decision-log.md`、`docs/dev/rebuild/08-09-decision-record.md`（D-01..D-14）、`docs/dev/rebuild/domains/P1-account/research/exit-pool/{PLAN,CONTRACT}.md`、项目记忆（`exit_pool_lease_api`、`external_mode_no_direct_fallback`、`headless_qr_flow_not_verified` 等）、本次改动源码与实跑门禁。
- **结论：`CONDITIONAL`（0 P0 / 5 P1 / 5 P2 / 4 Q）**。三块改动的**跨边界契约本身成立**：region 403 不再触发 verify 连坐烧毁、verify 写锁入口已收敛、generation 直连拒绝覆盖冷启动、脚本 DRY_RUN 对四个危险动作全屏蔽、旧配置无新键可正常启动。但**管理器的自动恢复实现存在 3 个必须先处理的 P1**（持锁做网络/子进程导致全量租约阻塞、无小时/单实例预算的批量轮换、`rotate_on_auto_recover=true` + 空 `rotate_command` 会把健康出口判失败并最终 disable），叠加脚本与管理器对同一实例双重轮换、fanout-guard 无限期 10 分钟重启。**当前不建议整包直接上线；app 侧契约修复与脚本可先上（脚本保持 DRY_RUN=1），管理器需按 P1 修复或先以安全配置降级后再上。**

> **复核轮次 2（2026-11-24 12:44）**：两处修复后重新独立复核（未假设改对）。首轮 5 个 P1 的爆炸半径已消除；P1-1/P1-2/P1-3/P1-5 关闭，P1-4 脚本侧关闭但发现“管理器手动 `recover()` 默认仍轮换且不持共享锁、README 却称管理器从不执行 rotate-instance.sh”的新的 P1 口径冲突。**复核结论改为 `CONDITIONAL`**（0 P0 / 1 新 P1 / 8 P2）；详见文末「复核轮次 2」。

## 0. 审查边界与只读声明

- 唯一写操作：本文件。**未改任何代码/配置/脚本，未 commit、未 push、未启动服务/浏览器、未 ssh/外网/真实账号/真实 Google。**
- 只读命令：`git status/diff/log`、`read/grep`、`uv run ruff check .`、`uv run pyright src tests`、`uv run pytest -q`、管理器 `python -m unittest discover -s tests -v`、`git diff --check`、Git Bash `bash -n`、以及一个 `$env:TEMP` 下的旧配置 `--check-config` 验证（已删除临时目录）。
- 未触碰 `05/06/08-09` 文档；未修改 `data/` 下任何被审文件。
- 行号均以审查时工作区内容为准。

## 1. 门禁真实输出摘要

| 门禁 | 命令 | 实际结果 |
|---|---|---|
| 管理器单测 | `cd data/tools/exit-manager && python -m unittest discover -s tests -v` | **Ran 174 tests in 7.750s — OK**（含新增 `tests/test_auto_recover.py`） |
| 主仓 Lint | `uv run ruff check .` | **All checks passed!**（RC 0） |
| 主仓类型 | `uv run pyright src tests` | **0 errors, 0 warnings, 0 informations**（RC 0） |
| 主仓单测 | `uv run pytest -q` | **2162 passed in 66.19s**（RC 0） |
| 空白/冲突 | `git diff --check`（主仓 + 管理器） | 两侧 RC 0；仅 CRLF 提示，无空白错误 |
| 脚本语法 | `bash -n` × 4（`D:\coding\Git\bin\bash.exe`） | `warp-health-inst.sh` / `warp-health-all.sh` / `warp-provision-instance.sh` / `fanout-guard.sh` 均 **rc=0** |
| 旧配置兼容 | `python main.py --config <temp旧配置> --check-config` | **RC 0**，打印 `quarantine: ttl=1800.0s auto_recover_rotate=on`、`fanout: machine_swap=off swap_path=/api/machine/swap` |

> 说明：`C:\WINDOWS\system32\bash.exe` 是未安装发行版的 WSL 桩（`bash -n` 全部 rc=1，属环境问题），已改用本机 Git Bash `D:\coding\Git\bin\bash.exe` 完成语法校验并全部通过。

## 2. 跨边界契约逐条验证

### 契约 1：事件契约（region 403 不再烧出口）— **PASS**

**App 侧（发送方）：**
- `region_suspect` 事件上报：`exit_arbitration.py:428-440` → `report_event_nowait(event_kind=REGION_SUSPECT_EVENT_KIND, canary="account_bad")`；`REGION_SUSPECT_EVENT_KIND = "other_403"`（`exit_arbitration.py:52`）。
- 仅当 canary 探测到**出口本身**区域封锁时才发 `region_403/exit_bad`（`exit_arbitration.py:415-427`）；canary 返回 200 → `region_suspect`（`exit_arbitration.py:236-241`），不再叫 `account_bad`。
- 实际 payload 组装：`resolver.py:781-795`（`kind`/`canary`/`account`(HMAC)/`exit_id`/`ts`）。

**管理器侧（接收方）：**
- `EVENT_KINDS = {"region_403","verify_account","other_403"}`（`manager.py:50`）→ 接受 `other_403`；`CANARY_VERDICTS = {"exit_bad","account_bad","inconclusive"}`（`manager.py:51`）→ 接受 `account_bad`。
- 隔离出口**只在** `canary == "exit_bad"` 时发生：`manager.py:1367-1375`。
- verify 连坐烧毁**只在** `kind == "verify_account"` 时发生：`manager.py:1380-1382` → `_record_verify_burn`（`manager.py:1385-1413`）。

**结论：** `other_403 + account_bad` 走 `else` 分支仅记录事件（`manager.py:1376-1378`），既不 quarantine、也不进 verify 烧毁分支，**region 403 不再烧出口**。唯一仍会隔离出口的是“canary 也被 region 封锁”的 `exit_bad`（`manager.py:1367-1375`），属出口级真实区域封锁的正确隔离，不属于 verify 连坐。**契约成立。**

### 契约 2：verify 写锁的唯一入口 — **PASS（5 个写点，均显式 verify 报文/Sandbox）**

| # | file:line | 触发条件 |
|---|---|---|
| 1 | `domain/errors.py:59` + `errors.py:352-361` | `AuthError.verification_required` 仅由报文特征置位（markers：`errors.py:13-20`，含 `aistudio.google.com/verify-account`、`free_tier_verification_required`），`classify_error` 对 verify 报文优先返回 `AuthError` |
| 2 | `routing_pipeline.py:1018-1022` → `account_orchestrator.py:72-83` → `account_rotator.py:1116-1146`（写 registry 在 `:1133`） | 生成/令牌请求的真实上游 403 里被 `classify_error` 判为 `verification_required` |
| 3 | `account_info_refresher.py:885-918`（`_verify_required_response`，写锁 `:896`）；调用点 `:1212-1224` / `:1552-1568` | maintenance 探针 `status==403` 且 body 命中 verify 特征 |
| 4 | `account_keepalive.py:437-463`（写锁 `:446`）的调用点：`:838-842`（`region_outcome=="verify_account"`）与 `:1293-1299`（RotateCookies 响应体显式 verify，`_rotation_is_verify_account` `:466-471`） | 仅显式 verify 报文；region canary 已改为只记 `region_suspect`（`:420-435`） |
| 5 | `api/ui_sandbox.py:2177` | 仅 `AISTUDIO_UI_DEBUG` 纯内存沙盒 fixture，不进生产 |

- `region_outcome="verify_account"` 全仓**唯一**赋值点：`account_info_refresher.py:918`（显式 verify 响应内）。
- `region_suspect`/`account_bad` 在 keepalive（`:420-435`）与 refresher（`:867-878`）都**只记日志、不写 `verify_required`**，并有测试 `test_metadata_region_suspect_does_not_write_verify` / `test_auth_update_signer_rotate_region_403_account_bad_stays_suspect` 断言 `stored.verify_required is False`。

**结论：** 写入点已收敛为“显式 verify 报文”+ Sandbox fixture，**region 403 与 canary 判定不会再写 `verify_required`**。

### 契约 3：自动恢复与轮换的爆炸半径 — **不达标（见 P1-1 / P1-2 / P1-3）**

**代码路径：** `_refresh`（`manager.py:412-464`，`auto_recover` 默认 True）→ `_auto_recover_expired`（`:1261-1276`，选出 `now - quarantined_at >= ttl` 的全部出口）→ 依次 `_auto_recover_one`（`:1198-1259`）：
- 先置 `PROBING` 并落盘（`:1200-1201`）；
- `_auto_probe`（`:1170-1196`，WARP 仅 L0/L1）；
- 若 L1 通过且 `kind=="warp"` 且 `rotate_on_auto_recover` → **立即** `_rotate_and_update` 一次（`:1208-1213`），再复检（`:1214-1215`）；
- 成功 → 回 healthy、`quarantined_at=None`、`auto_recover_failures=0`（`:1226-1234`）；
- 失败 → `quarantined_at=now`、`auto_recover_failures+=1`，达到 3 次转 `DISABLED`（`:1236-1250`）。

**推导（`quarantine_ttl_s=1800` + `rotate_on_auto_recover=true`，4 条 WARP 全部隔离）：**
- t0 全部隔离 → 约 t0+1800（健康循环 120s 粒度，最迟 ≤ t0+1920）一次 `_refresh` **串行**处理 4 条；每条可触发 1 次 `rotate-instance.sh` → **单轮最多 4 次、集中在一轮内（约数分钟）**。
- 若每次轮换后复检仍失败，则各自 `quarantined_at` 重置，再等一个 TTL；**最多 3 轮**（第 3 次失败即 disable）→ **约 90 分钟内最多 12 次 `rotate-instance.sh`**，且呈“每 ~30 分钟 4 连发”的突发形态。
- **同一实例**：单轮内不会重复（`expired` 快照 + 置 `PROBING` 后不再被同轮选中，`:1263-1273`），但跨轮最多 3 次；其间若被新事件/健康失败重新隔离，可继续叠加。
- 管理器**没有**全局小时预算或单实例冷却；`config.py:126` 默认 `rotate_command=()`，示例配置 `config.example.json:41-46` 指向 `rotate-instance.sh`。脚本侧的 `MAX_ROTATE_PER_HOUR=3` / `PER_INSTANCE_COOLDOWN_S=1800`（`warp-health-inst.sh:45-46`）**完全不参与**管理器决策。

**结论：** “最少几次”不是问题（0 次也可），但“最多”是 **12 次/90 分钟、突发 4 连发且无预算闸门**，与脚本设计的安全预算相反；同时见 P1-1 的持锁阻塞。

### 契约 4：脚本默认安全（DRY_RUN=1）— **PASS（但有 P2 副作用）**

逐个脚本核对“**绝不** 执行 `rotate-instance.sh` / `systemctl restart` / 写 `/etc/wireproxy/inst-N.conf` / 改 `instances.env`”：
- `warp-health-inst.sh`：`DRY_RUN` 默认 1（`:43`）；轮换唯一入口 `do_rotation`（`:271-294`）仅在 `hard_failure` 的 `DRY_RUN` 闸门之后调用（`:337-343`），dry-run 直接 `emit_result ... dry_run` 返回（`:337-341`）。**四个危险动作均不执行**。
- `warp-health-all.sh`：只循环调用单实例脚本（`:35-38`），自身不执行任何危险动作。
- `warp-provision-instance.sh`：`run_cmd`（`:92-99`）dry-run 只打印；`install_file`（`:102-113`）dry-run 只打印；`create_conf`（`:190-193`）dry-run 直接返回、不调注册函数；`record_instance`（`:245-251,298-303`）dry-run 只 diff；`rollback`（`:312-315`）dry-run 只打印；`post_check`（`:324-327`）dry-run 不执行。**不写 `/etc/wireproxy/inst-N.conf`、不改 `instances.env`、不 systemctl**。
- `fanout-guard.sh`：`DRY_RUN` 默认 1（`:22`）；`systemctl restart` 在 dry-run/限频判定之后（`:101-110`），dry-run 返回 2 且不写 `last-restart`（`:107-109`）。
- `cron.d-warp-health-pool`：两条 cron 均显式 `DRY_RUN=1`（`:20-21`），注释中的 `DRY_RUN=0` 仍被注释（`:24-25`）。

**P2 副作用（不违反上述四动作，但需知晓）：** `warp-health-inst.sh` 在 dry-run 下仍会 `mkdir /var/run/warp-pool` 并**递增** `fail-N`/`http-N` 计数（`:299-301,377-379`）、追加 `events.log`；`warp-provision-instance.sh` 在 dry-run 下仍会 `source warp-common.sh`（`:343-346`）并在 `create_unit` 写 `$STATE_DIR/*.tmp`（`:224-240`）。README 称“只打日志”不准确（见 P2-2）。长期 dry-run 后翻 `DRY_RUN=0`，失败计数可能已 ≥2，首轮即可能轮换。

### 契约 5：脚本与现有系统冲突 / fanout 重启风暴 — **部分不达标（见 P1-4 / P1-5）**

- **旧系统独立性（PASS）**：旧 `warp-health-check.sh` 只处理 `40000/40001`、`STATE_DIR=/var/run/warp-health`、调用 `wireproxy-rotate.sh`（`warp-health-check.sh:4-12,82`；`wireproxy-rotate.sh:47-53`）。新脚本处理 `40010..40013 / 40110..40113`、`STATE_DIR=/var/run/warp-pool`、调用 `rotate-instance.sh N`（`warp-health-inst.sh:74-75,52`）。端口、状态目录、轮换脚本、systemd 单元均不重叠；`fanout-guard.sh` 用 `/var/run/fanout-guard`。**无直接冲突。**
- **管理器 vs 脚本双重轮换（P1-4，不达标）**：管理器 `_run_rotate_command`（`manager.py:1417-1444`）与脚本 `do_rotation`（`warp-health-inst.sh:271-294`）调用同一个 `rotate-instance.sh N`，二者无共享锁、无共享冷却，管理器也不做 quiet-gap。管理器可在 quarantine TTL 到期时对脚本刚轮换过的实例再次轮换，或在有在途流量时轮换（cut live stream）。
- **fanout-guard 重启风暴（P1-5）**：`MIN_INTERVAL_S=600`（`:24`）配合 cron 5 分钟，最坏恒定 **6 次/小时**重启整个 fanout 服务；`SETTLE_S=5` 后若仍不达 `EXPECTED_OPENVPN=4` 只返回 1，下一轮仍会再试，**无连续失败上限/退避**（`:112-126`）。若某条 openvpn 永久不可用，会无限期每 10 分钟杀掉 4 条 paid 隧道。另外 `count_openvpn` 用全局 `ps -eo args | grep -c '[o]penvpn'`（`:54-57`），会把无关 openvpn 计入，可能误判健康。

### 契约 6：generation 直连拒绝 — **PASS**

- 冷启动：`engine_runtime.py:484-489` 在 `create_session` 调 `_resolve_exit_proxy(..., category="generation")`（`:542-548`）；`direct_fallback` 在 `:562-578` 被代码级 invariant 拒绝并抛 `RoutingError`（不读应急阀）。
- 后续租约：`request_scope` → `_lease("generation")`（`:124-136`）→ `resolver.lease`（`resolver.py:523-615`），`LeaseResult` 只可能 `ok/no_capacity/error`，解析阶段 `_parse_lease` 对空/非法 socks 返回 error（`:647-665`），**结构上不可能产出 direct**；legacy `resolve` 路径无出口返回 `None` 并抛 `RoutingError`（`:138-149`）。
- maintenance：`_lease("maintenance")`（`:184,197`）同样走 `lease`（无 direct）；凭据维护的 `resolve_credential_exit(category="maintenance")` 未被改动，仍保持“显式开阀才 direct”的旧行为（`resolver.py:243-317`，`:311-315`）。
- 测试：`test_credential_exit_routing.py:1391/1419/2387`（生成/维护/cold-start 三条 direct 语义）。

### 契约 7：INVALID 自愈退避 — **PASS（附 Q-1 / Q-3）**

- 退避：`INVALID_RECOVERY_BACKOFF_S=(30,120,600)`（`instance_manager.py:54-56`）。
- `active_requests>0` 时**不重启**：`_heal_invalid_records` 先查 `slot.active_requests>0`，命中则仅 `invalid_recovery_at=now+30` 重排、**不消耗尝试次数**（`:646-649`）；`_restart_record` 再兜底检查并抛 `InstanceBusyError`（`:613-618`）。未违反 D-05 的“在途请求不得被中断”。
- 3 次失败停止：`invalid_recovery_attempts>=MAX` 后 `_schedule_invalid_recovery` 置 `invalid_recovery_at=0`（`:623-631`），`_heal_invalid_records` 直接 `continue`（`:639-645`）；测试 `test_invalid_slot_self_heal_gives_up_after_three_failures` 断言后续 10000s 不再 attempt。
- 触发点：`list_instances`（`:337-338`）与 `wake_eligible`（`:719-720`），**非独立定时器**；无流量/无 UI 轮询时不会自愈（`wake_eligible` 覆盖消息到达）。

### 契约 8：配置兼容（旧 config.json 无新键）— **PASS**

- `health.quarantine_ttl_s`：`config.py:119` 默认 1800.0，解析 `raw.get("quarantine_ttl_s", 1800.0)`（`:350-357`）。
- `warp.rotate_on_auto_recover`：`config.py:129` 默认 True，`if "rotate_on_auto_recover" in raw else True`（`:382-384`）。
- `fanout.machine_token`（默认 None）/`swap_path`（默认 `/api/machine/swap`）：`config.py:399-424`。
- 实跑旧配置（删掉 4 个新键）`--check-config` → **RC 0**，正确回落到上述默认值；`main.py:94-167` 的 `--check-config` 在任何 `ExitManager`/state 目录副作用之前返回。

## 3. 问题清单

### P0（阻塞，0 项）
无。

### P1（上线前必须处理）

**P1-1 自动恢复在持有管理器全局锁时做网络探测 + `rotate-instance.sh` 子进程，阻塞全部租约。**
- 证据：`refresh()` 持 `self._lock` 调 `_refresh`（`manager.py:1166-1168`）；`status_summary()` 同样持锁调 `_refresh(now)`（`:1563-1566`）；`_refresh` 在锁内调 `_auto_recover_expired`（`:456-457`）；`_auto_recover_one` 在锁内 `_auto_probe`（`:1207`）、`_rotate_and_update`（`:1213`，默认 `_run_rotate_command` 子进程 timeout=120，`:96,1437-1444`）、再 `_auto_probe`（`:1215`）。服务端是 `ThreadingHTTPServer`（`server.py:11,307`），`lease()` 需要同一把 `RLock`（`manager.py:742-744`，且 `auto_recover=False` 只是不主动触发，**仍会被持锁者挡住**）。
- 影响：4 条 WARP 同时到期时，单个 `_refresh` 可在锁内串行执行最长 ~4×(探针 ≤16s + 轮换 ≤120s + 复检 ≤16s) ≈ **10 分钟**，期间 `/v1/lease` 全部排队 → app 生成请求阻塞/超时。
- 修复建议：`_auto_recover_expired` 移出锁（先快照到期列表并置 `PROBING`，释放锁后再探测/轮换，再回锁写终态）；`status_summary` 改为只做 `auto_recover=False` 的轻量刷新或异步触发；探针/子进程绝不在 `_lock` 内执行。

**P1-2 自动恢复轮换无小时/单实例预算，且在 L1 已健康时仍强制轮换（IP churn / verify 放大）。**
- 证据：`_auto_recover_one` 在 `result.status==HEALTHY` 仍 `rotate_on_auto_recover` 时轮换（`manager.py:1208-1213`）；管理器无小时预算或单实例冷却（`config.py` 无对应项，`_auto_recover_expired` `:1261-1276` 仅按 TTL）。脚本侧本有 `MAX_ROTATE_PER_HOUR=3`、`PER_INSTANCE_COOLDOWN_S=1800`（`warp-health-inst.sh:45-46`），管理器绕过。
- 影响：见契约 3 推导，**12 次/90 分钟、4 连发**；且“已健康还换 IP”会把本已可用的出口换成可能被 region 封锁的新 IP，疑似反向放大风控/verify。
- 修复建议：① 增加管理器级 `rotate_hourly_limit`（≤3）与 `per_exit_rotate_cooldown_s`；② 仅在“L1 仍为 region/失败”时才考虑轮换，L1 已健康直接回 healthy；③ 默认 `warp.rotate_on_auto_recover=false`，把轮换交给脚本的 quiet-gap 预算。

**P1-3 `rotate_on_auto_recover=true`（默认）叠加空 `warp.rotate_command`（默认/旧配置）会把健康 WARP 出口判失败并最终 disable。**
- 证据：`config.py:126` 默认 `rotate_command=()`，`_parse_warp` 缺失时为空（`:371`）；`_run_rotate_command` 空命令直接 `return False, "not configured"`（`manager.py:1418-1420`）；`_auto_recover_one` 在首探健康后轮换失败 → 覆盖为 `ProbeResult(ERROR)`（`:1214-1219`）→ 计失败（`:1235-1241`）→ 3 轮后 `DISABLED`（`:1242-1250`）。
- 影响：若 VPS 的 `/etc/exit-manager/config.json` 未配置 `warp.rotate_command`，**本来健康的出口会被自动判死并 disable**（配置条目保留但不再分配）。
- 修复建议：`rotate` 未配置或失败时，若首探已健康应回 `healthy`（不要把“轮换不可用”当出口失败）；或默认 `rotate_on_auto_recover=false`；并让 `--check-config` 在 `rotate_on_auto_recover=true && rotate_command=()` 时给出 WARNING。

**P1-4 脚本与管理器对同一 `rotate-instance.sh N` 无共享互斥，存在并发/连续双重轮换。**
- 证据：脚本 `do_rotation` 调 `${ROTATE_SCRIPT} ${N}`（`warp-health-inst.sh:271-294`）；管理器 `_run_rotate_command` 调同一脚本（`manager.py:1417-1444`），无共享 state/锁/冷却；管理器轮换无 quiet-gap。
- 影响：同实例短时间内被两个自治系统各轮换一次；管理器轮换还可能切断在途流（脚本专门用 quiet-gap 规避，管理器未继承）。
- 修复建议：二者共用同一把实例锁文件（如 `/var/run/warp-pool/rotate-N.lock`）与同一冷却时间戳；或明确二选一（推荐脚本负责轮换、管理器只负责探针归位）。

**P1-5 `fanout-guard.sh` 在 openvpn 持续不足时无限期地每 10 分钟重启整个 fanout（4 条 paid 隧道）。**
- 证据：`MIN_INTERVAL_S=600`（`:24`）、`systemctl restart "${SERVICE}"`（`:112-116`）、失败后仅 `return 1`（`:125-126`），无连续失败上限/指数退避；`count_openvpn` 全局 grep（`:54-57`）。
- 影响：若某条 openvpn 配置永久坏，guard 会恒定 6 次/小时重启 fanout，反复打断 paid 在途流；全局 grep 还可能把无关 openvpn 计入而误判健康。
- 修复建议：连续 N 次（如 3）重启仍不达标则停止并告警（人工介入），或对“连续失败次数”做指数退避；`count_openvpn` 限定 fanout 子进程（cgroup/`systemctl show -p MainPID` 子树）而非全局 `ps`。

### P2（非阻塞，建议本周期修）

**P2-1 `/v1/status` 新增 `recent_events` 返回 `exit_id` 与 `account_hmac_prefix8`，与“status 只返回聚合白名单”的既有决策和 DEPLOY.md 自述冲突。**
- 证据：`status_summary` 返回 `recent_events`（`manager.py:1588-1592`），记录含 `exit_id`/`account_hmac_prefix8`（`:1358-1365`，`state` 持久化同）；测试 `test_status_reports_structured_recent_events_summary` 明确断言 `event["exit_id"]=="warp-1"`、`account_hmac_prefix8==ACCOUNT_A[:8]`（`tests/test_server.py:336-347`）；而 `DEPLOY.md` §5.4 仍写“不返回 … 出口 id 或完整账号标识”，项目记忆决策 `exit_pool_lease_api` 为“GET /v1/status 只返回聚合白名单”。
- 建议：把逐出口/事件明细只放 `/v1/exits`（已具备），`/v1/status` 移除 `recent_events` 或至少去掉 `exit_id`/账号前缀；或显式更新 DEPLOY 与决策记录（二选一，不能代码与文档互相打架）。

**P2-2 脚本 DRY_RUN 会写状态计数，README“只打日志”表述不准确。**
- 证据：`warp-health-inst.sh:299-301,377-379` 在 dry-run 下仍递增 `fail-N`/`http-N`；README `:10` 称“全部动作默认 DRY_RUN=1：只打日志”。长期 dry-run 后翻 0，首轮可能因历史计数 ≥2 直接轮换。
- 建议：dry-run 不递增硬失败计数（或单独 `dry-N`），并修正 README。

**P2-3 `warp-provision-instance.sh` dry-run 仍 `source warp-common.sh` 并写 `$STATE_DIR/*.tmp`。**
- 证据：`source_warp_common`（`:173-182`）在 dry-run 也会执行；`create_unit` 不受 `DRY_RUN` 约束地写临时文件（`:224-240`）。虽不触碰 `/etc`/systemd/instances.env，但 source 可能执行 warp-common.sh 顶层代码。
- 建议：dry-run 下 source 前提示，或改为 `bash -c '. file; declare -F'` 的子 shell 探测；临时文件写 dry 目录。

**P2-4 管理器 `self._recent_events` 的类型注解已与实现不符。**
- 证据：`manager.py:114` 仍是 `dict[str, float]`，但值已是结构化 dict（`:358-410`）。不影响运行，但会误导后续维护/静态检查。
- 建议：改为 `dict[str, dict[str, Any]]`。

**P2-5 `_auto_probe` 对 fanout 的 `swap_node` 无频率限制。**
- 证据：`manager.py:1170-1185`，`auto_swap_fanout=true && machine_swap_available` 即先 swap 再 probe；每次自动恢复都可能换节点（当前默认 `auto_swap_fanout=false`，故非默认风险）。
- 建议：与 WARP 轮换共用小时预算/单实例冷却。

### Q（提示 / 需确认）

- **Q-1 INVALID 自愈与 `AGENT.md` 硬规则冲突，需用户/决策记录确认。** `AGENT.md` §4.5/§7.1 写“失败/stale 才手动重启，禁止自动 stale 复活”；本次新增有界自愈（`instance_manager.py:337-338,633-682`）。技术上未违反 D-05（busy 不重启）且 3 次即停，但**策略上**是“自动 stale 复活”。若 Phase 计划已授权，请同步更新 `AGENT.md`/决策记录；否则应改为仅手动或加显式开关默认关。另注：`list_instances` 是 GET，会同步触发 `_activate`（真实/沙盒浏览器会话创建），UI 轮询可能造成浏览器启动副作用。
- **Q-2 verify-account 后台确认仍未经实机验证。** `verify_account_service.py:114-128,1446-1455,1620-1626,1722-1884` 新增“页面 success 后有界后台确认（2/5/10/30/60/120/300s，上限 600s）”，逻辑有界且在 `close()` 取消，但项目记忆 `headless_qr_flow_not_verified` 明确“QR 生成→扫码→Network 回调→upstream 2/3 从未实机跑通”。**不得据此宣称扫码闭环已可用**。
- **Q-3 自愈非定时驱动。** 仅在 `list_instances`/`wake_eligible` 触发；无流量且无 UI 轮询时 INVALID 常驻（无请求时通常可接受）。
- **Q-4 部分 INVALID 分支未排程自愈。** `InstanceTierError`/`InstanceCapacityError` 直接把状态置 `INVALID` 但未 `_schedule_invalid_recovery`（`instance_manager.py:1437-1448`）；tier 错误不可自愈可接受，capacity 是否应重试需确认。

### N（观察，无需动作）
- 管理器 `MAX_RECENT_EVENTS` 由 2000 降到 200（`manager.py:55`），事件留存变短但仍在 24h 裁剪 + 200 条上限内，可接受。
- `recent_events` 旧格式（裸时间戳）有兼容迁移（`manager.py:309-345`），重启旧 state 不炸。

## 4. 部署建议（给主控/用户）

1. **app 侧契约修复可先部署**：事件契约、verify 写锁收敛、generation 拒绝直连三块有代码 + 单测 + 全量绿，满足这次“region 403 不再烧出口”的核心目标。
2. **管理器在修复 P1-1/P1-2/P1-3/P1-4 前不要直接上线**。若必须先上，至少用安全配置降级：
   ```json
   { "health": { "quarantine_ttl_s": 604800 },
     "warp": { "rotate_on_auto_recover": false } }
   ```
   即“隔离后只自动探针归位、不自动换 IP、不把健康出口判死”，把轮换继续留给脚本的 quiet-gap + 3 次/小时预算。
3. **脚本先以 DRY_RUN=1 部署观察 24h**（cron 样例已默认 dry-run），同时修正 P2-2 的计数副作用；`fanout-guard` 建议先只观察不加 `DRY_RUN=0`，待补 P1-5 的连续失败上限。
4. **`/v1/status` 的 `recent_events` 泄露口径**：要么移到 `/v1/exits`，要么更新 DEPLOY/决策记录，避免“文档说不返回出口 id、代码却返回”。
5. **INVALID 自愈**：先与用户确认是否授权自动 stale 复活，并同步 `AGENT.md`；未确认前建议加开关默认关。

## 5. 只读声明

本次审查除新建本文件外，未修改任何源码、测试、配置、脚本或文档；未执行 git add/commit/push，未启动服务或浏览器，未访问 VPS/外网/真实 Google/真实账号。所有结论均可由上述 file:line 与门禁命令复验。

---

# 复核轮次 2（2026-11-24 12:44）

- 复核对象：`data/tools/exit-manager/`（未提交 diff 相对其本地 `HEAD=26eb4a9`）与 `data/tools/vps-exit-scripts/`（gitignored，现场文件）。
- 方法：重新读全部关键路径 + 新增测试，重跑全部门禁；**不沿用首轮结论**。
- **复核结论：`CONDITIONAL`（0 P0 / 1 新 P1 / 8 P2 / 4 Q）。** 首轮 5 个 P1 的灾难性爆炸半径（持锁 ~10 分钟、90 分钟 12 次轮换、健康出口被 disable、脚本/管理器双重轮换、fanout 无限重启）已消除；但发现 **P1-R2**：管理器手动 `recover()` 默认 `rotate_on_recover=true` 仍直接执行 `rotate-instance.sh` 且**不持**脚本的 `rotate.lock`，同时 README §3.1.2 声称“管理器不执行 rotate-instance.sh”，交付内文档与代码自相矛盾。

## R2-1. 五个 P1 关闭状态表

| P1 | 状态 | 证据（file:line / 测试） |
|---|---|---|
| **P1-1** 自动恢复移出全局锁 | **关闭（自动恢复路径）；残留 P2-R1** | `refresh()` 锁内只 `_refresh` 认领、锁外逐条处理：`manager.py:1190-1195`；`_refresh` 只归位/认领、返回 id：`:428-488`；`_claim_auto_recover` 认领并置 `probing`：`:1359-1390`；`_auto_recover_exit`/`_auto_recover_probe` 锁外探针：`:1225-1248`；`_auto_probe_timed` 独立超时线程：`:1290-1325`；`_rotate_and_update` 仅在读/写状态时短暂持锁、子进程在锁外：`:1617-1641`（`subprocess.run` 在 `:1609-1611`）。`lease` 用 `auto_recover=False`：`:766-768`；`status_summary` 用 `auto_recover=False`：`:1734-1738`；`/v1/lease` 实为 **POST**：`server.py:149-153`，`/v1/status` 为 GET：`server.py:169-173`。测试：`test_probe_and_rotation_never_run_under_manager_lock`、`test_slow_auto_probe_does_not_block_other_exit_leases`（lease 在创作中的 refresh 下 <1s 返回）、`test_one_exit_is_claimed_per_refresh_pass`、`test_auto_probe_timeout_is_bounded_and_counted`（`tests/test_auto_recover.py:253-366`）。**残留**：冷却到期 L0 探针仍在锁内，见 P2-R1。 |
| **P1-2** 默认关 + 三重闸门 + 失败不判失败 | **关闭** | 默认 `rotate_on_auto_recover=False`：`config.py:130`，`config.example.json` 亦为 `false`；三重闸门在 `_reserve_rotation_slot`：命令非空 `:1333`、单实例 `last_rotated_at + auto_recover_rotate_cooldown_s`（默认 1800s）`:1341-1346`、全局 `_rotation_log` 1h ≤ `auto_recover_rotate_hourly_limit`（默认 3）`:1347-1355`；轮换失败保留探针结论、不计失败：`manager.py:1269-1278`；`_rotation_log` 持久化并在启动时按 1h 裁剪：`:151-161,292`。测试：`test_rotation_budget_limits_auto_recover_rotations`（第 4 条不轮换）、`test_failed_rotation_keeps_probe_result_and_exit_healthy`、`test_auto_recover_config_overrides`。 |
| **P1-3** 空 `rotate_command` 绝不轮换、不判失败 | **关闭** | `_reserve_rotation_slot` 空命令直接 `return False, "warp.rotate_command not configured"`：`manager.py:1333-1334`；`_auto_recover_probe` 拿不到配额即保留探针结果返回：`:1259-1268`；`_run_rotate_command` 双保险 `:1589-1591`；`--check-config` 额外 WARNING：`main.py:140-145`。测试：`test_empty_rotate_command_does_not_fail_healthy_exit`（healthy、`auto_recover_failures==0`、`generation==1`、`rotate.calls==[]`）。 |
| **P1-4** 共享 flock + README 声明 | **部分关闭（脚本侧关闭；管理器手动 recover 仍轮换且不持锁）** | 脚本：`ROTATE_LOCK=$STATE_DIR/rotate.lock`：`warp-health-inst.sh:59`；`acquire_rotate_lock` 用 `flock -n`，busy/unavailable 均跳过：`:282-314`；轮换前取锁、拿不到即 `reason=locked/lock_unavailable` 返回 2：`:403-412`；release：`:316-322`。README 明确“管理器必须 `rotate_on_auto_recover=false`、轮换只由本套脚本负责”：`README.md:187-206`（含 `:194-201`）；cron 注释同口径：`cron.d-warp-health-pool:14-17`。**但** `recover()` 在 `rotate_on_recover`（默认 **true**，`config.py:129`）时直接 `_rotate_and_update`，不取 `rotate.lock`：`manager.py:1069-1072`；而 README §3.1.2 称管理器“只做探针/隔离/状态机归位，不执行 rotate-instance.sh”（`README.md:194-196`），DEPLOY 又说“人工 recover 会调用 rotate-instance.sh”——三处口径不一致。见 **P1-R2**。 |
| **P1-5** fanout-guard 小时上限/停止/跨窗口恢复 | **关闭** | `MAX_RESTARTS_PER_HOUR=3`：`fanout-guard.sh:33`；统计与裁剪：`:87-105`；超限 `return 1` 并 WARNING“需要人工介入”：`:150-154`；重启尝试（无论成败）计数+刷新最小间隔：`:107-113`；跨 1h 窗口后旧记录被 cutoff 忽略、自动恢复重试：`:87-94`；README 明示：`:129-133,266-268`。 |

## R2-2. 新发现问题

### P1（新增 1 项）

**P1-R2 管理器手动 `recover()` 默认仍执行 `rotate-instance.sh`，且不使用脚本的 `rotate.lock`；与 README“管理器不执行 rotate-instance.sh”直接矛盾。**
- 证据：`recover()` 把状态置 `probing` 后，`if kind == "warp" and self.config.warp.rotate_on_recover: rotated, detail = self._rotate_and_update(exit_id)`（`manager.py:1069-1072`）；`_rotate_and_update` 调 `self.rotate_runner(state)`（默认 `_run_rotate_command`，`manager.py:96,1609-1611`），**全程未打开 `/var/run/warp-pool/rotate.lock`**。`rotate_on_recover` 默认 `True`（`config.py:129`），`config.example.json` 也是 `true`。
- 文档冲突：`README.md:194-196` 写“管理器只做探针/隔离/状态机归位，不执行 `rotate-instance.sh`”；`DEPLOY.md` 却写“只有人工 `recover`（`rotate_on_recover`）…才调用 `rotate-instance.sh`”。代码符合 DEPLOY、违反 README 的“强制”约束。
- 影响：运维按 README 只设 `rotate_on_auto_recover=false` 后，执行一次 `POST /v1/admin/recover` 仍会触发无 quiet-gap 的轮换；若恰逢 cron 脚本在轮换，二者会同时 `rotate-instance.sh N`（脚本侧持锁只能挡住另一个脚本，挡不住管理器）。这会切断在途流并可能连续换 IP——正是首轮 P1-4 的残余。
- 修复（二选一）：① 在强制部署配置里同时 `"rotate_on_recover": false`（配合 README 明确“管理器零轮换”），或 ② `_rotate_and_update` 在调用 `rotate_runner` 前对 `/var/run/warp-pool/rotate.lock` 做 `flock -n`，拿不到即放弃本轮（与脚本同一把锁）。当前默认配置属于“违反 README 部署约束”。

### P2（新增/遗留，非阻塞）

**P2-R1 冷却到期 L0 探针仍在 `_refresh` 锁内，且 docstring 声称“从不探针”。** `_refresh` 的注释写“Never probes or spawns subprocesses here”（`manager.py:431`），但冷却分支仍 `result = self.l0_probe(state)`（`:440-475`，探针在 `:444`）。`lease`/`status_summary` 以 `auto_recover=False` 调用它（`:768,1737`），因此冷却出口 cooldown 到期时，`/v1/lease`、`/v1/status` 仍可能被一个 L0 TCP 探测阻塞，单条上限 `health.timeout_s`（默认 8s），多条同时到期可叠加。新增的 `test_probe_and_rotation_never_run_under_manager_lock` 只覆盖自动恢复探针/轮换，未覆盖此路径。建议把冷却归位也改为“锁内置状态、锁外 L0”。
**P2-R2 手动 recover 轮换不持锁** —— 已升级为 P1-R2。
**P2-R3 运行期无 PROBING 超时 sweep。** 崩溃/重启路径有归一：`_load` 把遗留 `probing` 归一为 `quarantined`（`manager.py:190-203`；测试 `test_probing_with_last_error_normalizes_to_quarantined`、`test_normalized_probing_exit_can_be_recovered`）；`_auto_recover_exit` 也捕获探针/轮换异常并把 `probing→quarantined`（`manager.py:1227-1237`）。但 `_apply_auto_recover_result` 在 `try` 之外（`:1238`），且运行期没有按 `probe_started_at` 超时的扫描；若认领后线程被杀或 apply 前异常，状态会留在 `probing`（不再被 `_claim_auto_recover` 选中）直到重启。建议在 `_refresh` 增加 sweep：`status==probing and probe_started_at 早于 auto_recover_timeout_s*2+60` → 回 `quarantined`。
**P2-R4 `auto_recover_timeout_s=10` 小于探针最坏 2×`timeout_s=16s`。** `probe_exit` 顺序做 TCP + HTTP，各用 `health.timeout_s`（`health.py:204-214`），最坏约 16s，而 `auto_probe_timed` 的墙钟上限默认 10s（`manager.py:1290-1294`，`config.py:122,360-366`）。健康但慢的出口可能被误判失败、连续 3 次后被 disable。建议默认 ≥ `2*timeout_s + margin`（如 20s）或在 `--check-config` 校验/告警。
**P2-R5 `/v1/status` 仍返回 `recent_events` 的 `exit_id`/`account_hmac_prefix8`，与 DEPLOY 及既有“status 只返回聚合白名单”决策冲突。** `status_summary` 返回 `[dict(record) for record in recent]`（`manager.py:1755-1759`），record 含 `exit_id`（`:1529-1537`）；测试明确断言 `event["exit_id"]=="warp-1"`、`account_hmac_prefix8==ACCOUNT_A[:8]`（`tests/test_server.py:341-346`）；DEPLOY 同时写“recent_events 含 exit_id”又写“`/v1/status` 仍不返回…出口 id”，自相矛盾。建议把逐出口明细只放 `/v1/exits`，或改文档/决策。
**P2-R6 `self._recent_events` 类型注解仍是 `dict[str, float]`**（`manager.py:116`），实际值是 dict。建议改为 `dict[str, dict[str, Any]]`。
**P2-R7 脚本 dry-run 仍递增失败计数且可能 sleep 60s；README“只打日志”不准确。** `warp-health-inst.sh` 在 `DRY_RUN` 判定（`:397`）之前就 `write_counter FAIL_FILE`（`:353-354`）、且 quiet-gap 在 `:388-396` 先执行（可 sleep `QUIET_GAP_S=60`）。长期 dry-run 后翻 0，失败计数可能已 ≥2，首轮即可能轮换。建议 dry-run 不写 fail 计数并修正 README `:10`。
**P2-R8 fanout `count_openvpn` 仍全局 `ps | grep -c '[o]penvpn'`**（`fanout-guard.sh:64-67`），会把无关 openvpn 计入，可能误判健康/异常。建议限定 fanout 服务子树/cgroup。
**P2-R9 管理器与脚本的轮换预算相互独立。** 脚本 `MAX_ROTATE_PER_HOUR=3`（`warp-health-inst.sh:52`）与管理器 `auto_recover_rotate_hourly_limit=3`（`config.py:132`）不共享，若两者同时开启理论上限 6 次/h。README §3.1 已禁止同时开启，属已缓解；若未来允许共存，应合并预算。

### Q（提示）

- **Q-R1 4 条同时隔离的最坏遍历时间**：`AUTO_RECOVER_MAX_PER_PASS=1`（`manager.py:60`），健康循环 `run_once` 后 `wait(interval_s)`（`health.py:287-295`，默认 120s）。默认只探针：4 轮 ≈ 4×120s + 4×≤10s ≈ **6–7 分钟**；若开启轮换且每次吃满 120s 子进程超时，≈ 3×250s + 1×130s ≈ **13 分钟**。因这 4 条本就处于 `quarantined`（不在池内），串行化不额外损失可用容量。
- **Q-R2 flock 在 NFS/容器**：`STATE_DIR` 文档为 `/var/run/warp-pool`（tmpfs，本地文件系统），`flock` 在同一内核/同一 inode 上可靠；跨容器共享同一 host 路径通常也有效。若把 `STATE_DIR` 放到 NFS/SMB，`flock` 的跨主机语义不可靠（NFSv3 基本无效、NFSv4 依赖 lockd，可能静默只做本机锁）。脚本仅在 `flock` 缺失或锁文件不可写时 fail-closed（`:286-300`），**不会**检测“文件系统忽略锁”。结论：当前 tmpfs 路径可用；若迁到网络 FS 则互斥失效。
- **Q-R3** verify-account 后台确认仍未实机验证（沿用首轮 Q-2，项目记忆 `headless_qr_flow_not_verified`）。
- **Q-R4** INVALID 自愈与 `AGENT.md` “禁止自动 stale 复活”的冲突未处理（沿用首轮 Q-1）。

## R2-3. 门禁真实输出（复核轮次 2）

| 门禁 | 命令 | 实际结果 |
|---|---|---|
| 管理器单测 | `cd data/tools/exit-manager && python -m unittest discover -s tests -v` | **Ran 182 tests in 9.572s — OK** |
| 主仓单测 | `uv run pytest -q` | **2162 passed in 75.40s**（RC 0） |
| 主仓 Lint | `uv run ruff check .` | **All checks passed!**（RC 0） |
| 主仓类型 | `uv run pyright src tests` | **0 errors, 0 warnings, 0 informations**（RC 0） |
| 脚本语法 | `bash -n` × 4（`D:\coding\Git\bin\bash.exe`） | 4 个脚本均 **rc=0** |
| 空白/冲突 | `git diff --check`（主仓 + 管理器） | 两侧 **RC 0**（仅 CRLF 提示） |
| 旧配置兼容 | `python main.py --config <去掉全部新键的旧配置> --check-config` | **RC 0**；输出 `auto_recover_rotate=off`、`probe_timeout=10.0s`、`hourly_limit=3` |

## R2-4. 复核轮次 2 部署建议

1. **默认安全配置可直接观察部署**：管理器 `rotate_on_auto_recover=false`（默认）+ 脚本 `DRY_RUN=1`（默认）；此时无自动轮换、无双重轮换、无持锁长阻塞。
2. **在把 cron 改为 `DRY_RUN=0` 之前**，必须先关闭 P1-R2 的路径：把 VPS `config.json` 设为 `"rotate_on_recover": false`（推荐，配合 README 同步），或让管理器轮换前对 `/var/run/warp-pool/rotate.lock` 做 `flock -n`。否则一次人工 `recover` 就可能与脚本并发轮换。
3. **若要严格满足“探针全在锁外”**，补 P2-R1（冷却 L0 移出锁）；否则接受 `/v1/lease`/`/v1/status` 在冷却到期时最多被 L0 探测阻塞 `timeout_s`。
4. 建议顺手修 P2-R3（probing sweep）、P2-R4（探针超时 ≥2×timeout）、P2-R5（status 泄露口径）、P2-R6（类型注解）、P2-R7（dry-run 计数）。
5. 不提交 git、不 push、不碰 VPS。

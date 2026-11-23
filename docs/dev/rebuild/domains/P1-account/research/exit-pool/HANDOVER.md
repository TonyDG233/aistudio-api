# 出口池 2.1.5 交接说明（HANDOVER）

- 仓库：`D:\LLM\self_programming\aistudio-api`
- 性质：交接/开工文档，**不含任何 token、Cookie、邮箱明文**；只记录路径、端口、参数与结论。
- 日期：2026-11-23
- 事实口径：本文以当前实际提交与运行态为准；与旧文档冲突时，`AGENT.md` §4、`PLAN.md` v2、`CONTRACT.md` 与实机 `/v1/status` 优先。

---

## 一、开工必读顺序

1. `AGENT.md`（仓库根，工程红线：分层、并发、防风控、测试纪律）。
2. `docs/dev/rebuild/domains/P1-account/research/exit-pool/PLAN.md`（出口池 v2 定稿：车道、预算、调度、实施状态）。
3. 同目录 `CONTRACT.md`（app ↔ exit-manager / fanout 的 HTTP 契约与字段白名单）。
4. 最近审查：`docs/dev/rebuild/domains/P1-account/reviews/REV-2.1.5.16-batch-and-metadata-arbitration.md`（分批、元数据 403 仲裁、汇总口径）；需要追溯时再看 `REV-2.1.5.12`（fanout 机器接口）、`13`（租约/defer）、`14`（管理器 v2）、`15`（verify 上报）。
5. 本文（运行态、账号状态、运维命令、上下文保真协议）。

原则：先读文档再动代码；`PLAN.md`/`CONTRACT.md` 是方案与契约，代码是事实；审查结论优先于旧文档。

---

## 二、运行态拓扑与路径

### 2.1 主机

- VPS：Debian 13，1 核，1.9GB 内存，TUN 可用。
- 出口池全部为宿主侧服务；app 跑在 Docker Compose 内，通过宿主桥地址访问管理器与出口。

### 2.2 app（`aistudio-api`）

- 容器状态：`running (healthy)`；容器地址 `172.18.0.2`。
- 端口映射：`127.0.0.1:7860`、`172.17.0.1:7860`。
- 部署目录：`/opt/aistudio-api`（`docker compose` 管理）。
- 运行模式：`AISTUDIO_EXIT_MODE=external`，引擎为 `browserless`（`browser` + external 会 fail-fast）。

### 2.3 exit-manager（宿主 systemd）

- 部署目录：`/opt/exit-manager`。
- 配置：`/etc/exit-manager/config.json`（权限 `0600`）。
- 状态目录：`/var/lib/exit-manager/state`（持久盘；内含 `exits_state.json` / `bindings.json` / `idempotency.json`，原子写）。
- 监听：`172.17.0.1:8788`（loopback/bridge 白名单内，拒绝公网绑定）。
- 单写者：systemd 单实例 + `Restart=on-failure`；禁止同 state 目录起第二个进程。

### 2.4 fanout（宿主 systemd）

- 部署目录：`/opt/fanout`。
- 当前 4 个出口，SOCKS 端口：`58138` / `31595` / `52215` / `59285`；**SOCKS 需认证**（`socks5://user:pass@...`，凭据只在 lease 响应中，不进状态/UI/日志）。
- 机器面 `/api/machine/*` 需 `FANOUT_MACHINE_TOKEN`；管理器当前只用只读面 `GET /api/readonly/tunnels`，机器面尚未接线（`PLAN.md` §9、`CONTRACT.md` §9）。

### 2.5 WARP（宿主 wireproxy）

- `wireproxy-inst-1..4`，端口 `40110..40113`；另有旧实例端口 `40001`。
- 轮换脚本：`/usr/local/bin/warp-multi/rotate-instance.sh N`（按实例号；单次轮换可恢复临时 region 403，见 §五/§六）。

### 2.6 池与车道（当前实测）

| 项 | 当前值 |
|---|---|
| 健康出口 | 8 条：free WARP 4 + paid fanout 3 + free_overflow 1 |
| 账号绑定 | 43 个账号全部已绑定出口 |
| paid 预算 | 窗口 1800s；软 30 / 硬 45 / 日 400 |
| free 预算 | 窗口 1800s；软 50 / 硬 75 / 日 2400 |
| 维护/探针预留 | 20 / 6（每出口每窗口，生成不得占用） |
| 租约 TTL | 30~60s |
| 无容量 | 429 `no_capacity` + `retry_after_s` + `Retry-After` |
| verify 连坐烧毁 | 同出口 30min ≥2 个不同账号 verify → `cooling` 120min |
| `disabled` | 配置硬开关，任何事件/探针/refresh/recover 都不复活 |

---

## 三、环境变量与参数

### 3.1 app 侧（仅环境变量，不进 `config.yaml` / schema / UI 设置项）

| 变量 | 作用 |
|---|---|
| `AISTUDIO_EXIT_MODE` | 置 `external` 开启出口池；缺失/其他值走原全局代理 |
| `AISTUDIO_EXIT_ENDPOINT` | 管理器 base URL（宿主机桥地址，`http://`/`https://`） |
| `AISTUDIO_EXIT_TOKEN` | 管理器 Bearer token（与 `config.json` 的 `token` 一致） |
| `AISTUDIO_EXIT_HMAC_KEY` | 账号 HMAC 键；管理器只存 HMAC，不反推账号 |
| `AISTUDIO_EXIT_ALLOWED_HOSTS` | 允许的 socks 主机（默认 `127.0.0.1,172.17.0.1`） |
| `AISTUDIO_EXIT_HTTP_TIMEOUT` | 控制面超时（默认 2s） |
| `AISTUDIO_EXIT_DIRECT_FALLBACK` | 直连应急阀，默认关；目标仅 maintenance，generation 永不直连 |

红线：`AISTUDIO_EXIT_*` 只走 env；UI 只读；不新增 schema 字段。

### 3.2 管理器侧（`/etc/exit-manager/config.json`，权限 0600）

- `budget`：`window_s=1800`、`maint_reserve=20`、`probe_reserve=6`、`paid{30,45,400}`、`free{50,75,2400}`、`burn_cooldown_s=7200`、`verify_cluster_window_s=1800`、`verify_cluster_threshold=2`、`lease_min/max_ttl_s=30/60`、`max_calls=10`。
- `replenish`：`paid_target/cap=3/3`、`free_target/cap=3/6`、`overflow_target/cap=1/1`、`cooldown_s=300`、`hourly_limit=3`、`interval_s=60`。
- `health`：`interval_s=120`、`timeout_s=8`、`fail_threshold=2`。
- `security.allowed_client_cidrs`：必须放行 Docker 网络 `172.18.0.0/16`（当前 app 容器在 `172.18.0.2`），否则 app 直连管理器会被 403。
- `listen.host`：仅 loopback / `172.17.0.1`。
- `MAX_FANOUT_CAP=4`：`paid_cap + overflow_cap > 4` 加载期拒绝；静态 `exits` 数量需人工保证 ≤4。

### 3.3 保活节流（app 侧实现事实）

- 元数据刷新：每轮 12 个账号；auth 刷新：每轮 8 个账号。
- 连续 3 个 `no_capacity` 停止当前轮，剩余账号保持 due。
- `region` defer 不计入 no_capacity 连续计数。
- 双通道独立游标（metadata / auth），不互相推进。
- 日志：逐账号 INFO，每轮汇总 WARNING。

---

## 四、账号状态与三个待扫码

- 账号总数 43：Free 37 / Pro 6。
- `verify_required` 3 个：`acc_86bbfa31`、`acc_59c95a69`、`acc_c11b975f`。
  - 状态改动前备份：`/root/verifyflip2-backup-1795426068/`。
  - 已修改 `registry.json` + `meta.json` + `rotator` 状态（三处一致）。
  - 直连探测均返回 200 `[1]`；当前仍列为待扫码，未做软件层硬限制。
- 两个 Pro 账号 `acc_61a816d1`、`acc_6803137a`：当前 `enabled`，`tier=Pro` 正常。
- 关键事实：`registry.json` 是权威状态，`meta.json` 是镜像；改账号状态必须改 `registry.json`，只改 meta 不生效（meta 里的 `enabled:false` 曾未生效即因此）。

---

## 五、已完成 / 未完成 / 已知坑

### 5.1 已完成

- 管理器 v2：车道隔离、按调用预算、维护/探针预留、429 `no_capacity`、verify 连坐烧毁、幂等、自动补充。
- app 租约客户端：`/v1/lease` 主入口、429 defer、维护不跳过、冷启动单计租约。
- verify-account 事件上报（`record_attempts` 门控、无绑定跳过、fire-and-forget）。
- fanout 机器接口（编译级完成，Linux 运行时用例仍需显式实跑）。
- 2.1.5 分批与降噪（`e9081d2`）：首轮保活分批，逐账号 INFO + 每轮汇总 WARNING。
- 元数据/Cookie region 403 接 canary 出口仲裁（`4493ac0`）。
- 分批饥饿、仲裁 kind、汇总口径修复（`cf96f03`，审查 `REV-2.1.5.16`）。
- VPS 部署完成：8 出口健康、43 账号绑定、3 待扫码；部署后最近 2 分钟 `no_capacity`/`region` 均为 0。

### 5.2 未完成

- 后续保活批次是否把 43 个账号全部轮换完成，仍需观察。
- 无响应体 403 的标签取舍（无 body 无法区分出口/账号，当前只按出口嫌疑，见 Q2）。
- fanout 节点掉线与热备切换尚未补齐。
- 1 核 1.9GB 容量评估（WARP 4 + fanout 4 + app/signer 的 RSS 与负载）。
- `request_id` 幂等 app 未发送；`kind` 不参与管理器绑定（恒 `chat`）。
- 直连应急阀尚未按 `class` 收窄为 maintenance-only（默认关）。

### 5.3 已知坑（务必先读）

1. **状态权威**：账号状态改 `registry.json`，meta 只是镜像；只改 meta 会被覆盖。
2. **app 租约客户端直连管理器**：`trust_env=False`，不走环境代理；容器内访问 `172.17.0.1:8788`。
3. **CIDR**：Compose 网络 `172.18.0.0/16`，管理器 `allowed_client_cidrs` 必须放行，否则 403。
4. **WARP region 403 多为临时**：单次 `rotate-instance.sh N` 可恢复；不要连续高频轮换（会触发 verify 风控）。
5. **模型发现 signer 403**：靠 35 模型快照兜底，不是池故障。
6. **403 空体**：无响应体时只能按出口嫌疑（Q2），不要据此直接烧账号或反复切号。
7. **首轮保活噪音**：首轮曾一次性跑完 43 账号而撞维护预留（当时 3 WARP=60、2 fanout=40），产生 `no_capacity` 排队与 600s 重试；现已改为分批（每出口维护预留 20/窗口），属设计内行为，不要误判为故障。
8. **meta `enabled:false` 曾未生效**：权威在 registry；两个 Pro 号刷新后均为 valid/Pro，无需禁用。
9. **不要反复重启服务/浏览器**：冷启动触发 BotGuard 探针，高频启停会风控。

---

## 六、运维、备份与回滚命令

### 6.1 日常运维

```bash
# app 重启（单次，避免反复重启）
cd /opt/aistudio-api && docker compose stop aistudio-api && docker compose up -d aistudio-api

# 宿主服务重启
systemctl restart exit-manager
systemctl restart fanout

# WARP 单实例轮换（仅在确认 region 403 后）
/usr/local/bin/warp-multi/rotate-instance.sh N

# 只读检查
curl -s -H "Authorization: Bearer <token>" http://172.17.0.1:8788/healthz
curl -s -H "Authorization: Bearer <token>" http://172.17.0.1:8788/v1/status
```

- `/v1/status` 只返回聚合白名单（无 socks/token/exit id/账号），可安全查看车道 health/target/cap、`maintenance_queue`、`cooldown_count`。
- 人工恢复隔离出口：`POST /v1/admin/recover {"exit_id":"..."}`；`disabled` 返回 409。

### 6.2 备份

- 账号：已存在备份目录（本次为 `/root/verifyflip2-backup-1795426068/`），改账号状态前先备份 `registry.json` / `meta.json` / rotator 状态。
- 管理器：备份 `/var/lib/exit-manager/state` 与 `/etc/exit-manager/config.json`；state 丢失会导致重新绑定、出口 IP 可能变化，属已知单点风险。
- fanout：备份 `/opt/fanout` 配置。

### 6.3 回滚

1. 只回滚 app 行为：`AISTUDIO_EXIT_MODE` 置空并重启 → 回到 `settings.proxy_url` 全局代理，管理器可继续运行（无请求）。
2. 管理器回滚：停止 `exit-manager`，恢复 `state/` 与 `config.json` 备份；不强制 WARP 轮换。
3. 镜像回滚：回退到上一提交并重建镜像，不 merge/checkout 旧 `rebuild` 分支。
4. 不自动购买付费出口；不因单次失败推翻方案文档。

---

## 七、上下文保真协议

1. **权威顺序**：仓库实际代码/文档（`AGENT.md` → `PLAN.md` → `CONTRACT.md` → 最近 REV）＞ 远端运行态（`registry.json` 为准、meta 为镜像）＞ `/v1/status` 与 `systemctl status` 实况 ＞ 记忆（memory 仅作索引，不作事实）。
2. **用户总结时逐条对账**：用户给出的结论必须与仓库文件、`registry.json`、`/v1/status`、systemd 状态逐条核对；不一致时以现存仓库/实机为准并明确指出差异，不沿用记忆中的旧数字。
3. **不编造**：不推测端口、账号数、预算值、提交号；未实测的标“未验证”。
4. **敏感值**：只记路径、端口、参数与推导方式，不写 token/Cookie/邮箱/密码明文；`socks_url` 含凭据，只在 lease 响应内。
5. **变更边界**：改代码/配置前先读 `AGENT.md` §4 与本文 §五；不访问真实 Google/账号/VPS，除非用户单独授权。

---

## 八、下一步

1. 观察后续保活批次是否把 43 个账号全部轮换完成，确认无连续 `no_capacity`/`region` 堆积。
2. 处理无响应体 403 的标签取舍（出口嫌疑 vs 账号级记录），避免误烧出口或反复切号。
3. 补充 fanout 节点掉线检测与热备切换（当前 paid 3 = 2 主力 + 1 热备，掉线不自动补）。
4. 做 1 核 1.9GB 容量评估（`free -m` / `top` 实测 WARP 4 + fanout 4 + app/signer 的 RSS）。
5. 把 `HANDOVER.md` 与 `AGENT.md` 固化为开工必读；后续所有结论落盘到 `docs/dev/rebuild/domains/` 对应域，不依赖会话记忆。

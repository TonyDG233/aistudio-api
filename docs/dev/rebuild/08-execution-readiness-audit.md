# 08 Execution Readiness Audit

- 审计基准：`rebuild` @ `be6eda6`，工作树干净；对照 `upstream/master` @ `4623109`（`rebuild` 相对 upstream 为 108 ahead / 0 behind）。
- 方法：只读文档、代码、测试和 git 元数据；未 fetch、未访问账号/IP/网络、未修改代码。离线执行 `pytest -q`：**942 passed / 33.58s**。
- 审计对象：05 的迁移流程、P1-P10 决策依赖、upstream 扩展缝、最小运行前置、测试/证据可验收性、当前分支状态。

## 结论

**不可执行（按当前文档直接开工）。** 文档把“移植”描述成在干净 upstream 上做增量，但 upstream 只有 66 个 `src/aistudio_api` 文件，P2/P3/P4/P6/P7/P8 的核心模块在 upstream 根本不存在；`rebuild` 相对 upstream 新增 60 个源文件、修改 39 个，整树 518 文件 / +66,739 行。真正缺的不是“再写几片代码”，而是目标 seam、实例/并发状态机和可验收切片。

同时，06 的机器索引几乎全部指错台账行，且若按 CSV 的 `decision` 执行，会直接复活 P2/P3 刚刚 drop 的 lease、评分、pinned、defer 行为。**在 B0 项清零前，任何按 feature ID 的“逐片移植”都会走偏。**

当前确实具备的基础：P1-P10 已有书面决定（CSV 518 行无 `pending`）；942 单测全绿；upstream 基线干净且是 `rebuild` 的祖先；worktree 方案在 git 层面可行。这些只说明“有材料”，不说明“可执行”。

## 阻塞项（按严重度）

### B0-1 文档索引与决策自相矛盾（阻断）

- **索引错位**：`06-feature-index.csv` 有 518 行，但 `ledger_line` 不是可用映射。批量按 feature 文案定位后，425 个可定位行全部偏移：+1 共 267 行、+3 共 137 行、+2 共 21 行；另有 86 行因文案不同无法定位，3 行 `ledger_line=0`。
  - `D04-020`（CSV:149）标 ledger 193，实际在 194；读到的是 `close ACTIVE → RECLAIMING`，不是 `acquire 预留`。
  - `D06-066`（CSV:300）标 ledger 354，实际在 355；读到的是 `204 空响应重试`，不是 `取消/断连 lease 释放`。
- **决策与 feature 文本冲突**（不是措辞差异，而是行为相反）：
  - `D05-016..019` 为 `port`，保留 affinity/weight/公平分（CSV:207-210）；P2-D 却明确“丢弃权重、公平分、affinity 打分”（`06-decision-log.md:126`）。
  - `D05-021` 为 `port`，feature 是“返回 lease”（CSV:212）；P3 总原则却“取消 lease/reservation，保留 active_requests”（`06-decision-log.md:161`）。同组 `D04-020` 又 `drop` lease/reservation（CSV:149）。
  - `D05-039` 为 `port`，feature 含“pinned 不可 rebind / 单实例 defer”（CSV:230）；P2 丢弃固定 1s defer（`06-decision-log.md:134`），P3 丢弃 pinned（`:164`）。
  - `D06-066` 为 `port`，`decision_note` 却写“lease removed; active_requests instead”（CSV:300）；字段与内容不一致。
- 桥接表也不闭合：P4/P5/P10 分别写 43/69/36（`06-decision-log.md:31-38`），CSV 实际 44/70/37，总 515 vs 518。

影响：agent 按 CSV 选 feature、按 `ledger_line` 读上下文时，读到的往往不是该 feature；按 `decision` 实现时，会把已决定删除的旧行为写回来。P2/P3/P4/P6 的验收会互相打架。

### B0-2 upstream seam / 目标架构缺失（阻断）

- `upstream/master` 只有 66 个 `src/aistudio_api` 文件，**没有** `instance_pool.py`、`routing_pipeline.py`、`generation_service.py`、`account_keepalive.py`、`build_surface.py`、`routes_logs.py`、`routes_openai.py`，也没有 Instances/Logs/Stats UI。P2/P3/P4/P6/P7/P8 不是“port upstream 原生”，而是把 fork 的 60 个新增文件重新设计/回填。
- 05 的第 1 步只写“新分支先更新 `ARCHITECTURE.md` 与 upstream 扩展缝文档”（`05-restart-map.md:50`），没有交付物、接口清单或验收。07 是**当前 fork 现状**（`07-architecture-lifecycle.md:3-6`），不是目标 seam；02 已指出 `ARCHITECTURE.md` 仍写单 Chromium/单页、与代码严重漂移（`02-architecture-audit.md:19`）。
- 目标五层（Account/Auth、Instance、Pool/Router、Executor、Event Sink）没有对应接口定义：账号身份/tier 谁权威、Context/Page 如何工厂化、surface 如何从模型解析、executor 的 `active_requests` 契约、事件 schema、config schema 全部缺失。
- P9 被放在最后（`05-restart-map.md:59`；`06-decision-log.md:220`），但 P9 的 lifespan 正是 P3/P6 的装配点（`07-architecture-lifecycle.md:10`）。先做 P3/P6、最后补 boot/config 在顺序上不成立。

### B0-3 实例/并发模型未定（阻断）

至少四组未解决冲突，直接决定 P2/P3/P4/P6 能否写测试：

1. **lease vs active_requests**：P2-E 保留 lease/容量（`06-decision-log.md:133`），P3 取消 lease/reservation（`:161`），CSV 又同时 `port` `D05-021`、`drop` `D04-020`。当前代码 `instance_pool.py:61` 仍有 `reservations`，`routing_pipeline.py` 全链路以 `InstanceLease` 为中心。没有单一并发原语，就无法定义 acquire/release/取消/关闭的验收。
2. **控制面 vs 实例页**：P3 要求“1 账号 + 1 Context + 1 Page + 1 surface”（`:157`），又保留“控制面也保持单页”（`:166`）。控制面若独立于实例，同一账号就有 2 页；若并入实例，P6 的 `active_activity` 滚控制页（`:262`）、P4 的 build 取控制面 CDP（`client.py:176`；07:207）就失去落点。
3. **sleep/wake/close**：P3 说“消息到达后逐一热机休眠实例”（`:160`）、“关闭即释放实例；休眠实例手动重启”（`:163`）；P6 说“idle browser 默认关闭；需要时手动重启”（`:263`）；CSV 又 `port` `D03-035` 空闲回收、`D04-031` stale slot 复活（CSV:129,160）。自动唤醒、自动复活、手动重启三种策略并存，状态机未定义。
4. **rebind**：P2 说容量满时“把满的实例切到下一个候选”（`:94`）；P3 明确“丢弃 pinned、后台 rebind、自动 rebalance”（`:164`），CSV `drop` `D04-022/039`。这里的“切到下一个候选”到底是换 slot、换账号还是新建实例，没有定义。

### B1-1 迁移命令与敏感数据不安全（高）

- 05 的迁移命令是 `git -C ..\aistudio-api-upstream restore --source=rebuild -- docs`（`05-restart-map.md:84`），不是只迁 `docs/dev/rebuild`。`rebuild` 的 `docs/` 有 267 个文件，含 17 份旧 WP/规划正文和整套 `docs/dev/evidence`；这与“不把旧规划正文当新设计”直接冲突（`:98`）。
- 当前仓库已存在凭据形状内容：`docs/dev/evidence/p3-build3/spikeQ4-evidence.json:39,41`（`authorization` / `x-goog-api-key`），`docs/BOTGUARD_VERIFICATION_CHAIN.md:128,140,284,286`（API key / SAPISIDHASH）。05 要求“evidence 必须脱敏；原始 auth/cookie/key 不进入新仓库”（`:97`），但没有扫描/脱敏步骤，且 `docs/BOTGUARD...` 是 upstream 原生文档，P9 又要求“原生无必要不改”（`06-decision-log.md:342`）——安全红action 与原生优先规则没有裁决。
- `restore` 后新文件在目标 worktree 是 **untracked**；05 的检查是 `git status --short` + `git diff --stat -- docs`（`:89-92`）。`git diff` 不显示 untracked，迁移结果可能“看起来为空”，随后提交漏文件。
- P10 要求的 `docs/dev/evidence/p1-ark-merge-archive/` 不存在，`docs/changelog.md` 不存在，`docs/dev/evidence/INDEX.md` 不存在（`06-decision-log.md:366-368`）；当前 `docs/dev` 下 17 份旧文档仍在活动区。

### B1-2 最小可运行路径与验收顺序倒置（高）

- P5 被称为“最小 chat 生成 + OpenAI + API 配置，形成最小可持续运行”（`05-restart-map.md:54`），却排在 P6 保活、P7 日志之后（`:52-55`）；P5 的 `routes_openai.py` 直接依赖 `generation_service.stream_generation`、`routing_pipeline`、`format_converters`，这些又都依赖 P3/P2。也就是说前 4 步没有可用的 OpenAI 纵向切片，P3/P2 只能做单元测试，无法做端到端验收。
- P8 才要求“API Key 默认要求；显式配置才允许空 Key”（`06-decision-log.md:310-311`），但 upstream 默认 `api_keys: []` 即开放；P5 若先暴露端点、P8 后收紧，中间版本是开放代理。
- 05 的 P7 只做日志、不做统计（`:54`），但验收底线又要求“统计页能回答账号/模型/surface/尝试/错误/用量/延迟”（`:145-146`）；统计页被排到第 9 步（`:58`）。P7 的验收无法在 P7 阶段通过。

### B1-3 测试/证据不足以支撑验收（高）

- 942 全绿已复核，但它是**旧行为回归**：P10 自己说“现有 942 单测保留为理解旧项目预期行为的参考，不是新契约”（`06-decision-log.md:360`）。没有逐文件处置表。P3 取消 lease/多页、P2 丢弃评分/rebind 后，大量测试会按旧契约失败（`test_instance_pool.py` 40、`test_instance_manager.py` 27、`test_routing_pipeline.py` 11、`test_build_surface.py` 38、`test_account_keepalive.py` 9 等）；agent 很可能为“保持绿”而保留旧实现。
- CSV 没有 acceptance 列；`evidence` 列是旧测试/旧截图，不是新切片的验收命令。05 的验收底线只有 6 条抽象断言（`:138-146`），覆盖不了 518 个 feature。
- 已知不可复跑/未验证项没有关闭计划：build 双并发 200 仅文字（`06-feature-ledger.md:574`）、24h 保活无实耗样本（`:587`）、paid 前端字段依赖真实账号（`:578`）、cookie/header 探针含真实凭据（`:582`）、消息超时无 E2E（`:589`）。P10 的“IP 冷却后 WARP 小流量”没有条件、脚本或判定标准（`06-decision-log.md:363-364`）。
- 单实例候选锁死仍标记 `broken`：候选只保留已有实例账号（`routing_pipeline.py:252-259`），台账明说未覆盖“429 无候选失败”（`06-feature-ledger.md:580`）。P2 的决定没有把它转成可验收项，也没有保留“来消息即唤醒 / 保存消息后切实例再重试”的明确条款。

### B2 其他高风险

- **P4 参考项目不可追溯**：`D07-044` 的“参考项目移植 BotGuard/失败现场”无 Git provenance（`06-feature-ledger.md:408`），P4-Q3 无法复跑；9998 native/signer、realm 隔离、build 并发都没有离线 harness。
- **P8 UI 与 P9 原生优先冲突**：P9 要求 upstream 原生 UI 无必要不改（`06-decision-log.md:342`），P8 却要 port fork 的 Logs/Instances/Stats 等 UI（`:313-317`）。upstream 只有 4 个 view，fork 有 6 个且 static bundle 已改；没有“复用 upstream Vue 还是替换为 fork 静态产物”的裁决。
- **worktree/分支状态易误操作**：当前主 worktree 是 `rebuild`，另有 `.kilo/worktrees/steady-ketch`（0cb1b82）；`rebuild/upstream` 尚不存在。05 说 `rebuild` 只读（`:134`），但 05/06/07 只存在于 `rebuild`，审计和后续修订也只能先落在这里；迁移后没有“唯一事实源”规则。
- **07 已过期**：`07-architecture-lifecycle.md:3` 仍说 ledger/ledger-batches 是“未跟踪文件”，实际已在 `be6eda6` 跟踪；基线写 `e3a77c2`。文档自身的可信度需要先校准。

## 缺失项

| # | 缺失物 | 为什么必须先有 | 建议落点 |
|---|---|---|---|
| M1 | 目标 seam/ADR：Account 权威、Context/Page 工厂、surface 解析、Executor、Event Sink | P2/P3/P4/P6/P7 的接口都未定义；现在写任何一片都会自造一套 | 新分支 `docs/dev/rebuild/09-target-seams.md` + `ARCHITECTURE.md` 目标版 |
| M2 | 实例/并发状态机：sleep/wake/closed/busy、active_requests、关闭/重开、是否 rebind | B0-3 的四组冲突必须先收敛成一个状态表 | 同一 ADR，附状态转换测试矩阵 |
| M3 | 配置 schema + 示例：`count/surface/min_tier`、api_keys、formats、keepalive、日志路径 | P3/P6/P5 都要读写配置；upstream 的 `config.yaml` 没有 `features`，rebuild 的仍是旧 schema | `config.schema` / `config.example.yaml` + 迁移脚本 |
| M4 | 事件 schema 与聚合口径：request/attempt/usage/error/latency/surface/paid；client vs attempt | P5 生成时就要记账，P7 才能只做聚合；否则 P7 重写 P5 | `docs/dev/rebuild/10-event-model.md` |
| M5 | feature → 源文件/目标文件/测试 的迁移清单 | 60 个新文件不能靠“看旧实现”逐片猜；CSV 只有旧 provenance | 在 CSV 增 `target_paths` / `acceptance_test`，或单独 manifest |
| M6 | 942 单测逐文件处置表：keep/rewrite/delete，绑定 feature ID | 否则旧契约测试会反向绑架新实现 | P10 附件，先于 P3/P2 编码 |
| M7 | 每片验收矩阵：测试项 + 预期结果 + 命令 + 原始工件路径 | 05 的 6 条底线覆盖不了 518 feature；CSV 无 acceptance | 模板 + 每片 `docs/dev/rebuild/acceptance/` |
| M8 | 证据治理：脱敏规则、secret scan、archive 目录、`docs/changelog.md`、INDEX | 当前已含凭据形状内容；P10 要求的路径不存在 | 迁移前一次性建立并跑扫 |
| M9 | UI 决策：upstream Vue 增量 vs fork static bundle | P8/P9 直接冲突；迁移 docs 会带旧 UI 设计 | ADR，列出保留/新增视图 |
| M10 | 离线 E2E 方案：Fake CDP/9998/signer、fake clock、WARP 触发条件 | P4/P6 的真实链路不能在当前约束下验证 | `tests/integration` 设计 + 运行手册 |
| M11 | 模型目录/paid map 的唯一所有者与 schema | P1-E3 说归模型目录，P7 只消费；目前 upstream `model_discovery` 与 fork `model_catalog` 并存 | ADR + 接口测试 |
| M12 | 文档唯一事实源规则 | `rebuild` 与 `rebuild/upstream` 各存一份 05/06 后必然分叉 | 冻结 `rebuild` docs 为只读，或改为指针文件 |

## 执行风险（命令 / 路径 / 环境）

| 风险 | 具体坑 | 建议 |
|---|---|---|
| `git fetch upstream --prune` | 仓库有 `origin`/`source`/`upstream` 三个 remote；`upstream`=DeconstructedCube，`source`=chrysoljq。05 未说明“upstream”指哪个；fetch 需要网络/代理 | 在 05 固定 remote URL + base SHA；离线时直接用现有 `upstream/master`，不把 fetch 当阻塞 |
| `git worktree add ..\aistudio-api-upstream ...` | 路径相对“当前目录”；若在 `.kilo/worktrees/steady-ketch` 执行会落到错误位置；目标目录已存在则失败；新目录在 VS Code workspace 之外，文件工具可能受权限限制 | 从仓库根执行或 `git -C <repo>`；优先放 `.kilo/worktrees/` 内；先 `Test-Path`；把新 worktree 加入 workspace |
| branch 已在其他 worktree | `rebuild` 已 checkout 在主 worktree；`rebuild/upstream` 目前不存在。若误用 `rebuild` 作 start point 会失败；重复执行 `-b rebuild/upstream` 也会失败 | 先 `git worktree list`；只允许新分支；脚本化幂等检查 |
| `git restore --source=rebuild -- docs` | 复制全部 267 docs（含旧 WP/evidence/疑似凭据）；新文件 untracked；`git diff --stat -- docs` 看不到；会覆盖 upstream 的 `docs/ARCHITECTURE.md` | 改为 `docs/dev/rebuild` allowlist；`git add -A docs` 后 `git status -uall`；先跑 secret scan；目标架构文档不要用 fork 旧版覆盖 |
| `git diff --no-index <old>\<path> <new>\<path>` | PowerShell 把 `<` 当重定向/保留操作符，直接粘贴会语法错误；有差异时退出码 1 | 用变量/引号：`git diff --no-index -- "$old\$path" "$new\$path"`，并显式忽略退出码 1 |
| `git diff --name-status rebuild -- <path>` | 这是 working tree vs `rebuild`，不是“新分支相对 rebuild”的残留检查；会给出误导结果 | 用 `git diff --name-status rebuild...HEAD -- <path>`；逐 commit 检查 |
| `git range-diff upstream/master...rebuild` | 单区间、线性祖先，输出基本是噪音，不能当验收 | 用 `git log upstream/master..rebuild -- <path>` + `git show` 逐片对照 |
| 真实运行环境 | 需要 Chrome/CDP、9998、signer、cookie/账号、代理；当前约束禁止真实账号/IP/网络 | 离线 gate 用 Fake CDP/9998；真实 E2E 明确后置，不在首轮验收内 |
| 测试/构建 | `pytest` 942 全绿（33.58s）；但 `bun`/`node_modules` 与前端构建未验证；测试编码是旧契约 | 先建 M6 处置表；前端构建单独跑 `bun run build`，不要把“能装”当“能验收” |
| 现有 worktree/分支 | `steady-ketch` 与 `master` 8e5e0cb 都可能被误当目标；`master` 比 upstream 新，含 build 修复 | 05 明确 base SHA 与禁止分支；开工前打印 `git status` + `git worktree list` + `git rev-parse HEAD` |

## 建议的最小执行顺序

顺序原则：先让文档/索引可信，再定 seam，再做一条可运行的纵向切片；P9/P10 必须拆分，不能整体后置。

0. **Gate 0：文档与证据冻结**（不改业务代码）
   - 重建 CSV `ledger_line` 映射并以 feature 文本校验；修正 B0-1 的决策冲突；补 P4/P5/P10 行数。
   - secret scan + 脱敏；建立 `docs/dev/evidence/p1-ark-merge-archive/`、`docs/dev/evidence/INDEX.md`、`docs/changelog.md`。
   - 冻结 `rebuild` docs 为只读参考，明确新分支是唯一事实源。
1. **Gate 1：目标 seam + 状态机 + 配置/事件 schema**
   - M1/M2/M3/M4/M11；更新 `ARCHITECTURE.md` 目标版和 `AGENT.md` 目标版（不是验收后补）。
   - 输出每片验收模板（M7）和 942 测试处置表（M6）。
2. **P9-pre + P1**：lifespan/config 骨架（只做装配钩子）+ 账号/Cookie/tier。以 fake CDP 验证，不接真实账号。
3. **P3 + P2**：单实例单 Context 单 Page；简单 free/pro 池；候选包含“休眠/未启动”账号；同号 retry、保存消息后切实例重试、429/403/transient、max switch → 503。此时**不要**引入 lease；用 `active_requests` 写测试。
4. **P5 最小纵向切片**：Gemini + OpenAI Chat + API Key 默认拒绝 + 事件记账；这是第一个可端到端验收的切片。Token 口径先按“system/tools/content 打包成一个 body”固定，删除“content-only”表述。
5. **P6**：24h 保活、build-only 回写、job 锁；用 fake clock 验证 24h，不依赖真实等待。
6. **P7**：日志页/导出 + 统计聚合；统计 UI 可后置，但事件 schema 在 P5 已定，不能另起一套。P7 验收只覆盖日志，不把统计页塞进 P7 gate。
7. **P8 + P4**：最小 API/UI 配置与实例 UI；再做 Build（先 fake 9998/signer，再受控真实测试）。P8 的 UI 决策（M9）必须先于实现。
8. **其余协议 + 统计 UI + P10 收尾**：Claude/Responses、统计页、证据索引、changelog、最终 E2E（明确 IP/WARP 条件）。

## 验收清单

| ID | 通过条件 | 证据/命令 |
|---|---|---|
| G0 | CSV 518 行全部能定位到正确 ledger 行；P1-P10 行数与 CSV 一致；无 `pending`；无 decision/feature 冲突 | 校验脚本输出 0 mismatch；人工复核 B0-1 五组 |
| G1 | `docs` 中无真实 API key/cookie/auth header；archive/INDEX/changelog 存在；旧 WP 不在活动区 | secret scan 只命中占位符；`git ls-files` 检查 |
| G2 | 目标 seam/状态机/config/event schema 已评审；`ARCHITECTURE.md`/`AGENT.md` 与目标一致 | ADR + 评审记录；文档 diff |
| G3 | 旧 942 测试有逐文件 keep/rewrite/delete 处置；新分支基线可解释 | M6 表；`pytest -q` 结果 |
| G4 | 同账号永远只有 1 Context + 1 Page；chat/build 不并存；关闭释放；sleep/wake 状态可枚举 | fake CDP 单测；状态转换表 |
| G5 | 休眠/未启动账号可被消息唤醒；保存消息后切实例可重试；429/403/transient 路径符合 P2；耗尽为 503 | `test_routing_*` 新用例；无 lease 断言 |
| G6 | OpenAI Chat/Gemini 契约通过；API Key 默认拒绝、显式空 Key 才开放；token 口径与代码一致 | SDK/fixture 测试；config 测试 |
| G7 | 24h 回写覆盖 chat/build-only/active；保存失败不 mark；fake clock 不真实等待 | keepalive 单测 + 注入时钟 |
| G8 | 日志 SSE/1/3/7 天导出；client 与 attempt 分母分离；token 汇总不含 countTokens | `test_logs_routes` / `test_stats_*` 新断言 |
| G9 | Build 9998/signer fallback、realm 隔离、并发、失败 PNG/HTML 可离线验证 | fake 9998/signer 测试；失败工件路径 |
| G10 | upstream 原生 UI 未被无必要重写；实例/日志/设置入口符合 P8 | UI diff + 截图；M9 决策 |
| G11 | 每片有 acceptance 工件；P10 证据索引可查；真实 E2E 仅在后置条件满足时执行 | `docs/changelog.md` + evidence INDEX + 原始 SSE/日志 |

## 引用

- 迁移/顺序/验收：`05-restart-map.md:3,50-59,74-76,84,89-92,97-98,102-109,126,131-134,138-146`
- 决策冲突：`06-decision-log.md:31-38,126,133,157-166,174,187,212-220,249,258-264,290,342`
- CSV 示例行：`06-feature-index.csv:149`（D04-020）、`:212`（D05-021）、`:230`（D05-039）、`:300`（D06-066）、`:207-210`（D05-016..019）
- 现状问题：`07-architecture-lifecycle.md:3,10,95-97,162,207,241,252,293-296,300,334,343-353`
- 旧审计：`02-architecture-audit.md:19,29,45`；`01-docs-audit.md:45-47`
- 代码：`routing_pipeline.py:252-259,514-522,559`；`instance_pool.py:61,642,689,739`；`client.py:176,187-197`；`account_keepalive.py:311-325`；`build_surface.py:150-162,1312-1321`
- 测试/证据：`06-feature-ledger.md:408,568,574,578,580,582,587-589`
- 凭据形状文件（不复制值）：`docs/dev/evidence/p3-build3/spikeQ4-evidence.json:39,41`；`docs/BOTGUARD_VERIFICATION_CHAIN.md:128,140,284,286`
- 审计运行的基线：`pytest -q` 942 passed / 33.58s；`git diff --shortstat upstream/master...rebuild` = 518 files, +66,739/-1,981；`git worktree list` = main + steady-ketch；`rebuild/upstream` 尚不存在。

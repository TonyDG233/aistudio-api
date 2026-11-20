# REV-2.1.5.7 — Firefox 152 ClientHello 出处注释与回归锁审查

- **审查对象**：基线 `a79cbe3` 之后工作区未提交改动（`waa-signer/internal/aistudio/transport_browser.go` 注释补充 + 新增 `transport_browser_test.go`）
- **审查方式**：100% 独立只读；全量 diff 逐行审计、源码/依赖只读核对、Go 门禁独立实跑、变异实验
- **最终判定**：**PASS**（P0/P1 为零；P2 均为注释文案精度项，不阻断提交，建议提交前顺手修正）
- **只读声明**：本次未修改任何生产代码、测试、配置或依赖；唯一写入为本报告。变异实验使用 Go `-overlay` 指向系统临时目录的副本，仓库工作树未被改写。

## 0. 业务域选择理由

改动位于 `waa-signer` 的 TLS ClientHello 构造与无浏览器引擎传输层，`docs/dev/rebuild/domains/INDEX.md:26` 将 `P3-pool-instance` 的职责明确列为「双引擎（`browser` CDP/CloakBrowser + `browserless` WAA Signer）……指纹加固」，`P3-pool-instance/index.md:9` 亦将 `waa-signer`（Firefox 152 uTLS）列为该域核心代码模块。因此本报告归入 `P3-pool-instance/reviews/`；`P5-generation` 虽也承载过 signer 生存期/配置类审查，但其职责聚焦模型发现与生成主链，不是本改动的直接归属。

## 1. 变更盘点与 diff 全量审计

`git status --porcelain`：

```text
 M waa-signer/internal/aistudio/transport_browser.go
?? waa-signer/internal/aistudio/transport_browser_test.go
```

`git diff --numstat`：`14  1  waa-signer/internal/aistudio/transport_browser.go`。全量 diff 只有一处 hunk（`transport_browser.go:637-650`）：删除 1 行旧注释，新增 13 行注释；`firefox152ClientHello` 函数体（`:651-681`）逐行未变。生产逻辑零字节变化，Python/前端/配置零改动。

新增测试文件已完整读取（104 行），未发现 diff 之外的第三处改动或残留调试代码。

## 2. 注释事实核验

### 2.1 `Version` 标签语义

`waa-signer/internal/aistudio/transport_browser.go:652-655` 以 `profiles.Firefox_148` 为底并覆盖 `hello.Version = "152"`。只读核对 uTLS：

- `ClientHelloID.Version` 的字段注释明确为「specifies version of a mimicked clients… Not used in randomized, custom handshake, and default Go」（`github.com/bogdanfinn/utls@v1.7.7-barnius/u_common.go:150-167`）。
- 实际握手分派 `applyPresetByID` 按 `id.Client` 分支，非 randomized 路径调用 `id.ToSpec()`（即 `SpecFactory`），不按 Version 字符串查表（`u_parrots.go:3239-3262`）。
- Version 参与 `Str()` 的场景仅用于 `HelloGolang`/`HelloCustom` 比较与 `skipResumptionOnNilExtension`（本处恒为 true），不改变线上字节（`u_conn.go:78,112,284`）。

结论：注释「Version 字符串只是标签、不产生任何线上字节」与代码事实一致。

### 2.2 16 条 cipher 与 `spec.CipherSuites` 实际值

测试失败输出可直接读出实现实际值（16 条，顺序如下）：

```text
0x1301 0x1303 0x1302 0xc02b 0xc02f 0xcca9 0xcca8
0xc02c 0xc030 0xc00a 0xc013 0xc014 0x9c 0x9d 0x2f 0x35
```

与 `transport_browser.go:660-677` 的 16 条逐一对应，数量与顺序一致；`0xC009`（`TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA`）不在其中。

进一步用 Firefox 152 发布源码独立验证成员集合（`mozilla-firefox/firefox` 标签 `FIREFOX_152_0_RELEASE`）：

- `security/manager/ssl/nsNSSComponent.cpp:763-776` 仍登记 `ecdhe_ecdsa_aes_128_sha`、`ecdhe_rsa_aes_128/256_sha`、`dhe_rsa_aes_128/256_sha` 等 pref。
- `modules/libpref/init/StaticPrefList.yaml:18548-18575` 中 `security.ssl3.ecdhe_ecdsa_aes_128_sha=false`、`security.ssl3.dhe_rsa_aes_128_sha=false`、`security.ssl3.dhe_rsa_aes_256_sha=false`，其余列表内套件为 `true` 或发布版为真。

因此 16 条列表与 Firefox 152 发布版默认 cipher 成员集合一致；顺序由第三方抓包声明背书（见 Q-1），本次离线不能独立复核顺序。

### 2.3 0xC009 与 Bug 2035801

- 注释所述 Bug 存在且标题为「Disable the pref security.ssl3.ecdhe_ecdsa_aes_128_sha for release」，`cf_status_firefox150/151/152` 均为 `fixed`，目标里程碑 `152 Branch`（Bugzilla REST 只读查询）。核心事实「0xC009 在 151/152 已禁用」成立。
- 但 Bug 同时标记 Firefox 150 为 `fixed`，注释写「151/152 起」不够精确（见 P2-1）。
- 测试 `transport_browser_test.go:48-50` 在逐位相等之外另有显式 `0xC009` 缺席断言，语义清晰。

### 2.4 传承链、抓包对照与未来基线引用

- `Mag1cFall/AIStudio2API` 的 `e0d6f08` 经 GitHub API 核实存在；其 `internal/aistudio/transport_browser.go` 中 `firefox152Profile()` 含与本处完全相同的 16 条 cipher 序列及 `Version="152"`。
- `DeconstructedCube/aistudio-api` 的 `9000d1b` 存在于本地对象库，且位于 `upstream/feat/browserless-waa-engine`；其 `firefox152ClientHello()` 函数体与当前一致（注释除外）。
- 本仓 `69904e3` 的同一函数与当前一致（注释除外）。
- 三代使用的 `bogdanfinn/tls-client` 均为 `v1.15.1`（`e0d6f08` 的 `go.mod`、`9000d1b`/`69904e3` 的 `waa-signer/go.mod`、当前 `waa-signer/go.mod` 核对一致），故 `profiles.Firefox_148` 的 17 项扩展顺序在三代间继承自同一底版。
- `Camoufox` 仓库存在 `v152.0.4-beta.30` 等 152.0.4 预发布线，注释的「Camoufox 152.0.4」版本线属实；但抓包产物未入库（见 Q-1）。
- `github.com/SaamoCha/parroteer` 仓库存在，README 明确列出 `cmd/gen-spec`（browserleaks JSON → uTLS spec）与 `cmd/replay-verify`（回放校验）流程（`README.md:57-59,278-281`），注释引用属实。

## 3. 测试有效性与变异实验

### 3.1 是否硬编码、是否存在同义反复

`transport_browser_test.go:27-44` 的 `wantCiphers` 是测试内独立写死的 16 项序列，`slices.Equal` 做顺序敏感比较；`52-70` 的 17 项扩展类型序列同样独立写死。测试没有从 `spec.CipherSuites`/`firefox152ClientHello()` 反读期望值，不构成同义反复。

`wantCiphers` 使用 `tls.*` 常量而非裸 hex，仍能锁定线上值（这些是 RFC 固定的协议常量）；若未来库改名但线值不变，测试仍通过，属可接受行为。

### 3.2 变异实验（Go `-overlay`，不改工作树）

| 变异 | 结果 | 证据 |
|---|---|---|
| 在 `0xC00A` 后插入 `0xC009` | **FAIL** | `transport_browser_test.go:46`，got 17 项 vs want 16 项 |
| 交换 `0xC02B` 与 `0xC02F` 顺序 | **FAIL** | `transport_browser_test.go:46`，顺序不等 |
| 删除末尾 `0x0035` | **FAIL** | `transport_browser_test.go:46`，got 15 项 vs want 16 项 |
| 交换 `spec.Extensions[0]` 与 `[1]` | **FAIL** | `transport_browser_test.go:76`，`SNIExtension`/`ExtendedMasterSecretExtension` 顺序不符 |

结论：cipher 增、删、换序与扩展换序均会真实变红，测试有牙。

### 3.3 扩展断言与 Worker 自报偏离

- 测试 `:52-70` 的 17 项扩展序列与 `profiles.Firefox_148` 底版一致，`:78-83` 另断言首尾扩展，`:85-103` 断言 `X25519MLKEM768` 同时出现在 supported curves 与 key shares。
- Worker 自报的测试侧修正属实：uTLS 定义 `type DelegatedCredentialsExtension = FakeDelegatedCredentialsExtension`（`github.com/bogdanfinn/utls@v1.7.7-barnius/u_tls_extensions.go:1802`），别名不影响 `%T` 输出，故期望类型名必须是 `*tls.FakeDelegatedCredentialsExtension`；仅测试侧修正，生产零改动。

## 4. 门禁独立实跑

配方：`CGO_ENABLED=1`、`GOOS=windows`、`GOARCH=amd64`、`GOTOOLCHAIN=auto`、仓库内置 Zig 0.13.0（`x86_64-windows-gnu`）与 `waa-signer/.deps/spidermonkey/windows-amd64` 头/库；实际解析到 `go1.27.0 windows/amd64`。

| 门禁 | 命令 | 实际结果 |
|---|---|---|
| 格式 | `gofmt -l internal/aistudio/transport_browser.go internal/aistudio/transport_browser_test.go` | 空输出（exit 0） |
| 构建 | `go build ./...` | exit 0 |
| 全量测试 | `go test -count=1 ./...` | 全部 `ok`：`cmd/waa-signer 0.210s`、`internal/aistudio 0.186s`、`internal/poolstate 0.107s`、`internal/signerapi 0.121s`、`internal/upstreamguard 0.092s`；其余 `no test files`；exit 0 |
| 新增用例 | `go test -count=1 -run TestFirefox152ClientHelloSpec -v ./internal/aistudio` | `--- PASS: TestFirefox152ClientHelloSpec (0.00s)`；exit 0 |
| 空白 | `git diff --check` | 空输出（exit 0） |

未运行 Python/前端门禁：本次 diff 无对应文件，`git status` 已证明零改动。

## 5. 隐私扫描

对本轮 `git diff`、新增测试文件、改动源文件及本报告，按指定模式集合（本机盘符绝对路径、指定私有网段样例、常见邮箱域名）检索：命中 **0**。diff 无 Cookie/Token/API Key/邮箱/内网地址；报告仅引用公开仓库、模块路径与公开 Bug 号。

## 6. 问题清单

### P0

- 无。

### P1

- 无。

### P2

- **P2-1（注释版本表述不精确）**：`transport_browser.go:644` 写「Mozilla Bug 2035801 记载 151/152 起禁用」，而 Bugzilla 同时标记 `cf_status_firefox150=fixed`、`151=fixed`、`152=fixed`。建议改为「152 起（Bug 同时标记 150/151 fixed）」或「≥150」；不影响代码与测试。
- **P2-2（出处文案与真实差量/传承链精度）**：`:640-648` 只把相对 `Firefox_148` 的差量归因于移除 0xC009，但 16 条列表相对底版还移除了 `0x0033/0x0039` 两条 DHE 套件（Firefox 152 源码确认这两项 pref 为 false，实现正确）；同时「三代逐字一致」仅对 16 条 cipher 序列成立，`e0d6f08` 的函数整体是 `firefox152Profile() (profiles.ClientProfile)` 并经 `NewClientProfile` 组装，与当前返回 `tls.ClientHelloID` 的函数并非逐字相同。建议把差量与「cipher 序列三代一致」写明，避免后续维护者误读。

### Q

- **Q-1（抓包产物不可复现）**：注释称 16 条 cipher 与扩展顺序经 Camoufox 152.0.4 实抓逐字段比对，但仓库未提交抓包/对照产物；本次只能独立验证 cipher 成员集合（Firefox 152 发布源码）与扩展顺序继承自 `Firefox_148` 底版，无法离线复核抓包本身与顺序声明。
- **Q-2（「全生态」范围）**：注释称「该库与 refraction uTLS 全生态均无原生 Firefox 152 规格」。本次只验证了锁定的 `bogdanfinn/tls-client v1.15.1`（最高 `Firefox_148`）与 `bogdanfinn/utls v1.7.7-barnius`、`Mag1cFall/utls v1.8.2-firefox.3`（最高 `HelloFirefox_148`，模块缓存无 `Firefox_152`）；「全生态」超出可离线穷举范围。
- **Q-3（未做线上/JA3/JA4 复核）**：本次仅做 spec 级断言，未发起真实 TLS 握手或抓包；「Version 不产生线上字节」由 uTLS 源码静态确认，非线上抓包确认。

### N

- **N-1**：`transport_browser_test.go:78-83` 的首/尾扩展断言在 `:75-77` 的 `slices.Equal` 之后属于冗余保护，保留无害。
- **N-2**：扩展断言依赖 `%T` 的包限定类型名，未来 uTLS 重命名具体类型会产生预期内的红灯；这是锁定行为的一部分，不是缺陷。
- **N-3**：`transport_browser_test.go` 当前为 untracked，提交时必须显式纳入（`git add`），否则回归锁不会进入版本库。
- **N-4**：测试直接调用 `hello.SpecFactory()`，未走 `UClient` 握手路径；它锁定的是 spec 形状，不是握手行为。两者边界清晰。

## 7. 结论

- **PASS**。改动严格为「一处注释 + 一个新测试文件」；生产逻辑零字节变化，Python/前端/配置零改动。
- 注释的 Version 标签语义、16 条 cipher 实际值、0xC009 缺席、Bug/commit/Parroteer 引用均与可核验事实一致；Firefox 152 发布源码进一步独立确认了 cipher 成员集合。
- 测试断言为独立硬编码序列，cipher 增/删/换序与扩展换序均实测变红，无同义反复；Go 构建、全量测试、目标测试、gofmt、`git diff --check` 全部通过。
- **可裁决遗留**：仅 P2-1/P2-2 两处注释文案精度项，可由 Worker 自裁修正，不阻断提交、不需要再开复审轮次；Q-1/Q-2/Q-3 为外部证据或线上复核范围，保持开放。

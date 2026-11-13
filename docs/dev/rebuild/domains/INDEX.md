# `docs/dev/rebuild/domains/` 总索引与双层治理规范

本目录按业务域组织仓库各核心子系统的**任务计划看板**与**独立审查留档**，统一消除同号域分裂与跨目录混淆。

## 1. 双层治理结构规范

- **域根目录（`domains/<Domain>/index.md`）**：存放该域的职责边界、核心代码模块、架构红线、任务计划与里程碑进度看板，以及本域 `reviews/` 产物索引。
- **审查目录（`domains/<Domain>/reviews/REV-X.Y.Z.W-<slug>.md` 与 `assets/`）**：存放独立只读 Reviewer 的审计报告与实测截图成果留档（`REV-*.md` 须附 PASS/CONDITIONAL/FAIL 结论、`file:line` 证据、门禁实跑结果与只读声明；涉及前端 UI 的步骤须在 `reviews/assets/` 保留代表性页面实测截图）。
- **文档专属域（`domains/docs-and-audit/`）**：专门负责全仓 `docs/` 与仓库根目录 `.md`（如 `AGENT.md`、`README.md`、`README_EN.md`）变更工作的任务计划看板与审计留档。

## 2. 版本号与审查文档四位标号制度（`X.Y.Z` + `.W`）

遵循 `AGENT.md` §10.3 定义的 **「三段式发布版本号（`X.Y.Z`）+ 隐藏第四位审查文档标号（`REV-X.Y.Z.W`）」** 规范：

1. **第一位（Major 主版本号 `X`）**：**完全推倒重构级变更**（`0` 原版 → `1` 上游双方案 → `2` 本仓四层架构与多实例池重构；除非再次全盘推倒重构，否则保持 `2` 不变）。
2. **第二位（Minor 功能版本号 `Y`）**：**重大功能性变更**（如 `2.1.0` 无浏览器双引擎、`2.2.0` 独立 Android APK 服务端化等）。
3. **第三位（Patch 维护版本号 `Z`）**：**维护式更新**（防风控加固、协议/配额校准、体验与性能调优等）。对外发布、Git Tag 与 `docs/changelog.md` 始终使用前三位 `X.Y.Z`。
4. **隐藏第四位（Review 步进标号 `W`）**：**单个改进意见/子任务的审查文档标号**（`REV-X.Y.Z.W-<slug>.md`）。同一维护版本 `X.Y.Z` 周期内的多次子任务迭代审查按时间先后递增第四位 `.1`、`.2`、`.3`…，严禁因单步审查私自递增第三位版本号 `Z`；历史阶段性标号（`REV-B*`、`REV-C*`、`REV-2.1-A/B`、`REV-2.1.1`、`REV-2.1.2` 等）保留既成文件名不回改。

## 3. 全域总览索引表（11 个子域）

| 域目录 | 职责范围 | 根计划与看板 | 最新审查报告 |
|---|---|---|---|
| `P1-account` | 账号注册表、Cookie 跨域保真与 `/RotateCookies` 轮换、同源兄弟同步、Tier 探测安全、预设与 D-08/D-13 | [`P1-account/index.md`](P1-account/index.md) | [`REV-C1b-vps-tier-and-403-fix.md`](P1-account/reviews/REV-C1b-vps-tier-and-403-fix.md) |
| `P2-routing` | Free/Pro 分池路由、`active_requests` 准入、403 两阶段重签/切号/不降级、`verify-account` 熔断、429 重试切换与 `quota_scope` | [`P2-routing/index.md`](P2-routing/index.md) | [`REV-B2-4-p2-routing.md`](P2-routing/reviews/REV-B2-4-p2-routing.md) |
| `P3-pool-instance` | 单实例边界、双引擎（`browser` CDP/CloakBrowser + `browserless` WAA Signer）、多实例池、指纹加固、快照预热与事务热切换 | [`P3-pool-instance/index.md`](P3-pool-instance/index.md) | [`REV-2.1.3.1-session-expired-sibling-sync.md`](P3-pool-instance/reviews/REV-2.1.3.1-session-expired-sibling-sync.md) |
| `P5-generation` | 四协议生成主链（Gemini/OpenAI/Claude/Responses）、WIRE/JSPB 解析、PG-01..PG-08 工具调用、ATG、SSE Keepalive、模型发现与 `countTokens` | [`P5-generation/index.md`](P5-generation/index.md) | [`REV-2.1.3.4-atg-ttft-hydrogen.md`](P5-generation/reviews/REV-2.1.3.4-atg-ttft-hydrogen.md) |
| `P6-keepalive` | 24h Cookie 回写与轮换保活、24h Tier/Email 刷新、单全局串行 job 锁、手动保活入口与 429 冷却重置 | [`P6-keepalive/index.md`](P6-keepalive/index.md) | [`REV-B3-p6-keepalive.md`](P6-keepalive/reviews/REV-B3-p6-keepalive.md) |
| `P7-logs-stats` | 统一日志三 Sink（5000 ring / 7d 归档 / SSE）、脱敏与安全 dump、噪音过滤、单一统计事件账本与趋势图 | [`P7-logs-stats/index.md`](P7-logs-stats/index.md) | [`REV-C4b.md`](P7-logs-stats/reviews/REV-C4b.md) |
| `P8-api-ui` | FastAPI 管理接口、Vue 3 管理台（Dashboard/Accounts/Settings/Logs）、`AISTUDIO_UI_DEBUG=1` 离线沙盒、油猴脚本与三分辨率 UI 验收 | [`P8-api-ui/index.md`](P8-api-ui/index.md) | [`REV-2.1.3.5-ui-polish-mobile.md`](P8-api-ui/reviews/REV-2.1.3.5-ui-polish-mobile.md) |
| `P9-boot` | 启动配置校验与深合并、单一模板 `defaults/config.yaml`、D-14 API/Web 鉴权分离、lifespan 装配与关闭顺序 | [`P9-boot/index.md`](P9-boot/index.md) | [`REV-B1-p9-pre.md`](P9-boot/reviews/REV-B1-p9-pre.md) |
| `P10-android-apk` | Stage 2.2.0 Android APK 独立服务端化（NDK 交叉编译 `libwaa_signer.so`、嵌入式 Python 网关、`MediaSession` 无感保活、WebView 提取直通） | [`P10-android-apk/index.md`](P10-android-apk/index.md) | 规划中（[`reviews/.gitkeep`](P10-android-apk/reviews/.gitkeep)） |
| `docs-and-audit` | 全仓 `docs/` 与仓库根目录 `.md`（`AGENT.md`、`README.md` 等）变更任务计划看板与审计留档 | [`docs-and-audit/index.md`](docs-and-audit/index.md) | [`REV-2.1.3.6-versioning-and-review-audit.md`](docs-and-audit/reviews/REV-2.1.3.6-versioning-and-review-audit.md) |
| `upstream-merges` | 上游 `upstream/master` 提交对账、冲突取舍与受保护架构审计台账 | [`upstream-merges/index.md`](upstream-merges/index.md) | [`REV-MERGE-852de53-oct31.md`](upstream-merges/reviews/REV-MERGE-852de53-oct31.md) |

# `docs/dev/rebuild/domains/` 总索引与双层治理规范

本目录按业务域组织仓库各核心子系统的**任务计划看板**与**独立审查留档**，统一消除同号域分裂与跨目录混淆。

## 1. 双层治理结构规范

- **域根目录（`domains/<Domain>/index.md`）**：存放该域的职责边界、核心代码模块、架构红线、任务计划与里程碑进度看板，以及本域 `reviews/` 产物索引。
- **审查目录（`domains/<Domain>/reviews/REV-*.md` 与 `assets/`）**：存放独立只读 Reviewer 的审计报告与实测截图成果留档（`REV-*.md` 须附 PASS/CONDITIONAL/FAIL 结论、`file:line` 证据、门禁实跑结果与只读声明；涉及前端 UI 的步骤须在 `reviews/assets/` 保留代表性页面实测截图）。
- **文档专属域（`domains/docs-and-audit/`）**：专门负责全仓 `docs/` 与仓库根目录 `.md`（如 `AGENT.md`、`README.md`、`README_EN.md`）变更工作的任务计划看板与审计留档。

## 2. 全域总览索引表（11 个子域）

| 域目录 | 职责范围 | 根计划与看板 | 最新审查报告 |
|---|---|---|---|
| `P1-account` | 账号注册表、Cookie 跨域保真与 `/RotateCookies` 轮换、同源兄弟同步、Tier 探测安全、预设与 D-08/D-13 | [`P1-account/index.md`](P1-account/index.md) | [`REV-C1b-vps-tier-and-403-fix.md`](P1-account/reviews/REV-C1b-vps-tier-and-403-fix.md) |
| `P2-routing` | Free/Pro 分池路由、`active_requests` 准入、403 两阶段重签/切号/不降级、`verify-account` 熔断、429 重试切换与 `quota_scope` | [`P2-routing/index.md`](P2-routing/index.md) | [`REV-B2-4-p2-routing.md`](P2-routing/reviews/REV-B2-4-p2-routing.md) |
| `P3-pool-instance` | 单实例边界、双引擎（`browser` CDP/CloakBrowser + `browserless` WAA Signer）、多实例池、指纹加固、快照预热与事务热切换 | [`P3-pool-instance/index.md`](P3-pool-instance/index.md) | [`REV-2.1.3.md`](P3-pool-instance/reviews/REV-2.1.3.md) |
| `P5-generation` | 四协议生成主链（Gemini/OpenAI/Claude/Responses）、WIRE/JSPB 解析、PG-01..PG-08 工具调用、ATG、SSE Keepalive、模型发现与 `countTokens` | [`P5-generation/index.md`](P5-generation/index.md) | [`REV-C3.md`](P5-generation/reviews/REV-C3.md) |
| `P6-keepalive` | 24h Cookie 回写与轮换保活、24h Tier/Email 刷新、单全局串行 job 锁、手动保活入口与 429 冷却重置 | [`P6-keepalive/index.md`](P6-keepalive/index.md) | [`REV-B3-p6-keepalive.md`](P6-keepalive/reviews/REV-B3-p6-keepalive.md) |
| `P7-logs-stats` | 统一日志三 Sink（5000 ring / 7d 归档 / SSE）、脱敏与安全 dump、噪音过滤、单一统计事件账本与趋势图 | [`P7-logs-stats/index.md`](P7-logs-stats/index.md) | [`REV-C4b.md`](P7-logs-stats/reviews/REV-C4b.md) |
| `P8-api-ui` | FastAPI 管理接口、Vue 3 管理台（Dashboard/Accounts/Settings/Logs）、`AISTUDIO_UI_DEBUG=1` 离线沙盒、油猴脚本与三分辨率 UI 验收 | [`P8-api-ui/index.md`](P8-api-ui/index.md) | [`REV-2.1.2.md`](P8-api-ui/reviews/REV-2.1.2.md) |
| `P9-boot` | 启动配置校验与深合并、单一模板 `defaults/config.yaml`、D-14 API/Web 鉴权分离、lifespan 装配与关闭顺序 | [`P9-boot/index.md`](P9-boot/index.md) | [`REV-B1-p9-pre.md`](P9-boot/reviews/REV-B1-p9-pre.md) |
| `P10-android-apk` | Stage 2.2.0 Android APK 独立服务端化（NDK 交叉编译 `libwaa_signer.so`、嵌入式 Python 网关、`MediaSession` 无感保活、WebView 提取直通） | [`P10-android-apk/index.md`](P10-android-apk/index.md) | 规划中（[`reviews/.gitkeep`](P10-android-apk/reviews/.gitkeep)） |
| `docs-and-audit` | 全仓 `docs/` 与仓库根目录 `.md`（`AGENT.md`、`README.md` 等）变更任务计划看板与审计留档 | [`docs-and-audit/index.md`](docs-and-audit/index.md) | [`REV-C5a.md`](docs-and-audit/reviews/REV-C5a.md) |
| `upstream-merges` | 上游 `upstream/master` 提交对账、冲突取舍与受保护架构审计台账 | [`upstream-merges/index.md`](upstream-merges/index.md) | [`REV-MERGE-852de53-oct31.md`](upstream-merges/reviews/REV-MERGE-852de53-oct31.md) |

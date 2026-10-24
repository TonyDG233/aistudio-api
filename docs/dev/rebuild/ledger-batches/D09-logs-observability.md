# D09 Logs / Observability

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 待用户决定 |
|---|---|---|---|---|---|---|---|
| D09 | 统一日志格式与 TTY 颜色（`UnifiedFormatter`） | `880954f`（upstream）；`src/aistudio_api/infrastructure/utils/logger.py:26-60` | 原生 | working | `tests/unit/test_logging.py:33-54` | keep | 是否保留彩色 stderr |
| D09 | stderr sink + uvicorn/uvicorn.error/uvicorn.access 接入统一格式（propagate=False） | `880954f`（upstream）；`logger.py:269-295` | 原生 | working | 代码证据；`tests/unit/test_logging.py:68-86` 仅覆盖级别 | keep | uvicorn access 是否继续单独输出 |
| D09 | `setup_logging` 三 sink 装配（stderr + broadcast + daily file，幂等 clear） | `eed30b6`；`logger.py:264-297` | 改造 | working | 代码证据；`api/app.py:49-50` import 时执行 | keep | 是否允许运行期重建 sink |
| D09 | root 固定 DEBUG 捕获全量，stderr 按 level 过滤 | `eed30b6`、`86391c8`；`logger.py:245-258`、`:283` | 改造 | working | 代码证据；无独立断言 | port | 是否改回 root 跟随 level（将影响抓全量） |
| D09 | 动态日志级别 `set_log_level`（aistudio/uvicorn 同步） | `880954f`（upstream）；`logger.py:229-261` | 原生 | working | `tests/unit/test_logging.py:68-86` | keep | 级别是否含第三方 logger |
| D09 | 启动期读取 `logging.level` 并初始化 | `880954f`（upstream）；`api/app.py:49-50`；`infrastructure/gateway/model_defaults.py` | 原生 | working | 代码证据；`test_logging.py:182-193` 覆盖 /config 字段 | keep | 启动失败时回退级别 |
| D09 | 运行期 `PUT /config/logging`（level/dump_requests/dump_to_file/dump_dir）持久化 | `880954f`、`d8b1c6c`（upstream）；`api/routes_system.py:494-549`；`web/src/components/settings/LogConfigCard.vue` | 原生 | working | `tests/unit/test_logging.py:195-230` | keep | 跨 D01/D11，归属是否留在 D09 |
| D09 | `DEBUG`/`AISTUDIO_DEBUG` 仅触发 dump、不改日志级别 | `880954f`（upstream）；`logger.py:169-217` | 原生 | working | `tests/unit/test_logging.py:89-123` | keep | 是否合并统一开关 |
| D09 | 应用日志总线（`BroadcastHandler`→ring→SSE），与请求生命周期日志分离 | `eed30b6`；`log_broadcast.py:276-298`、`:415-419` | 新增 | working | `tests/unit/test_log_broadcast.py:141-160` | keep | 总线是否只收 aistudio 命名空间 |
| D09 | ring 容量 5000 + 单调整数 id | `eed30b6`、`86391c8`（2000→5000）；`log_broadcast.py:23`、`:150-155`、`:184-193` | 新增 | working | `tests/unit/test_log_broadcast.py:24-38`、`:72-79` | keep | 容量是否可配置 |
| D09 | WARNING 保底淘汰（DEBUG/INFO 先出队） | `86391c8`；`log_broadcast.py:26`、`:194-201` | 新增 | working | `tests/unit/test_log_broadcast.py:82-91` | keep | 溢出为 O(n) 扫描，是否接受 |
| D09 | `replay(level,last_id,limit)` 默认回放 5000 | `eed30b6`、`86391c8`（100→5000）；`log_broadcast.py:25`、`:239-261` | 新增 | working | `tests/unit/test_log_broadcast.py:40-79` | keep | 首连是否改为不自动回放全量 |
| D09 | gap 事件（`last_id < oldest_id-1`） | `eed30b6`；`log_broadcast.py:251-254`；`api/routes_logs.py:63-65` | 新增 | working | `tests/unit/test_logs_routes.py:83-107` | keep | gap 是否携带 missed 数量 |
| D09 | 订阅上限 8，超限返回 503 | `eed30b6`；`log_broadcast.py:27`、`:213-232`；`routes_logs.py:133-134` | 新增 | working | `test_log_broadcast.py:111-119`；`test_logs_routes.py:183-196` | keep | 8 是否按用户/按 IP 计 |
| D09 | 订阅队列 500，满则丢最旧并累计 dropped | `eed30b6`；`log_broadcast.py:24`、`:263-273` | 新增 | working | `test_log_broadcast.py:94-108`；`routes_logs.py:92-94` | keep | 丢弃阈值是否可配 |
| D09 | `dropped` 事件下发 | `eed30b6`；`routes_logs.py:67-68`、`:92-94` | 新增 | working | 代码证据；`test_logs_routes.py:109-125` 未断言 dropped | keep | 是否合并进 heartbeat 统计 |
| D09 | SSE 心跳 15s（`: ping`）+ 断连清理 | `eed30b6`；`routes_logs.py:30`、`:88-102`、`:108-109` | 新增 | working | `tests/unit/test_logs_routes.py:109-125` | keep | 心跳间隔是否可配 |
| D09 | `/logs/stream` 路由（level 校验、Last-Event-ID、SSE 头、web auth） | `eed30b6`；`routes_logs.py:112-153`；`api/app.py:273` | 新增 | working | `tests/unit/test_logs_routes.py:127-210` | keep | 是否保留 `fetch` 而非 EventSource |
| D09 | 总线/落盘脱敏 `sanitize_log_message`（auth/cookie/sapisid/api key/bearer） | `eed30b6`；`log_broadcast.py:40-88`、`:182`、`:381` | 新增 | working | `tests/unit/test_log_broadcast.py:122-139`；`test_log_archive.py:47-61` | keep | 正则脱敏是否足够（需转结构化白名单） |
| D09 | dump 报文头/查询参数脱敏 `_mask_headers/_mask_query_params` | `4cd38ae`（upstream）；`logger.py:311-337` | 原生 | working | `tests/unit/test_logging.py:126-178` | keep | 掩码长度是否需调整 |
| D09 | dump 落盘 `req_*.json` + `latest_request.json` | `d8b1c6c`（upstream）；`logger.py:339-449` | 新增 | partial | `tests/unit/test_logging.py:247-309`；落盘含原始 `headers`/`raw_body`（`logger.py:428`、`:433`） | port | 是否只落 `safe_*`/禁止明文 body |
| D09 | dump 开关链（env/config/runtime override；`dump_to_file` 默认跟随 `dump_requests`） | `880954f`、`d8b1c6c`（upstream）；`logger.py:72-105`、`:189-227` | 原生 | working | `tests/unit/test_logging.py:89-123`、`:195-230` | keep | 默认是否关闭文件落盘 |
| D09 | 请求生命周期 access log（middleware，独立于应用总线） | `880954f`（upstream）；`api/app.py:376-480`、`:471-478` | 原生 | partial | 代码证据；`test_logging.py:233-244` 仅覆盖 dump 路径；uvicorn.access 另出同类行 | keep | 是否关闭 uvicorn.access 避免重复 |
| D09 | dump 模式响应体缓存回放 + SSE 不消费 body | `880954f`（upstream）；`api/app.py:438-470` | 原生 | working | 代码证据；无独立断言 | keep | 大响应缓存上限 |
| D09 | 按日归档 `aistudio-YYYYMMDD.log` + 跨天切换文件 | `eed30b6`；`log_broadcast.py:317-322`、`:356-412` | 新增 | working | `tests/unit/test_log_archive.py:41-77` | keep | 时区按本地日还是 UTC 日 |
| D09 | 归档 7 天保留清理（启动 + 跨天） | `eed30b6`；`log_broadcast.py:28`、`:325-353`；`logger.py:285-287` | 新增 | working | `tests/unit/test_log_archive.py:80-102` | keep | 7 天是否可配 |
| D09 | 日志导出：单日 `.log`、多日 `.zip`、最多 7 天、web auth | `eed30b6`；`routes_logs.py:31`、`:166-260` | 新增 | partial | `tests/unit/test_logs_routes.py:212-309`；单日缺文件返回 200 空文件（`routes_logs.py:244-250`） | keep | 单日缺档是否改 404 |
| D09 | LogsView SSE 消费、指数退避重连、401 跳登录 | `c7bdd80`；`web/src/views/LogsView.vue:337-405`；`web/src/api/logs.ts:24-47` | 新增 | unverified | 无前端自动化测试；截图 `docs/dev/evidence/wp7-v2-20261020/p5-logs-*.png` | keep | 是否补前端/接口联调测试 |
| D09 | LogsView 级别过滤 + 多词搜索高亮 | `c7bdd80`、`2944c3c`；`LogsView.vue:116-173`、`:212-237` | 新增 | unverified | 无前端测试；代码证据 | keep | 级别过滤是否应多选下发而非单阈值 |
| D09 | LogsView 暂停/继续、清屏、暂停缓冲计数 | `c7bdd80`；`LogsView.vue:260-295`、`:733-739` | 新增 | unverified | 无前端测试；代码证据 | keep | 暂停是否服务端停止推送 |
| D09 | LogsView 下载当前过滤结果 | `c7bdd80`；`LogsView.vue:436-454` | 新增 | unverified | 无前端测试；客户端 Blob 导出 | keep | 是否保留与归档导出并存 |
| D09 | LogsView 按时间导出（1/3/7 天、范围选择、ZIP） | `c7bdd80`；`LogsView.vue:456-491`；`web/src/api/logs.ts:55-94` | 新增 | unverified | 无前端测试；后端测试见导出行 | keep | 日期边界时区与后端是否一致 |
| D09 | LogsView 前端 5000 条上限 + 虚拟滚动 | `c7bdd80`；`LogsView.vue:29-30`、`:175-182`、`:283-295` | 新增 | unverified | 无前端测试；代码证据 | keep | 上限与后端 ring 是否需同源配置 |
| D09 | gap/dropped 提示条（UI） | `c7bdd80`；`LogsView.vue:324-334`、`:741-755` | 新增 | partial | 服务端 gap 载荷为 `last_id/oldest_id`（`routes_logs.py:63-65`），UI 读取 `missed/dropped/gap`，只显示泛化文案；dropped 提示不清除 | port | 是否统一 gap 事件字段 |
| D09 | WARP/tunnel/CDP 及三方库 DEBUG 噪音抑制 | 无实现提交；`logger.py:245-258`（root DEBUG）、`log_broadcast.py:194-201`（仅靠淘汰） | 待判定 | unverified | 未验证；未发现按 logger 名过滤（如 websockets/httpx/uvicorn.access/CDP） | port（需新增） | 是否需要噪音名单/采样 |

## 冲突/缺口

| 项 | 说明 | 证据 | 影响 |
|---|---|---|---|
| dump 文件泄露敏感信息 | `req_*.json` 同时写原始 `headers`/`raw_body`，与总线脱敏口径不一致 | `logger.py:421-444`（尤其 `:428`、`:433`） | D09 安全、D02 cookie |
| dump 默认目录可疑 | `parents[3]/dumps` 解析为 `src/dumps`，非 data 目录；UI 默认值 `dumps` | `logger.py:154`；`LogConfigCard.vue:32` | D09、D01 配置 |
| 请求日志双份 | middleware access log 与 `uvicorn.access` 输出同类行；dump 开启时 access 行被 dump 取代 | `app.py:289-295`、`:438-478`；`logger.py:260-261` | D09、D11 |
| gap 字段不一致 | 后端 `{"last_id","oldest_id"}`，前端读 `missed/dropped/gap` | `routes_logs.py:63-65`；`LogsView.vue:324-329` | D09 |
| 全量 DEBUG 无降噪 | root DEBUG 使三方库/CDP 调试行进入 ring，仅容量淘汰兜底 | `logger.py:245-258`；`log_broadcast.py:194-201` | D09、D03 |
| 单日导出缺档 | 单日不存在仍返回 200 空 `.log`，多日才 404 | `routes_logs.py:244-253` | D09、D11 |
| 日志级别跨域 | 运行期改级别入口在 `routes_system.py`，UI 在 Settings 卡片 | `routes_system.py:494-549`；`LogConfigCard.vue` | D09、D01、D11 |

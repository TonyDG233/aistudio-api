# D10 Stats / Token / Usage — 功能台账

基线：`upstream/master@4623109..rebuild`；仅 D10；证据缺失一律标 `未验证`。

| domain | feature | provenance(commits/files) | 原生/改造/新增 | 当前状态 | 测试/证据 | keep/port/drop | 待用户决定 |
|---|---|---|---|---|---|---|---|
| D10 Stats/Token/Usage | 上游 attempt 总量计数（requests/success/rate_limited/errors/last_used） | `4623109` `src/aistudio_api/api/state.py:25-39,120-151`；`1adf3c2` `src/aistudio_api/api/state.py:65-133,248-263` | 原生 | working | `tests/unit/test_model_stats_v2.py:31-81`；`tests/unit/test_runtime_persistence.py:13-43` | keep | 待定 |
| D10 Stats/Token/Usage | attempt Token 累计（prompt/completion/total） | `4623109` `src/aistudio_api/api/state.py:120-151`；`1adf3c2` `src/aistudio_api/api/state.py:216-245` | 原生 | working | `tests/unit/test_model_stats_v2.py:69-75`；`tests/unit/test_runtime_persistence.py:30-34` | keep | 待定 |
| D10 Stats/Token/Usage | attempt 失败覆盖（实际只记 429 与服务端异常） | `4623109` `src/aistudio_api/application/api_service_gemini.py:205,281,288`；`6a6afbe` `src/aistudio_api/application/upstream_errors.py:247-392` | 原生 | partial | `src/aistudio_api/application/upstream_errors.py:186-245,290-360` 无 record 调用；完整分母未验证 | port（补齐或改名“部分错误”） | 待定：换号/鉴权失败是否计入 attempt |
| D10 Stats/Token/Usage | client 层总量（client_requests/success/errors/rate_limited/aborted） | `1adf3c2` `src/aistudio_api/api/state.py:73-77,266-278,456-470`；`src/aistudio_api/application/generation_service.py:470-503,810-852` | 新增 | partial | `tests/unit/test_model_stats_v2.py:31-81`；`tests/unit/test_stats_routes.py:39-60` | port（保留两层） | 待定：两层是否继续并排 |
| D10 Stats/Token/Usage | client × surface 分桶 | `1adf3c2` `src/aistudio_api/api/state.py:265-270`；`src/aistudio_api/application/generation_service.py:488-495,837-850` | 新增 | partial | 仅 `generation_service.py:497-502` 传 surface；失败/流式分支未传（`:488-495,837-848`） | port（修正全部调用点） | 待定：surface 是否要求全路径 |
| D10 Stats/Token/Usage | client 429 同时记 client_errors 与 client_rate_limited | `1adf3c2` `src/aistudio_api/api/state.py:273-275`；`tests/unit/test_model_stats_v2.py:52-67` | 新增 | working | `tests/unit/test_model_stats_v2.py:66-67` | keep 或 drop（二选一） | 待定：429 是否算客户端失败 |
| D10 Stats/Token/Usage | client aborted 归类（cancel/GeneratorExit/已出流异常） | `1adf3c2` `src/aistudio_api/application/generation_service.py:836-848`；`src/aistudio_api/api/state.py:271-272` | 新增 | unverified | 未验证（无 aborted 用例） | keep | 待定 |
| D10 Stats/Token/Usage | format 分桶（api_format） | `6a6afbe` `src/aistudio_api/api/state.py:140-146`；`1adf3c2` `src/aistudio_api/api/state.py:438-443` | 改造 | working | `tests/unit/test_model_stats_v2.py:76-78` | keep | 待定 |
| D10 Stats/Token/Usage | surface 分桶（attempt） | `1adf3c2` `src/aistudio_api/api/state.py:444-450`；`src/aistudio_api/application/generation_service.py:401-407,787-793` | 新增 | working | `tests/unit/test_model_stats_v2.py:79-80` | keep | 待定 |
| D10 Stats/Token/Usage | daily 聚合 + 90 天保留裁剪 | `1adf3c2` `src/aistudio_api/api/state.py:29,314,357-367,420-424` | 新增 | partial | `tests/unit/test_model_stats_v2.py:169-188` 仅 today；prune 无测试 | port（载入时也 prune） | 待定：保留期是否 90 天 |
| D10 Stats/Token/Usage | all-time 与 daily 分离（all 读 model_stats） | `1adf3c2` `src/aistudio_api/api/state.py:530-551` | 新增 | working | `tests/unit/test_stats_routes.py:50-65` | keep | 待定：all-time 是否仍需要 |
| D10 Stats/Token/Usage | schema v2 存取 + v1 合并 + 模型名归一 | `1adf3c2` `src/aistudio_api/api/state.py:28,34-40,150-213,321-355,369-391` | 改造 | working | `tests/unit/test_model_stats_v2.py:126-166`；`tests/unit/test_runtime_persistence.py:13-43` | keep | 待定 |
| D10 Stats/Token/Usage | 2s debounce / 5s 强制 / 失败立即落盘 | `4623109` `src/aistudio_api/api/state.py:93-118`；`1adf3c2` `src/aistudio_api/api/state.py:393-418,451-454,470` | 原生 | unverified | 未验证（无时序用例） | keep | 待定：2s/5s 是否保留 |
| D10 Stats/Token/Usage | 原子写 JSON | `4623109` `src/aistudio_api/infrastructure/utils/common.py:13-31`；`1adf3c2` `src/aistudio_api/api/state.py:386` | 原生 | unverified | 未验证（无并发/断电用例） | keep | 待定 |
| D10 Stats/Token/Usage | usage 主统计与 total 回退 | `4623109` `src/aistudio_api/api/state.py:120-151`；`1adf3c2` `src/aistudio_api/api/state.py:216-245` | 原生 | working | `tests/unit/test_model_stats_v2.py:31-75` | keep | 待定 |
| D10 Stats/Token/Usage | usage_missing 显式计数 | `1adf3c2` `src/aistudio_api/api/state.py:216-236`；`src/aistudio_api/api/response_models.py:88` | 新增 | partial | `tests/unit/test_model_stats_v2.py:115-123`；UI 仅类型未消费 `web/src/types/stats.ts:25` | port（页面展示） | 待定：是否暴露到页面 |
| D10 Stats/Token/Usage | reasoning_tokens 统计 | `1adf3c2` `src/aistudio_api/api/state.py:80,228-230,243`；`4623109` `src/aistudio_api/infrastructure/gateway/wire_parser.py:365-394` | 新增 | partial | `tests/unit/test_model_stats_v2.py:71`；parser 缺省写 0（`wire_parser.py:391-394`），0/null 不可分 | port（区分 0 与未知） | 待定 |
| D10 Stats/Token/Usage | cache_read_tokens + cache_hit_rate | `1adf3c2` `src/aistudio_api/api/state.py:81,96-100,225-227,244` | 新增 | working | `tests/unit/test_model_stats_v2.py:72-75`（0.3） | keep | 待定 |
| D10 Stats/Token/Usage | cache_write_tokens | `1adf3c2` `src/aistudio_api/api/state.py:82,231,245`；`4623109` `src/aistudio_api/infrastructure/gateway/wire_parser.py:381-395` 无该键 | 新增 | partial | 未验证真实上游；`web/src/components/stats/StatsOverview.vue:202` 自述无数据 | keep（预留）或 drop | 待定：保留空列还是删除 |
| D10 Stats/Token/Usage | countTokens 独立计数（count_requests/success/errors/input_tokens） | `af3f55c` `src/aistudio_api/api/state.py:167-177`（初版计入 requests）；`1adf3c2` `src/aistudio_api/api/state.py:84-87,280-295,472-502`；`af3f55c` `src/aistudio_api/application/token_counter.py` | 新增 | working | `tests/unit/test_model_stats_v2.py:83-112`；`tests/unit/test_token_counter.py:302-319` | port | 待定 |
| D10 Stats/Token/Usage | countTokens 不污染生成统计 | `1adf3c2` `src/aistudio_api/application/token_counter.py:274-306`；`src/aistudio_api/api/state.py:472-502` | 新增 | working | `tests/unit/test_model_stats_v2.py:99-112`；`tests/unit/test_token_counter.py:175-213` | keep | 待定 |
| D10 Stats/Token/Usage | range 解析（today/7d/30d/custom/all + 366 上限） | `1adf3c2` `src/aistudio_api/api/state.py:504-528` | 新增 | working | `tests/unit/test_model_stats_v2.py:169-188`（today/custom）；all/7d/30d 未验证 | keep | 待定 |
| D10 Stats/Token/Usage | partial / data_start 语义 | `1adf3c2` `src/aistudio_api/api/state.py:535-580`；`src/aistudio_api/api/routes_system.py:53-67` | 新增 | partial | `tests/unit/test_model_stats_v2.py:184-188`；`start` 返回请求值而非生效值（`state.py:572-580`） | port（统一 start/effective） | 待定 |
| D10 Stats/Token/Usage | timeseries（day 桶、缺日 null） | `1adf3c2` `src/aistudio_api/api/routes_system.py:183-219` | 新增 | working | `tests/unit/test_stats_routes.py:68-88` | keep | 待定 |
| D10 Stats/Token/Usage | paid 标记注入 /api/stats | `1adf3c2` `src/aistudio_api/api/routes_system.py:88-133`；`a10953d`/`1980ada` `src/aistudio_api/infrastructure/gateway/model_discovery.py:526-632` | 新增 | unverified | 未验证（无测试）；发现失败返回空 map（`routes_system.py:124-126`） | port（缓存/降级） | 待定：paid 是否必须实时发现 |
| D10 Stats/Token/Usage | StatsView 范围/自定义/10s 轮询/partial 提示 | `1adf3c2` `web/src/views/StatsView.vue`；`c7bdd80`；`2944c3c` | 新增 | partial | UI 无单测；`StatsView.vue:32-37,130-138,247-257`；rangeOptions 无 all | port（补 all） | 待定：UI 是否开放 all |
| D10 Stats/Token/Usage | StatsView attempt/client 并排展示 | `1adf3c2` `web/src/components/stats/StatsOverview.vue:46-108`；`ModelTokenTable.vue:106-109` | 新增 | partial | `StatsOverview.vue:55,103-107`；429 同时进 attempt 与 client 分母 | port（标注口径） | 待定：是否合并为单口径 |
| D10 Stats/Token/Usage | StatsView 历史字段缺失提示 | `1adf3c2` `web/src/views/StatsView.vue:51-61,252-257` | 新增 | partial | 仅 total_tokens>0 且 4 字段；未覆盖 cache_write/client_rate_limited/aborted/usage_missing | port（补字段） | 待定 |
| D10 Stats/Token/Usage | CountTokensPanel | `1adf3c2` `web/src/components/stats/CountTokensPanel.vue:1-87`；`web/src/views/StatsView.vue:286-289` | 新增 | working | `1adf3c2` 截图 `docs/dev/evidence/wp6-20261020/` | keep | 待定 |
| D10 Stats/Token/Usage | totals 聚合（跨模型，含 formats/surfaces） | `1adf3c2` `src/aistudio_api/api/routes_system.py:135-168`；`src/aistudio_api/api/state.py:102-147` | 新增 | working | `tests/unit/test_stats_routes.py:64-65` | keep | 待定 |
| D10 Stats/Token/Usage | Paid 徽标与 “free 使用 Paid 模型” 异常 | `1adf3c2` `web/src/components/stats/AccountHealthPanel.vue:64-80,247-249`；`web/src/views/StatsView.vue:43-49` | 新增 | unverified | 未验证（无 UI 用例） | keep | 待定：paid 徽标口径 |
| D10 Stats/Token/Usage | 成功率/错误率派生指标 | `1adf3c2` `src/aistudio_api/api/response_models.py:66-95`；`web/src/components/stats/StatsOverview.vue:67-108` | 待判定 | partial | 无 rate 字段；前端是否另算未验证 | port（补派生率）或 drop | 待定：是否需要 rate |

## 冲突/缺口

| # | 冲突/缺口 | 证据 | 影响 |
|---|---|---|---|
| 1 | attempt 与 client 两套口径混排在同一卡片 | `web/src/components/stats/StatsOverview.vue:46-108` | 两个分母并列；429 同时进 attempt.rate_limited 与 client_errors |
| 2 | client 的 surface 分桶只在非流式成功路径写入 | `src/aistudio_api/application/generation_service.py:488-495,497-502,837-850` | surfaces[*].client_requests 系统性偏低 |
| 3 | attempt 错误分母不全 | `src/aistudio_api/application/upstream_errors.py:186-245,290-360` | 鉴权/4xx/204/404/hang 不入 errors，页面“错误”偏低 |
| 4 | v1 历史把 countTokens 计入 requests | `af3f55c` `src/aistudio_api/api/state.py:167-177` | 已持久化数据无法与新 count_* 拆分 |
| 5 | range.start 是请求值，统计取生效值；all 的 start 取 daily 起点 | `src/aistudio_api/api/state.py:535-580` | 标签与数据不一致；90 天裁剪后 all-time 起点误导 |
| 6 | usage 字段语义不齐 | `src/aistudio_api/infrastructure/gateway/wire_parser.py:391-394`；`src/aistudio_api/api/state.py:231` | reasoning 缺省 0 而非 null；cache_write 恒 null |
| 7 | countTokens 失败路径不传 surface | `src/aistudio_api/application/token_counter.py:290-297` | surfaces 的 count_requests 只含成功 |
| 8 | paid 依赖模型发现结果 | `src/aistudio_api/api/routes_system.py:119-133` | 发现失败时 paid=null，UI 异常检测漏报 |

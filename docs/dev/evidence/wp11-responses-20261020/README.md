# WP11.1 OpenAI Responses 面真机证据（2026-10-20）

## 环境

- 仓库：`D:\LLM\self_programming\aistudio-api`
- 分支：`p1-ark-merge`
- 服务：`uv run python main.py server --port 8080`
- 账号：`acc_46223636`（pro）
- 模型：`gemini-3.8-flash`
- 功能开关：`features.formats.responses: true`

## 非流式

命令：

```powershell
$body = @{ model = "gemini-3.8-flash"; input = "Hello world"; max_output_tokens = 32 } | ConvertTo-Json -Depth 8
Invoke-WebRequest -UseBasicParsing -Method Post `
  -Uri "http://127.0.0.1:8080/v1/responses" `
  -ContentType "application/json" -Body $body
```

结果：`200`，约 2.0s

```json
{
  "object": "response",
  "status": "completed",
  "model": "gemini-3.8-flash",
  "output": [
    {
      "type": "message",
      "role": "assistant",
      "content": [{"type": "output_text", "text": "Hello!", "annotations": []}]
    }
  ],
  "usage": {
    "input_tokens": 3,
    "input_tokens_details": {"cached_tokens": 0},
    "output_tokens": 28,
    "output_tokens_details": {"reasoning_tokens": 26},
    "total_tokens": 31
  }
}
```

对照：同一 `Hello world` 的生成 `usageMetadata.promptTokenCount = 3`，Responses `input_tokens = 3` 一致。

## 流式

命令：

```powershell
$body = @{ model = "gemini-3.8-flash"; input = "Say hi in one word"; max_output_tokens = 16; stream = $true } | ConvertTo-Json -Depth 8
Invoke-WebRequest -UseBasicParsing -Method Post `
  -Uri "http://127.0.0.1:8080/v1/responses" `
  -ContentType "application/json" -Body $body
```

结果：`200`，约 5.2s

SSE 事件：

```text
event: response.created
event: response.output_item.added
event: response.reasoning_summary_text.delta
event: response.output_item.done
event: response.completed
```

`response.completed` usage：

```json
{
  "input_tokens": 6,
  "input_tokens_details": {"cached_tokens": 0},
  "output_tokens": 12,
  "output_tokens_details": {"reasoning_tokens": 12},
  "total_tokens": 18
}
```

## 说明

- 前两次请求失败是浏览器会话 stale：
  - `CDP error: Inspected target navigated or closed`
  - `PooledSession snapshot generation failed`
- 第三次在会话恢复后成功；不是 Responses 转换错误。
- 本证据只覆盖非流式与文本/推理流式；工具调用流式尚未单独真机验证。
- ATG per-format 缺口：`features.atg.mode_by_format.responses` 尚未迁移，Responses 当前继承全局 ATG 模式。
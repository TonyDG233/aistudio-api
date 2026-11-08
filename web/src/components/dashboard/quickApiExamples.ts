export type ApiProtocolKey = 'openai' | 'gemini' | 'claude' | 'responses'

export interface ProtocolSnippet {
  title: string
  code: string
}

export interface ProtocolExample {
  key: ApiProtocolKey
  label: string
  baseUrl: string
  endpoint: string
  endpointTitle: string
  snippets: ProtocolSnippet[]
}

export const PROTOCOL_LABELS: Record<ApiProtocolKey, string> = {
  openai: 'OpenAI 兼容',
  gemini: 'Gemini 原生',
  claude: 'Claude 兼容',
  responses: 'Responses 协议',
}

export const PROTOCOL_ORDER: ApiProtocolKey[] = [
  'openai',
  'gemini',
  'claude',
  'responses',
]

export function protocolEnabled(
  key: ApiProtocolKey,
  formats?: Record<string, boolean | undefined> | null,
): boolean {
  if (key === 'gemini') return true
  const value = formats?.[key]
  return typeof value === 'boolean' ? value : true
}

function trimOrigin(origin: string): string {
  return String(origin || '').replace(/\/+$/, '')
}

export function buildProtocolExamples(
  origin: string,
  model: string,
): ProtocolExample[] {
  const base = trimOrigin(origin)
  const modelId = model || 'gemini-3.7-flash'
  const openaiEndpoint = `${base}/v1/chat/completions`
  const geminiEndpoint = `${base}/v1beta/models/${modelId}:generateContent`
  const claudeEndpoint = `${base}/v1/messages`
  const responsesEndpoint = `${base}/v1/responses`

  return [
    {
      key: 'openai',
      label: PROTOCOL_LABELS.openai,
      baseUrl: `${base}/v1`,
      endpoint: openaiEndpoint,
      endpointTitle: 'OpenAI 兼容 完整接口端点',
      snippets: [
        {
          title: 'cURL',
          code: `curl ${openaiEndpoint} \\
  -H "Authorization: Bearer your-api-key" \\
  -H "Content-Type: application/json" \\
  -d '{
  "model": "${modelId}",
  "messages": [{"role": "user", "content": "Hello"}]
}'`,
        },
        {
          title: 'Python (OpenAI SDK)',
          code: `from openai import OpenAI

client = OpenAI(
    api_key="your-api-key",
    base_url="${base}/v1",
)

response = client.chat.completions.create(
    model="${modelId}",
    messages=[{"role": "user", "content": "Hello"}],
)
print(response.choices[0].message.content)`,
        },
      ],
    },
    {
      key: 'gemini',
      label: PROTOCOL_LABELS.gemini,
      baseUrl: base,
      endpoint: geminiEndpoint,
      endpointTitle: 'Gemini 原生 完整接口端点',
      snippets: [
        {
          title: 'cURL',
          code: `curl "${geminiEndpoint}" \\
  -H "x-goog-api-key: your-api-key" \\
  -H "Content-Type: application/json" \\
  -d '{
  "contents": [
    {"role": "user", "parts": [{"text": "Hello"}]}
  ]
}'`,
        },
        {
          title: 'Python (Google GenAI SDK)',
          code: `from google import genai

client = genai.Client(
    api_key="your-api-key",
    http_options={
        "api_version": "v1beta",
        "base_url": "${base}",
    },
)

response = client.models.generate_content(
    model="${modelId}",
    contents="Hello",
)
print(response.text)`,
        },
      ],
    },
    {
      key: 'claude',
      label: PROTOCOL_LABELS.claude,
      baseUrl: base,
      endpoint: claudeEndpoint,
      endpointTitle: 'Claude 兼容 完整接口端点',
      snippets: [
        {
          title: 'cURL',
          code: `curl ${claudeEndpoint} \\
  -H "x-api-key: your-api-key" \\
  -H "anthropic-version: 2023-06-01" \\
  -H "Content-Type: application/json" \\
  -d '{
  "model": "${modelId}",
  "max_tokens": 1024,
  "messages": [{"role": "user", "content": "Hello"}]
}'`,
        },
        {
          title: 'Python (Anthropic SDK)',
          code: `import anthropic

client = anthropic.Anthropic(
    api_key="your-api-key",
    base_url="${base}",
)

message = client.messages.create(
    model="${modelId}",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello"}],
)
print(message.content[0].text)`,
        },
      ],
    },
    {
      key: 'responses',
      label: PROTOCOL_LABELS.responses,
      baseUrl: `${base}/v1`,
      endpoint: responsesEndpoint,
      endpointTitle: 'Responses 协议 完整接口端点',
      snippets: [
        {
          title: 'cURL',
          code: `curl ${responsesEndpoint} \\
  -H "Authorization: Bearer your-api-key" \\
  -H "Content-Type: application/json" \\
  -d '{
  "model": "${modelId}",
  "input": "Hello"
}'`,
        },
        {
          title: 'Python (OpenAI SDK)',
          code: `from openai import OpenAI

client = OpenAI(
    api_key="your-api-key",
    base_url="${base}/v1",
)

response = client.responses.create(
    model="${modelId}",
    input="Hello",
)
print(response.output_text)`,
        },
      ],
    },
  ]
}

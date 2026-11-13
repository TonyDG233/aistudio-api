import { expect, test } from 'bun:test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

function source(relativePath: string): string {
  return readFileSync(
    fileURLToPath(new URL(relativePath, import.meta.url)),
    'utf8',
  )
}

test('ModelStatsTable keeps the count and adds the list/chart segmented slider', () => {
  const table = source('../src/components/dashboard/ModelStatsTable.vue')

  expect(table).toContain('个模型记录')
  expect(table).toContain("viewMode = ref<'table' | 'chart'>('table')")
  expect(table).toContain('📋 列表明细')
  expect(table).toContain('📈 趋势图表')
  expect(table).toContain('aria-pressed')
  expect(table).toContain('<ModelUsageChart v-else />')
})

test('ModelUsageChart wires ranges, model filter and loading/empty/error states', () => {
  const chart = source('../src/components/dashboard/ModelUsageChart.vue')

  for (const label of [
    '近 7 天',
    '近 30 天',
    '近 90 天',
    '全部模型 (汇总)',
    '客户端请求',
    '节点尝试',
    '输出(含思考)',
  ]) {
    expect(chart).toContain(label)
  }
  expect(chart).toContain('systemApi.getStats(days.value)')
  expect(chart).toContain('loading && !payload')
  expect(chart).toContain('error && !payload')
  expect(chart).toContain('该时间范围暂无调用记录')
})

test('charts measure card width instead of relying on a fixed 520px min-width', () => {
  const forbiddenFixedWidth = ['min-w-', '[520px]'].join('')
  for (const file of ['UsageRequestChart.vue', 'UsageTokenChart.vue']) {
    const chart = source(`../src/components/dashboard/${file}`)
    expect(chart).not.toContain(forbiddenFixedWidth)
    expect(chart).toContain("import { useChartWidth } from './useChartWidth.ts'")
    expect(chart).toContain('ref="container"')
    expect(chart).toContain(':text-anchor="labelAnchor(index)"')
    expect(chart).toContain(
      "if (index === props.points.length - 1) return 'end'",
    )
  }
})

test('Tokens chart stacks only cached/uncached/output and marks reasoning as included', () => {
  const tokenChart = source('../src/components/dashboard/UsageTokenChart.vue')
  const usage = source('../src/components/dashboard/modelUsage.ts')

  expect(tokenChart).toContain('stack.stackTotal')
  expect(tokenChart).not.toContain("key: 'reasoning', value")
  expect(tokenChart).toContain('思考 Tokens（已包含于输出）')
  expect(usage).toContain('stackTotal: cached + uncached + output')
})

test('N-2 protocol card describes Claude and Responses as enabled by default', () => {
  const card = source('../src/components/settings/ProtocolAtgCard.vue')

  expect(card).toContain('兼容 Claude 消息协议，默认开启')
  expect(card).toContain('启用 OpenAI Responses 协议，默认开启')
  expect(card).not.toContain('兼容 Claude 消息协议，默认关闭')
  expect(card).not.toContain('启用 OpenAI Responses 协议，默认关闭')
  expect(card).toContain("asBool(formats.value.claude, true)")
  expect(card).toContain("asBool(formats.value.responses, true)")
})

test('protocol card places ATG wrap_output, wrap_input and strict switches in responsive grid with tooltips', () => {
  const card = source('../src/components/settings/ProtocolAtgCard.vue')

  expect(card).toContain('<div class="grid grid-cols-1 gap-3 xl:grid-cols-3">')
  expect(card).toContain('输出侧伪装封装（工具输出）')
  expect(card).toContain('跟随协议策略 · 将模型回答装入合成工具')
  expect(card).toContain('asBool(atg.wrap_output, true)')
  expect(card).toContain("updateAtg('wrap_output', $event)")
  expect(card).toContain('schema-key="features.atg.wrap_output"')

  expect(card).toContain('输入侧伪装封装（图文双轨）')
  expect(card).toContain('跟随协议策略 · 文本装入工具，图片走原生')
  expect(card).toContain('asBool(atg.wrap_input, false)')
  expect(card).toContain("updateAtg('wrap_input', $event)")
  expect(card).toContain('schema-key="features.atg.wrap_input"')

  expect(card).toContain('严格模式')
  expect(card).toContain('对不完整的合成协议更早报错')
  expect(card).toContain('asBool(atg.strict, false)')
  expect(card).toContain("updateAtg('strict', $event)")
  expect(card).toContain('schema-key="features.atg.strict"')
})

test('protocol card renders full-width ATG prompt template textareas with reset buttons in advanced panel', () => {
  const card = source('../src/components/settings/ProtocolAtgCard.vue')

  expect(card).toContain('公开提示词模板配置')
  expect(card).toContain('md:col-span-2')
  expect(card).toContain('输出侧工具引导提示词模板')
  expect(card).toContain('schema-key="features.atg.control_template"')
  expect(card).toContain("asString(atg.control_template, DEFAULT_CONTROL_TEMPLATE)")
  expect(card).toContain("updateAtg('control_template', DEFAULT_CONTROL_TEMPLATE)")

  expect(card).toContain('输入侧上下文封装引导词')
  expect(card).toContain('schema-key="features.atg.input_context_instruction"')
  expect(card).toContain(
    'asString(atg.input_context_instruction, DEFAULT_INPUT_CONTEXT_INSTRUCTION)',
  )
  expect(card).toContain(
    "updateAtg('input_context_instruction', DEFAULT_INPUT_CONTEXT_INSTRUCTION)",
  )
  expect(card).toContain('恢复默认模板')
  expect(card).toContain('<textarea')
})

test('N-7 client tooltip separates 429 terminal failures, rescues, errors and aborts', () => {
  const chart = source('../src/components/dashboard/UsageRequestChart.vue')

  expect(chart).toContain('429 限流失败')
  expect(chart).toContain('重试/切号救援成功')
  expect(chart).toContain('最终失败')
  expect(chart).toContain('中断')
  expect(chart).toContain('tooltip.errors.toLocaleString()')
  expect(chart).toContain('tooltip.aborted.toLocaleString()')
  expect(chart).not.toContain('429 重试')
  expect(chart).not.toContain('text-gray-300">救援</span>')
})

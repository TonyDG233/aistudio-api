<script setup lang="ts">
import type { CircuitBreakerConfig, RoutingConfig } from './types.ts'
import ConfigInput from './ConfigInput.vue'
import ConfigSwitch from './ConfigSwitch.vue'
import FieldHelpTip from './FieldHelpTip.vue'
import { Activity, ShieldCheck } from 'lucide-vue-next'

const props = withDefaults(defineProps<{ routing?: RoutingConfig }>(), {
  routing: () => ({}),
})

const emit = defineEmits<{
  'update:routing': [val: RoutingConfig]
}>()

function update(key: keyof RoutingConfig, value: number | boolean) {
  emit('update:routing', { ...props.routing, [key]: value })
}

function updateBreaker(key: keyof CircuitBreakerConfig, value: number) {
  const breaker = { ...(props.routing.circuit_breaker || {}), [key]: value }
  emit('update:routing', { ...props.routing, circuit_breaker: breaker })
}

function toNumber(val: string | number | null, fallback: number): number {
  const n = Number(val)
  return Number.isFinite(n) ? n : fallback
}

function nonNegative(val: string | number | null, fallback: number): number {
  return Math.max(0, Math.round(toNumber(val, fallback)))
}

const breaker = () => props.routing.circuit_breaker || {}
</script>

<template>
  <div class="bg-white border border-gray-200/90 rounded-2xl shadow-xs overflow-hidden">
    <div class="px-5 py-3.5 bg-gray-50/60 border-b border-gray-100 flex items-center gap-2.5">
      <ShieldCheck class="w-4 h-4 text-brand-600" />
      <div>
        <h4 class="font-bold text-gray-900 text-xs flex items-center gap-1.5">
          <span>路由安全 (routing)</span>
          <FieldHelpTip schema-key="features.routing.circuit_breaker" />
        </h4>
        <p class="text-[11px] text-gray-400 mt-0.5">
          max_pages 是页面数上限，不是并发数；Context 与 Page 分开计数。耗尽一律显式报错，不无限重试。
        </p>
      </div>
    </div>

    <div class="p-5 space-y-4">
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div class="flex items-end gap-1.5">
          <div class="flex-1">
            <ConfigInput
              :model-value="routing.max_switches ?? 2"
              type="number"
              label="换账号/换实例上限"
              description="max_switches"
              placeholder="2"
              @update:model-value="update('max_switches', nonNegative($event, 2))"
            />
          </div>
          <FieldHelpTip
            schema-key="features.routing.max_switches"
            class="mb-2 shrink-0"
          />
        </div>

        <div class="flex items-end gap-1.5">
          <div class="flex-1">
            <ConfigInput
              :model-value="routing.global_max_pages ?? 2"
              type="number"
              label="全局页面硬顶"
              description="global_max_pages"
              placeholder="2"
              @update:model-value="update('global_max_pages', nonNegative($event, 2))"
            />
          </div>
          <FieldHelpTip
            schema-key="features.routing.global_max_pages"
            class="mb-2 shrink-0"
          />
        </div>

        <div class="flex items-end gap-1.5">
          <div class="flex-1">
            <ConfigInput
              :model-value="routing.max_contexts ?? 2"
              type="number"
              label="Context 上限"
              description="max_contexts"
              placeholder="2"
              @update:model-value="update('max_contexts', nonNegative($event, 2))"
            />
          </div>
          <FieldHelpTip
            schema-key="features.routing.max_contexts"
            class="mb-2 shrink-0"
          />
        </div>

        <div class="rounded-xl border border-gray-200/80 bg-gray-50/60 px-3.5 py-3 text-[11px] text-gray-400 leading-relaxed">
          页面 = 可复用的浏览器页面；Context = 独立会话隔离域。二者独立计数，互不替代。
        </div>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div class="flex items-center gap-1.5">
          <div class="flex-1">
            <ConfigSwitch
              :model-value="Boolean(routing.borrow)"
              label="临时借入就绪槽"
              description="仅首字节前；默认关闭"
              @update:model-value="update('borrow', $event)"
            />
          </div>
          <FieldHelpTip
            schema-key="features.routing.borrow"
            class="shrink-0"
          />
        </div>

        <div class="flex items-center gap-1.5">
          <div class="flex-1">
            <ConfigSwitch
              :model-value="Boolean(routing.prewarm)"
              label="备用实例预热"
              description="增加常驻内存成本；默认关闭"
              @update:model-value="update('prewarm', $event)"
            />
          </div>
          <FieldHelpTip
            schema-key="features.routing.prewarm"
            class="shrink-0"
          />
        </div>
      </div>

      <!-- Circuit breaker -->
      <div class="p-4 bg-gray-50/80 border border-gray-200/80 rounded-xl space-y-3">
        <div class="flex items-center gap-2 text-xs font-bold text-gray-800">
          <Activity class="w-4 h-4 text-rose-500" />
          <span>路由熔断</span>
          <FieldHelpTip schema-key="features.routing.circuit_breaker" />
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <ConfigInput
            :model-value="breaker().window_seconds ?? 600"
            type="number"
            label="统计窗口（秒）"
            description="window_seconds"
            placeholder="600"
            @update:model-value="updateBreaker('window_seconds', nonNegative($event, 600))"
          />
          <ConfigInput
            :model-value="breaker().failure_threshold ?? 5"
            type="number"
            label="失败阈值"
            description="failure_threshold"
            placeholder="5"
            @update:model-value="updateBreaker('failure_threshold', nonNegative($event, 5))"
          />
          <ConfigInput
            :model-value="breaker().cooldown_seconds ?? 300"
            type="number"
            label="冷却时长（秒）"
            description="cooldown_seconds"
            placeholder="300"
            @update:model-value="updateBreaker('cooldown_seconds', nonNegative($event, 300))"
          />
        </div>
        <p class="text-[11px] text-gray-400">
          滚动窗口内失败切换达到阈值 → 全局冷却；冷却期间请求显式 503，不会继续放大失败。
        </p>
      </div>
    </div>
  </div>
</template>
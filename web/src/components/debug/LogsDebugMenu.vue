<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ChevronDown, ChevronRight } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import { systemApi } from '@/api/system.ts'
import {
  clearDebugLogs,
  injectDebugLogs,
  type DebugLogKind,
} from '@/api/logs.ts'
import { useToastStore } from '@/stores/toast.ts'

interface InjectAction {
  kind: DebugLogKind
  label: string
  success: string
}

const toast = useToastStore()
const uiDebug = ref(false)
const expanded = ref(true)
const busy = ref<DebugLogKind | 'clear' | null>(null)

const injectActions: InjectAction[] = [
  {
    kind: 'all_levels',
    label: '💉 注入全级别样例日志',
    success: '已注入 DEBUG/INFO/WARNING/ERROR/CRITICAL 样例日志',
  },
  {
    kind: 'sanitize_test',
    label: '🔒 注入密钥脱敏测试日志',
    success: '已注入密钥脱敏测试日志，凭据已替换为 ***',
  },
  {
    kind: 'long_payload',
    label: '📏 注入超长报文测试日志',
    success: '已注入约 6KB 超长报文测试日志',
  },
]

async function checkUiDebug(): Promise<void> {
  try {
    const status = await systemApi.getStatus()
    uiDebug.value = status.ui_debug === true
  } catch {
    uiDebug.value = false
  }
}

async function runInject(kind: DebugLogKind): Promise<void> {
  if (busy.value !== null) return
  busy.value = kind
  try {
    await injectDebugLogs(kind)
    const action = injectActions.find((item) => item.kind === kind)
    toast.success(action?.success ?? '样例日志已注入')
  } catch (err: unknown) {
    toast.error(err instanceof Error ? err.message : '注入样例日志失败')
  } finally {
    busy.value = null
  }
}

async function runClear(): Promise<void> {
  if (busy.value !== null) return
  busy.value = 'clear'
  try {
    const result = await clearDebugLogs()
    toast.success(`已清空沙盒日志 ${result.cleared} 条；正在刷新日志视图`)
    window.setTimeout(() => window.location.reload(), 400)
  } catch (err: unknown) {
    toast.error(err instanceof Error ? err.message : '清空沙盒日志失败')
    busy.value = null
  }
}

onMounted(() => {
  void checkUiDebug()
})
</script>

<template>
  <div
    v-if="uiDebug"
    class="rounded-2xl border border-amber-200 bg-amber-50/70 p-3 shadow-xs"
  >
    <button
      type="button"
      class="flex w-full items-center justify-between gap-2 text-left cursor-pointer"
      @click="expanded = !expanded"
    >
      <span class="flex flex-wrap items-center gap-1.5 text-xs font-semibold text-amber-900">
        <span>🛠️ UI Debug 菜单 (日志沙盒调试)</span>
        <span
          v-if="busy !== null"
          class="rounded border border-amber-200 bg-amber-100 px-1.5 py-0.5 font-mono text-[10px] font-normal text-amber-800"
        >
          处理中…
        </span>
      </span>
      <ChevronDown
        v-if="expanded"
        class="h-4 w-4 shrink-0 text-amber-500"
      />
      <ChevronRight
        v-else
        class="h-4 w-4 shrink-0 text-amber-500"
      />
    </button>

    <div
      v-if="expanded"
      class="mt-2 space-y-2 border-t border-amber-200/70 pt-2"
    >
      <p class="text-[11px] leading-relaxed text-amber-800">
        仅沙盒模式可用；注入会经过真实日志管线，SSE 与按时间导出会同步出现样例记录。
      </p>

      <div class="flex flex-wrap items-center gap-2">
        <Button
          v-for="action in injectActions"
          :key="action.kind"
          size="sm"
          variant="secondary"
          :loading="busy === action.kind"
          :disabled="busy !== null && busy !== action.kind"
          @click="runInject(action.kind)"
        >
          {{ action.label }}
        </Button>
        <Button
          size="sm"
          variant="danger"
          :loading="busy === 'clear'"
          :disabled="busy !== null && busy !== 'clear'"
          @click="runClear"
        >
          🗑️ 清空沙盒日志
        </Button>
      </div>
    </div>
  </div>
</template>

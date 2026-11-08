import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'

export function useChartWidth(
  fallback = 720,
  minimum = 280,
): { container: Ref<HTMLElement | null>; width: Ref<number> } {
  const container = ref<HTMLElement | null>(null)
  const width = ref(fallback)
  let observer: ResizeObserver | null = null

  onMounted(() => {
    const element = container.value
    if (!element) return
    const update = () => {
      const measured = element.clientWidth
      if (measured > 0) width.value = Math.max(minimum, measured)
    }
    update()
    if (typeof ResizeObserver === 'undefined') return
    observer = new ResizeObserver(update)
    observer.observe(element)
  })

  onBeforeUnmount(() => {
    observer?.disconnect()
    observer = null
  })

  return { container, width }
}

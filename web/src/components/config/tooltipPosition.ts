export interface TooltipRect {
  left: number
  top: number
  width: number
  height: number
}

export interface TooltipSize {
  width: number
  height: number
}

export interface ViewportSize {
  width: number
  height: number
}

export type TooltipPlacement = 'top' | 'bottom'

export interface TooltipPosition {
  left: number
  top: number
  placement: TooltipPlacement
  arrowLeft: number
}

export const TOOLTIP_VIEWPORT_MARGIN = 8
export const TOOLTIP_GAP = 8
export const TOOLTIP_ARROW_INSET = 12

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

/**
 * Clamp a teleported tooltip inside the viewport and keep its arrow aimed at
 * the trigger centre. All coordinates are fixed-position pixels.
 */
export function clampTooltipPosition(options: {
  trigger: TooltipRect
  tooltip: TooltipSize
  viewport: ViewportSize
  gap?: number
  margin?: number
}): TooltipPosition {
  const margin = options.margin ?? TOOLTIP_VIEWPORT_MARGIN
  const gap = options.gap ?? TOOLTIP_GAP
  const viewportWidth = Math.max(0, options.viewport.width)
  const viewportHeight = Math.max(0, options.viewport.height)
  const availableWidth = Math.max(0, viewportWidth - margin * 2)
  const width = Math.min(Math.max(0, options.tooltip.width), availableWidth)
  const height = Math.max(0, options.tooltip.height)
  const triggerCenter = options.trigger.left + options.trigger.width / 2

  const maxLeft = Math.max(margin, viewportWidth - width - margin)
  const left = clamp(triggerCenter - width / 2, margin, maxLeft)

  const above = options.trigger.top - height - gap
  const below = options.trigger.top + options.trigger.height + gap
  const fitsAbove = above >= margin
  const fitsBelow = below + height <= viewportHeight - margin

  let placement: TooltipPlacement
  let top: number
  if (fitsAbove) {
    placement = 'top'
    top = above
  } else if (fitsBelow) {
    placement = 'bottom'
    top = below
  } else {
    const spaceAbove = options.trigger.top
    const spaceBelow = viewportHeight - (options.trigger.top + options.trigger.height)
    placement = spaceAbove >= spaceBelow ? 'top' : 'bottom'
    const desired = placement === 'top' ? above : below
    top = clamp(
      desired,
      margin,
      Math.max(margin, viewportHeight - height - margin),
    )
  }

  const arrowLeft = clamp(
    triggerCenter - left,
    Math.min(TOOLTIP_ARROW_INSET, width / 2),
    Math.max(Math.min(TOOLTIP_ARROW_INSET, width / 2), width - TOOLTIP_ARROW_INSET),
  )

  return { left, top, placement, arrowLeft }
}

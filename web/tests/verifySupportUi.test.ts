import { describe, expect, test } from 'bun:test'
import {
  resolveVerifySupport,
  scanFlowButtonState,
  shouldOpenScanFlow,
} from '../src/utils/verifySupport.ts'

describe('verify capability resolution', () => {
  test('missing capability keeps the legacy supported behaviour', () => {
    expect(resolveVerifySupport(undefined)).toEqual({ supported: true, reason: null })
    expect(resolveVerifySupport(null)).toEqual({ supported: true, reason: null })
    expect(resolveVerifySupport({})).toEqual({ supported: true, reason: null })
  })

  test('unsupported desktop-less hosts disable the QR flow', () => {
    expect(resolveVerifySupport({ supported: false, reason: 'no-desktop' })).toEqual({
      supported: false,
      reason: 'no-desktop',
    })
    expect(resolveVerifySupport({ supported: false })).toEqual({
      supported: false,
      reason: null,
    })
  })

  test('unsupported hosts never auto-open a verify/start session', () => {
    expect(shouldOpenScanFlow(false)).toBe(false)
    expect(shouldOpenScanFlow(true)).toBe(true)
  })
})

describe('scan flow button state', () => {
  test('unsupported mode disables refresh and status check with a hint title', () => {
    const state = scanFlowButtonState(false, 'idle', false, false)
    expect(state.refreshDisabled).toBe(true)
    expect(state.refreshTitle).toBe('服务所在环境不支持扫码')
    expect(state.checkDisabled).toBe(true)
    expect(state.primaryLabel).toBe('直接验证账号')
  })

  test('supported mode keeps the existing QR button semantics', () => {
    const pending = scanFlowButtonState(true, 'pending', false, false)
    expect(pending.refreshDisabled).toBe(false)
    expect(pending.refreshTitle).toBe('')
    expect(pending.checkDisabled).toBe(false)
    expect(pending.primaryLabel).toBe('查询账号状态')

    const idle = scanFlowButtonState(true, 'idle', false, false)
    expect(idle.checkDisabled).toBe(true)
    expect(scanFlowButtonState(true, 'pending', true, false).refreshDisabled).toBe(true)
  })
})


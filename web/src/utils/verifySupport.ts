export interface VerifySupportInfo {
  supported: boolean
  reason: string | null
}

export interface ScanFlowButtonState {
  refreshDisabled: boolean
  refreshTitle: string
  checkDisabled: boolean
  checkTitle: string
  primaryLabel: string
}

/**
 * Resolve the backend `status.verify` payload into a modal capability.
 * Missing or malformed capability data keeps the historical behaviour (true)
 * so an unreachable status endpoint never disables the QR flow.
 */
export function resolveVerifySupport(
  raw?: { supported?: boolean; reason?: string | null } | null,
): VerifySupportInfo {
  if (!raw || typeof raw.supported !== 'boolean') {
    return { supported: true, reason: null }
  }
  return { supported: raw.supported, reason: raw.reason ?? null }
}

/** Unsupported hosts must never auto-open a QR session (no verify/start). */
export function shouldOpenScanFlow(supported: boolean): boolean {
  return supported !== false
}

export function scanFlowButtonState(
  supported: boolean,
  phase: string,
  busy: boolean,
  checking: boolean,
): ScanFlowButtonState {
  if (!supported) {
    return {
      refreshDisabled: true,
      refreshTitle: '服务所在环境不支持扫码',
      checkDisabled: true,
      checkTitle: '服务所在环境不支持扫码',
      primaryLabel: '直接验证账号',
    }
  }
  return {
    refreshDisabled: busy,
    refreshTitle: '',
    checkDisabled: phase !== 'pending' || checking || busy,
    checkTitle: '仅检查当前扫码会话的验证结果，需要二维码会话已创建',
    primaryLabel: '查询账号状态',
  }
}


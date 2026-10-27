// 把请求错误转成给人看的一句话，并弹出提示
import { ElMessage } from 'element-plus'

import { i18n } from '@/i18n'

import { ApiError } from './http'

export function errorText(err: unknown): string {
  const t = i18n.global.t
  if (err instanceof ApiError) {
    if (err.code === 'network') return t('error.network')
    // 后端的业务错误带说明（目前是中文）；以后可按 err.code 翻译
    return err.message || t('error.unknown', { status: err.status })
  }
  return err instanceof Error ? err.message : String(err)
}

export function showError(err: unknown): void {
  ElMessage.error({ message: errorText(err), duration: 5000, showClose: true })
}

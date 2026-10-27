// 系统状态：文件存储能不能用、备份是否正常（备份的细节只给系统管理员，普通用户是 null）
import { api } from './http'

export interface SystemStatus {
  version: string
  storage: { ok: boolean; error: string | null }
  backup: {
    configured: boolean
    ok: boolean
    last_ok: string | null
    error: string | null
    stale: boolean
  } | null
}

export const systemApi = {
  status: () => api.get<SystemStatus>('/api/system/status'),
}

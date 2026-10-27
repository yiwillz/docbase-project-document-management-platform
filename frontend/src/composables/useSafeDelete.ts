// 删除配置类数据（设备、设备种类、模块……）：没用过的真删除；用过的说明原因，并提供"改为停用"
// 规则在后端 services/usage.py：挂了文件、进过传送单 / 基线、或发 MDL 时已经存在的，只能停用
import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'

import { showError } from '@/api/errors'

export interface Usage {
  deletable: boolean
  usage: Record<string, number>
}

interface Options {
  name: string // 显示用：如"72 Mixer"
  check: () => Promise<Usage>
  remove: () => Promise<unknown>
  deactivate?: () => Promise<unknown> // 没有就只提示原因
  done?: () => unknown // 删除 / 停用后刷新页面
}

export function useSafeDelete() {
  const { t } = useI18n()

  /** 用过的原因写成一句话，如"文件 3 份、传送单 1 张"。 */
  function reasons(usage: Record<string, number>): string {
    return Object.entries(usage)
      .map(([key, n]) => t(`usage.reason.${key}`, { n }))
      .join(t('common.listSep'))
  }

  async function confirm(message: string, title: string, ok: string, type: 'warning' | 'info'): Promise<boolean> {
    try {
      await ElMessageBox.confirm(message, title, { confirmButtonText: ok, cancelButtonText: t('common.cancel'), type })
      return true
    } catch {
      return false
    }
  }

  async function safeDelete(o: Options): Promise<void> {
    try {
      const u = await o.check()
      if (u.deletable) {
        if (!(await confirm(t('usage.confirmDelete', { name: o.name }), t('common.delete'), t('common.delete'), 'warning'))) return
        await o.remove()
        ElMessage.success(t('common.deleted'))
      } else if (o.deactivate) {
        const message = t('usage.usedDeactivate', { name: o.name, reasons: reasons(u.usage) })
        if (!(await confirm(message, t('usage.cannotDelete'), t('usage.deactivateInstead'), 'info'))) return
        await o.deactivate()
        ElMessage.success(t('usage.deactivated'))
      } else {
        await ElMessageBox.alert(t('usage.usedNoDelete', { name: o.name, reasons: reasons(u.usage) }), t('usage.cannotDelete'))
        return
      }
      await o.done?.()
    } catch (err) {
      showError(err)
    }
  }

  return { safeDelete, reasons }
}

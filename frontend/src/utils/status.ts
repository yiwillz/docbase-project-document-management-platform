// 状态的显示：颜色和导出的 MDL / 传送单一致（已批准绿、审核中蓝、需修改重交橙、未提交灰、逾期红）
import type { Status } from '@/api/settings'

export const STATUSES: Status[] = [
  'not_submitted',
  'submitted',
  'under_review',
  'approved',
  'revise_resubmit',
  'not_applicable',
]

// 还没完成的状态（各方待办里数的）
export const OPEN_STATUSES: Status[] = ['not_submitted', 'submitted', 'under_review', 'revise_resubmit']

export const COLORS: Record<Status | 'overdue', { bg: string; fg: string }> = {
  approved: { bg: '#C6EFCE', fg: '#006100' },
  under_review: { bg: '#DDEBF7', fg: '#1F4E79' },
  submitted: { bg: '#E2EFDA', fg: '#375623' },
  revise_resubmit: { bg: '#FCE4D6', fg: '#9C4400' },
  not_submitted: { bg: '#EDEDED', fg: '#404040' },
  not_applicable: { bg: '#FFFFFF', fg: '#7F7F7F' },
  overdue: { bg: '#FFC7CE', fg: '#9C0006' },
}

// 逾期（未提交且过了截止日）优先显示成逾期
export function displayKey(status: Status, overdue: boolean): Status | 'overdue' {
  return overdue ? 'overdue' : status
}

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${Math.round(value * 1000) / 10}%`
}

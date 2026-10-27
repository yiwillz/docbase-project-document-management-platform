// 英文目前要求和中文一样全（缺的会显示成中文，第 9 步就出现过）
import { describe, expect, it } from 'vitest'

import en from '@/i18n/en'
import zhCN from '@/i18n/zh-CN'

type Tree = { [key: string]: string | Tree }
const keys = (tree: Tree, prefix = ''): string[] =>
  Object.entries(tree).flatMap(([k, v]) => (typeof v === 'string' ? [prefix + k] : keys(v, `${prefix}${k}.`)))

describe('english completeness', () => {
  it('has every chinese key', () => {
    const enKeys = new Set(keys(en as Tree))
    expect(keys(zhCN as Tree).filter((k) => !enKeys.has(k))).toEqual([])
  })
})

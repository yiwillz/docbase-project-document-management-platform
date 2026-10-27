// 语言文件：英文的键都必须是中文里有的（防止写错键）；中文不能有空值
import { describe, expect, it } from 'vitest'

import en from '@/i18n/en'
import zhCN from '@/i18n/zh-CN'

type Tree = { [key: string]: string | Tree }

function flatten(tree: Tree, prefix = ''): Record<string, string> {
  return Object.entries(tree).reduce<Record<string, string>>((out, [k, v]) => {
    const key = prefix ? `${prefix}.${k}` : k
    return typeof v === 'string' ? { ...out, [key]: v } : { ...out, ...flatten(v, key) }
  }, {})
}

describe('language files', () => {
  const zh = flatten(zhCN as Tree)
  const enFlat = flatten(en as Tree)

  it('english keys all exist in chinese', () => {
    expect(Object.keys(enFlat).filter((k) => !(k in zh))).toEqual([])
  })

  it('chinese has no empty texts', () => {
    expect(Object.entries(zh).filter(([, v]) => !v.trim())).toEqual([])
  })

  it('placeholders match between languages', () => {
    const names = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
    const mismatched = Object.keys(enFlat).filter((k) => names(enFlat[k]).join() !== names(zh[k]).join())
    expect(mismatched).toEqual([])
  })
})

// 从 Excel 粘贴的文字解析
import { describe, expect, it } from 'vitest'

import { mapRows, parseTsv } from '@/utils/paste'

describe('parseTsv', () => {
  it('splits rows and columns, trims and drops empty rows', () => {
    expect(parseTsv('a\tb\r\n c \td\n\n')).toEqual([
      ['a', 'b'],
      ['c', 'd'],
    ])
  })

  it('handles quoted cells with newlines, tabs and doubled quotes', () => {
    expect(parseTsv('"line1\nline2"\tx\n"say ""hi"""\t"a\tb"')).toEqual([
      ['line1\nline2', 'x'],
      ['say "hi"', 'a\tb'],
    ])
  })
})

describe('mapRows', () => {
  const columns = [
    { key: 'tag', headers: ['设备编号', 'Tag'] },
    { key: 'name', headers: ['设备名', 'Name'] },
    { key: 'quantity', headers: ['台数', 'Qty'] },
  ]

  it('uses default column order when there is no header', () => {
    expect(mapRows([['72', 'Mixer', '2']], columns)).toEqual({
      header: false,
      records: [{ tag: '72', name: 'Mixer', quantity: '2' }],
    })
  })

  it('follows header order and leaves missing columns empty', () => {
    const { header, records } = mapRows(
      [
        ['Name', 'Qty'],
        ['Beta part', '3000'],
      ],
      columns,
    )
    expect(header).toBe(true)
    expect(records).toEqual([{ tag: null, name: 'Beta part', quantity: '3000' }])
  })
})

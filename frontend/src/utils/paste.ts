// 解析从 Excel 复制来的文字：行用换行分隔，列用 Tab 分隔；
// Excel 会把含换行、Tab 或引号的格子用双引号包起来（里面的引号写成两个）。
export function parseTsv(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let cell = ''
  let quoted = false
  let atCellStart = true
  const src = text.replace(/\r\n?/g, '\n')
  for (let i = 0; i < src.length; i++) {
    const ch = src[i]
    if (quoted) {
      if (ch === '"' && src[i + 1] === '"') {
        cell += '"'
        i++
      } else if (ch === '"') {
        quoted = false
      } else {
        cell += ch
      }
      continue
    }
    if (ch === '"' && atCellStart) {
      quoted = true
      atCellStart = false
    } else if (ch === '\t') {
      row.push(cell)
      cell = ''
      atCellStart = true
    } else if (ch === '\n') {
      row.push(cell)
      rows.push(row)
      row = []
      cell = ''
      atCellStart = true
    } else {
      cell += ch
      atCellStart = false
    }
  }
  if (cell !== '' || row.length) {
    row.push(cell)
    rows.push(row)
  }
  // 去掉整行都是空的（Excel 复制时末尾常带一个换行）
  return rows.map((r) => r.map((c) => c.trim())).filter((r) => r.some((c) => c !== ''))
}

export interface PasteColumn {
  key: string
  // 表头可能的写法（中英文），用来判断第一行是不是表头、以及列的顺序
  headers: string[]
}

// 第一行像表头时，按表头决定列顺序；否则按 columns 的默认顺序
export function mapRows(
  rows: string[][],
  columns: PasteColumn[],
): { header: boolean; records: Record<string, string | null>[] } {
  if (!rows.length) return { header: false, records: [] }
  const norm = (s: string) => s.replace(/\s+/g, '').toLowerCase()
  const first = rows[0].map(norm)
  const positions = columns.map((c) => first.findIndex((h) => c.headers.map(norm).includes(h)))
  const header = positions.some((p) => p >= 0)
  const order = header ? positions : columns.map((_, i) => i)
  const body = header ? rows.slice(1) : rows
  const records = body.map((r) =>
    Object.fromEntries(columns.map((c, i) => [c.key, order[i] >= 0 && r[order[i]] ? r[order[i]] : null])),
  )
  return { header, records }
}

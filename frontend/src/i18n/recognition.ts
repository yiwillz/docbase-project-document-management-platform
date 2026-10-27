// 识别用词表：从 Excel 粘贴时，用来认表头和取值的各种写法（中英文都认）。
// 这些不是界面文字，不随界面语言切换，所以单独放在这里。

// 文件种类粘贴：表头写法
export const DOC_TYPE_HEADERS = {
  name: ['名称', '文件名称', 'name', 'document', 'title'],
  category: ['分类', 'category'],
  origin: ['要求类别', 'requirement', 'origin'],
  per_equipment: ['按设备', 'perequipment', 'machine-specific'],
  due_date: ['截止日期', 'due', 'duedate'],
  basis: ['依据', 'basis', 'reference'],
}

// 设备粘贴：表头写法（和后端设备清单导入认的一致）
export const EQUIPMENT_HEADERS = {
  tag: ['设备编号', '编号', '位号', 'tag', 'tagno', 'no'],
  name: ['设备名', '设备名称', '名称', 'name', 'equipment'],
  equipment_type: ['设备种类', '类别', '种类', 'type', 'category'],
  vendor: ['供应商', '厂家', 'vendor', 'supplier'],
  quantity: ['台数', '数量', 'qty', 'quantity'],
}

// 要求类别的写法
export const ORIGIN_WORDS: Record<string, 'contract' | 'client' | 'internal'> = {
  合同规定: 'contract',
  合同: 'contract',
  contract: 'contract',
  客户要求: 'client',
  客户: 'client',
  client: 'client',
  内部要求: 'internal',
  内部: 'internal',
  internal: 'internal',
}

// "是"的写法（其余都当"否"）
export const YES_WORDS = ['是', 'y', 'yes', 'true', '1', '√']

// 筛选条件和地址栏互相转换
import { describe, expect, it } from 'vitest'

import { filtersFromQuery, filtersToQuery } from '@/utils/filterQuery'

describe('filter query', () => {
  it('round-trips lists, numbers, flags and keyword', () => {
    const filters = {
      milestone_id: [3, 5],
      status: ['not_submitted' as const],
      vendor: ['Vendor A'],
      overdue: true,
      in_client_mdl: false,
      q: 'pump',
    }
    const query = filtersToQuery(filters)
    expect(query).toEqual({
      milestone_id: ['3', '5'],
      status: ['not_submitted'],
      vendor: ['Vendor A'],
      overdue: 'true',
      in_client_mdl: 'false',
      q: 'pump',
    })
    expect(filtersFromQuery(query as never)).toEqual(filters)
  })

  it('accepts single values and ignores junk', () => {
    expect(filtersFromQuery({ milestone_id: '7', module_id: 'x', overdue: 'maybe', status: 'approved' })).toEqual({
      milestone_id: [7],
      status: ['approved'],
    })
  })

  it('drops empty values', () => {
    expect(filtersToQuery({ milestone_id: [], q: '', overdue: undefined })).toEqual({})
  })
})

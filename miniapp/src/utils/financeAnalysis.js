export function financeCategoryAnalysis(data) {
  return ['expense', 'income'].map(kind => {
    const rows = (data?.budgets || [])
      .filter(row => row.kind === kind && Number(row.spent) > 0)
    const commission = Number(data?.summary.commission || 0)
    if (kind === 'income' && commission > 0) {
      rows.push({ id: 'commission', name: '客人提成（自动入账）', spent: commission })
    }
    rows.sort((left, right) => Number(right.spent) - Number(left.spent))
    return {
      kind,
      title: kind === 'expense' ? '支出分类' : '收入分类',
      rows,
      total: rows.reduce((sum, row) => sum + Number(row.spent), 0),
    }
  })
}

export function movieSummary(data, category, query) {
  const shown = data.series.length
  if (query) return `当前展示 ${shown} 部`
  const total = category ? data.categories.find(item => item.name === category)?.count : data.total
  return total == null ? `当前展示 ${shown} 部` : `共 ${total} 部 · 当前展示 ${shown} 部`
}

export function musicSummary(total, page, pageSize) {
  if (!total) return '共 0 首'
  const start = (page - 1) * pageSize + 1
  return `共 ${total} 首 · 本页 ${start}–${Math.min(page * pageSize, total)} 首`
}

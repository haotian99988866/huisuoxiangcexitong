export function movieSummary(data, category, query) {
  const shown = data.series.length
  if (data.matching_total != null) return `共 ${data.matching_total} 部 · 已展示 ${shown} 部`
  if (query) return `当前展示 ${shown} 部`
  const total = category ? data.categories.find(item => item.name === category)?.count : data.total
  return total == null ? `当前展示 ${shown} 部` : `共 ${total} 部 · 当前展示 ${shown} 部`
}

export function musicSummary(total, visible) {
  if (!total) return '共 0 首'
  return `共 ${total} 首 · 已展示 ${Math.min(visible, total)} 首`
}

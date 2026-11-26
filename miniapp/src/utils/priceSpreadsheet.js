export const priceAmount = value => {
  if (!/^\d{1,10}(?:\.\d{1,2})?$/.test(value)) return null
  const cents = Math.round(Number(value) * 100)
  return Number.isSafeInteger(cents) ? cents : null
}

const headers = ['项目', '价格（美元）', '会所编号', '项目编号']

export function priceSheetRows(items) {
  return [
    headers,
    ...items.map(item => [item.service_name, item.price == null ? null : Number(item.price),
      item.venue_id, item.service_key]),
  ]
}

export function importedPrices(rows, items, venue) {
  if (!Array.isArray(rows) || !Array.isArray(rows[0]) ||
      headers.some((header, index) => rows[0][index] !== header)) {
    throw new Error('表格格式不正确，请使用本会所导出的 Excel 表格')
  }
  const byKey = new Map(items.filter(item => String(item.venue_id) === venue)
    .map(item => [item.service_key, item]))
  const seen = new Set()
  const prices = {}
  for (const [index, row] of rows.slice(1).entries()) {
    if (!row || row.every(value => value == null || value === '')) continue
    const [name, rawPrice, venueId, key] = row
    const item = byKey.get(key)
    if (String(venueId) !== venue || !item || item.service_name !== name || seen.has(key)) {
      throw new Error(`第 ${index + 2} 行的会所或项目不匹配，请勿修改项目及编号`)
    }
    seen.add(key)
    if (rawPrice == null || (typeof rawPrice === 'string' && !rawPrice.trim())) continue
    if (typeof rawPrice !== 'string' && typeof rawPrice !== 'number') {
      throw new Error(`第 ${index + 2} 行价格无效`)
    }
    const price = String(rawPrice).trim()
    if (priceAmount(price) === null || priceAmount(price) <= 0) {
      throw new Error(`第 ${index + 2} 行价格须大于零且最多两位小数`)
    }
    prices[`${venue}:${key}`] = price
  }
  if (!Object.keys(prices).length) throw new Error('表格中没有可导入的价格')
  return prices
}

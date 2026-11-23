export const listingText = value => (value || '').replace(/#\s*/g, '').trim()

export function listingValue(item, ...labels) {
  const attributes = item.attributes || []
  for (const label of labels) {
    const values = [...new Set(attributes.filter(field => field.label === label)
      .map(field => listingText(field.value)).filter(Boolean))]
    if (values.length) return values.length === 1 ? values[0] : ''
  }
  return ''
}

export function listingInfo(item, vehicles = false) {
  const priceLabels = vehicles ? ['出租', '租金', '价格'] : ['租金', '售价', '价格']
  const price = listingValue(item, ...priceLabels)
  const priceLabel = price ? priceLabels.find(label => listingValue(item, label) === price) : ''
  const title = listingValue(item, vehicles ? '车型' : '户型') || item.title
  const location = vehicles ? '' : listingValue(item, '位置', '地址')
  const features = (vehicles ? ['年款', '年份', '年龄', '里程', '公里数', '变速箱', '排量', '燃料']
    : ['面积', '押金', '租期']).flatMap(label => {
    const value = listingValue(item, label)
    if (!value) return []
    const displayLabel = vehicles && label === '年龄' && /^(?:19|20)\d{2}年?$/.test(value) ? '年份' : label
    return [{ label: displayLabel, value }]
  })
  return { title: listingText(title), location, price, priceLabel, features }
}

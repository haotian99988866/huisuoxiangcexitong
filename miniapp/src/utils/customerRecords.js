export const consumptionDate = value => value
  ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Phnom_Penh', hour12: false }) : '暂无'

export function consumptionProjects(items, venueId, service) {
  const options = items.filter(item => item.venue_id === Number(venueId))
  return { options, selected: options.find(item => item.service_name === service) }
}

export function consumptionCommission(items, record, prior) {
  const venueId = Number(record.venue_id)
  if (prior && prior.service === record.service && prior.venue_id === venueId && prior.commission_service_key) {
    return prior.commission_base_amount
  }
  if (record.revision) return null
  const { options, selected } = consumptionProjects(items, venueId, record.service)
  const fallback = record.service_kind === 'delivery'
    ? options.find(item => item.service_key === 'delivery-order') : null
  if (!selected && !fallback && record.service_kind === 'delivery' && venueId === 10) return null
  return selected?.commission_amount ?? fallback?.commission_amount ?? 0
}

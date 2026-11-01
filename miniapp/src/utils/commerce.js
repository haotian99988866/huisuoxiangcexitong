export const money = value => Number(value).toLocaleString('en-US', {
  minimumFractionDigits: 2, maximumFractionDigits: 6,
})

export const orderFilters = [
  ['all', '全部'], ['processing', '处理中'], ['completed', '已完成'], ['attention', '退款 / 待核实'],
]

export function orderMatches(order, filter) {
  if (filter === 'processing') return ['manual_pending', 'paid', 'delivering'].includes(order.status)
  if (filter === 'completed') return order.status === 'delivered'
  if (filter === 'attention') return ['refund_pending', 'refunded', 'delivery_unknown'].includes(order.status)
  return true
}

export function navigationSection(pathname) {
  if (/^\/(?:media|movies)(?:\/|$)/.test(pathname)) return 'movies'
  if (/^\/music(?:\/|$)/.test(pathname)) return 'music'
  if (/^\/announcements(?:\/|$)/.test(pathname)) return 'news'
  if (/^\/(?:services|membership)(?:\/|$)/.test(pathname)) return 'services'
  if (/^\/(?:personal|recharge|orders|wallet-history|admin)(?:\/|$)/.test(pathname)) return 'personal'
  return 'home'
}

export function orderGuidance(status) {
  return ({
    manual_pending: '余额已扣除，昊天将人工处理此套餐；确认开通后更新订单，请勿重复购买。',
    paid: '余额已扣除，无需再次付款。系统正在等待开通。',
    delivering: '正在为收件人开通会员，请等待结果，不要重复购买。',
    delivered: '会员已开通，请收件人在 Telegram 中查看会员权益。',
    refund_pending: '已确认开通失败，正在返还站内余额，请等待到账。',
    refunded: '款项已返还站内余额，不是退回 ABA 或链上钱包。',
    delivery_unknown: '结果需要人工核实。请提供订单编号联系客服，不要重复购买。',
  })[status] || '请保留订单编号并联系客服核实状态。'
}

export function validPurchaseAttempt(value) {
  return Boolean(value && /^[0-9a-f]{32}$/.test(value.order_id)
    && [3, 6, 12].includes(value.months)
    && typeof value.recipient_username === 'string'
    && /^@?[A-Za-z0-9_]{5,32}$/.test(value.recipient_username))
}

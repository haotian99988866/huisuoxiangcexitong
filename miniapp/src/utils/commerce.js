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
  if (/^\/(?:services|membership|phone-topup|hotels|short-dramas)(?:\/|$)/.test(pathname)) return 'services'
  if (/^\/(?:personal|recharge|orders|wallet-history|consumption|admin)(?:\/|$)/.test(pathname)) return 'personal'
  return 'home'
}

export const orderTitle = order => order.product_kind === 'phone'
  ? `柬埔寨话费 · $${money(order.topup_usd)}`
  : `Telegram会员 · ${order.months}个月`

export const orderRecipient = order => order.product_kind === 'phone'
  ? `手机号：${order.phone}` : `@${order.recipient_username}`

export function orderStatus(order) {
  if (order.product_kind !== 'phone') return null
  return ({
    manual_pending: '待人工代充', delivered: '已代充完成',
    refunded: '已全额退款', refund_pending: '退款中', delivery_unknown: '代充结果待核实',
  })[order.status] || '处理中'
}

export function orderGuidance(status, productKind) {
  if (productKind === 'phone') return ({
    manual_pending: '话费及手续费已扣除，昊天将人工代充，请勿重复下单。',
    delivered: '话费已代充完成，请在对应手机号核对到账。',
    refunded: '话费和手续费已全额退回站内余额，不是退回原付款渠道。',
    refund_pending: '未代充成功，话费和手续费正在全额退回站内余额。',
    delivery_unknown: '代充结果待人工核实，请保留订单编号联系客服，不要重新下单。',
  })[status] || '请保留订单编号并联系客服核实状态。'
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

export function normalizePhone(value) {
  if (typeof value !== 'string') return null
  let phone = value.replace(/[\s()-]/g, '')
  if (phone.startsWith('+855')) phone = `0${phone.slice(4)}`
  else if (phone.startsWith('855')) phone = `0${phone.slice(3)}`
  return /^0[1-9][0-9]{7,8}$/.test(phone) ? `+855${phone.slice(1)}` : null
}

export function phoneAmountCents(value) {
  if (typeof value !== 'string' || !/^[0-9]{1,3}(?:\.[0-9]{1,2})?$/.test(value)) return null
  const [dollars, fraction = ''] = value.split('.')
  const cents = Number(dollars) * 100 + Number(fraction.padEnd(2, '0'))
  return cents >= 500 && cents <= 10000 ? cents : null
}

export function validPhoneAttempt(value) {
  return Boolean(value && /^[0-9a-f]{32}$/.test(value.order_id)
    && normalizePhone(value.phone) && phoneAmountCents(value.amount) !== null)
}

export function validPurchaseAttempt(value) {
  return Boolean(value && /^[0-9a-f]{32}$/.test(value.order_id)
    && [3, 6, 12].includes(value.months)
    && typeof value.recipient_username === 'string'
    && /^@?[A-Za-z0-9_]{5,32}$/.test(value.recipient_username))
}

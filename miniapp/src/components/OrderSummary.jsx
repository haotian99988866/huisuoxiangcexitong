import { Link } from 'react-router-dom'
import { memberStatuses } from '../utils/membership'
import { money } from '../utils/commerce'

const labels = { manual_pending: '待人工开通', paid: '待开通', delivering: '开通中', delivered: '已完成',
  refund_pending: '退款中', refunded: '已退款', delivery_unknown: '待核实' }

export default function OrderSummary({ order }) {
  return <Link className="account-order-link" to={`/orders/${order.order_id}`}>
    <article className="membership-order">
      <div className="membership-row"><strong>Telegram会员 · {order.months}个月</strong><strong>${money(order.price_usd)}</strong></div>
      <div className="membership-row"><p>@{order.recipient_username}</p><span className="account-status">{labels[order.status] || memberStatuses[order.status] || order.status}</span></div>
      <small>{order.created_at}</small>
      <span className="account-order-more">详情 →</span>
    </article>
  </Link>
}

import { Link } from 'react-router-dom'
import { memberStatuses } from '../utils/membership'
import { money } from '../utils/commerce'

export default function OrderSummary({ order }) {
  return <Link className="account-order-link" to={`/orders/${order.order_id}`}>
    <article className="membership-order">
      <div className="membership-row"><strong>Telegram会员 · {order.months}个月</strong><strong>${money(order.price_usd)}</strong></div>
      <p>@{order.recipient_username} · {memberStatuses[order.status] || order.status}</p>
      <small>{order.created_at}</small>
      <span className="account-order-more">查看订单 →</span>
    </article>
  </Link>
}

import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import AccountAccess from '../components/AccountAccess'
import OrderSummary from '../components/OrderSummary'
import { bindBackButton, openTelegramLink } from '../utils/twa'
import { memberStatuses, requestMember } from '../utils/membership'
import { money, orderFilters, orderGuidance, orderMatches } from '../utils/commerce'
import useMemberAccount from '../utils/useMemberAccount'
import { useModuleScroll } from '../utils/moduleState'
import './Membership.css'

export default function Orders() {
  const { orderId } = useParams()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const { account, orders, loading, error, refresh, reportFailure } = useMemberAccount({ withOrders: !orderId })
  const [order, setOrder] = useState(null)
  const [detailError, setDetailError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState(false)
  const filter = params.get('filter') || 'all'
  useModuleScroll(orderId ? `order:${orderId}` : `orders:${filter}`, !loading && (!orderId || Boolean(order) || Boolean(detailError)))
  useEffect(() => bindBackButton(() => navigate(orderId ? '/orders' : '/personal', { replace: true })), [navigate, orderId])
  useEffect(() => {
    setOrder(null)
    setDetailError('')
    if (!account || !orderId) return undefined
    let active = true
    let polling = false
    let terminal = false
    async function load() {
      if (polling || terminal || document.visibilityState === 'hidden') return
      polling = true
      try {
        const result = await requestMember('member_order', { order_id: orderId })
        if (active) {
          setOrder(result)
          setDetailError('')
          terminal = ['delivered', 'refunded'].includes(result.status)
        }
      } catch (failure) {
        if (active) { setDetailError(failure.message); if (failure.status === 401) reportFailure(failure) }
      } finally { polling = false }
    }
    load()
    const timer = window.setInterval(load, 5000)
    return () => { active = false; window.clearInterval(timer) }
  }, [account?.user_id, orderId, attempt, reportFailure])
  const shown = orders.filter(item => orderMatches(item, filter))

  return <main className="account-page membership-page">
    <header className="membership-header">
      <Link to={orderId ? '/orders' : '/personal'}>← {orderId ? '全部订单' : '我的'}</Link>
      <h1>{orderId ? '订单详情' : '我的订单'}</h1>
      <p>这里展示会员购买订单；余额充值申请请查看充值记录。</p>
    </header>
    {error && <p className="membership-error" role="alert">{error}</p>}
    {!account ? <AccountAccess loading={loading} /> : orderId ? <>
      {detailError && <p className="membership-error" role="alert">{detailError}</p>}
      {!order && !detailError && <p role="status">正在查询订单…</p>}
      {order && <section className="membership-panel">
        <h2>{memberStatuses[order.status] || order.status}</h2>
        <p>{orderGuidance(order.status)}</p>
        <h3>Telegram Premium · {order.months}个月</h3>
        <p>收件人：@{order.recipient_username}</p>
        <p>支付金额：${money(order.price_usd)} USD</p>
        <p>订单编号</p><p className="membership-reference">{order.order_id}</p>
        <p>请保留订单编号。退款回站内余额，结果不明时先核实，不要重新下单。</p>
      </section>}
      <section className="membership-panel membership-actions">
        <button type="button" onClick={() => setAttempt(value => value + 1)}>刷新订单</button>
        <button type="button" onClick={() => openTelegramLink('https://t.me/haotian99988866')}>联系客服 · 提供订单编号</button>
        <Link to="/personal">查看账户余额</Link>
      </section>
    </> : <section className="membership-panel">
      <nav className="account-link-grid account-filter-tabs" aria-label="订单状态">
        {orderFilters.map(([key, label]) => <button key={key} type="button" aria-pressed={filter === key}
          onClick={() => setParams(key === 'all' ? {} : { filter: key }, { replace: true })}>{label}</button>)}
      </nav>
      <p>展示最近 30 笔会员订单，可按状态筛选；早期订单请保留编号联系客服查询。</p>
      {shown.length ? shown.map(item => <OrderSummary key={item.order_id} order={item} />) : <p>暂无此类订单。</p>}
      <button type="button" disabled={busy} onClick={async () => {
        setBusy(true)
        try { await refresh() } catch (failure) { reportFailure(failure) }
        finally { setBusy(false) }
      }}>{busy ? '读取中…' : '刷新订单'}</button>
    </section>}
  </main>
}

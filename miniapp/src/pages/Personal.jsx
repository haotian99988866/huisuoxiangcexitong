import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AccountAccess from '../components/AccountAccess'
import OrderSummary from '../components/OrderSummary'
import BrandArtwork from '../components/BrandArtwork'
import { bindBackButton, openTelegramLink } from '../utils/twa'
import { isTelegramMemberEntry } from '../utils/membership'
import { money, orderMatches } from '../utils/commerce'
import { useModuleScroll } from '../utils/moduleState'
import useMemberAccount from '../utils/useMemberAccount'
import './Membership.css'

export default function Personal() {
  const { account, orders, loading, error, refresh, reportFailure, logout } = useMemberAccount({ withOrders: true })
  const [busy, setBusy] = useState(false)
  const user = window.Telegram?.WebApp?.initDataUnsafe?.user
  const verifiedUser = account && user?.id === account.user_id ? user : null
  const pendingDeposit = account?.pending_deposit || account?.deposits.find(item => item.status === 'pending')
  const attention = orders.find(item => ['manual_pending', 'delivery_unknown', 'refund_pending', 'paid', 'delivering'].includes(item.status))
  useModuleScroll('personal', !loading)
  useEffect(() => bindBackButton(null), [])

  async function accountAction(action) {
    setBusy(true)
    try { await action() }
    catch (failure) { reportFailure(failure) }
    finally { setBusy(false) }
  }

  return <main className="account-page membership-page personal-page">
    <header className="account-profile">
      <span className="account-avatar" aria-hidden="true">
        {verifiedUser?.first_name?.slice(0, 1) || '我'}
      </span>
      <div>
        <h1>{verifiedUser ? [verifiedUser.first_name, verifiedUser.last_name].filter(Boolean).join(' ') : '我的'}</h1>
        {account && <p>{verifiedUser?.username ? `@${verifiedUser.username}` : `ID ${account.user_id}`} · 已验证</p>}
        {!account && <p>你的专属账户</p>}
      </div>
    </header>
    {error && <p className="membership-error" role="alert">{error}</p>}
    {!account ? <AccountAccess loading={loading} /> : <>
      <section className="membership-panel wallet-card" aria-labelledby="my-wallet">
        <BrandArtwork />
        <div className="membership-row"><h2 id="my-wallet">我的钱包</h2><span>USD</span></div>
        <p className="membership-balance">${money(account.balance)}</p>
        <Link className="account-primary-link" to="/recharge">{pendingDeposit || account.rejected_deposit ? '查看复核进度' : '充值余额'}</Link>
        <div className="account-link-grid">
          <Link to="/wallet-history?view=ledger">资金明细</Link>
          <Link to="/wallet-history?view=deposits">充值记录</Link>
        </div>
        {pendingDeposit && <Link className="account-reminder" to="/recharge">
          申请 #{pendingDeposit.id} 待复核 · 尚未入账 →
        </Link>}
      </section>
      <section className="membership-panel" aria-labelledby="my-orders">
        <div className="membership-row"><h2 id="my-orders">我的订单</h2><Link to="/orders">全部订单 →</Link></div>
        <div className="account-link-grid account-order-filters">
          {[['processing', '处理中'], ['completed', '已完成'], ['attention', '退款 / 待核实']].map(([filter, label]) =>
            <Link key={filter} to={`/orders?filter=${filter}`}>
              <strong>{orders.filter(item => orderMatches(item, filter)).length}</strong><span>{label}</span>
            </Link>)}
        </div>
        <p>最近30笔</p>
        {attention ? <OrderSummary order={attention} /> : orders[0] ? <OrderSummary order={orders[0]} /> : <p>暂无订单</p>}
      </section>
    </>}
    <section className="membership-panel" aria-labelledby="my-help">
      <h2 id="my-help">帮助与账户</h2>
      <div className="account-menu">
        <button type="button" onClick={() => openTelegramLink('https://t.me/haotian99988866')}>联系客服</button>
        {account && <button type="button" disabled={busy} onClick={() => accountAction(refresh)}>{busy ? '处理中…' : '刷新账户'}</button>}
        {account?.can_test && isTelegramMemberEntry() && <Link to="/admin">运营管理台</Link>}
        {account && !isTelegramMemberEntry() && <button type="button" disabled={busy} onClick={() => accountAction(logout)}>退出网站账户</button>}
      </div>
      <details className="account-details"><summary>账户与退款说明</summary>
        <p>充值需人工复核。会员未开通的退款返回站内余额；结果不明先核实。退回原付款渠道请联系管理员，不支持自助提现。</p>
        {account && <p>账户 ID：{account.user_id}</p>}
      </details>
    </section>
  </main>
}

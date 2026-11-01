import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AccountAccess from '../components/AccountAccess'
import OrderSummary from '../components/OrderSummary'
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
  const pendingDeposits = account?.deposits.filter(item => item.status === 'pending') || []
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
        {account && <p>{verifiedUser?.username ? `@${verifiedUser.username} · ` : ''}账户已验证 · ID {account.user_id}</p>}
        {!account && <p>余额、订单与专属内容，都在这里</p>}
      </div>
    </header>
    {error && <p className="membership-error" role="alert">{error}</p>}
    {!account ? <AccountAccess loading={loading} /> : <>
      <section className="membership-panel" aria-labelledby="my-wallet">
        <div className="membership-row"><h2 id="my-wallet">我的钱包</h2><span>USD</span></div>
        <p>可用余额</p>
        <p className="membership-balance">${money(account.balance)}</p>
        <Link className="account-primary-link" to="/recharge">充值余额</Link>
        <div className="account-link-grid">
          <Link to="/wallet-history?view=ledger">资金明细</Link>
          <Link to="/wallet-history?view=deposits">充值记录</Link>
        </div>
        {pendingDeposits.length > 0 && <Link className="account-reminder" to="/wallet-history?view=deposits">
          最近充值申请中有 {pendingDeposits.length} 笔待核款，申报金额不计入可用余额 →
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
        <p>以上数量为最近 30 笔会员订单。</p>
        {attention ? <OrderSummary order={attention} /> : orders[0] ? <OrderSummary order={orders[0]} /> : <p>还没有购买订单，去服务中心看看。</p>}
        <Link to="/services">选购服务 →</Link>
      </section>
    </>}
    <section className="membership-panel" aria-labelledby="my-content">
      <h2 id="my-content">我的内容</h2>
      <div className="account-link-grid">
        <Link to="/music?view=favorites">音乐收藏</Link>
        <Link to="/music?view=playlists">我的歌单</Link>
        <Link to="/?module=venue">出勤与提醒</Link>
        <button type="button" onClick={() => openTelegramLink('https://t.me/haotianzhaopian_bot?start=news')}>资讯订阅管理</button>
      </div>
    </section>
    <section className="membership-panel" aria-labelledby="my-help">
      <h2 id="my-help">帮助与账户</h2>
      <p>充值需人工核款；会员失败退款返回站内余额，结果不明先核实。暂不支持自助提现或自动退回付款渠道。</p>
      <div className="account-link-grid">
        <button type="button" onClick={() => openTelegramLink('https://t.me/haotian99988866')}>联系客服</button>
        {account && <button type="button" disabled={busy} onClick={() => accountAction(refresh)}>{busy ? '处理中…' : '刷新账户'}</button>}
        {account?.can_test && isTelegramMemberEntry() && <Link to="/admin">运营管理与模拟测试</Link>}
        {account && !isTelegramMemberEntry() && <button type="button" disabled={busy} onClick={() => accountAction(logout)}>退出网站账户</button>}
      </div>
    </section>
  </main>
}

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import OrderSummary from '../components/OrderSummary'
import BrandArtwork from '../components/BrandArtwork'
import AccountHeader from '../components/AccountHeader'
import ModuleIcon from '../components/ModuleIcon'
import { bindBackButton, openTelegramLink } from '../utils/twa'
import { isTelegramMemberEntry } from '../utils/membership'
import { money, orderMatches } from '../utils/commerce'
import { useModuleScroll } from '../utils/moduleState'
import useMemberAccount from '../utils/useMemberAccount'
import './Membership.css'

export default function Personal() {
  const { account, orders, loading, error, refresh, reportFailure } = useMemberAccount({ withOrders: true, telegramOnly: true })
  const [busy, setBusy] = useState(false)
  const user = window.Telegram?.WebApp?.initDataUnsafe?.user
  const telegramEntry = isTelegramMemberEntry()
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
    <AccountHeader>
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
    </AccountHeader>
    {error && <p className="membership-error" role="alert">{error}</p>}
    {!account ? <section className="membership-panel" role="status">
      {telegramEntry ? <>
        <h2>{loading ? '正在识别 Telegram 账户' : '账户信息暂不可用'}</h2>
        <p>{loading ? '无需登录，正在读取你的账户信息。' : '无需登录。请重新读取账户，或稍后重试。'}</p>
        {!loading && <button type="button" onClick={() => accountAction(refresh)}>重新读取账户</button>}
      </> : <>
        <h2>请从 Telegram 打开“我的”</h2>
        <p>账户会由 Telegram 自动识别，无需登录。</p>
        <a className="account-primary-link" href="https://t.me/haotianzhaopian_bot?startapp=personal">在 Telegram 中打开</a>
      </>}
    </section> : <>
      {account.can_test && isTelegramMemberEntry() && <Link className="personal-admin-entry" to="/admin">
        <span className="personal-admin-icon"><ModuleIcon name="personal" /></span>
        <div><strong>运营管理平台</strong><span>业务管理 · 运营健康 · 更多管理</span></div>
        <span className="personal-admin-action">进入管理</span>
      </Link>}
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
          申请 #{pendingDeposit.id} 待复核 · 尚未入账
        </Link>}
      </section>
      <section className="membership-panel" aria-labelledby="my-orders">
        <div className="membership-row"><h2 id="my-orders">我的订单</h2><Link to="/orders">全部订单</Link></div>
        <div className="account-link-grid account-order-filters">
          {[['processing', '处理中'], ['completed', '已完成'], ['attention', '退款 / 待核实']].map(([filter, label]) =>
            <Link key={filter} to={`/orders?filter=${filter}`}>
              <strong>{orders.filter(item => orderMatches(item, filter)).length}</strong><span>{label}</span>
            </Link>)}
        </div>
        <p>最近30笔</p>
        {attention ? <OrderSummary order={attention} /> : orders[0] ? <OrderSummary order={orders[0]} /> : <p>暂无订单</p>}
      </section>
      <section className="membership-panel">
        <div className="membership-row"><h2>我的消费</h2><Link to="/consumption">查看消费记录</Link></div>
        <p>会所消费明细、已退款金额及净累计消费。</p>
      </section>
    </>}
    <footer className="personal-footer" aria-label="帮助与客服">
      <button className="personal-support" type="button" onClick={() => openTelegramLink('https://t.me/haotian99988866')}>
        帮助 / 联系人工客服昊天
      </button>
      {account && <div className="personal-account-actions">
        <button type="button" disabled={busy} onClick={() => accountAction(refresh)}>{busy ? '处理中…' : '刷新账户'}</button>
      </div>}
    </footer>
  </main>
}

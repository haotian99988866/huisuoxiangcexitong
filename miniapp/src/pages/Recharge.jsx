import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import AccountAccess from '../components/AccountAccess'
import BalanceDeposit from '../components/BalanceDeposit'
import { bindBackButton } from '../utils/twa'
import { money } from '../utils/commerce'
import useMemberAccount from '../utils/useMemberAccount'
import { useModuleScroll } from '../utils/moduleState'
import './Membership.css'

export default function Recharge() {
  const navigate = useNavigate()
  const { account, loading, error, refresh, reportFailure } = useMemberAccount()
  const [busy, setBusy] = useState(false)
  useModuleScroll('recharge', !loading)
  useEffect(() => bindBackButton(() => navigate('/personal', { replace: true })), [navigate])
  return <main className="account-page membership-page">
    <header className="membership-header">
      <Link to="/personal">← 我的钱包</Link>
      <h1>充值余额</h1>
      <p>充值的是账户 USD 余额，不是直接开通会员。</p>
    </header>
    {error && <p className="membership-error" role="alert">{error}</p>}
    {!account ? <AccountAccess loading={loading} /> : <>
      <section className="membership-panel">
        <div className="membership-row"><h2>可用余额</h2><Link to="/wallet-history?view=deposits">充值记录 →</Link></div>
        <p className="membership-balance">${money(account.balance)}</p>
        <button type="button" disabled={busy} onClick={async () => {
          setBusy(true)
          try { await refresh() } catch (failure) { reportFailure(failure) }
          finally { setBusy(false) }
        }}>{busy ? '读取中…' : '刷新余额与核款结果'}</button>
      </section>
      <section className="membership-panel">
        <h2>选择充值方式</h2>
        <BalanceDeposit account={account} onRefresh={refresh} onFailure={reportFailure} />
      </section>
      <section className="membership-panel">
        <h2>到账后如何使用</h2>
        <p>管理员核款到账后，前往服务中心选套餐并确认购买。充值到账不会自动扣款或开通会员。</p>
        <Link to="/services">前往服务中心 →</Link>
        <p>不支持自助提现；退回 ABA 或链上钱包请联系管理员人工处理。不要提供密码或验证码。</p>
      </section>
    </>}
  </main>
}

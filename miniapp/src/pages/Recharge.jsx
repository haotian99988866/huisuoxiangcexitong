import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import AccountAccess from '../components/AccountAccess'
import BalanceDeposit from '../components/BalanceDeposit'
import { bindBackButton, hasTelegramBackButton } from '../utils/twa'
import { money } from '../utils/commerce'
import useMemberAccount from '../utils/useMemberAccount'
import { useModuleScroll } from '../utils/moduleState'
import './Membership.css'

export default function Recharge() {
  const navigate = useNavigate()
  const { account, loading, error, refresh, reportFailure } = useMemberAccount()
  const [busy, setBusy] = useState(false)
  const [localPending, setLocalPending] = useState(false)
  const pending = Boolean(account?.pending_deposit || account?.deposits.some(item => item.status === 'pending') || localPending)
  const progressOnly = pending || Boolean(account?.rejected_deposit)
  useModuleScroll('recharge', !loading)
  useEffect(() => bindBackButton(() => navigate('/personal', { replace: true })), [navigate])
  return <main className="account-page membership-page">
    <header className="membership-header">
      {!hasTelegramBackButton && <Link to="/personal">← 我的钱包</Link>}
      <h1>{progressOnly ? '充值复核进度' : '充值余额'}</h1>
      {!progressOnly && <p>充值的是账户 USD 余额，不是直接开通会员或充值手机话费。</p>}
    </header>
    {error && <p className="membership-error" role="alert">{error}</p>}
    {!account ? <AccountAccess loading={loading} /> : <>
      {!progressOnly && <section className="membership-panel">
        <div className="membership-row"><h2>可用余额</h2><Link to="/wallet-history?view=deposits">充值记录</Link></div>
        <p className="membership-balance">${money(account.balance)}</p>
        <button type="button" disabled={busy} onClick={async () => {
          setBusy(true)
          try { await refresh() } catch (failure) { reportFailure(failure) }
          finally { setBusy(false) }
        }}>{busy ? '读取中…' : '刷新余额与核款结果'}</button>
      </section>}
      <section className="membership-panel">
        {!progressOnly && <h2>选择充值方式</h2>}
        <BalanceDeposit account={account} onRefresh={refresh} onFailure={reportFailure} onPendingChange={setLocalPending} />
      </section>
    </>}
  </main>
}

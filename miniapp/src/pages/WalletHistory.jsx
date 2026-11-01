import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import AccountAccess from '../components/AccountAccess'
import DepositRecords from '../components/DepositRecords'
import { bindBackButton, hasTelegramBackButton } from '../utils/twa'
import { requestMember } from '../utils/membership'
import { money } from '../utils/commerce'
import useMemberAccount from '../utils/useMemberAccount'
import { useModuleScroll } from '../utils/moduleState'
import './Membership.css'

const labels = { deposit: '充值到账', purchase: '会员购买', refund: '会员退款' }

export default function WalletHistory() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const ledgerView = params.get('view') === 'ledger'
  const { account, loading, error, refresh, reportFailure } = useMemberAccount()
  const [ledger, setLedger] = useState(null)
  const [ledgerError, setLedgerError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState(false)
  useModuleScroll(ledgerView ? 'wallet-ledger' : 'wallet-deposits', !loading && (!ledgerView || Boolean(ledger) || Boolean(ledgerError)))
  useEffect(() => bindBackButton(() => navigate('/personal', { replace: true })), [navigate])
  useEffect(() => {
    let active = true
    setLedger(null)
    setLedgerError('')
    if (account && ledgerView) requestMember('member_ledger').then(result => {
      if (active) setLedger(result.entries)
    }).catch(failure => {
      if (active) { setLedgerError(failure.message); if (failure.status === 401) reportFailure(failure) }
    })
    return () => { active = false }
  }, [account?.user_id, ledgerView, attempt, reportFailure])

  return <main className="account-page membership-page">
    <header className="membership-header">
      {!hasTelegramBackButton && <Link to="/personal">← 我的钱包</Link>}
      <h1>{ledgerView ? '资金明细' : '充值记录'}</h1>
    </header>
    {error && <p className="membership-error" role="alert">{error}</p>}
    {!account ? <AccountAccess loading={loading} /> : <section className="membership-panel">
      <nav className="account-segment" aria-label="钱包记录类型">
        <button type="button" aria-pressed={!ledgerView} onClick={() => setParams({ view: 'deposits' }, { replace: true })}>充值申请</button>
        <button type="button" aria-pressed={ledgerView} onClick={() => setParams({ view: 'ledger' }, { replace: true })}>资金流水</button>
      </nav>
      <p>最近30条 · {ledgerView ? '实际余额变动' : '复核通过才入账'}</p>
      {ledgerView ? <>
        {ledgerError && <p className="membership-error" role="alert">{ledgerError}</p>}
        {!ledger && !ledgerError && <p role="status">正在读取流水…</p>}
        {ledger?.length === 0 && <p>暂无资金流水。</p>}
        {ledger?.map(entry => <article className="membership-order" key={`${entry.event}-${entry.id}`}>
          <div className="membership-row"><strong>{entry.product_kind === 'phone'
            ? entry.event === 'refund' ? '话费代充退款（含手续费）' : '话费代充（含手续费）'
            : labels[entry.event] || entry.event}</strong>
            <strong>{Number(entry.amount) > 0 ? '+' : ''}{money(entry.amount)} USD</strong></div>
          <p>{entry.created_at}</p>
          {entry.event === 'deposit' ? <p>充值 #{entry.reference}</p>
            : <Link to={`/orders/${entry.reference}`}>查看订单</Link>}
        </article>)}
      </> : <DepositRecords deposits={account.deposits} />}
      <button type="button" disabled={busy} onClick={async () => {
        setBusy(true)
        try { await refresh(); setAttempt(value => value + 1) }
        catch (failure) { reportFailure(failure) }
        finally { setBusy(false) }
      }}>{busy ? '读取中…' : '刷新记录与余额'}</button>
    </section>}
  </main>
}

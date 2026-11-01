import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AccountAccess from '../components/AccountAccess'
import { ConsumptionSummary, ConsumptionRows, ConsumptionPager } from '../components/CustomerRecords'
import { requestMember } from '../utils/membership'
import useMemberAccount from '../utils/useMemberAccount'
import { bindBackButton, openTelegramLink } from '../utils/twa'
import { useNavigate } from 'react-router-dom'
import './Membership.css'

export default function Consumption() {
  const navigate = useNavigate()
  const { account, loading, error: accountError } = useMemberAccount()
  const [page, setPage] = useState(1)
  const [reload, setReload] = useState(0)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => bindBackButton(() => navigate('/personal')), [navigate])
  useEffect(() => {
    if (!account) { setData(null); return }
    let active = true
    setBusy(true)
    setData(null)
    setError('')
    requestMember('customer_mine', { page })
      .then(result => { if (active) setData(result) })
      .catch(failure => { console.error('[My consumption]', failure); if (active) setError(failure.message) })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [account?.user_id, page, reload])
  return <main className="account-page membership-page">
    <header className="membership-header"><Link to="/personal">← 返回</Link><h1>我的消费</h1>
      <button type="button" disabled={busy || !account} onClick={() => setReload(value => value + 1)}>刷新</button></header>
    {(error || accountError) && <p role="alert" className="membership-error">{error || accountError}</p>}
    {!account ? <AccountAccess loading={loading} /> : <section className="membership-panel">
      <p>仅展示人工核实登记的会所消费，与钱包订单及资金明细分别记录。</p>
      {busy && <p role="status">正在读取消费记录…</p>}
      {data && <>
        <ConsumptionSummary summary={data.summary} />
        <ConsumptionRows records={data.records} />
        <ConsumptionPager page={page} total={data.total} onChange={setPage} busy={busy} />
      </>}
      <p>记录有误请联系昊天核对；已作废记录不计入累计，已退款金额从累计中扣除。</p>
      <button type="button" onClick={() => openTelegramLink('https://t.me/haotian99988866')}>联系昊天核对</button>
    </section>}
  </main>
}

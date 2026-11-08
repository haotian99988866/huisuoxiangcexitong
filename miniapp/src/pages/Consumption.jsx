import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import AccountAccess from '../components/AccountAccess'
import AccountHeader from '../components/AccountHeader'
import MusicIcon from '../components/MusicIcon'
import { ConsumptionSummary, ConsumptionRows, ConsumptionPager } from '../components/CustomerRecords'
import { requestMember } from '../utils/membership'
import useMemberAccount from '../utils/useMemberAccount'
import { bindBackButton, openTelegramLink } from '../utils/twa'
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
  return <main className="account-page membership-page consumption-page">
    <AccountHeader>
      <h1>我的消费</h1>
    </AccountHeader>
    {(error || accountError) && <p role="alert" className="membership-error">{error || accountError}
      {error && account && <button className="consumption-retry" type="button" disabled={busy}
        onClick={() => setReload(value => value + 1)}>重新读取</button>}</p>}
    {!account ? <AccountAccess loading={loading} /> : <>
      <p className="consumption-intro">人工核实登记的会所及外送消费，与钱包及订单分别记录。</p>
      {busy && <p role="status">正在读取消费记录…</p>}
      {data && <>
        <section className="membership-panel consumption-totals" aria-label="消费汇总">
          <ConsumptionSummary summary={data.summary} />
        </section>
        <section className="membership-panel consumption-records">
          <h2>消费记录</h2>
          {data.records.length ? <ConsumptionRows records={data.records} /> :
            <div className="consumption-empty"><MusicIcon name="list" size={28} />
              <p>暂无消费记录</p><small>浏览和预约不会计为消费。</small></div>}
          {(data.total > 20 || page > 1) && <ConsumptionPager page={page} total={data.total} onChange={setPage} busy={busy} />}
        </section>
      </>}
      <footer className="consumption-support">
        <p>作废记录不计入累计，已退款金额从累计中扣除。</p>
        <button type="button" onClick={() => openTelegramLink('https://t.me/haotian99988866')}>记录有疑问？联系昊天核对 <span aria-hidden="true">›</span></button>
      </footer>
    </>}
  </main>
}

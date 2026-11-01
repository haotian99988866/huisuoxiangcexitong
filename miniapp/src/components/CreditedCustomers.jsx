import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { requestAdmin } from '../utils/admin'
import { money } from '../utils/commerce'
import MusicDialog from './MusicDialog'
import './CreditedCustomers.css'

const date = value => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Phnom_Penh', hour12: false }) : '无记录'
const name = row => row.alias || row.display_name || (row.username ? `@${row.username}` : `用户 ${row.user_id}`)

function Pager({ page, total, busy, onChange }) {
  if (total <= 20) return null
  return <nav className="credited-pager" aria-label="充值记录分页">
    <button type="button" disabled={busy || page <= 1} onClick={() => onChange(page - 1)}>上一页</button>
    <span>{page} / {Math.ceil(total / 20)}</span>
    <button type="button" disabled={busy || page * 20 >= total} onClick={() => onChange(page + 1)}>下一页</button>
  </nav>
}

function DepositDetail({ userId, onClose }) {
  const [page, setPage] = useState(1)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let active = true
    setBusy(true); setError(''); setData(null)
    requestAdmin('admin_credited_customers', { user_id: userId, page })
      .then(result => { if (active) setData(result) })
      .catch(failure => {
        console.error('[Credited customer detail]', failure)
        if (active) setError(failure.message)
      })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [userId, page, reload])
  return <MusicDialog title="钱包充值明细" scrollBody onClose={onClose} footer={<div className="credited-footer">
    <button type="button" disabled={busy} onClick={() => setReload(value => value + 1)}>刷新记录</button>
    <button type="button" onClick={onClose}>关闭</button>
  </div>}>
    {busy && <p role="status">正在读取充值明细…</p>}
    {error && <p className="admin-error" role="alert">{error}</p>}
    {data && <>
      <h3>{name(data.customer)}</h3>
      <p className="admin-hint">Telegram ID {userId}{data.customer.username ? ` · @${data.customer.username}` : ''}</p>
      <p>累计充值 ${money(data.customer.credited_total)} · {data.customer.deposit_count} 笔</p>
      <p>当前钱包余额 ${money(data.customer.balance)}</p>
      <Link to={`/admin?customer=${userId}`} onClick={onClose}>查看客户档案</Link>
      {data.deposits.map(row => <article className="credited-deposit" key={row.id}>
        <h4>充值 #{row.id} · {row.method.toUpperCase()}</h4>
        <p><strong>实际入账 ${money(row.credited)}</strong> · 申报 ${money(row.claimed)}</p>
        <p>申请：{date(row.created_at)}<br />入账：{date(row.credited_at)}</p>
        <p>复核管理员 ID {row.admin_id}</p>
        <p className="credited-reference">交易编号：{row.reference}</p>
      </article>)}
      <Pager page={page} total={data.total} busy={busy} onChange={setPage} />
    </>}
  </MusicDialog>
}

export default function CreditedCustomers() {
  const [page, setPage] = useState(1)
  const [input, setInput] = useState('')
  const [query, setQuery] = useState('')
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState(null)
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let active = true
    setBusy(true); setError(''); setData(null)
    requestAdmin('admin_credited_customers', { page, query })
      .then(result => { if (active) setData(result) })
      .catch(failure => {
        console.error('[Credited customers]', failure)
        if (active) setError(failure.message)
      })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [page, query, reload])
  return <section className="admin-card credited-customers" aria-label="已充值用户">
    <div className="admin-header"><h2>已充值用户</h2>
      <button type="button" disabled={busy} onClick={() => setReload(value => value + 1)}>刷新数据</button></div>
    <p className="admin-hint">仅钱包实际已入账充值，不含待审核、拒绝申请、Stars或会员购买。余额与累计充值不同。</p>
    <form className="credited-search" onSubmit={event => {
      event.preventDefault(); setQuery(input.trim()); setPage(1); setReload(value => value + 1)
    }}>
      <label><span className="music-sr-only">搜索已充值用户</span><input value={input} maxLength={128}
        onChange={event => setInput(event.target.value)} placeholder="用户 ID、用户名、名称或客户称呼" /></label>
      <button type="submit" disabled={busy}>查询</button>
    </form>
    {busy && <p role="status">正在读取已入账充值…</p>}
    {error && <p className="admin-error" role="alert">{error}</p>}
    {data && <>
      <div className="credited-summary">
        <div><span>{query ? '匹配用户' : '已充值用户'}</span><strong>{data.summary.users} 位</strong></div>
        <div><span>充值笔数</span><strong>{data.summary.deposits} 笔</strong></div>
        <div><span>累计实际入账</span><strong>${money(data.summary.credited_total)}</strong></div>
      </div>
      {!data.customers.length && <p className="admin-hint">{query ? '没有匹配的已充值用户。' : '暂无已入账的钱包充值。'}</p>}
      {data.customers.map(row => <article className="credited-user" key={row.user_id}>
        <div className="admin-header"><h3>{name(row)}</h3><button type="button" onClick={() => setSelected(row.user_id)}>充值明细</button></div>
        <p className="admin-hint">Telegram ID {row.user_id}{row.username ? ` · @${row.username}` : ''}</p>
        <p>累计充值 <strong>${money(row.credited_total)}</strong> · {row.deposit_count} 笔</p>
        <p>当前余额 ${money(row.balance)}</p>
        <p className="admin-hint">首次入账：{date(row.first_credited_at)}<br />最近入账：{date(row.last_credited_at)}</p>
      </article>)}
      <Pager page={page} total={data.total} busy={busy} onChange={setPage} />
    </>}
    {selected && <DepositDetail key={selected} userId={selected} onClose={() => setSelected(null)} />}
  </section>
}

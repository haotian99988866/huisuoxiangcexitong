import { useEffect, useState } from 'react'
import { requestAdmin } from '../utils/admin'
import { consumptionDate } from '../utils/customerRecords'
import './CouponAdminOverview.css'

const inviteStates = { pending: '留存中', qualified: '已达标', left: '未满7天退出', review: '待人工核实', invalid: '未通过核实' }
const voucherStates = { available: '有效', redeemed: '已核销', invalid: '已失效', reversal_pending: '撤销待核对' }
const voucherFilters = [
  ['all', '全部'], ['expiring', '7天内到期'], ['available', '有效'], ['redeemed', '已核销'],
  ['expired', '已过期'], ['invalid', '已失效'], ['review', '待核实'],
]
const personName = row => row.display_name || (row.username ? `@${row.username}` : `ID ${row.owner_id}`)

function InviteDetail({ person, onClose, onCustomer }) {
  const [page, setPage] = useState(1)
  const [response, setData] = useState(null)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const requestKey = JSON.stringify([person.owner_id, page, reload])
  const data = response?.requestKey === requestKey ? response : null
  useEffect(() => {
    let active = true
    setData(null)
    setError('')
    requestAdmin('admin_coupon_detail', { owner_id: person.owner_id, page })
      .then(result => { if (active) setData({ ...result, requestKey }) })
      .catch(failure => { if (active) setError(failure.message) })
    return () => { active = false }
  }, [person.owner_id, page, reload])
  return <section className="coupon-overview-detail" aria-label="邀请明细">
    <div className="admin-header"><h3>{personName(person)} · 邀请明细</h3><button type="button" onClick={onClose}>返回列表</button></div>
    <p className="admin-hint">Telegram ID {person.owner_id} · 可在客户档案中核对和处理优惠券。</p>
    <button type="button" onClick={() => onCustomer(person.customer_id || person.owner_id)}>打开客户档案</button>
    {error && <p className="admin-error" role="alert">{error} <button type="button" onClick={() => setReload(value => value + 1)}>重新读取</button>
      {page > 1 && <button type="button" onClick={() => setPage(1)}>返回第一页</button>}</p>}
    {!data && !error && <p role="status">正在读取邀请明细…</p>}
    {data && <>
      <p className="admin-hint">共 {data.invite_total} 人 · 已达标未兑换 {data.eligible} 人 · 留存中 {data.retaining} 人</p>
      {data.invites.map(invite => <article className="coupon-overview-row" key={invite.invitee_id}>
        <strong>{invite.display_name || (invite.username ? `@${invite.username}` : `ID ${invite.invitee_id}`)}</strong>
        <span>{inviteStates[invite.state] || '待核实'} · 加入 {consumptionDate(invite.joined_at)}</span>
        {invite.left_at && <span>退出 {consumptionDate(invite.left_at)}</span>}
        {invite.review_reason && <span className="coupon-overview-warning">{invite.review_reason}</span>}
      </article>)}
      {!data.invites.length && <p className="admin-hint">暂无邀请记录。</p>}
      {(data.invite_total > 20 || page > 1) && <Pager page={page} total={data.invite_total} onChange={setPage} />}
    </>}
  </section>
}

function Pager({ page, total, onChange }) {
  return <nav className="coupon-overview-pager" aria-label="列表分页">
    <button type="button" disabled={page <= 1} onClick={() => onChange(page - 1)}>上一页</button>
    <span>{page} / {Math.max(1, Math.ceil(total / 20))} · {total}条</span>
    <button type="button" disabled={page * 20 >= total} onClick={() => onChange(page + 1)}>下一页</button>
  </nav>
}

export default function CouponAdminOverview({ onCustomer }) {
  const [view, setView] = useState('invites')
  const [status, setStatus] = useState('all')
  const [input, setInput] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [reload, setReload] = useState(0)
  const [response, setData] = useState(null)
  const [overview, setOverview] = useState(null)
  const [error, setError] = useState(null)
  const [person, setPerson] = useState(null)
  const requestKey = JSON.stringify([view, status, query, page, reload])
  const data = response?.requestKey === requestKey ? response : null
  const currentError = error?.requestKey === requestKey ? error.message : ''
  useEffect(() => {
    let active = true
    setData(null)
    setError(null)
    requestAdmin('admin_coupon_overview', { view, status, query, page })
      .then(result => {
        if (active) {
          setData({ ...result, requestKey })
          setOverview(result)
          setError(null)
        }
      })
      .catch(failure => { if (active) setError({ requestKey, message: failure.message }) })
    return () => { active = false }
  }, [view, status, query, page, reload])
  const choose = (nextView, nextStatus = 'all') => {
    setPerson(null)
    setView(nextView)
    setStatus(nextStatus)
    setPage(1)
  }
  const health = overview?.health
  const stale = health?.last_poll && new Date(overview.server_time) - new Date(health.last_poll) > 23 * 3600000
  const healthText = !health ? '监听状态不可用' : !health.enabled ? '活动已关闭' :
    !health.last_poll ? '尚无监听记录，需核实' : stale ? '监听超过23小时未更新，需核实' :
      !health.caught_up ? '监听尚未追平，需核实' : '最近监听已追平'
  return <section className="coupon-overview">
    <div className="admin-header"><h2>优惠券管理</h2>
      <button type="button" onClick={() => setReload(value => value + 1)}>刷新</button></div>
    {currentError && <p className="admin-error" role="alert">{currentError} <button type="button" onClick={() => setReload(value => value + 1)}>重新读取</button>
      {page > 1 && <button type="button" onClick={() => setPage(1)}>返回第一页</button>}</p>}
    {!overview && !currentError && <p role="status">正在读取优惠券管理…</p>}
    {overview && <>
      <p className={`coupon-overview-health${health?.enabled && !stale && health?.caught_up && health?.last_poll ? '' : ' is-warning'}`}>
        {healthText}{health?.last_poll && ` · 最近监听 ${consumptionDate(health.last_poll)}`}
      </p>
      <div className="coupon-overview-stats">
        {[['issued', '累计发券', 'all'], ['available', '有效', 'available'], ['expiring', '7天内到期', 'expiring'],
          ['redeemed', '已核销', 'redeemed'], ['expired', '已过期', 'expired'], ['invalid', '已失效', 'invalid'],
          ['review', '券待核实', 'review']].map(([key, label, filter]) =>
          <button type="button" key={key} onClick={() => choose('vouchers', filter)}>
            <span>{label}</span><strong>{overview.summary[key]}</strong>
          </button>)}
        <button type="button" onClick={() => choose('invites', 'review')}>
          <span>邀请待核实</span><strong>{overview.summary.invite_review}</strong>
        </button>
      </div>
      {overview.summary.future > 0 && <p className="admin-hint">另有 {overview.summary.future} 张券尚未到发券时间，不计入有效券。</p>}
      <details className="coupon-overview-notes"><summary>统计说明</summary>
        <p className="admin-hint">发券为累计记录；各券状态为当前状态。有效券不代表每笔消费都符合抵扣条件。</p>
        <p className="admin-hint">仅统计已记录的邀请；无法完整识别历史频道成员，老成员退出后重新加入可能被计入。</p>
      </details>
      {person ? <InviteDetail key={person.owner_id} person={person} onClose={() => setPerson(null)} onCustomer={onCustomer} /> : <>
        <nav className="coupon-overview-tabs" aria-label="优惠券管理分类">
          <button type="button" aria-pressed={view === 'invites'} onClick={() => choose('invites')}>邀请进度</button>
          <button type="button" aria-pressed={view === 'vouchers'} onClick={() => choose('vouchers')}>优惠券</button>
        </nav>
        <form className="coupon-overview-search" onSubmit={event => { event.preventDefault(); setQuery(input.trim()); setPage(1) }}>
          <label><span className="music-sr-only">搜索客户</span>
            <input type="search" maxLength={128} placeholder="搜索客户名称、用户名或 ID" value={input} onChange={event => setInput(event.target.value)} />
          </label>
          <button type="submit">搜索</button>
        </form>
        <label className="coupon-overview-filter">筛选
          <select value={status} onChange={event => { setStatus(event.target.value); setPage(1) }}>
            {(view === 'invites' ? [['all', '全部邀请人'], ['review', '邀请待核实']] : voucherFilters)
              .map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        {!data && !currentError && <p role="status">正在读取列表…</p>}
        {data && <>
        <p className="admin-hint">共 {data.total} {view === 'invites' ? '位邀请人' : '张券'} · 点击查看邀请明细</p>
        {data.items.map(item => <button type="button" className="coupon-overview-item" key={view === 'invites' ? item.owner_id : item.id} onClick={() => setPerson(item)}>
          <span className="coupon-overview-item-head"><strong>{personName(item)}</strong>
            <span>{view === 'invites' ? `已发 ${item.issued} 张` :
              item.status === 'available' && new Date(item.issued_at) > new Date(data.server_time) ? '尚未生效' :
                item.status === 'available' && new Date(item.expires_at) <= new Date(data.server_time) ? '已过期' :
                  voucherStates[item.status] || '待核实'}</span></span>
          {view === 'invites' ? <>
            <span>邀请 {item.invite_total} 人 · 累计达标 {item.qualified_total} 人 · 本轮已达标 {item.eligible} 人 · 留存中 {item.retaining} 人</span>
            <span>本轮还需达标 {Math.max(0, 5 - item.eligible)} 人 · 预计还需邀请 {Math.max(0, 5 - item.eligible - item.retaining)} 人</span>
            {item.review_count > 0 && <span className="coupon-overview-warning">待核实 {item.review_count} 人</span>}
          </> : <>
            <span>到期 {consumptionDate(item.expires_at)} · 发券 {consumptionDate(item.issued_at)}</span>
            <span>券号 {item.id}</span>
          </>}
        </button>)}
        {!data.items.length && <p className="admin-hint">没有符合条件的记录。</p>}
        {view === 'invites' && <p className="admin-hint">预计还需邀请按留存好友全部通过核验计算；退出或未通过核验时可能增加。</p>}
        {(data.total > 20 || page > 1) && <Pager page={page} total={data.total} onChange={setPage} />}
        </>}
      </>}
    </>}
  </section>
}

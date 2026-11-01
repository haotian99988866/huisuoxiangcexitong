import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { requestAdmin } from '../utils/admin'
import { duration, pageLabels } from '../utils/analytics'
import './CustomerAnalytics.css'

const dateTime = value => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Phnom_Penh', hour12: false }) : '未知'
const who = row => row.username ? `@${row.username}` : row.display_name || `ID ${row.user_id}`
const stateLabels = { unknown: '未确认', reachable: '最近确认可联系', blocked: '已屏蔽机器人' }
const eventLabels = { started: '启动机器人', reachable: '解除屏蔽 / 可联系', blocked: '屏蔽机器人' }
const count = value => Number(value || 0).toLocaleString('zh-CN')

function Pager({ page, total, onChange, busy }) {
  const last = Math.max(1, Math.ceil(total / 20))
  return <div className="analytics-pager">
    <button type="button" disabled={busy || page <= 1} onClick={() => onChange(page - 1)}>上一页</button>
    <span>{page} / {last} · {count(total)}条</span>
    <button type="button" disabled={busy || page >= last} onClick={() => onChange(page + 1)}>下一页</button>
  </div>
}

function PageTable({ rows }) {
  return <div className="analytics-table-scroll"><table>
    <caption className="music-sr-only">页面浏览与停留排行</caption>
    <thead><tr><th>页面</th><th>浏览次数</th><th>总停留</th><th>平均每次</th></tr></thead>
    <tbody>{rows.map(row => <tr key={row.page_key}>
      <th>{pageLabels[row.page_key] || row.page_key}</th><td>{count(row.views)}</td>
      <td>{duration(row.active_ms)}</td><td>{duration(row.views ? row.active_ms / row.views : 0)}</td>
    </tr>)}</tbody>
  </table>{!rows.length && <p className="admin-hint">暂无页面记录</p>}</div>
}

function Trend({ daily }) {
  const [metric, setMetric] = useState('visits')
  const metrics = { visits: '访问次数', customers: '访问人数', active_ms: '停留分钟' }
  const values = daily.map(row => metric === 'active_ms' ? row.active_ms / 60000 : row[metric])
  const max = Math.max(1, ...values)
  const points = values.map((value, index) => `${48 + index * 604 / Math.max(1, values.length - 1)},${194 - value * 160 / max}`).join(' ')
  return <section className="admin-card analytics-trend">
    <div className="admin-header"><h2>访问趋势</h2><select aria-label="折线图指标" value={metric} onChange={event => setMetric(event.target.value)}>
      {Object.entries(metrics).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
    </select></div>
    <svg viewBox="0 0 680 240" role="img" aria-label={`${metrics[metric]}每日趋势，详细数值可展开查看`}>
      {[0, 1, 2, 3, 4].map(step => <g key={step}>
        <line x1="48" x2="652" y1={34 + step * 40} y2={34 + step * 40} stroke="#e5e6eb" />
        <text x="40" y={38 + step * 40} textAnchor="end">{Math.round(max * (1 - step / 4))}</text>
      </g>)}
      <polygon points={`48,194 ${points} ${values.length === 1 ? '48' : '652'},194`} fill="#b5946014" />
      <polyline points={points} fill="none" stroke="#805b2e" strokeWidth="3" strokeLinejoin="round" />
      {daily.map((row, index) => <circle key={row.day} cx={48 + index * 604 / Math.max(1, values.length - 1)}
        cy={194 - values[index] * 160 / max} r={daily.length > 30 ? 2 : 4} fill="#805b2e">
        <title>{row.day}：{metrics[metric]} {metric === 'active_ms' ? values[index].toFixed(1) : values[index]}</title>
      </circle>)}
      <text x="48" y="222">{daily[0]?.day.slice(5)}</text><text x="652" y="222" textAnchor="end">{daily.at(-1)?.day.slice(5)}</text>
    </svg>
    <details className="account-details"><summary>每日详细数据</summary>
      <div className="analytics-table-scroll"><table><thead><tr><th>日期</th><th>人数</th><th>访问</th><th>停留</th></tr></thead>
        <tbody>{daily.map(row => <tr key={row.day}><th>{row.day}</th><td>{count(row.customers)}</td><td>{count(row.visits)}</td><td>{duration(row.active_ms)}</td></tr>)}</tbody>
      </table></div>
    </details>
  </section>
}

function CustomerDetail({ userId, onClose, onSelect }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [invitePage, setInvitePage] = useState(1)
  const [sessionPage, setSessionPage] = useState(1)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true
    setBusy(true)
    setError('')
    requestAdmin('admin_analytics_customer', { user_id: userId, invite_page: invitePage, session_page: sessionPage })
      .then(result => { if (active) setData(result) })
      .catch(failure => { console.error('[Customer detail]', failure); if (active) { setData(null); setError(failure.message) } })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [userId, invitePage, sessionPage, revision])
  return <div className="analytics-detail">
    <div className="admin-header"><button type="button" onClick={onClose}>← 客户列表</button>
      <button type="button" disabled={busy} onClick={() => setRevision(value => value + 1)}>刷新</button></div>
    {error && <p role="alert" className="admin-error">{error}</p>}
    {busy && <p role="status" className="admin-hint">正在读取客户记录…</p>}
    {data && <>
      <section className="admin-card">
        <h2>{who(data.customer)}</h2><p className="admin-hint">ID {userId} · 首次记录 {dateTime(data.customer.first_seen)}</p>
        <Link to={`/admin?customer=${userId}`}>客户档案 / 消费登记</Link>
        <p>{stateLabels[data.customer.bot_state]} · 状态确认 {dateTime(data.customer.bot_state_at)}</p>
        <p>启动机器人：{dateTime(data.customer.bot_started_at)}<br />最近互动：{dateTime(data.customer.last_interaction)}</p>
        {data.customer.locally_blocked ? <p className="admin-hint">管理员已在客服系统拉黑（与客户屏蔽机器人不同）</p> : null}
        <p>浏览统计：{data.customer.recording ? '开启' : '客户已关闭'}</p>
        <div className="analytics-metrics">
          <div><span>90天访问</span><strong>{count(data.session_total)}</strong></div>
          <div><span>90天停留</span><strong>{duration(data.pages.reduce((sum, row) => sum + Number(row.active_ms), 0))}</strong></div>
          <div><span>当前音乐收藏</span><strong>{count(data.favorites)}</strong></div>
          <div><span>当前歌单</span><strong>{count(data.playlists)}</strong></div>
        </div>
        {data.inviter ? <p>首次邀请人：<button type="button" onClick={() => onSelect(data.inviter.inviter_id)}>{who(data.inviter)}</button>
          <br />{dateTime(data.inviter.created_at)} · {data.inviter.existing_customer ? '已有客户回访' : '新客户'}</p> : <p className="admin-hint">没有可确认的邀请来源</p>}
      </section>
      <section className="admin-card"><h2>停留最久的页面</h2><PageTable rows={data.pages} /></section>
      <section className="admin-card"><h2>访问记录 · 90天</h2>
        {data.sessions.map(session => <details key={session.id} className="analytics-session">
          <summary><span>{dateTime(session.started_at)}</span><strong>{duration(session.active_ms)}</strong><small>{session.views}次页面浏览</small></summary>
          <div className="analytics-table-scroll"><table><thead><tr><th>进入时间</th><th>页面</th><th>停留</th></tr></thead>
            <tbody>{session.timeline.map((view, index) => <tr key={index}><td>{dateTime(view.started_at)}</td><td>{pageLabels[view.page_key]}</td><td>{duration(view.active_ms)}</td></tr>)}</tbody>
          </table></div>
          {session.timeline_truncated && <p className="admin-hint">本次访问超过200条页面记录，仅展示前200条。</p>}
        </details>)}
        {!data.sessions.length && <p className="admin-hint">暂无浏览记录</p>}
        <Pager page={data.session_page} total={data.session_total} onChange={setSessionPage} busy={busy} />
      </section>
      <section className="admin-card"><h2>邀请的客户 · 90天</h2>
        <div className="analytics-table-scroll"><table><thead><tr><th>客户</th><th>邀请时间</th><th>类型</th><th>机器人状态</th><th>最近互动</th></tr></thead>
          <tbody>{data.invited.map(row => <tr key={row.user_id}>
            <td><button type="button" onClick={() => onSelect(row.user_id)}>{who(row)}</button><small>ID {row.user_id}</small></td>
            <td>{dateTime(row.created_at)}</td><td>{row.existing_customer ? '老客回访' : '新客'}</td>
            <td>{stateLabels[row.bot_state]}<small>{dateTime(row.bot_state_at)}</small></td><td>{dateTime(row.last_interaction)}</td>
          </tr>)}</tbody>
        </table></div>
        <Pager page={data.invite_page} total={data.invite_total} onChange={setInvitePage} busy={busy} />
      </section>
      <section className="admin-card"><h2>机器人状态变化 · 最近30条</h2>
        {data.bot_events.map((row, index) => <p className="admin-hint" key={index}>{dateTime(row.created_at)} · {eventLabels[row.state]}</p>)}
        {!data.bot_events.length && <p className="admin-hint">暂无已观察到的状态变化，不能据此判断客户是否关注。</p>}
      </section>
    </>}
  </div>
}

export default function CustomerAnalytics() {
  const [days, setDays] = useState(7)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [input, setInput] = useState('')
  const [sort, setSort] = useState('last_seen')
  const [data, setData] = useState(null)
  const [selected, setSelected] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const top = useRef(null)
  useEffect(() => {
    let active = true
    setBusy(true)
    setError('')
    requestAdmin('admin_analytics', { days, page, search, sort })
      .then(result => { if (active) setData(result) })
      .catch(failure => { console.error('[Customer analytics]', failure); if (active) { setData(null); setError(failure.message) } })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [days, page, search, sort, revision])
  const select = userId => { setSelected(userId); top.current?.scrollIntoView({ block: 'start' }) }
  return <section ref={top} className="customer-analytics">
    {selected ? <CustomerDetail key={selected} userId={selected} onSelect={select} onClose={() => setSelected(null)} /> : <>
      <div className="admin-header analytics-heading"><h2>客户行为分析</h2>
        <button type="button" disabled={busy} onClick={() => setRevision(value => value + 1)}>{busy ? '读取中…' : '刷新数据'}</button></div>
      <button type="button" onClick={() => select(window.Telegram.WebApp.initDataUnsafe.user.id)}>查看我邀请的客户</button>
      <nav className="analytics-periods" aria-label="统计时间范围">
        {[[1, '今日'], [7, '7天'], [30, '30天'], [90, '90天']].map(([value, label]) => <button key={value}
          type="button" aria-pressed={days === value} onClick={() => { setDays(value); setPage(1) }}>{label}</button>)}
      </nav>
      <p className="admin-hint">柬埔寨时间 UTC+7 · 不含管理员 · 前台可见停留时间</p>
      {error && <p role="alert" className="admin-error">{error}</p>}
      {data && <>
        <div className="analytics-metrics">
          {[
            ['访问客户', count(data.overview.customers)], ['访问次数', count(data.overview.visits)],
            ['页面浏览', count(data.overview.page_views)], ['总停留', duration(data.overview.active_ms)],
            ['平均每次访问', duration(data.overview.visits ? data.overview.active_ms / data.overview.visits : 0)],
            ['近45秒活跃', count(data.overview.online)], ['确认邀请', count(data.overview.referrals)],
            ['邀请新客户', count(data.overview.new_referrals)],
          ].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}
        </div>
        <Trend daily={data.daily} />
        <section className="admin-card"><h2>页面停留排行</h2><PageTable rows={data.pages} /></section>
        <section className="admin-card">
          <h2>客户明细</h2>
          <form className="analytics-search" onSubmit={event => { event.preventDefault(); setSearch(input.trim()); setPage(1) }}>
            <input aria-label="搜索客户用户名、名称或ID" placeholder="用户名 / 名称 / ID" maxLength={64} value={input} onChange={event => setInput(event.target.value)} />
            <button type="submit" disabled={busy}>搜索</button>
          </form>
          <select aria-label="客户排序" value={sort} onChange={event => { setSort(event.target.value); setPage(1) }}>
            <option value="last_seen">最近访问 / 互动</option><option value="duration">停留时长最多</option>
            <option value="visits">访问次数最多</option><option value="invites">邀请人数最多</option>
          </select>
          <div className="analytics-table-scroll"><table><thead><tr>
            <th>客户</th><th>访问</th><th>浏览</th><th>总停留</th><th>最久页面</th><th>邀请</th><th>机器人状态</th><th>最近访问 / 互动</th>
          </tr></thead><tbody>{data.customers.map(row => <tr key={row.user_id}>
            <td><button type="button" onClick={() => select(row.user_id)}>{who(row)}</button><small>ID {row.user_id}</small></td>
            <td>{count(row.sessions)}</td><td>{count(row.views)}</td><td>{duration(row.active_ms)}</td>
            <td>{pageLabels[row.top_page] || '暂无'}</td><td>{count(row.invites)}</td>
            <td>{stateLabels[row.bot_state]}</td><td>{dateTime(row.last_seen)}</td>
          </tr>)}</tbody></table></div>
          {!data.customers.length && <p className="admin-hint">没有匹配的客户</p>}
          <Pager page={data.page} total={data.total} onChange={setPage} busy={busy} />
        </section>
        <details className="account-details"><summary>统计口径与准确性</summary>
          <p>一次打开或刷新记为一次访问；进入页面或详情记为一次浏览，跨柬埔寨日历日会分段记录（页面次数包含跨天分段）。心跳每15秒提交，切页、切后台和退出时补交；只记录前台可见时间，不统计后台挂机。网络中断、系统强制关闭可能导致少量时长漏记；近45秒活跃不等于实时在线。</p>
          <p>未登录的普通浏览器和租户专属入口不记录个人行为。旧口径打开记录共 {count(data.legacy_visits)} 次（5分钟去重），不合并进新会话统计。客户可在“我的”关闭并删除浏览明细。客户列表包含已知机器人客户，零访问不表示从未访问过。</p>
          <p>邀请必须由对方通过专属链接启动机器人或小程序确认，首次有效归属固定；后续回访不会改归属。新客指系统此前没有该账户记录，不保证其从未使用过 Telegram。机器人只显示已观察到的启动、屏蔽或解除状态，不等于频道关注；未知状态不作推断。</p>
          <p>浏览、邀请事件和状态历史保留90天；账户资料、首次邀请归属及当前机器人状态保留以避免重归属。收藏数、歌单数为当前实际操作结果，不将停留时长当成喜欢。</p>
        </details>
      </>}
    </>}
  </section>
}

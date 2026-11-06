import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import AccountHeader from '../components/AccountHeader'
import { API_BASE_URL, bindBackButton } from '../utils/twa'
import { requestCustomerDuty } from '../utils/customerDuty'
import { newMemberOrderId } from '../utils/membership'
import { isTenantEntry, mediaUrl } from '../utils/tenant'
import { readSubscriptionOperations } from '../utils/subscriptionOperations'
import { useModuleScroll } from '../utils/moduleState'
import './Membership.css'
import './Subscriptions.css'

const identity = item => `${item.kind}:${item.id}`

export default function Subscriptions() {
  const navigate = useNavigate()
  const [catalog, setCatalog] = useState(null)
  const [venue, setVenue] = useState('')
  const [selected, setSelected] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [unconfirmed, setUnconfirmed] = useState(false)
  const [failedPhotos, setFailedPhotos] = useState({})
  const [view, setView] = useState('add')
  const [search, setSearch] = useState('')
  const lock = useRef(false)
  const authenticated = Boolean(window.Telegram?.WebApp?.initData) && !isTenantEntry()
  const user = window.Telegram?.WebApp?.initDataUnsafe?.user?.id
  const pendingKey = `subscription-center-pending-${user}`
  useModuleScroll('subscriptions', !authenticated || Boolean(catalog))
  useEffect(() => bindBackButton(() => navigate('/services', { replace: true })), [navigate])
  const load = async () => {
    const result = await requestCustomerDuty('subscription_catalog', venue ? { venue_id: Number(venue) } : {})
    setCatalog(result)
  }
  useEffect(() => {
    if (!authenticated) return
    let cancelled = false
    setBusy(true)
    try {
      setUnconfirmed(readSubscriptionOperations(sessionStorage, pendingKey).length > 0)
    } catch (failure) {
      console.error('[Subscription pending data]', failure)
      setError(failure.message)
      setUnconfirmed(true)
      setBusy(false)
      return
    }
    requestCustomerDuty('subscription_catalog', venue ? { venue_id: Number(venue) } : {})
      .then(result => { if (!cancelled) { setCatalog(result); setError('') } })
      .catch(failure => {
        console.error('[Subscription center]', failure)
        if (!cancelled) setError(failure.name === 'AbortError' ? '订阅读取超时，请重试' : failure.message)
      })
      .finally(() => { if (!cancelled) setBusy(false) })
    return () => { cancelled = true }
  }, [authenticated, venue])

  const apply = async operations => {
    if (lock.current) return
    lock.current = true
    setBusy(true)
    setError('')
    setNotice('')
    let completed = 0
    try {
      let pending = readSubscriptionOperations(sessionStorage, pendingKey)
      if (pending.length && operations.length) throw new Error('存在未确认操作，请先点击“重试未确认操作”核对结果')
      if (!pending.length) {
        pending = operations.map(item => ({ ...item, request_id: newMemberOrderId() }))
        sessionStorage.setItem(pendingKey, JSON.stringify(pending))
        setUnconfirmed(pending.length > 0)
      }
      while (pending.length) {
        await requestCustomerDuty('subscription_save', pending[0])
        completed += 1
        pending = pending.slice(1)
        sessionStorage.setItem(pendingKey, JSON.stringify(pending))
        setUnconfirmed(pending.length > 0)
      }
      setSelected({})
      setNotice(`已完成${completed}项订阅操作。只提醒订阅后的新发布，不补发历史。`)
      await load()
    } catch (failure) {
      console.error('[Subscription save]', failure)
      if ([400, 401, 403, 409].includes(failure.status)) {
        sessionStorage.removeItem(pendingKey)
        setUnconfirmed(false)
        setSelected({})
        try { await load() } catch (refreshFailure) {
          console.error('[Subscription after rejection]', refreshFailure)
        }
      }
      setError(`已确认完成${completed}项。${failure.name === 'AbortError' || failure.name === 'TypeError'
        ? '网络异常，剩余结果待确认，请点击“重试未确认操作”，不要重复提交。' : failure.message}`)
    } finally {
      lock.current = false
      setBusy(false)
    }
  }
  const cancel = item => apply([{ kind: item.kind, id: item.id, enabled: false, revision: item.revision }])
  const submit = () => apply(Object.values(selected).map(item => ({
    kind: item.kind, id: item.id, enabled: true, revision: item.revision,
  })))
  const choice = item => {
    const disabled = busy || unconfirmed || item.subscribed || item.available === false || Boolean(item.published_at)
    const person = item.kind === 'staff' || item.kind === 'person'
    const photoKey = `${identity(item)}:${item.cover}`
    const imagePath = item.kind === 'staff'
      ? `/staff-photos/${item.cover}/image?thumb=1` : `/delivery-media/${item.cover}?thumb=1`
    return <label className={`subscription-choice${person ? ' subscription-person' : ''}${selected[identity(item)] ? ' is-selected' : ''}${disabled ? ' is-disabled' : ''}`} key={identity(item)}>
      {person && <div className="subscription-person-photo">
        {item.cover && !failedPhotos[photoKey]
          ? <img src={mediaUrl(`${API_BASE_URL}${imagePath}`)} alt={`${item.label}照片`} loading="lazy"
            onError={() => {
              console.error('[Subscription photo unavailable]', item.kind, item.id, item.cover)
              setFailedPhotos(current => ({ ...current, [photoKey]: true }))
            }} />
          : <span>{item.cover ? '照片暂不可用' : '暂无公开照片'}</span>}
      </div>}
      <div className="subscription-person-info">
      <input type="checkbox" aria-label={item.label} disabled={disabled} checked={Boolean(item.subscribed || selected[identity(item)])}
        onChange={event => {
          const checked = event.target.checked
          setSelected(current => {
            const next = { ...current }
            if (checked) next[identity(item)] = item
            else delete next[identity(item)]
            return next
          })
        }} />
      <span>{item.label}</span>
      </div>
      {(person || item.subscribed || item.available === false) && <small>{item.subscribed ? '已订阅' : item.published_at ? '本班已发布，下班次可订阅'
        : item.available === false ? '暂不可订阅' : selected[identity(item)] ? '已选择'
          : '点击选择'}</small>}
    </label>
  }
  const subscribedScopes = catalog?.scopes.filter(item => item.subscribed) || []
  const subscriptions = [...subscribedScopes, ...(catalog?.subscribed_people || [])]
  const selections = Object.values(selected)
  const visiblePeople = catalog?.people.filter(item => item.label.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())) || []
  const removeSelection = key => setSelected(current => {
    const next = { ...current }
    delete next[key]
    return next
  })
  return <main className="account-page membership-page subscriptions-page">
    <AccountHeader><header className="membership-header"><h1>订阅服务</h1></header></AccountHeader>
    <p className="consumption-intro">关注动态，新发布时接收机器人提醒。</p>
    {!authenticated ? <section className="membership-panel">
      <p>请从 Telegram 主入口验证身份，才能查看和管理自己的订阅。</p>
      <a className="subscription-primary" href="https://t.me/haotianzhaopian_bot?startapp=subscriptions">登录并管理订阅</a>
      <p>请先在机器人聊天点击“开始”，以接收提醒。</p>
    </section> : <>
      {error && <p role="alert" className="membership-error">{error}</p>}
      {notice && <p role="status" className="membership-notice">{notice}</p>}
      {busy && !catalog && <p role="status">正在读取订阅…</p>}
      {(unconfirmed || error) && <div className="subscription-actions">
        {unconfirmed && <button className="subscription-retry" disabled={busy} onClick={() => apply([])}>重试未确认操作</button>}
        {error && !unconfirmed && <button disabled={busy} onClick={() => {
          setBusy(true)
          load().then(() => setError(''))
            .catch(failure => { console.error('[Subscription reload]', failure); setError(failure.message) })
            .finally(() => setBusy(false))
        }}>重新读取</button>}
      </div>}
      {catalog && <>
        <nav className="subscription-tabs" aria-label="订阅管理">
          <button aria-pressed={view === 'add'} onClick={() => setView('add')}>添加订阅</button>
          <button aria-pressed={view === 'mine'} onClick={() => setView('mine')}>我的订阅 <span>{subscriptions.length}</span></button>
        </nav>
        {view === 'mine' ? <section className="membership-panel">
          <div className="subscription-section-heading"><h2>正在关注</h2><span>{subscriptions.length}项</span></div>
          {!subscriptions.length && <div className="subscription-empty"><p>还没有订阅</p><p>添加感兴趣的分类或人员，及时接收新动态。</p><button onClick={() => setView('add')}>去添加订阅</button></div>}
          {subscriptions.map(item => <div className="subscription-current" key={identity(item)}>
            <div><strong>{item.label}</strong><small>{item.venue_id ? catalog.venues.find(v => v.id === item.venue_id)?.name || '历史档案'
              : item.kind === 'news' ? '资讯提醒' : item.kind === 'delivery' ? '外送分类提醒' : '会所出勤提醒'}</small></div>
            <button disabled={busy || unconfirmed} onClick={() => cancel(item)}>取消订阅</button>
          </div>)}
        </section> : <>
        <section className="membership-panel">
          <div className="subscription-section-heading"><h2>分类动态</h2><span>可多选</span></div>
          {[['venue', '会所出勤', '每班一次'], ['delivery', '外送动态', '每晚班一次'], ['news', '大事件', '每条新资讯']].map(([kind, title, frequency]) =>
            <div className="subscription-scope-group" key={kind}>
              <div className="subscription-group-heading"><h3>{title}</h3><span>{frequency}</span></div>
              <div className="subscription-scope-grid">{catalog.scopes.filter(item => item.kind === kind).map(choice)}</div>
            </div>)}
        </section>
        <section className="membership-panel">
          <div className="subscription-section-heading"><h2>人员出勤</h2><span>可跨分类多选</span></div>
          <select aria-label="选择会所或外送分类" disabled={busy} value={venue} onChange={event => { setVenue(event.target.value); setSearch('') }}>
            <option value="">请选择会所或外送分类</option>
            {catalog.venues.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}
          </select>
          {!venue ? <p className="subscription-empty">先选择分类，再按照片挑选技师或外送人员。</p> : <>
            <input className="subscription-search" type="search" aria-label="搜索人员名称或编号" placeholder="搜索名称或编号" value={search} onChange={event => setSearch(event.target.value)} />
            <p className="subscription-people-hint">{busy ? '正在读取人员…' : `${visiblePeople.length}位人员`} · 新出勤时提醒，本班已发布的人员下班次可订阅</p>
            {!busy && !visiblePeople.length && <p className="subscription-empty">{catalog.people.length ? '没有匹配的人员，试试其他名称或编号。' : '此分类暂无可订阅人员。'}</p>}
          </>}
          <div className="subscription-people-grid" aria-busy={busy}>{visiblePeople.map(choice)}</div>
          {Object.keys(failedPhotos).length > 0 && <button type="button" onClick={() => setFailedPhotos({})}>重试照片</button>}
        </section>
        </>}
        {selections.length > 0 && <div className="subscription-submit-bar">
          {selections.length > 0 && <details className="subscription-selection-summary">
            <summary>已选 {selections.length} 项 · 查看</summary>
            <ul>{selections.map(item => <li key={identity(item)}><span>{item.venue_id ? `${catalog.venues.find(v => v.id === item.venue_id)?.name || ''} · ` : ''}{item.label}</span><button aria-label={`移除${item.label}`} disabled={busy || unconfirmed} onClick={() => removeSelection(identity(item))}>移除</button></li>)}</ul>
          </details>}
          <div className="subscription-submit-row">
            <button className="subscription-clear" disabled={busy || unconfirmed || !selections.length} onClick={() => setSelected({})}>清空</button>
            <button className="subscription-primary" disabled={busy || unconfirmed || !selections.length} onClick={submit}>
              {busy ? '正在处理…' : `确认订阅${selections.length ? `（${selections.length}）` : ''}`}
            </button>
          </div>
        </div>}
        <p className="subscription-footnote">只提醒订阅后的新发布，可随时在“我的订阅”中取消。</p>
      </>}
    </>}
  </main>
}

import { useEffect, useState } from 'react'
import MusicDialog from './MusicDialog'

const expiryLabel = value => {
  if (!value) return '长期有效，随时可取消'
  const date = new Date(value.replace(' ', 'T') + '+08:00')
  if (Number.isNaN(date.getTime())) return `有效至 ${value}`
  return `有效至 ${date.toLocaleString('zh-CN', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })}`
}

export function SubscriptionControl({
  id, onDuty, subscription, compact = false, code = null, label = null, duration = '30d',
}) {
  const state = subscription.states?.[id]
  const pending = subscription.pending[id]
  const published = state ? Boolean(state.published_at) : onDuty
  if (published && !state?.subscribed && !pending) return null
  const who = code ? `工号 ${code}` : (label || `档案 #${id}`)
  return <button type="button" className={`staff-replace delivery-subscribe${compact ? ' delivery-subscribe-compact' : ''}${state?.subscribed ? ' is-subscribed' : ''}`}
    aria-label={`${state?.subscribed ? '取消订阅' : '订阅出勤'} · ${who}`}
    disabled={subscription.busy !== null || subscription.refreshing || subscription.corrupt || !subscription.authenticated ||
      !state || (!pending && !state.subscribed && (!state.available || published))}
    onClick={() => subscription.toggle(id, { code, label: who, duration })}>
    {subscription.busy === id ? '保存中…' : pending ? (compact ? '待核对' : '结果待核对 · 原编号重试') :
      state?.subscribed ? (compact ? '取消订阅' : '取消订阅') : (compact ? '订阅出勤' : '订阅出勤提醒')}
  </button>
}

export function SubscriptionFeedback({ subscription }) {
  const [visibleNotice, setVisibleNotice] = useState('')
  useEffect(() => {
    setVisibleNotice(subscription.notice || '')
    if (!subscription.notice) return undefined
    const timer = setTimeout(() => setVisibleNotice(''), 3200)
    return () => clearTimeout(timer)
  }, [subscription.notice])
  return <>
    {subscription.error && <div role="alert" className="membership-error">
      {subscription.error}<button type="button" disabled={subscription.busy !== null || subscription.refreshing}
        onClick={subscription.refresh}>重新核对订阅</button>
    </div>}
    {visibleNotice && <p role="status" className="subscription-toast">{visibleNotice}</p>}
  </>
}

export function SubscriptionDetails({ state, duration, setDuration }) {
  const subscribed = Boolean(state?.subscribed)
  return <div className="mingxi-subscription-details">
    <div>
      <strong>出勤订阅</strong>
      <span>{subscribed ? expiryLabel(state.expires_at) : '新出勤发布时提醒一次'}</span>
    </div>
    <ul>
      <li>白班、晚班各最多提醒一次，只提醒订阅后的新发布。</li>
      <li>发布记录不代表实时在岗，具体请联系确认。</li>
      <li>可随时取消；到期后不会自动续订。</li>
    </ul>
    {!subscribed && <label>
      订阅期限
      <select value={duration} onChange={event => setDuration(event.target.value)}>
        <option value="shift">仅本班次</option>
        <option value="7d">7 天</option>
        <option value="30d">30 天</option>
        <option value="forever">长期有效</option>
      </select>
    </label>}
  </div>
}

export function SubscribedManager({ members, subscription, onOpen }) {
  const [open, setOpen] = useState(false)
  const items = (members || []).filter(member => subscription.states?.[member.id]?.subscribed)
  return <>
    <button
      type="button"
      className={`mingxi-subscribed-open${items.length ? ' has-items' : ''}`}
      aria-label={`查看已订阅人员，当前 ${items.length} 人`}
      onClick={() => setOpen(true)}
    >
      已订阅 <span>{items.length}</span>
    </button>
    {open && <MusicDialog
      title={`我的订阅 · ${items.length}`}
      className="subscription-manager-sheet"
      scrollBody
      onClose={() => setOpen(false)}
    >
      <p className="subscription-manager-summary">只提醒订阅后的新出勤发布，发布不代表实时在岗。</p>
      {!items.length && <div className="subscription-manager-empty">
        <strong>还没有订阅人员</strong>
        <p>打开人员详情，选择提醒期限后即可订阅。</p>
      </div>}
      <div className="subscription-manager-list">
        {items.map(member => (
          <article key={member.id} className="subscription-manager-item">
            <button type="button" className="subscription-manager-person"
              onClick={() => { setOpen(false); onOpen?.(member) }}>
              <strong>工号 {member.code}</strong>
              <span>{subscription.states[member.id].published_at ? '本班已发布' : '等待新出勤发布'}</span>
              <small>{expiryLabel(subscription.states[member.id].expires_at)}</small>
            </button>
            <button
              type="button"
              className="subscription-manager-cancel"
              disabled={subscription.busy !== null || subscription.refreshing || !subscription.authenticated}
              onClick={() => subscription.toggle(member.id, { code: member.code })}
            >
              {subscription.busy === member.id ? '保存中…' : '取消订阅'}
            </button>
          </article>
        ))}
      </div>
    </MusicDialog>}
  </>
}

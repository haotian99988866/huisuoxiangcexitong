export function SubscriptionControl({ id, onDuty, subscription, compact = false, code = null, label = null }) {
  const state = subscription.states?.[id]
  const pending = subscription.pending[id]
  const published = state ? Boolean(state.published_at) : onDuty
  if (published && !state?.subscribed && !pending) return null
  const who = code ? `工号 ${code}` : (label || `档案 #${id}`)
  return <button type="button" className={`staff-replace delivery-subscribe${compact ? ' delivery-subscribe-compact' : ''}${state?.subscribed ? ' is-subscribed' : ''}`}
    aria-label={`${state?.subscribed ? '取消订阅' : '订阅出勤'} · ${who}`}
    disabled={subscription.busy !== null || subscription.refreshing || subscription.corrupt || !subscription.authenticated ||
      !state || (!pending && !state.subscribed && (!state.available || published))}
    onClick={() => subscription.toggle(id, { code, label: who })}>
    {subscription.busy === id ? '保存中…' : pending ? (compact ? '待核对' : '结果待核对 · 原编号重试') :
      state?.subscribed ? (compact ? '取消订阅' : '取消订阅') : (compact ? '订阅出勤' : '订阅出勤提醒')}
  </button>
}

export function SubscriptionFeedback({ subscription }) {
  return <>
    {subscription.error && <div role="alert" className="membership-error">
      {subscription.error}<button type="button" disabled={subscription.busy !== null || subscription.refreshing}
        onClick={subscription.refresh}>重新核对订阅</button>
    </div>}
    {subscription.notice && <p role="status" className="membership-notice">{subscription.notice}</p>}
  </>
}

/** 已订阅清单：看清订了谁，一键取消 */
export function SubscribedList({ members, subscription }) {
  const items = (members || []).filter(member => subscription.states?.[member.id]?.subscribed)
  if (!items.length) return null
  return (
    <div className="mingxi-subscribed-bar" aria-label="已订阅技师">
      <p className="mingxi-subscribed-title">已订阅 {items.length} 人 · 点右侧可取消</p>
      <div className="mingxi-subscribed-chips">
        {items.map(member => (
          <button
            key={member.id}
            type="button"
            className="mingxi-subscribed-chip"
            disabled={subscription.busy !== null || subscription.refreshing || !subscription.authenticated}
            onClick={() => subscription.toggle(member.id, { code: member.code })}
          >
            <span>工号 {member.code}</span>
            <span>{subscription.busy === member.id ? '…' : '取消'}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

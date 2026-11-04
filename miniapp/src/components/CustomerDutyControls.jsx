export function SubscriptionControl({ id, onDuty, subscription }) {
  const state = subscription.states?.[id]
  const pending = subscription.pending[id]
  const published = state ? Boolean(state.published_at) : onDuty
  if (published && !state?.subscribed && !pending) return null
  return <button type="button" className="staff-replace delivery-subscribe"
    aria-label={`${state?.subscribed ? '取消订阅' : '订阅出勤'} · 档案 #${id}`}
    disabled={subscription.busy !== null || subscription.refreshing || subscription.corrupt || !subscription.authenticated ||
      !state || (!pending && !state.subscribed && (!state.available || published))}
    onClick={() => subscription.toggle(id)}>
    {subscription.busy === id ? '保存中…' : pending ? '结果待核对 · 原编号重试' :
      state?.subscribed ? '已订阅 · 取消订阅' : '订阅出勤提醒'}
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

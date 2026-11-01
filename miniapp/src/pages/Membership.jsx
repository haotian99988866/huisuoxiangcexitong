import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import AccountAccess from '../components/AccountAccess'
import MusicDialog from '../components/MusicDialog'
import { bindBackButton, hasTelegramBackButton } from '../utils/twa'
import { newMemberOrderId, requestMember } from '../utils/membership'
import { money, validPurchaseAttempt } from '../utils/commerce'
import useMemberAccount from '../utils/useMemberAccount'
import { useModuleScroll } from '../utils/moduleState'
import './Membership.css'

const pendingKey = userId => `member-purchase-${userId}`
const draftKey = userId => `member-draft-${userId}`

export default function Membership() {
  const navigate = useNavigate()
  const { account, orders, catalog, loading, error, refresh, reportFailure } = useMemberAccount({ withOrders: true, withCatalog: true })
  const [username, setUsername] = useState('')
  const [months, setMonths] = useState(3)
  const [draftUser, setDraftUser] = useState(null)
  const [pending, setPending] = useState(null)
  const [busy, setBusy] = useState(false)
  const [localError, setLocalError] = useState('')
  const [recoveryError, setRecoveryError] = useState(false)
  const [confirmationOpen, setConfirmationOpen] = useState(false)
  const submitting = useRef(false)
  const selected = catalog?.plans.find(plan => plan.months === months)
  const insufficient = Boolean(selected && account && Number(account.balance) < Number(selected.price_usd))
  const attention = orders.find(order => order.status === 'delivery_unknown')
  useModuleScroll('membership', !loading)

  useEffect(() => bindBackButton(() => navigate('/services', { replace: true })), [navigate])
  useEffect(() => {
    setPending(null)
    setDraftUser(null)
    setConfirmationOpen(false)
    setRecoveryError(false)
    setLocalError('')
    if (!account) return
    try {
      const savedPending = sessionStorage.getItem(pendingKey(account.user_id))
      const savedDraft = sessionStorage.getItem(draftKey(account.user_id))
      const attempt = savedPending ? JSON.parse(savedPending) : null
      if (attempt && !validPurchaseAttempt(attempt)) throw new Error('待确认购买记录损坏，请先在订单页核实，勿重新下单')
      const draft = savedDraft ? JSON.parse(savedDraft) : null
      if (draft && (typeof draft.username !== 'string' || ![3, 6, 12].includes(draft.months))) {
        throw new Error('购买草稿损坏，请联系客服核实后再购买')
      }
      const user = window.Telegram?.WebApp?.initDataUnsafe?.user
      setUsername(attempt?.recipient_username || draft?.username || (user?.id === account.user_id && user.username ? `@${user.username}` : ''))
      setMonths(attempt?.months || draft?.months || 3)
      setPending(attempt)
      setDraftUser(account.user_id)
    } catch (failure) {
      setRecoveryError(true)
      setLocalError(`无法恢复购买记录：${failure.message}`)
    }
  }, [account?.user_id])

  useEffect(() => {
    if (!account || draftUser !== account.user_id || recoveryError) return
    try { sessionStorage.setItem(draftKey(account.user_id), JSON.stringify({ username, months })) }
    catch (failure) {
      setRecoveryError(true)
      setLocalError(`无法保存购买草稿：${failure.message}；请先恢复浏览器存储，避免丢失待确认订单。`)
    }
  }, [account?.user_id, draftUser, username, months, recoveryError])

  function goRecharge() {
    if (!account || recoveryError || pending || submitting.current) return
    try {
      sessionStorage.setItem(draftKey(account.user_id), JSON.stringify({ username, months }))
      navigate('/recharge?from=membership')
    } catch (failure) {
      setRecoveryError(true)
      setLocalError(`无法保存已选套餐：${failure.message}；请恢复浏览器存储后再充值。`)
    }
  }

  async function purchase(event) {
    event.preventDefault()
    if (submitting.current || !account || recoveryError) return
    if (!pending && insufficient) { goRecharge(); return }
    if (!pending && !catalog?.enabled) {
      setLocalError('正式会员余额购买暂未开放，请勿付款。')
      return
    }
    submitting.current = true
    setBusy(true)
    setLocalError('')
    const userId = account.user_id
    try {
      const attempt = pending || { order_id: newMemberOrderId(), months, recipient_username: username.trim() }
      sessionStorage.setItem(pendingKey(userId), JSON.stringify(attempt))
      setPending(attempt)
      const order = await requestMember('member_purchase', attempt)
      sessionStorage.removeItem(pendingKey(userId))
      setPending(null)
      navigate(`/orders/${order.order_id}`)
    } catch (failure) {
      if (failure.status === 400) {
        sessionStorage.removeItem(pendingKey(userId))
        setPending(null)
      }
      setLocalError(`${failure.message}；如结果未确认，请查询或重试同一订单，不要重新下单。`)
      if (failure.status === 401) reportFailure(failure)
    } finally { submitting.current = false; setBusy(false) }
  }

  async function checkPending() {
    if (!pending || submitting.current || !account) return
    submitting.current = true
    setBusy(true)
    setLocalError('')
    try {
      const order = await requestMember('member_order', { order_id: pending.order_id })
      sessionStorage.removeItem(pendingKey(account.user_id))
      setPending(null)
      navigate(`/orders/${order.order_id}`)
    } catch (failure) {
      setLocalError(failure.status === 404
        ? '暂未查到订单，请重试同一订单；系统不会重复扣款。'
        : failure.message)
      if (failure.status === 401) reportFailure(failure)
    } finally { submitting.current = false; setBusy(false) }
  }

  return <main className="account-page membership-page">
    <header className="membership-header">
      {!hasTelegramBackButton && <Link to="/services">← 服务中心</Link>}
      <h1>Telegram会员</h1>
      <p>余额付款 · 昊天人工开通</p>
    </header>
    {(error || localError) && <p className="membership-error" role="alert">{localError || error}</p>}
    {!account ? <AccountAccess loading={loading} /> : <>
      {attention && <p className="membership-notice" role="status">
        你有一笔开通结果待核实的订单，请勿重复购买。<Link to={`/orders/${attention.order_id}`}>查看订单 →</Link>
      </p>}
      <section className="membership-panel">
          <div className="membership-row"><h2>选择时长</h2><span className="membership-tag">PREMIUM</span></div>
          {!catalog && !error && <p role="status">正在读取套餐…</p>}
          <div className="membership-plans">
            {catalog?.plans.map(plan => <button key={plan.months} type="button"
              disabled={busy || Boolean(pending) || recoveryError} aria-pressed={months === plan.months}
              onClick={() => { setMonths(plan.months); setLocalError(''); setConfirmationOpen(true) }}>
              <strong>{plan.months}个月</strong><span>${money(plan.price_usd)}</span>
            </button>)}
          </div>
          {catalog && !catalog.enabled && <p role="status">会员购买暂未开放，套餐仅供查看。</p>}
      </section>
      {pending && <p className="membership-notice" role="status">有一笔购买结果待确认，请勿重新下单。
        <button type="button" disabled={busy} onClick={() => setConfirmationOpen(true)}>核实这笔订单</button></p>}
      {recoveryError && <Link to="/orders">先查询订单并联系客服 →</Link>}
      <Link to="/orders">我的订单 →</Link>
      {confirmationOpen && <MusicDialog className="account-sheet" title="确认会员套餐" onClose={() => setConfirmationOpen(false)}>
        {(error || localError) && <p className="membership-error" role="alert">{localError || error}</p>}
        <form onSubmit={purchase}>
          <h3>Telegram Premium · {months}个月</h3>
          <p>应付：{selected ? `$${money(selected.price_usd)} USD` : '套餐暂不可用'}</p>
          <p>可用余额：${money(account.balance)} USD</p>
          <label>收件人 Telegram 用户名
            <input required placeholder="@username" pattern="@?[A-Za-z0-9_]{5,32}" maxLength={33}
              autoCapitalize="none" autoCorrect="off" value={username} disabled={busy || Boolean(pending) || recoveryError}
              onChange={event => setUsername(event.target.value)} />
          </label>
          <p>收件人先向机器人发送 /start，无需密码。</p>
          {catalog && !catalog.enabled && <p role="status">正式会员购买暂未开放，不会扣款开通。</p>}
          {pending ? <div role="status">
            <p className="membership-reference">待确认订单：{pending.order_id}。重试沿用同一订单，请勿重新下单。</p>
            <button type="button" disabled={busy} onClick={checkPending}>查询这笔订单</button>
          </div> : insufficient ? <p>余额不足，还差 ${money(Number(selected.price_usd) - Number(account.balance))} USD。充值需人工核款，到账后请返回确认购买。</p> : null}
          <p>确认即扣余额，由昊天人工开通。</p>
          <details className="account-details"><summary>开通与退款规则</summary><p>请核对用户名，已开通通常无法撤回。明确未开通时由昊天确认退回站内余额；结果不明先核实，请勿重复购买。</p></details>
          <div className="membership-actions">
            <button type="button" disabled={busy} onClick={() => refresh().catch(reportFailure)}>刷新余额</button>
            {!pending && insufficient ? <button type="button" className="membership-primary" disabled={busy || recoveryError} onClick={goRecharge}>余额不足，去充值</button>
              : !pending && selected && catalog && !catalog.enabled ? <button type="button" className="membership-primary"
                disabled={busy || recoveryError} onClick={() => setLocalError('会员购买暂未开放：余额已到账，但当前不能开通会员，不会扣款。')}>
                购买暂未开放
              </button> : <button type="submit" className="membership-primary" disabled={busy || recoveryError || (!pending && (!catalog?.enabled || !selected))}>
                {busy ? '正在处理…' : pending ? '重试同一订单' : '确认余额付款'}
              </button>}
          </div>
        </form>
      </MusicDialog>}
    </>}
    {!account && !hasTelegramBackButton && <Link to="/services">返回服务中心</Link>}
  </main>
}

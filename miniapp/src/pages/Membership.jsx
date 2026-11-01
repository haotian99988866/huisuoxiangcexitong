import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import AccountAccess from '../components/AccountAccess'
import BalanceDeposit from '../components/BalanceDeposit'
import MusicDialog from '../components/MusicDialog'
import { bindBackButton } from '../utils/twa'
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
  const [depositOpen, setDepositOpen] = useState(false)
  const submitting = useRef(false)
  const selected = catalog?.plans.find(plan => plan.months === months)
  const insufficient = Boolean(selected && account && Number(account.balance) < Number(selected.price_usd))
  const attention = orders.find(order => order.status === 'delivery_unknown')
  useModuleScroll('membership', !loading)

  useEffect(() => bindBackButton(() => navigate('/services', { replace: true })), [navigate])
  useEffect(() => {
    setPending(null)
    setDraftUser(null)
    setDepositOpen(false)
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

  async function purchase(event) {
    event.preventDefault()
    if (submitting.current || !account || recoveryError) return
    if (!pending && insufficient) { setDepositOpen(true); return }
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
      <Link to="/services">← 服务中心</Link>
      <h1>Telegram会员</h1>
      <p>官方赠送 Premium · 无需提供密码或验证码</p>
    </header>
    {(error || localError) && <p className="membership-error" role="alert">{localError || error}</p>}
    {!account ? <AccountAccess loading={loading} /> : <>
      {attention && <p className="membership-notice" role="status">
        你有一笔开通结果待核实的订单，请勿重复购买。<Link to={`/orders/${attention.order_id}`}>查看订单 →</Link>
      </p>}
      <section className="membership-panel">
        <h2>给谁开通</h2>
        <p>收件人需先私聊本机器人发送 /start，并拥有有效用户名。成功赠送后通常无法撤回。</p>
        <form id="membership-purchase" onSubmit={purchase}>
          <label>收件人 Telegram 用户名
            <input required placeholder="@username" pattern="@?[A-Za-z0-9_]{5,32}" maxLength={33}
              autoCapitalize="none" autoCorrect="off" value={username} disabled={busy || Boolean(pending) || recoveryError}
              onChange={event => setUsername(event.target.value)} />
          </label>
          <h2>选择套餐</h2>
          {!catalog && !error && <p role="status">正在读取套餐…</p>}
          <div className="membership-plans">
            {catalog?.plans.map(plan => <button key={plan.months} type="button"
              disabled={busy || Boolean(pending) || recoveryError} aria-pressed={months === plan.months}
              onClick={() => setMonths(plan.months)}>
              <strong>{plan.months}个月</strong><span>${money(plan.price_usd)}</span>
            </button>)}
          </div>
          <p>大文件上传、更快下载及更多会员功能，以 Telegram 当前权益为准。</p>
        </form>
      </section>
      <section className="membership-panel">
        <div className="membership-row"><h2>确认支付</h2><Link to="/orders">我的订单 →</Link></div>
        <p>可用余额：${money(account.balance)} USD · 待核款金额不计入余额</p>
        {selected && <p>收件人：{username || '请填写用户名'} · {months}个月 · 应付 ${money(selected.price_usd)} USD</p>}
        {catalog && !catalog.enabled && <p role="status">正式会员余额购买暂未开放。已提交订单仍会继续处理。</p>}
        {insufficient && !pending && <p>还差 ${money(Number(selected.price_usd) - Number(account.balance))} USD。充值核款到账后仍需你确认购买，不会自动扣款。</p>}
        <div className="membership-actions">
          <button type="button" disabled={busy} onClick={() => setDepositOpen(true)}>充值账户余额</button>
          <button type="button" disabled={busy} onClick={() => refresh().catch(reportFailure)}>刷新余额</button>
        </div>
        {pending && <div role="status">
          <p className="membership-reference">待确认订单：{pending.order_id}。收件人和套餐已锁定，重试沿用同一订单，不会重复扣款。</p>
          <button type="button" disabled={busy} onClick={checkPending}>查询这笔订单</button>
        </div>}
        {recoveryError && <Link to="/orders">先查询订单并联系客服 →</Link>}
      </section>
      <section className="membership-panel">
        <h2>购买与售后说明</h2>
        <p>确认购买后扣除站内余额，再由商家机器人支付官方赠送费用。开通时间受库存、Telegram 服务及核实情况影响，不承诺秒到账。</p>
        <p>明确未开通的失败订单退回站内余额，不自动退回 ABA 或链上钱包。结果不明先人工核实，请勿重复购买。</p>
        <a href="https://t.me/haotian99988866" target="_blank" rel="noreferrer">联系客服 · 请提供订单编号</a>
      </section>
      <div className="membership-checkout-bar">
        <div><small>{months}个月 · 应付</small><strong>{selected ? `$${money(selected.price_usd)}` : '—'}</strong></div>
        <button type="submit" form="membership-purchase"
          disabled={busy || recoveryError || (!pending && (!catalog?.enabled || !selected))}>
          {busy ? '正在处理…' : pending ? '重试同一订单' : insufficient ? '余额不足，去充值' : '确认余额付款'}
        </button>
      </div>
      {depositOpen && <MusicDialog title="充值账户余额" onClose={() => setDepositOpen(false)}>
        <section className="membership-panel account-deposit-dialog">
          <p>可用余额：${money(account.balance)} USD</p>
          <BalanceDeposit account={account} onRefresh={refresh} onFailure={reportFailure} />
          <Link to="/wallet-history?view=deposits">查看充值核款记录 →</Link>
          {selected && !insufficient && <p className="membership-notice" role="status">余额已足够。关闭此面板后核对套餐，再确认购买；不会自动扣款。</p>}
          <button type="button" onClick={() => setDepositOpen(false)}>保留套餐，返回购买</button>
        </section>
      </MusicDialog>}
    </>}
    {!account && <Link to="/services">返回服务中心</Link>}
  </main>
}

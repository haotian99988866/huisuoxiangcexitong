import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import AccountAccess from '../components/AccountAccess'
import AccountHeader from '../components/AccountHeader'
import MusicDialog from '../components/MusicDialog'
import { bindBackButton, hasTelegramBackButton } from '../utils/twa'
import { newMemberOrderId, requestMember } from '../utils/membership'
import { money, normalizePhone, phoneAmountCents, validPhoneAttempt } from '../utils/commerce'
import useMemberAccount from '../utils/useMemberAccount'
import { useModuleScroll } from '../utils/moduleState'
import './Membership.css'

const pendingKey = userId => `phone-purchase-${userId}`
const draftKey = userId => `phone-draft-${userId}`

export default function PhoneTopup() {
  const navigate = useNavigate()
  const { account, orders, catalog, loading, error, reportFailure } = useMemberAccount({ withOrders: true, withCatalog: true })
  const [phone, setPhone] = useState('')
  const [amount, setAmount] = useState('5')
  const [draftUser, setDraftUser] = useState(null)
  const [pending, setPending] = useState(null)
  const [busy, setBusy] = useState(false)
  const [localError, setLocalError] = useState('')
  const [recoveryError, setRecoveryError] = useState(false)
  const [confirmationOpen, setConfirmationOpen] = useState(false)
  const submitting = useRef(false)
  const cents = phoneAmountCents(amount)
  const fee = catalog?.phone ? Number(catalog.phone.fee_usd) : null
  const total = cents === null || fee === null ? null : cents / 100 + fee
  const insufficient = account && total !== null && Number(account.balance) < total
  const ready = account && draftUser === account.user_id && !recoveryError
  const attention = orders.find(order => order.product_kind === 'phone' && order.status === 'delivery_unknown')
  useModuleScroll('phone-topup', !loading)
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
      const draft = savedDraft ? JSON.parse(savedDraft) : null
      if (attempt && !validPhoneAttempt(attempt)) throw new Error('待确认订单损坏，请先查询订单并联系客服')
      if (draft && (typeof draft.phone !== 'string' || typeof draft.amount !== 'string')) {
        throw new Error('充值草稿损坏，请联系客服核实')
      }
      setPhone(attempt?.phone || draft?.phone || '')
      setAmount(attempt?.amount || draft?.amount || '5')
      setPending(attempt)
      setDraftUser(account.user_id)
    } catch (failure) {
      setRecoveryError(true)
      setLocalError(`无法恢复充值记录：${failure.message}；请先核实订单，勿重新下单。`)
    }
  }, [account?.user_id])

  useEffect(() => {
    if (!ready) return
    try { sessionStorage.setItem(draftKey(account.user_id), JSON.stringify({ phone, amount })) }
    catch (failure) {
      setRecoveryError(true)
      setLocalError(`无法保存充值草稿：${failure.message}；请恢复浏览器存储后再操作。`)
    }
  }, [account?.user_id, draftUser, recoveryError, phone, amount])

  function goRecharge() {
    if (!ready || pending || submitting.current) return
    try {
      sessionStorage.setItem(draftKey(account.user_id), JSON.stringify({ phone, amount }))
      navigate('/recharge?from=phone-topup')
    } catch (failure) {
      setRecoveryError(true)
      setLocalError(`无法保存手机号与金额：${failure.message}`)
    }
  }

  function review(event) {
    event.preventDefault()
    if (!ready || submitting.current) return
    if (!normalizePhone(phone)) { setLocalError('请输入 0 开头或 +855 格式的柬埔寨手机号。'); return }
    if (cents === null) { setLocalError('话费金额须为 $5–$100，最多两位小数。'); return }
    setLocalError('')
    setConfirmationOpen(true)
  }

  async function finish(order, userId) {
    try { sessionStorage.removeItem(pendingKey(userId)) }
    catch (failure) {
      setRecoveryError(true)
      throw new Error(`订单已保存，但无法清理本地记录：${failure.message}；请在我的订单查看，勿重新下单。`)
    }
    setPending(null)
    navigate(`/orders/${order.order_id}`)
  }

  async function purchase() {
    if (!ready || submitting.current) return
    if (!pending && insufficient) { goRecharge(); return }
    if (!pending && (!catalog?.phone?.enabled || !normalizePhone(phone) || cents === null)) {
      setLocalError('请检查手机号、金额及服务开放状态。')
      return
    }
    submitting.current = true
    setBusy(true)
    setLocalError('')
    const userId = account.user_id
    try {
      const attempt = pending || { order_id: newMemberOrderId(), phone: normalizePhone(phone), amount }
      try { sessionStorage.setItem(pendingKey(userId), JSON.stringify(attempt)) }
      catch (failure) {
        setRecoveryError(true)
        throw new Error(`无法保存待确认订单：${failure.message}；本次未发送扣款请求。`)
      }
      setPending(attempt)
      const order = await requestMember('phone_purchase', attempt)
      await finish(order, userId)
    } catch (failure) {
      if (failure.status === 400) {
        try { sessionStorage.removeItem(pendingKey(userId)); setPending(null) }
        catch (storageFailure) {
          setRecoveryError(true)
          setLocalError(`无法清理待确认订单：${storageFailure.message}；请先查询我的订单。`)
        }
      }
      setLocalError(previous => previous || `${failure.message}；结果未确认时请查询或重试同一订单，勿重新下单。`)
      if (failure.status === 401) reportFailure(failure)
    } finally { submitting.current = false; setBusy(false) }
  }

  async function checkPending() {
    if (!ready || !pending || submitting.current) return
    submitting.current = true
    setBusy(true)
    setLocalError('')
    try {
      await finish(await requestMember('member_order', { order_id: pending.order_id }), account.user_id)
    } catch (failure) {
      setLocalError(failure.status === 404 ? '暂未查到订单，请重试同一订单，不要重新生成订单。' : failure.message)
      if (failure.status === 401) reportFailure(failure)
    } finally { submitting.current = false; setBusy(false) }
  }

  return <main className="account-page membership-page">
    <AccountHeader><header className="membership-header">
      {!hasTelegramBackButton && <Link to="/services">← 服务中心</Link>}
      <h1>柬埔寨话费代充</h1>
    </header></AccountHeader>
    <p className="consumption-intro">余额付款 · 昊天人工代充</p>
    {(error || localError) && <p className="membership-error" role="alert">{localError || error}</p>}
    {!account ? <AccountAccess loading={loading} /> : <>
      {attention && <p className="membership-notice">你有一笔代充结果待核实订单，请勿重复下单。
        <Link to={`/orders/${attention.order_id}`}>查看订单</Link></p>}
      <section className="membership-panel">
        <div className="membership-row"><h2>填写充值信息</h2><span>USD</span></div>
        <form onSubmit={review}>
          <label>柬埔寨手机号<input type="tel" autoComplete="tel" value={phone} required maxLength={24}
            placeholder="0 开头或 +855 手机号" disabled={!ready || busy || Boolean(pending)}
            onChange={event => setPhone(event.target.value)} /></label>
          <label>话费金额<input type="number" inputMode="decimal" min="5" max="100" step="0.01"
            required value={amount} disabled={!ready || busy || Boolean(pending)}
            onChange={event => setAmount(event.target.value)} /></label>
          <p>话费 $5–$100 · 每单手续费 $1</p>
          <div className="membership-row"><span>合计</span><strong>{total === null ? '—' : `$${money(total)}`}</strong></div>
          <p>可用余额：${money(account.balance)}</p>
          {!catalog && !error && <p role="status">正在读取服务状态…</p>}
          {catalog && !catalog.phone?.enabled && <p role="status">话费代充暂未开放。</p>}
          {pending ? <div className="membership-actions">
            <p role="status">有一笔扣款结果待确认，请勿重新下单。</p>
            <button type="button" disabled={!ready || busy} onClick={checkPending}>查询这笔订单</button>
            <button type="button" disabled={!ready || busy} onClick={() => setConfirmationOpen(true)}>重试同一订单</button>
          </div> : <button className="membership-primary" type="submit" disabled={!ready || busy || !catalog?.phone?.enabled}>
            核对充值信息
          </button>}
        </form>
      </section>
      <Link to="/orders">我的订单</Link>
      {recoveryError && <p>请先查询订单并联系客服，避免重复付款。</p>}
      {confirmationOpen && <MusicDialog className="account-sheet" title="确认话费代充" onClose={() => setConfirmationOpen(false)}>
        {(error || localError) && <p className="membership-error" role="alert">{localError || error}</p>}
        <h3>{normalizePhone(phone)}</h3>
        <p>话费 ${cents === null ? '—' : money(cents / 100)} + 手续费 ${fee === null ? '—' : money(fee)}</p>
        <p>合计：{total === null ? '—' : `$${money(total)}`}</p>
        <p>请仔细核对手机号。余额扣款后人工代充，已完成不可退款；未代充成功则话费及手续费全额退回站内余额。</p>
        <button type="button" className="membership-primary" disabled={!ready || busy || (!pending && !catalog?.phone?.enabled)}
          onClick={purchase}>{busy ? '处理中…' : pending ? '重试同一订单' : insufficient ? '余额不足，先充值' : '确认扣款并申请代充'}</button>
        {pending && <button type="button" disabled={!ready || busy} onClick={checkPending}>查询这笔订单</button>}
      </MusicDialog>}
    </>}
  </main>
}

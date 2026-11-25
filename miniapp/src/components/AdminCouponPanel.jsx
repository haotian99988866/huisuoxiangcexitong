import { useEffect, useState } from 'react'
import { requestAdmin } from '../utils/admin'
import { consumptionDate } from '../utils/customerRecords'
import { newMemberOrderId } from '../utils/membership'
import './CouponPanel.css'

export default function AdminCouponPanel({ ownerId, records }) {
  const [data, setData] = useState(null)
  const [page, setPage] = useState(1)
  const [voucherPage, setVoucherPage] = useState(1)
  const [reload, setReload] = useState(0)
  const [operation, setOperation] = useState(null)
  const [confirmation, setConfirmation] = useState(null)
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const pendingKey = `coupon-pending-${window.Telegram?.WebApp?.initDataUnsafe?.user?.id || 'admin'}-${ownerId}`

  useEffect(() => {
    const pending = sessionStorage.getItem(pendingKey)
    if (!pending) return
    try {
      const saved = JSON.parse(pending)
      if (!['admin_coupon_redeem', 'admin_coupon_reverse', 'admin_coupon_review'].includes(saved.action) ||
          !/^[0-9a-f]{32}$/.test(saved.fields?.request_id)) throw new Error('暂存操作无效')
      setConfirmation(saved)
      setUncertain(true)
      setNotice('已恢复未确认操作，请使用原编号重试并核对记录，不要重新提交。')
    } catch (failure) {
      console.error('[Restore coupon operation]', failure)
      setError('暂存操作损坏，请联系管理员核对记录，勿重新操作。')
      setUncertain(true)
    }
  }, [pendingKey])

  useEffect(() => {
    let active = true
    setError('')
    requestAdmin('admin_coupon_detail', { owner_id: ownerId, page, voucher_page: voucherPage })
      .then(result => { if (active) setData(result) })
      .catch(failure => { if (active) setError(failure.message) })
    return () => { active = false }
  }, [ownerId, page, voucherPage, reload])

  function prepare(event) {
    event.preventDefault()
    setError('')
    if (operation.kind === 'redeem') {
      const record = records.find(item => item.id === operation.consumptionId)
      if (!record) { setError('请选择本页已登记的消费记录。'); return }
      setConfirmation({
        action: 'admin_coupon_redeem',
        fields: { owner_id: ownerId, voucher_id: operation.id, consumption_id: record.id,
          original_amount: operation.original, confirmed: true, request_id: newMemberOrderId() },
        description: `客户 ${ownerId} · ${record.venue_name} · ${record.service} · 原价 $${operation.original} · 已登记实付 $${record.amount} · 抵扣 $20`,
      })
    } else if (operation.kind === 'reverse') {
      setConfirmation({
        action: 'admin_coupon_reverse',
        fields: { voucher_id: operation.id, reason: operation.reason, confirmed: true, request_id: newMemberOrderId() },
        description: '撤销后券暂不可再次使用。请核实实际收款，再将原消费实付恢复至少原价，或作废原消费；完成后券才恢复可用。',
      })
    } else {
      setConfirmation({
        action: 'admin_coupon_review',
        fields: { invitee_id: operation.id, approved: operation.approved, reason: operation.reason, confirmed: true, request_id: newMemberOrderId() },
        description: `客户 ${operation.name} · 人工核实结果：${operation.approved ? '留存合格' : '不合格'} · 依据：${operation.reason}`,
      })
    }
  }

  async function submit() {
    setBusy(true)
    setError('')
    try {
      sessionStorage.setItem(pendingKey, JSON.stringify(confirmation))
      setUncertain(true)
      await requestAdmin(confirmation.action, confirmation.fields)
      sessionStorage.removeItem(pendingKey)
      setUncertain(false)
      setConfirmation(null)
      setOperation(null)
      setNotice('操作已保存，请核对最新状态。')
      setReload(value => value + 1)
    } catch (failure) {
      if ([400, 401, 403, 409].includes(failure.status)) {
        sessionStorage.removeItem(pendingKey)
        setUncertain(false)
        setError(failure.message)
      } else {
        setError(`${failure.message}。结果未确认，请使用原编号重试并核对记录。`)
      }
    } finally { setBusy(false) }
  }

  if (!ownerId) return <p>该客户尚未绑定 Telegram，不能使用个人优惠券。</p>
  return <section className="coupon-admin">
    {error && <p className="admin-error" role="alert">{error}</p>}
    {notice && <p className="admin-notice" role="status">{notice}</p>}
    {!data && !error && <p role="status">正在读取优惠券…</p>}
    {data && <>
      <p>待兑换合格邀请 {data.eligible} 人 · 邀请记录 {data.invite_total} 人</p>
      <h3>代金券</h3>
      {data.vouchers.map(voucher => <article className="coupon-item" key={voucher.id}>
        <strong>$20 · {voucher.status === 'redeemed' ? '已核销' : voucher.status === 'reversal_pending' ? '撤销待核对消费' : voucher.status === 'invalid' ? '已失效' :
          new Date(voucher.expires_at) <= new Date() ? '已过期' : '可使用'}</strong>
        <span>有效至 {consumptionDate(voucher.expires_at)}</span>
        <small>券号 {voucher.id}{voucher.consumption_id && ` · 消费 ${voucher.consumption_id}`}</small>
        {voucher.status === 'reversal_pending' && <small>请核实实际收款后修改或作废关联消费，券才会恢复可用。</small>}
        {voucher.status === 'available' && new Date(voucher.expires_at) > new Date() &&
          <button type="button" disabled={busy || uncertain} onClick={() => { setOperation({ kind: 'redeem', id: voucher.id, consumptionId: '', original: '' }); setConfirmation(null) }}>异常情况：人工补核销</button>}
        {voucher.status === 'redeemed' &&
          <button type="button" disabled={busy || uncertain} onClick={() => { setOperation({ kind: 'reverse', id: voucher.id, reason: '' }); setConfirmation(null) }}>撤销核销</button>}
      </article>)}
      {!data.vouchers.length && <p>暂无代金券。</p>}
      {data.voucher_total > 20 && <div className="coupon-pager">
        <button type="button" disabled={voucherPage <= 1} onClick={() => setVoucherPage(value => value - 1)}>上一页</button>
        <span>{voucherPage} / {Math.ceil(data.voucher_total / 20)}</span>
        <button type="button" disabled={voucherPage * 20 >= data.voucher_total} onClick={() => setVoucherPage(value => value + 1)}>下一页</button>
      </div>}
      <h3>邀请记录</h3>
      {data.invites.map(invite => <article className="coupon-item" key={invite.invitee_id}>
        <strong>{invite.display_name || `ID ${invite.invitee_id}`}</strong>
        <span>{invite.state} · 加入 {consumptionDate(invite.joined_at)}</span>
        {invite.left_at && <span>退出 {consumptionDate(invite.left_at)}</span>}
        {invite.review_reason && <small>{invite.review_reason}</small>}
        {invite.state === 'review' && <button type="button" disabled={busy || uncertain}
          onClick={() => { setOperation({ kind: 'review', id: invite.invitee_id, name: invite.display_name || `ID ${invite.invitee_id}`, reason: '', approved: false }); setConfirmation(null) }}>人工核实</button>}
      </article>)}
      {data.invite_total > 20 && <div className="coupon-pager">
        <button type="button" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button>
        <span>{page} / {Math.ceil(data.invite_total / 20)}</span>
        <button type="button" disabled={page * 20 >= data.invite_total} onClick={() => setPage(value => value + 1)}>下一页</button>
      </div>}
    </>}
    {operation && !confirmation && <form className="coupon-operation" onSubmit={prepare}>
      <h3>{operation.kind === 'redeem' ? '核对优惠券和消费' : operation.kind === 'reverse' ? '撤销核销' : '人工核实留存'}</h3>
      {operation.kind === 'redeem' ? <>
        <label>本页消费记录
          <select required value={operation.consumptionId} onChange={event => setOperation({ ...operation, consumptionId: event.target.value })}>
            <option value="">请选择</option>
            {records.filter(row => row.status === 'valid').map(row => <option key={row.id} value={row.id}>
              {consumptionDate(row.occurred_at)} · {row.venue_name} · {row.service} · 实付 ${row.amount}
            </option>)}
          </select>
        </label>
        <label>消费原价 USD<input required inputMode="decimal" value={operation.original}
          onChange={event => setOperation({ ...operation, original: event.target.value })} /></label>
        <p>先登记折后实付；不含名玺水汇按摩。原价不足 $20 时请人工处理，暂不核销。</p>
      </> : <>
        {operation.kind === 'review' && <label>核实结果<select value={operation.approved ? 'yes' : 'no'}
          onChange={event => setOperation({ ...operation, approved: event.target.value === 'yes' })}>
          <option value="no">不合格</option><option value="yes">留存合格</option></select></label>}
        <label>{operation.kind === 'review' ? '核实依据' : '撤销原因'}
          <textarea required maxLength={255} value={operation.reason}
            onChange={event => setOperation({ ...operation, reason: event.target.value })} /></label>
      </>}
      <button type="submit" disabled={busy}>下一步：核对</button>
      <button type="button" disabled={busy} onClick={() => setOperation(null)}>取消</button>
    </form>}
    {confirmation && <div className="coupon-operation">
      <p>{confirmation.description}</p>
      <button type="button" disabled={busy} onClick={submit}>{uncertain ? '使用原编号重试' : '确认操作'}</button>
      <button type="button" disabled={busy || uncertain} onClick={() => setConfirmation(null)}>返回修改</button>
    </div>}
  </section>
}

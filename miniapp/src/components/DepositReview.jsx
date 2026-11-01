import { useEffect, useRef, useState } from 'react'
import { requestAdmin } from '../utils/admin'
import { money } from '../utils/commerce'
import MusicDialog from './MusicDialog'

export default function DepositReview({ onReviewed }) {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [selected, setSelected] = useState(null)
  const [image, setImage] = useState('')
  const [imageLoading, setImageLoading] = useState(false)
  const [imageError, setImageError] = useState('')
  const [imageAttempt, setImageAttempt] = useState(0)
  const [amount, setAmount] = useState('')
  const [reference, setReference] = useState('')
  const [decision, setDecision] = useState(null)
  const [verified, setVerified] = useState(false)
  const lock = useRef(false)
  const mounted = useRef(true)
  const generation = useRef(0)

  async function refresh() {
    const id = ++generation.current
    setLoading(true)
    setError('')
    try {
      const result = await requestAdmin('admin_deposits')
      if (mounted.current && id === generation.current) setRows(result.deposits)
    } catch (failure) {
      if (mounted.current && id === generation.current) {
        setRows([])
        setError(failure.message)
      }
    } finally { if (mounted.current && id === generation.current) setLoading(false) }
  }
  useEffect(() => {
    mounted.current = true
    refresh()
    return () => { mounted.current = false; generation.current++ }
  }, [])
  useEffect(() => {
    let active = true
    setImage('')
    setImageError('')
    setImageLoading(Boolean(selected?.has_proof))
    if (selected?.has_proof) requestAdmin('admin_deposit_proof', { deposit_id: selected.id })
      .then(result => { if (active) setImage(result.image) })
      .catch(failure => { if (active) setImageError(failure.message) })
      .finally(() => { if (active) setImageLoading(false) })
    return () => { active = false }
  }, [selected?.id, imageAttempt])

  function open(row) {
    setSelected(row)
    setAmount('')
    setReference(row.reference || '')
    setDecision(null)
    setVerified(false)
    setError('')
    setNotice('')
  }
  async function review() {
    if (lock.current || !selected || !decision) return
    if (decision === 'approve' && !verified) {
      setError('请复核实际到账金额与真实交易编号，并勾选确认')
      return
    }
    lock.current = true
    setBusy(true)
    setError('')
    try {
      await requestAdmin('admin_deposit_review', {
        deposit_id: selected.id, decision, amount, reference: reference.trim(), confirmed: true,
      })
      setNotice(`申请 #${selected.id} 已${decision === 'approve' ? '核实入账' : '拒绝，余额未变动'}。`)
      setSelected(null)
      setDecision(null)
      setVerified(false)
      await refresh()
      await onReviewed()
    } catch (failure) {
      setDecision(null)
      setVerified(false)
      setError(`${failure.message}；请先刷新核对申请及资金记录，不要重复入账。`)
    } finally { lock.current = false; setBusy(false) }
  }
  return <section className="admin-card" aria-label="充值审核">
    <div className="admin-header"><h2>充值审核</h2>
      <button type="button" disabled={loading || busy} onClick={refresh}>{loading ? '读取中…' : '刷新申请'}</button></div>
    <p className="admin-hint">显示最早 8 笔待核款申请，处理后刷新查看下一批。请独立核实钱包／银行真实到账，不能仅凭截图入账。</p>
    {!selected && error && <p role="alert" className="admin-error">{error}</p>}
    {notice && <p role="status" className="admin-notice">{notice}</p>}
    {!loading && !error && !rows.length && <p>暂无待审核充值。</p>}
    {rows.map(row => <article className="admin-tenant" key={row.id}>
      <div className="admin-header"><h3>申请 #{row.id} · {row.method.toUpperCase()}</h3><strong>${money(row.claimed)}</strong></div>
      <p>用户 {row.user_id} · {row.created_at}</p>
      <p>{row.manual_review ? 'ABA 私下联系后提交金额，需核对私聊及银行记录' : row.has_proof ? '已上传付款凭证' : '已提交交易编号'}</p>
      <button type="button" disabled={busy || loading} onClick={() => open(row)}>查看并审核</button>
    </article>)}
    {selected && <MusicDialog title={`审核充值申请 #${selected.id}`} onClose={() => { if (!lock.current) { setSelected(null); setDecision(null) } }}>
      {error && <p role="alert" className="admin-error">{error}</p>}
      <p>用户 {selected.user_id} · {selected.method.toUpperCase()} · 申报 ${money(selected.claimed)} USD</p>
      <p style={{ overflowWrap: 'anywhere' }}>收款：{selected.address}</p>
      <p>{selected.created_at}</p>
      {selected.manual_review && <p>此申请没有付款截图或客户交易编号。请按用户编号核对私聊记录，并在银行独立核实美元收款账户、真实交易编号及到账金额；不能仅凭私聊确认入账。</p>}
      {imageLoading && <p role="status">正在读取付款凭证…</p>}
      {imageError && <p role="alert">{imageError}<button type="button" onClick={() => setImageAttempt(value => value + 1)}>重试凭证</button></p>}
      {image && <img src={image} alt={`申请 #${selected.id} 付款凭证`}
        onError={() => { setImage(''); setImageError('付款凭证显示失败，请重新加载后核对') }}
        style={{ display: 'block', maxWidth: '100%', margin: '12px auto' }} />}
      {decision ? <div className="admin-confirm">
        <h3>{decision === 'approve' ? '确认已核实真实到账' : '确认拒绝申请'}</h3>
        <p>申请 #{selected.id} · 用户 {selected.user_id}</p>
        {decision === 'approve' ? <><p>客户申报：${money(selected.claimed)} USD</p>
          <p><strong>本次按复核金额入账：${money(amount)} USD</strong></p>
          {Number(amount) !== Number(selected.claimed) && <p role="status">复核金额与客户申报不同，以复核金额为准。</p>}
          <p style={{ overflowWrap: 'anywhere' }}>真实交易：{reference}</p>
          <p>请确认币种、收款地址、交易确认和实际到账金额，入账后不能通过拒绝撤销。</p>
          <label className="admin-check"><input type="checkbox" checked={verified} disabled={busy} onChange={event => setVerified(event.target.checked)} />
            我已复核该交易编号对应的真实到账金额，以此金额入账</label></> : <p>拒绝不会增加余额。请确认不是已到账但尚未核实的付款。</p>}
        <div className="admin-actions"><button type="button" disabled={busy || (decision === 'approve' && !verified)} onClick={review}>{busy ? '处理中…' : decision === 'approve' ? '确认入账' : '确认拒绝'}</button>
          <button type="button" disabled={busy} onClick={() => { setDecision(null); setVerified(false) }}>返回核对</button></div>
      </div> : <form onSubmit={event => { event.preventDefault(); setVerified(false); setDecision('approve') }}>
        <fieldset disabled={busy}>
          <label>复核实际到账金额（USD）<input required type="number" min="0.000001" max="1000000" step="0.000001" placeholder="根据钱包／银行真实到账填写" value={amount} onChange={event => setAmount(event.target.value)} /></label>
          <p>客户申报仅供对照；余额与资金流水以你复核填写的实际到账金额为准。</p>
          <label>核实的真实交易编号<input required value={reference} disabled={!selected.has_proof && !selected.manual_review}
            pattern={selected.method === 'usdt' ? '[0-9a-fA-F]{64}' : '[A-Za-z0-9_\\-]{6,100}'}
            maxLength={selected.method === 'usdt' ? 64 : 100} onChange={event => setReference(event.target.value)} /></label>
          <div className="admin-actions"><button type="submit" disabled={selected.has_proof && !image}>核对并入账</button>
            <button type="button" onClick={() => setDecision('reject')}>拒绝申请</button></div>
        </fieldset>
      </form>}
    </MusicDialog>}
  </section>
}

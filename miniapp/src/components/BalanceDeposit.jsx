import { useEffect, useRef, useState } from 'react'
import QRCode from 'qrcode'
import { requestMember, uploadDeposit } from '../utils/membership'

export default function BalanceDeposit({ account, onRefresh, onFailure, onPendingChange }) {
  const [method, setMethod] = useState('usdt')
  const [amount, setAmount] = useState('')
  const [proof, setProof] = useState(null)
  const [qr, setQr] = useState('')
  const [preview, setPreview] = useState('')
  const [copied, setCopied] = useState(false)
  const [submitted, setSubmitted] = useState(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const lock = useRef(false)
  const fileRef = useRef(null)
  const pending = account.pending_deposit || account.deposits?.find(item => item.status === 'pending') || submitted
  const rejected = account.rejected_deposit
  const configured = method === 'usdt' ? account.address : true
  useEffect(() => {
    onPendingChange?.(Boolean(pending))
  }, [Boolean(pending), onPendingChange])
  useEffect(() => {
    let active = true
    setQr('')
    if (!pending && !rejected && method === 'usdt' && account.address) QRCode.toDataURL(account.address, { width: 240, margin: 2 })
      .then(value => { if (active) setQr(value) })
      .catch(failure => { if (active) setError(`收款二维码生成失败：${failure.message}，请复制地址付款`) })
    return () => { active = false }
  }, [method, account.address, Boolean(pending), Boolean(rejected)])
  useEffect(() => {
    const reviewed = submitted && account.deposits?.find(item => item.id === submitted.id && item.status !== 'pending')
    if (reviewed) {
      setSubmitted(null)
      setError('')
      setNotice(reviewed.status === 'approved'
        ? `申请 #${reviewed.id} 已审核入账，可以再次充值。`
        : '')
    }
  }, [account, submitted])
  useEffect(() => {
    if (!proof) { setPreview(''); return }
    const url = URL.createObjectURL(proof)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [proof])

  async function copyAddress() {
    try {
      await navigator.clipboard.writeText(account.address)
      setCopied(true)
    } catch (failure) { setError(`地址复制失败：${failure.message}；请长按下方地址手动复制`) }
  }

  async function submit(event) {
    event.preventDefault()
    if (lock.current) return
    if (pending) { setError('已有申请待审核，审核结束后才能再次充值'); return }
    if (rejected) { setError('申请已被拒绝，当天不能再次提交，柬埔寨时间次日00:00恢复'); return }
    lock.current = true
    setBusy(true)
    setError('')
    setNotice('')
    try {
      if (method === 'usdt' && !proof) throw new Error('请选择付款截图')
      if (!Number.isFinite(Number(amount)) || Number(amount) <= 0 || Number(amount) > 300) throw new Error('每笔充值金额须大于0且不超过300美元')
      const result = method === 'aba'
        ? await requestMember('member_deposit', { method, amount })
        : await uploadDeposit(method, amount, proof)
      setSubmitted({ id: result.id, claimed: amount, status: 'pending' })
      setNotice(`申请 #${result.id} 已提交。管理员核实真实到账后才增加可用余额；到账不会自动购买商品。${result.warning || ''}`)
      setAmount('')
      setProof(null)
      if (fileRef.current) fileRef.current.value = ''
      await onRefresh()
    } catch (failure) {
      setError(`${failure.message}；若提交结果未确认，请先查看充值记录或联系客服，勿重复转账。`)
      onFailure(failure)
    } finally { lock.current = false; setBusy(false) }
  }

  if (pending) return <div className="balance-deposit" role="status">
    <h3>充值申请等待审核</h3>
    <p>申请 #{pending.id} 已提交，申报金额 {pending.claimed} USD。审核结束前暂停再次充值，请勿重复付款。</p>
    <p>批准后恢复充值入口；若被拒绝，当天不能再次提交，柬埔寨时间次日00:00恢复。入账金额以管理员复核结果为准。</p>
    {notice && <p className="membership-notice">{notice}</p>}
    {error && <p className="membership-error" role="alert">{error}</p>}
    <button type="button" disabled={busy} onClick={async () => {
      setBusy(true)
      try { await onRefresh(); setError('') } catch (failure) { setError(failure.message); onFailure(failure) }
      finally { setBusy(false) }
    }}>{busy ? '读取中…' : '刷新审核结果'}</button>
  </div>

  if (rejected) return <div className="balance-deposit" role="status">
    <h3>充值复核申请已拒绝</h3>
    <p>申请 #{rejected.id} 已拒绝，余额未增加。当天暂停 USDT 和 ABA 申请，请勿再次付款。</p>
    <p>柬埔寨时间（UTC+7）{rejected.retry_at.slice(0, 10)} 00:00 恢复申请入口。</p>
    {error && <p className="membership-error" role="alert">{error}</p>}
    <button type="button" disabled={busy} onClick={async () => {
      setBusy(true)
      try { await onRefresh(); setError('') } catch (failure) { setError(failure.message); onFailure(failure) }
      finally { setBusy(false) }
    }}>{busy ? '读取中…' : '刷新复核状态'}</button>
  </div>

  return <div className="balance-deposit">
    <p>{method === 'aba' ? '① 联系昊天确认并付款　② 提交金额　③ 等待复核到账' : '① 付款　② 上传截图　③ 等待核款到账'}</p>
    <p>每笔最多300美元（USDT按1:1），请勿超额付款；有待审核申请时不能再次充值。</p>
    <div className="membership-actions">
      {['usdt', 'aba'].map(value => <button key={value} type="button" aria-pressed={method === value}
        disabled={busy} onClick={() => { setMethod(value); setProof(null); if (fileRef.current) fileRef.current.value = ''; setCopied(false); setError(''); setNotice('') }}>
        {value.toUpperCase()}
      </button>)}
    </div>
    {method === 'usdt' ? account.address ? <>
      <h3>USDT · TRON 主网（TRC20）</h3>
      {qr && <img src={qr} alt="USDT TRC20 收款地址二维码" width="240" height="240" style={{ display: 'block', maxWidth: '100%', height: 'auto', margin: '12px auto' }} />}
      <p>用 imToken 等钱包扫描二维码，或复制地址转账。二维码只包含收款地址，请选择 USDT 和 TRC20 网络，并填写付款金额。</p>
      <p className="membership-reference">{account.address}</p>
      <button type="button" onClick={copyAddress}>{copied ? '地址已复制' : '复制收款地址'}</button>
      <p>只收官方 USDT，勿使用其他网络。实际到账 1 USDT = 1 USD，链上手续费由付款方承担。</p>
    </> : <p role="status">USDT 收款地址未配置，请勿转账。</p>
      : <>
        <h3>ABA · 人工复核充值</h3>
        <p role="status">如需ABA充值请联系昊天</p>
        <p>先私下联系昊天确认美元付款方式，付款后填写金额提交申请，无需上传截图。私聊确认和提交申请都不会增加余额，昊天复核银行真实到账后才入账。</p>
      </>}
    {error && <p className="membership-error" role="alert">{error}</p>}
    {notice && <p className="membership-notice" role="status">{notice}</p>}
    {configured && <form onSubmit={submit}>
      <h3>{method === 'aba' ? '付款后提交复核申请' : '付款后上传凭证'}</h3>
      {method === 'usdt' && <p>上传钱包／银行的转账成功截图，保留金额、收款方和付款时间；遮盖无关个人信息。截图不代表到账，管理员核实后才入账。</p>}
      <label>付款金额（{method === 'usdt' ? 'USDT' : 'USD'}）
        <input required type="number" min="0.000001" max="300" step="0.000001"
          value={amount} disabled={busy} onChange={event => setAmount(event.target.value)} />
      </label>
      {method === 'usdt' && <label>付款截图（JPG / PNG / WebP，最多 5 MB）
        <input ref={fileRef} required type="file" accept="image/jpeg,image/png,image/webp" disabled={busy}
          onChange={event => {
            const file = event.target.files?.[0]
            setProof(null)
            if (!file) return
            if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024) {
              setError('请选择不超过 5 MB 的 JPG、PNG 或 WebP 付款截图')
              event.target.value = ''
              return
            }
            setError('')
            setProof(file)
          }} />
      </label>}
      {method === 'usdt' && preview && <img src={preview} alt="已选择的付款凭证" style={{ display: 'block', maxWidth: '100%', maxHeight: 280, margin: '12px auto' }} />}
      <button className="membership-primary" disabled={busy}>{busy ? '正在提交…' : method === 'aba' ? '提交ABA复核申请' : '提交充值核款申请'}</button>
    </form>}
  </div>
}

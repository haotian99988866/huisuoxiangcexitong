import { useRef, useState } from 'react'
import { requestMember } from '../utils/membership'

export default function BalanceDeposit({ account, onRefresh, onFailure }) {
  const [method, setMethod] = useState('usdt')
  const [amount, setAmount] = useState('')
  const [reference, setReference] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const lock = useRef(false)
  const configured = method === 'usdt' ? account.address : account.aba

  async function submit(event) {
    event.preventDefault()
    if (lock.current) return
    lock.current = true
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await requestMember('member_deposit', { method, amount, reference: reference.trim() })
      setNotice(`申请 #${result.id} 已提交。管理员核实真实到账后才增加可用余额；到账不会自动购买商品。`)
      setAmount('')
      setReference('')
      await onRefresh()
    } catch (failure) {
      setError(`${failure.message}；若提交结果未确认，请先查看充值记录，再使用同一交易编号核实，勿重复转账。`)
      onFailure(failure)
    } finally { lock.current = false; setBusy(false) }
  }

  return <div className="balance-deposit">
    <p>先实际付款，再提交交易编号。充值申请需人工核款，截图不代表到账。</p>
    <div className="membership-actions">
      {['usdt', 'aba'].map(value => <button key={value} type="button" aria-pressed={method === value}
        disabled={busy} onClick={() => { setMethod(value); setReference(''); setError(''); setNotice('') }}>
        {value.toUpperCase()}
      </button>)}
    </div>
    {method === 'usdt' ? account.address ? <>
      <h3>USDT · TRON 主网（TRC20）</h3>
      <p className="membership-reference">{account.address}</p>
      <p>只收官方 USDT，勿使用其他网络。实际到账 1 USDT = 1 USD，链上手续费由付款方承担。</p>
    </> : <p role="status">USDT 收款地址未配置，请勿转账。</p>
      : account.aba ? <>
        <h3>ABA · 美元账户</h3>
        <p className="membership-reference">账号：{account.aba.account_number}<br />收款人：{account.aba.account_name}</p>
        <p>仅支持美元，按实际到账 USD 入账；付款前请核对收款人，转账费用由付款方承担。</p>
      </> : <p role="status">ABA 收款账户未配置，请勿转账。</p>}
    {error && <p className="membership-error" role="alert">{error}</p>}
    {notice && <p className="membership-notice" role="status">{notice}</p>}
    {configured && <form onSubmit={submit}>
      <label>实际到账金额（{method === 'usdt' ? 'USDT' : 'USD'}）
        <input required type="number" min="0.000001" max="1000000" step="0.000001"
          value={amount} disabled={busy} onChange={event => setAmount(event.target.value)} />
      </label>
      <label>{method === 'usdt' ? '交易哈希（TXID）' : 'ABA 交易编号'}
        <input required value={reference} disabled={busy} autoCapitalize="none" autoCorrect="off"
          pattern={method === 'usdt' ? '[0-9a-fA-F]{64}' : '[A-Za-z0-9_\\-]{6,100}'}
          maxLength={method === 'usdt' ? 64 : 100} onChange={event => setReference(event.target.value)} />
      </label>
      <button className="membership-primary" disabled={busy}>{busy ? '正在提交…' : '提交充值核款申请'}</button>
    </form>}
  </div>
}

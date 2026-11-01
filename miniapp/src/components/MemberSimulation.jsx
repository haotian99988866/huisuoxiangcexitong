import { useEffect, useRef, useState } from 'react'
import { newMemberOrderId, requestMember } from '../utils/membership'

const statuses = {
  simulated_success: '模拟成功（未开通真实会员）',
  simulated_refunded: '模拟失败、金额已返还测试余额',
  simulated_unknown: '模拟结果不明、保留测试扣款待核实',
}

export default function MemberSimulation({ userId }) {
  const [account, setAccount] = useState(null)
  const [username, setUsername] = useState('@test_user')
  const [months, setMonths] = useState(3)
  const [scenario, setScenario] = useState('success')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState(null)
  const lock = useRef(false)
  const key = `member-simulation-${userId}`
  const refresh = async () => setAccount(await requestMember('member_test_account'))
  useEffect(() => {
    try {
      const saved = sessionStorage.getItem(key)
      if (saved) setPending(JSON.parse(saved))
    } catch (failure) { setError(`无法恢复模拟请求：${failure.message}`) }
    refresh().catch(failure => setError(failure.message))
  }, [key])
  async function purchase(event) {
    event.preventDefault()
    if (lock.current) return
    lock.current = true
    setBusy(true)
    setError('')
    try {
      const attempt = pending || { order_id: newMemberOrderId(), recipient_username: username, months, scenario }
      sessionStorage.setItem(key, JSON.stringify(attempt))
      setPending(attempt)
      const result = await requestMember('member_test_purchase', attempt)
      sessionStorage.removeItem(key)
      setPending(null)
      setNotice(`${result.order_id}：${statuses[result.status]}`)
      await refresh()
    } catch (failure) {
      if (failure.status === 400) { sessionStorage.removeItem(key); setPending(null) }
      setError(failure.message)
    } finally { lock.current = false; setBusy(false) }
  }
  return <section className="membership-panel">
    <h2>管理员隔离模拟测试</h2>
    <p><strong>隔离模拟 · 不动真实资金或会员</strong></p>
    <details className="account-details"><summary>模拟范围</summary>
      <p>独立测试余额初始 $50，不转账、不扣正式余额、不消耗 Stars、不赠送会员。仅供模拟订单演练，不能代替实际履约验收。</p>
    </details>
    {account && <p className="membership-balance">测试余额 ${Number(account.balance).toFixed(2)}</p>}
    {error && <p role="alert" className="membership-error">{error}</p>}
    {notice && <p role="status" className="membership-notice">{notice}</p>}
    <form onSubmit={purchase}>
      <label>模拟收件人（无需真实账号）
        <input required pattern="@?[A-Za-z0-9_]{5,32}" value={username} disabled={busy || Boolean(pending)}
          onChange={event => setUsername(event.target.value)} />
      </label>
      <label>模拟套餐
        <select value={months} disabled={busy || Boolean(pending)} onChange={event => setMonths(Number(event.target.value))}>
          <option value={3}>3个月 · $15.99</option><option value={6}>6个月 · $19.99</option>
          <option value={12}>12个月 · $33.99</option>
        </select>
      </label>
      <label>模拟结果
        <select value={scenario} disabled={busy || Boolean(pending)} onChange={event => setScenario(event.target.value)}>
          <option value="success">成功</option><option value="failure">失败返还测试余额</option>
          <option value="unknown">结果不明保留测试扣款</option>
        </select>
      </label>
      {pending && <p>待确认测试订单：{pending.order_id}，重试使用原编号。</p>}
      <button disabled={busy || !account}>{pending ? '重试同一模拟订单' : '提交模拟订单（不真实开通）'}</button>
    </form>
    <button type="button" disabled={busy} onClick={() => refresh().catch(failure => setError(failure.message))}>刷新测试记录</button>
    {account?.orders.map(order => <article key={order.order_id} className="membership-order">
      <strong>{order.months}个月 → @{order.username}</strong>
      <p>${Number(order.price_usd).toFixed(2)} · {statuses[order.status]}</p>
      <small className="membership-reference">{order.order_id}</small>
    </article>)}
  </section>
}

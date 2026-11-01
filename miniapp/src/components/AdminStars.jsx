import { useEffect, useRef, useState } from 'react'
import { requestAdmin } from '../utils/admin'
import { newMemberOrderId } from '../utils/membership'
import MusicDialog from './MusicDialog'

const statuses = { pending: '待付款', checkout: '付款核实中', paid: '已收到付款', expired: '已过期，请先核对实际库存' }

export default function AdminStars({ userId }) {
  const [data, setData] = useState(null)
  const [stars, setStars] = useState('2500')
  const [attempt, setAttempt] = useState(null)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const mounted = useRef(true)
  const lock = useRef(false)
  const key = `admin-stars-attempt-${userId}`

  async function refresh() {
    const saved = sessionStorage.getItem(key)
    const value = saved ? JSON.parse(saved) : null
    const result = await requestAdmin('admin_stars', value ? { order_id: value.order_id } : {})
    if (!mounted.current) return
    setData(result)
    if (value) {
      const row = result.current_order
      if (row && ['paid', 'expired'].includes(row.status)) {
        sessionStorage.removeItem(key)
        setAttempt(null)
      }
    }
  }
  useEffect(() => {
    mounted.current = true
    try {
      const saved = sessionStorage.getItem(key)
      if (saved) {
        const value = JSON.parse(saved)
        if (!/^[0-9a-f]{32}$/.test(value.order_id) || !Number.isInteger(value.stars) || value.stars < 1 || value.stars > 10000) {
          throw new Error('保存的 Stars 订单无效，请核对后台记录后清除该标签页的会话数据')
        }
        setAttempt(value)
        setStars(String(value.stars))
      }
    } catch (failure) { setError(`无法恢复补充订单：${failure.message}`) }
    setBusy(true)
    refresh().catch(failure => { if (mounted.current) setError(failure.message) })
      .finally(() => { if (mounted.current) setBusy(false) })
    return () => { mounted.current = false }
  }, [key])

  async function refreshStatus() {
    if (lock.current) return
    lock.current = true
    setBusy(true)
    setError('')
    try { await refresh() }
    catch (failure) { if (mounted.current) setError(failure.message) }
    finally { lock.current = false; if (mounted.current) setBusy(false) }
  }

  async function pay() {
    if (lock.current) return
    lock.current = true
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const tg = window.Telegram?.WebApp
      if (typeof tg?.openInvoice !== 'function' || Number(tg.version) < 6.1) {
        throw new Error('当前 Telegram 客户端不支持官方付款窗口，请更新客户端后打开管理台')
      }
      const value = attempt || { order_id: newMemberOrderId(), stars: Number(stars) }
      sessionStorage.setItem(key, JSON.stringify(value))
      setAttempt(value)
      const result = await requestAdmin('admin_stars_invoice', { ...value, confirmed: true })
      if (!mounted.current) return
      setConfirming(false)
      tg.openInvoice(result.invoice_url, status => {
        if (!mounted.current) return
        setNotice(status === 'paid'
          ? 'Telegram 已报告付款完成，请刷新实际库存和补充记录。'
          : status === 'cancelled' ? '付款已取消；请先刷新记录，再决定是否继续原订单。'
            : status === 'pending' ? 'Telegram 付款处理中，请刷新核对，勿重复付款。'
              : '付款未完成或结果未确认，请刷新实际库存和记录，勿重复付款。')
        window.setTimeout(() => { if (mounted.current) refreshStatus() }, 0)
      })
      setNotice('已打开 Telegram 官方付款窗口。只有官方付款成功通知才会更新补充记录，请勿重复付款。')
    } catch (failure) {
      if (mounted.current) setError(`${failure.message}；请先刷新库存和记录，重试沿用原订单。`)
    } finally { lock.current = false; if (mounted.current) setBusy(false) }
  }

  return <section className="admin-card" aria-label="补充机器人 Stars">
    <div className="admin-header"><h2>补充机器人 Stars</h2>
      <button type="button" disabled={busy} onClick={refreshStatus}>{busy ? '读取中…' : '刷新库存'}</button></div>
    {data && <p className="admin-star-balance"><strong>{data.balance}</strong> Stars{data.nanostar_amount !== 0 && `（另有 ${data.nanostar_amount} 纳星）`}</p>}
    <details className="account-details"><summary>补充方式与规则</summary>
      <p>用个人账号 Stars 支付给机器人；不足时先在 Telegram 购买。不增加客户美元余额，不自动开启会员销售。付款以官方成功通知及实际库存为准。</p>
    </details>
    {error && <p role="alert" className="admin-error">{error}</p>}
    {notice && <p role="status" className="admin-notice">{notice}</p>}
    <form onSubmit={event => { event.preventDefault(); setConfirming(true) }}>
      <label>补充 Stars 数量<input required type="number" min="1" max={data?.max_stars || 10000} step="1"
        value={stars} disabled={busy || Boolean(attempt)} onChange={event => setStars(event.target.value)} /></label>
      <button type="submit" disabled={busy || !data}>{attempt ? '继续原补充订单' : '补充 Stars'}</button>
    </form>
    {attempt && <p className="admin-hint">待核对订单：{attempt.order_id}。付款窗口有效期 15 分钟；结果不明时先刷新，勿新建订单重复付款。</p>}
    <details className="account-details"><summary>补充记录 · 最近10笔</summary>
    {data?.orders.map(row => <article className="admin-tenant" key={row.order_id}>
      <p><strong>{row.stars} Stars · {statuses[row.status] || row.status}</strong></p>
      <p>管理员 {row.admin_id} · {row.created_at}</p><p>订单 {row.order_id}</p>
    </article>)}
    </details>
    {confirming && <MusicDialog className="account-sheet" title="确认补充机器人 Stars" onClose={() => { if (!lock.current) setConfirming(false) }}>
      <p>本次将从你的个人 Telegram 账号支付 <strong>{attempt?.stars || stars} Stars</strong> 给机器人。</p>
      <p>不扣美元余额，不购买会员；请在官方付款窗口再次核对机器人和数量。订单过期或取消不等于退款，以官方交易及实际库存为准。</p>
      <button type="button" disabled={busy} onClick={pay}>{busy ? '创建付款窗口…' : '打开官方 Stars 付款窗口'}</button>
    </MusicDialog>}
  </section>
}

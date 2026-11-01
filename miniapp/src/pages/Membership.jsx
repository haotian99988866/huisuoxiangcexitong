import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import ModuleTabs from '../components/ModuleTabs'
import DepositRecords from '../components/DepositRecords'
import MemberSimulation from '../components/MemberSimulation'
import { AnnouncementCard, FullscreenAnnouncementBar } from '../components/Announcement'
import { bindBackButton, subscribeFullscreen } from '../utils/twa'
import {
  initializeMemberSession, isTelegramMemberEntry, logoutMember, memberStatuses,
  newMemberOrderId, requestMember,
} from '../utils/membership'
import './Membership.css'

const botUrl = 'https://t.me/haotianzhaopian_bot'
const money = value => Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 })

export default function Membership() {
  const location = useLocation()
  const [fullscreen, setFullscreen] = useState(false)
  const [account, setAccount] = useState(null)
  const [catalog, setCatalog] = useState(null)
  const [orders, setOrders] = useState([])
  const [username, setUsername] = useState('')
  const [months, setMonths] = useState(3)
  const [method, setMethod] = useState('usdt')
  const [amount, setAmount] = useState('')
  const [reference, setReference] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [pending, setPending] = useState(null)
  const submitting = useRef(false)
  const accountGeneration = useRef(0)
  const telegramEntry = isTelegramMemberEntry()
  const selected = catalog?.plans.find(plan => plan.months === months)
  const pendingKey = userId => `member-purchase-${userId}`

  async function refresh() {
    const generation = accountGeneration.current
    const [wallet, list, plans] = await Promise.all([
      requestMember('member_account'), requestMember('member_orders'), requestMember('member_catalog'),
    ])
    if (generation !== accountGeneration.current) return
    setAccount(wallet)
    setOrders(list.orders)
    setCatalog(plans)
    const saved = sessionStorage.getItem(pendingKey(wallet.user_id))
    if (saved) {
      const attempt = JSON.parse(saved)
      setPending(attempt)
      setUsername(attempt.recipient_username)
      setMonths(attempt.months)
    }
  }

  function reportFailure(failure, suffix = '') {
    if (failure.status === 401) {
      accountGeneration.current += 1
      setAccount(null)
      setOrders([])
      setPending(null)
    }
    setError(`${failure.message}${suffix}`)
  }

  useEffect(() => subscribeFullscreen(setFullscreen), [])
  useEffect(() => bindBackButton(() => { window.location.href = '/' }), [])
  useEffect(() => {
    let cancelled = false
    accountGeneration.current += 1
    if (location.hash.includes('login=')) {
      setAccount(null)
      setOrders([])
      setPending(null)
    }
    async function load() {
      try {
        if (await initializeMemberSession()) {
          if (!cancelled) await refresh()
        }
      } catch (failure) {
        if (!cancelled) reportFailure(failure)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [telegramEntry, location.hash])

  useEffect(() => {
    if (!account || (!account.deposits.some(deposit => deposit.status === 'pending')
      && !orders.some(order => ['paid', 'delivering', 'refund_pending'].includes(order.status)))) return undefined
    let polling = false
    const timer = window.setInterval(() => {
      if (polling || submitting.current) return
      polling = true
      refresh().catch(failure => reportFailure(failure, '；请保留订单编号，勿重复购买。'))
        .finally(() => { polling = false })
    }, 5000)
    return () => window.clearInterval(timer)
  }, [account, orders])

  async function deposit(event) {
    event.preventDefault()
    if (submitting.current) return
    submitting.current = true
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await requestMember('member_deposit', { method, amount, reference: reference.trim() })
      setNotice(`充值申请 #${result.id} 已提交，管理员确认真实到账后才增加 USD 余额。`)
      setAmount('')
      setReference('')
      await refresh()
    } catch (failure) { reportFailure(failure) }
    finally { submitting.current = false; setBusy(false) }
  }

  async function purchase(event) {
    event.preventDefault()
    if (submitting.current || !account) return
    submitting.current = true
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const attempt = pending || {
        order_id: newMemberOrderId(), months, recipient_username: username.trim(),
      }
      sessionStorage.setItem(pendingKey(account.user_id), JSON.stringify(attempt))
      setPending(attempt)
      const order = await requestMember('member_purchase', attempt)
      sessionStorage.removeItem(pendingKey(account.user_id))
      setPending(null)
      setNotice(`订单 ${order.order_id}：${memberStatuses[order.status] || order.status}。`)
      await refresh()
    } catch (failure) {
      if (failure.status === 400) {
        sessionStorage.removeItem(pendingKey(account.user_id))
        setPending(null)
      }
      reportFailure(failure, '；如结果未确认，请查询或重试同一订单，不要重新下单。')
    } finally { submitting.current = false; setBusy(false) }
  }

  async function checkPending() {
    if (!pending || busy) return
    setBusy(true)
    setError('')
    try {
      const order = await requestMember('member_order', { order_id: pending.order_id })
      sessionStorage.removeItem(pendingKey(account.user_id))
      setPending(null)
      setNotice(`订单 ${order.order_id}：${memberStatuses[order.status] || order.status}。`)
      await refresh()
    } catch (failure) {
      if (failure.status === 404) setError('暂未查到订单，请重试同一订单；系统不会重复扣款。')
      else reportFailure(failure)
    } finally { setBusy(false) }
  }

  return <main className="account-page membership-page">
    {fullscreen && <FullscreenAnnouncementBar />}
    <ModuleTabs active="membership" />
    {!fullscreen && <AnnouncementCard />}
    <header className="membership-header">
      <span className="membership-tag">TELEGRAM PREMIUM</span>
      <h1>会员充值</h1>
      <p>先充值余额，再购买会员 · 不需要提供密码或验证码</p>
    </header>
    {error && <p className="membership-error" role="alert">{error}</p>}
    {notice && <p className="membership-notice" role="status">{notice}</p>}
    {loading ? <p role="status">正在识别你的账户…</p> : !account ? (
      <section className="membership-panel">
        <h2>{telegramEntry ? '账户识别未完成' : '网站账户登录'}</h2>
        {telegramEntry ? <>
          <p>请在 Telegram 客户端登录自己的账户，再重新打开小程序。不要在此页面输入密码或验证码。</p>
          <button type="button" onClick={() => window.location.reload()}>重新识别账户</button>
          <a href={`${botUrl}?startapp=membership`} target="_blank" rel="noreferrer">在 Telegram 中重新打开</a>
        </> : <><ol>
          <li>私聊机器人发送 <strong>/member</strong>。</li>
          <li>用外部浏览器打开机器人发给你的专属登录链接。</li>
          <li>登录后即可查看余额、申请充值及购买会员。</li>
        </ol>
        <p>链接仅能使用一次，10 分钟内有效；不要转发给他人。网站会话有效期 24 小时。</p>
        <a href={botUrl} target="_blank" rel="noreferrer">前往机器人获取登录链接</a>
        </>}
      </section>
    ) : <>
      {account.can_test && <MemberSimulation userId={account.user_id} />}
      <section className="membership-panel">
        <div className="membership-row"><h2>我的账户</h2><span>USD</span></div>
        <p className="membership-balance">${money(account.balance)}</p>
        <p>账户：{account.user_id} · 待审核充值不计入可用余额</p>
        <div className="membership-actions">
          <button disabled={busy} onClick={() => refresh().catch(failure => reportFailure(failure))}>刷新余额与订单</button>
          {!telegramEntry && <button disabled={busy} onClick={async () => {
            setBusy(true)
            try {
              await logoutMember()
              accountGeneration.current += 1
              setAccount(null)
              setOrders([])
              setPending(null)
            } catch (failure) { reportFailure(failure) }
            finally { setBusy(false) }
          }}>退出网站账户</button>}
        </div>
      </section>
      <section className="membership-panel" id="member-deposit">
        <h2>① 充值账户余额</h2>
        <p>先实际付款，再提交交易编号。管理员核实后入账，不凭截图自动增加余额。</p>
        <div className="membership-actions">
          {['usdt', 'aba'].map(value => <button key={value} type="button" aria-pressed={method === value}
            disabled={busy} onClick={() => { setMethod(value); setReference('') }}>{value.toUpperCase()}</button>)}
        </div>
        {method === 'usdt' ? account.address ? <>
          <h3>USDT · TRON 主网（TRC20）</h3>
          <p className="membership-reference">{account.address}</p>
          <p>只收官方 USDT，勿使用其他网络。按实际到账 1 USDT = 1 USD 入账；链上手续费由付款方承担。</p>
        </> : <p role="status">USDT 收款地址未配置，请勿转账。</p>
          : account.aba ? <>
            <h3>ABA · 美元账户</h3>
            <p className="membership-reference">账号：{account.aba.account_number}<br />收款人：{account.aba.account_name}</p>
            <p>仅支持美元，按实际到账 USD 入账；转账费用由付款方承担。付款前请核对收款人。</p>
          </> : <p role="status">ABA 收款账户未配置，请勿转账。</p>}
        {(method === 'usdt' ? account.address : account.aba) && <form onSubmit={deposit}>
          <label>实际到账金额（{method === 'usdt' ? 'USDT' : 'USD'}）
            <input required type="number" min="0.000001" max="1000000" step="0.000001" value={amount}
              disabled={busy} onChange={event => setAmount(event.target.value)} />
          </label>
          <label>{method === 'usdt' ? '交易哈希（TXID）' : 'ABA 交易编号'}
            <input required value={reference} disabled={busy} autoCapitalize="none" autoCorrect="off"
              pattern={method === 'usdt' ? '[0-9a-fA-F]{64}' : '[A-Za-z0-9_-]{6,100}'}
              maxLength={method === 'usdt' ? 64 : 100} onChange={event => setReference(event.target.value)} />
          </label>
          <button className="membership-primary" disabled={busy}>提交充值核款申请</button>
        </form>}
        <h3>最近充值记录</h3>
        <DepositRecords deposits={account.deposits} />
      </section>
      <section className="membership-panel">
        <h2>② 选择会员套餐</h2>
        <p>官方赠送 Premium；大文件上传、更快下载及更多会员功能，以 Telegram 当前权益为准。</p>
        <p>收件人必须先私聊本机器人发送 /start，并拥有有效用户名。赠送成功后通常无法撤回。</p>
        <form onSubmit={purchase}>
          <label>收件人 Telegram 用户名
            <input required placeholder="@username" pattern="@?[A-Za-z0-9_]{5,32}" maxLength={33}
              autoCapitalize="none" autoCorrect="off" value={username} disabled={busy || Boolean(pending)}
              onChange={event => setUsername(event.target.value)} />
          </label>
          <div className="membership-plans">
            {catalog?.plans.map(plan => <button key={plan.months} type="button"
              disabled={busy || Boolean(pending)} aria-pressed={months === plan.months}
              onClick={() => setMonths(plan.months)}>
              <strong>{plan.months}个月</strong><span>${money(plan.price_usd)}</span>
            </button>)}
          </div>
          {selected && <p>收件人：{username || '请填写用户名'} · {months}个月 · 扣除 ${money(selected.price_usd)} USD</p>}
          {!catalog?.enabled && <p role="status">会员余额购买暂未开放。已提交订单仍会继续处理。</p>}
          {selected && Number(account.balance) < Number(selected.price_usd) && !pending
            && <p>余额不足，请先在上方充值并等待核款。</p>}
          {pending && <div role="status">
            <p>待确认订单：{pending.order_id}。收件人和套餐已锁定，重试沿用同一订单，不会重复扣款。</p>
            <button type="button" disabled={busy} onClick={checkPending}>查询这笔订单</button>
          </div>}
          <button className="membership-primary"
            disabled={busy || (!pending && (!catalog?.enabled || !selected || Number(account.balance) < Number(selected.price_usd)))}>
            {busy ? '正在处理，请等待…' : pending ? '重试同一订单' : '确认扣除余额并开通会员'}
          </button>
        </form>
      </section>
      <section className="membership-panel">
        <h2>③ 会员订单</h2>
        <p>扣款后显示充值中；开通完成后更新结果。结果不明时请勿重复购买。</p>
        {orders.length ? orders.map(order => <article className="membership-order" key={order.order_id}>
          <strong>{order.months}个月 → @{order.recipient_username}</strong>
          <p>${money(order.price_usd)} USD · {memberStatuses[order.status] || order.status}</p>
          <p className="membership-reference">{order.order_id}</p><small>{order.created_at}</small>
        </article>) : <p>暂无会员订单。</p>}
      </section>
    </>}
    <section className="membership-panel">
      <h2>充值与售后说明</h2>
      <p>明确未开通的失败订单退回站内余额，不是自动退回 ABA 或链上钱包。结果不明的订单先人工核实。</p>
      <p>由商家机器人 Stars 余额统一支付官方赠送费用，不需要客人提供账户密码。暂不支持自助提现；如需处理余额退款，请联系管理员。开通时间受库存、Telegram 服务及核实情况影响，不承诺秒到账。</p>
      <a href="https://t.me/haotian99988866" target="_blank" rel="noreferrer">联系昊天 · 请提供订单编号</a>
    </section>
    <Link to="/">返回首页</Link>
  </main>
}

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AnnouncementCard, FullscreenAnnouncementBar } from '../components/Announcement'
import ModuleTabs from '../components/ModuleTabs'
import { bindBackButton, hasTelegramBackButton, subscribeFullscreen } from '../utils/twa'
import { requestWallet } from '../utils/wallet'
import DepositRecords from '../components/DepositRecords'

const panelStyle = {
  background: '#fff',
  border: '1px solid rgba(174, 151, 122, 0.25)',
  borderRadius: '16px',
  padding: '20px',
  marginBottom: '16px',
}

const mutedStyle = { color: '#776b5c', fontSize: '14px', lineHeight: 1.8 }
const buttonStyle = {
  width: '100%',
  padding: '12px',
  marginTop: '16px',
  border: 0,
  borderRadius: '10px',
  fontSize: '15px',
  fontWeight: 700,
  background: '#eee8df',
  color: '#746b60',
  cursor: 'not-allowed',
}

export default function Recharge() {
  const navigate = useNavigate()
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [wallet, setWallet] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [amount, setAmount] = useState('')
  const [txid, setTxid] = useState('')
  const [busy, setBusy] = useState(false)
  const refresh = async () => {
    setBusy(true)
    setError('')
    try { setWallet(await requestWallet('wallet_account')) }
    catch (failure) { setError(failure.message) }
    finally { setBusy(false) }
  }
  useEffect(() => { refresh() }, [])
  const submitDeposit = async event => {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await requestWallet('wallet_submit', { amount, txid: txid.trim() })
      setNotice(`申请 #${result.id} 已保存，等待管理员核实到账；可稍后刷新查看。`)
      setAmount('')
      setTxid('')
      setWallet(await requestWallet('wallet_account'))
    } catch (failure) { setError(failure.message) }
    finally { setBusy(false) }
  }
  useEffect(() => subscribeFullscreen(setIsFullscreen), [])
  useEffect(() => bindBackButton(() => navigate('/', { replace: true })), [navigate])

  return (
    <main style={{ maxWidth: '480px', margin: '0 auto', padding: '12px 12px 32px' }}>
      {isFullscreen && <FullscreenAnnouncementBar />}
      <ModuleTabs active="recharge" />
      {!isFullscreen && <AnnouncementCard />}
      {!hasTelegramBackButton && (
        <button
          type="button"
          onClick={() => navigate('/', { replace: true })}
          style={{ border: 0, background: 'transparent', color: '#9a6f35', padding: '8px 0' }}
        >
          ← 返回首页
        </button>
      )}
      <header style={{ padding: '12px 4px 20px' }}>
        <h1 style={{ fontSize: '24px', color: '#5a4126' }}>充值中心</h1>
        <p style={mutedStyle}>Telegram Premium · 柬埔寨手机话费</p>
      </header>
      <div role="status" style={{
        padding: '12px 16px', marginBottom: '16px', borderRadius: '12px',
        background: '#fff3d6', color: '#855c17', fontSize: '14px',
      }}>
        商品暂未开放购买。余额充值仅在下方显示有效收款地址后开放，入账须管理员核实。
      </div>
      <section aria-labelledby="wallet-heading" style={panelStyle}>
        <h2 id="wallet-heading" style={{ fontSize: '19px' }}>我的余额</h2>
        <p style={{ fontSize: '28px', color: '#9a6f35' }}>{wallet ? `${wallet.balance} U` : '—'}</p>
        <p style={mutedStyle}>实际到账 1 USDT = 1 U，不另收充值手续费。不支持自助提现；退回链上须联系管理员人工处理。余额不用于购买 Premium。</p>
        <button type="button" disabled={busy} onClick={refresh} style={{ padding: '8px 14px', marginTop: '10px' }}>
          {busy ? '处理中…' : '刷新余额与记录'}
        </button>
        {error && <p role="alert" style={{ color: '#b42318', marginTop: '10px' }}>{error}</p>}
        {notice && <p role="status" style={{ color: '#28734d', marginTop: '10px' }}>{notice}</p>}
        {wallet?.address ? (
          <>
            <h3 style={{ marginTop: '18px' }}>USDT · TRC20 收款地址</h3>
            <p style={{ ...mutedStyle, overflowWrap: 'anywhere', userSelect: 'all' }}>{wallet.address}</p>
            <p style={mutedStyle}>仅支持 TRON 主网的 USDT（TRC20），请勿使用其他网络。链上手续费由付款方承担。提交申请不会自动入账，请先实际转账。</p>
            <form onSubmit={submitDeposit} style={{ display: 'grid', gap: '10px', marginTop: '12px' }}>
              <label>实际转入金额（USDT）
                <input aria-label="实际转入金额" required type="number" min="0.000001" max="1000000" step="0.000001"
                  value={amount} onChange={event => setAmount(event.target.value)}
                  style={{ width: '100%', padding: '10px', fontSize: '16px' }} />
              </label>
              <label>交易哈希（TXID）
                <input aria-label="交易哈希" required pattern="[0-9a-fA-F]{64}" maxLength={64}
                  value={txid} onChange={event => setTxid(event.target.value)}
                  style={{ width: '100%', padding: '10px', fontSize: '16px' }} />
              </label>
              <button type="submit" disabled={busy} style={{ padding: '12px' }}>提交到账核实申请</button>
            </form>
          </>
        ) : <p style={mutedStyle}>收款地址尚未配置，充值关闭，请勿转账。</p>}
        <h3 style={{ marginTop: '18px' }}>最近充值记录</h3>
        {wallet && <DepositRecords deposits={wallet.deposits} />}
      </section>
      <section aria-labelledby="premium-heading" style={panelStyle}>
        <h2 id="premium-heading" style={{ fontSize: '19px', marginBottom: '10px' }}>
          Telegram Premium
        </h2>
        <p style={mutedStyle}>自动开通渠道尚未接入，目前仅展示服务介绍。</p>
        <p style={mutedStyle}>可购时长、售价和到账时间待公布。支付开放后使用 Telegram Stars，不接受虚拟货币转账购买 Premium。</p>
        <button type="button" disabled style={buttonStyle}>暂未开放购买</button>
      </section>
      <section aria-labelledby="phone-heading" style={panelStyle}>
        <h2 id="phone-heading" style={{ fontSize: '19px', marginBottom: '10px' }}>
          柬埔寨手机话费
        </h2>
        <p style={mutedStyle}>计划提供人工充值服务；支持的运营商、充值面额、售价及处理时间待公布。</p>
        <ol style={{ ...mutedStyle, paddingLeft: '20px', marginTop: '12px' }}>
          <li>开放后选择运营商、核对号码和充值金额。</li>
          <li>余额充值核款到账后，用余额支付话费订单。</li>
          <li>管理员人工充值；失败退回余额，再通知处理结果。</li>
        </ol>
        <p style={{ ...mutedStyle, marginTop: '12px' }}>
          话费下单尚未开放。截图不代表到账；充值完成后，错号通常无法撤回，付款前需再次确认号码。
        </p>
        <button type="button" disabled style={buttonStyle}>暂未开放下单</button>
      </section>
      <p style={{ ...mutedStyle, padding: '0 4px' }}>
        不需要提供 Telegram 密码、验证码或两步验证密码，请勿向任何人发送这些信息。
      </p>
    </main>
  )
}

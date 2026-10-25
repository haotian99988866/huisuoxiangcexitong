import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import ModuleTabs from '../components/ModuleTabs'
import DepositRecords from '../components/DepositRecords'
import { AnnouncementCard, FullscreenAnnouncementBar } from '../components/Announcement'
import { bindBackButton, hasTelegramBackButton, subscribeFullscreen } from '../utils/twa'
import { requestWallet } from '../utils/wallet'

const cardStyle = {
  padding: '20px', marginBottom: '16px', borderRadius: '16px',
  background: '#fff', border: '1px solid #ebdfcd',
}
const detailStyle = { color: '#776b5c', fontSize: '14px', lineHeight: 1.8 }
const actionStyle = {
  display: 'block', padding: '12px', borderRadius: '10px', textAlign: 'center',
  background: '#9a6f35', color: '#fff', fontWeight: 700, marginTop: '12px',
}

export default function Personal() {
  const navigate = useNavigate()
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [wallet, setWallet] = useState(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const user = window.Telegram?.WebApp?.initDataUnsafe?.user

  const refresh = async () => {
    setBusy(true)
    setError('')
    setWallet(null)
    try { setWallet(await requestWallet('wallet_account')) }
    catch (failure) { setError(failure.message) }
    finally { setBusy(false) }
  }
  useEffect(() => { refresh() }, [])
  useEffect(() => subscribeFullscreen(setIsFullscreen), [])
  useEffect(() => bindBackButton(() => navigate('/', { replace: true })), [navigate])

  return (
    <main style={{ maxWidth: '480px', margin: '0 auto', padding: '12px 12px 32px' }}>
      {isFullscreen && <FullscreenAnnouncementBar />}
      <ModuleTabs active="personal" />
      {!isFullscreen && <AnnouncementCard />}
      {!hasTelegramBackButton && (
        <button type="button" onClick={() => navigate('/', { replace: true })}
          style={{ border: 0, background: 'transparent', color: '#9a6f35', padding: '8px 0' }}>
          ← 返回首页
        </button>
      )}
      <h1 style={{ fontSize: '24px', color: '#5a4126', margin: '16px 4px' }}>个人中心</h1>
      <section aria-label="我的账户" style={cardStyle}>
        <h2 style={{ fontSize: '19px', overflowWrap: 'anywhere' }}>
          {wallet && user ? [user.first_name, user.last_name].filter(Boolean).join(' ') || 'Telegram 用户' : '我的账户'}
        </h2>
        {wallet && user && (
          <div style={{ ...detailStyle, overflowWrap: 'anywhere' }}>
            {user.username && <p>@{user.username}</p>}
            <p>Telegram ID：{user.id}</p>
          </div>
        )}
        <p style={{ ...detailStyle, marginTop: '12px' }}>账户余额</p>
        <p style={{ fontSize: '30px', color: '#9a6f35' }}>
          {busy ? '加载中…' : wallet ? `${wallet.balance} U` : '—'}
        </p>
        {error && <p role="alert" style={{ color: '#b42318' }}>{error}</p>}
        <button type="button" disabled={busy} onClick={refresh} style={{ padding: '8px 14px', marginTop: '10px' }}>
          {busy ? '正在读取账户…' : '刷新账户'}
        </button>
        <Link to="/recharge" style={actionStyle}>余额充值</Link>
        {wallet?.can_manage && <Link to="/admin" style={actionStyle}>运营管理台</Link>}
      </section>
      <section aria-labelledby="personal-records" style={cardStyle}>
        <h2 id="personal-records" style={{ fontSize: '19px' }}>最近充值记录</h2>
        <p style={detailStyle}>展示最近 30 条充值申请及核款结果。</p>
        {wallet && <DepositRecords deposits={wallet.deposits} />}
        {!wallet && !busy && <p style={detailStyle}>验证账户身份后才能查看记录。</p>}
      </section>
      <section aria-labelledby="personal-help" style={cardStyle}>
        <h2 id="personal-help" style={{ fontSize: '19px' }}>帮助与规则</h2>
        <p style={detailStyle}>实际到账 1 USDT = 1 U，充值申请需人工核款。暂不支持自助提现，退回链上请联系管理员人工处理。</p>
        <p style={detailStyle}>Premium 与话费购买尚未开放，目前没有消费订单。余额不用于购买 Premium。</p>
        <a href="https://t.me/haotian99988866" target="_blank" rel="noreferrer" style={actionStyle}>联系昊天</a>
      </section>
    </main>
  )
}

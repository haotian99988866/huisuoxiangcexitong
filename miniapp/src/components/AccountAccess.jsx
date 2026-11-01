import { isTelegramMemberEntry } from '../utils/membership'

export default function AccountAccess({ loading }) {
  if (loading) return <p role="status">正在识别你的账户…</p>
  const telegram = isTelegramMemberEntry()
  return <section className="membership-panel">
    <h2>{telegram ? '账户识别未完成' : '登录我的账户'}</h2>
    {telegram ? <>
      <p>请重新打开 Telegram 小程序，无需输入密码。</p>
      <button type="button" onClick={() => window.location.reload()}>重新识别账户</button>
    </> : <>
      <p>向机器人发送 /member，打开专属登录链接。</p>
      <a className="account-primary-link" href="https://t.me/haotianzhaopian_bot" target="_blank" rel="noreferrer">前往 Telegram 登录</a>
      <details className="account-details"><summary>登录安全说明</summary><p>链接一次有效，10分钟过期，请勿转发。网站会话24小时有效，无需提供密码或验证码。</p></details>
      <div className="membership-actions"><button type="button" onClick={() => window.location.reload()}>重新读取账户</button></div>
    </>}
  </section>
}

import { isTelegramMemberEntry } from '../utils/membership'

export default function AccountAccess({ loading }) {
  if (loading) return <p role="status">正在识别你的账户…</p>
  const telegram = isTelegramMemberEntry()
  return <section className="membership-panel">
    <h2>{telegram ? '账户识别未完成' : '登录我的账户'}</h2>
    {telegram ? <>
      <p>请在 Telegram 客户端登录自己的账户，再重新打开小程序。不需要在此页面输入密码或验证码。</p>
      <button type="button" onClick={() => window.location.reload()}>重新识别账户</button>
    </> : <>
      <p>私聊机器人发送 /member，用当前浏览器打开专属登录链接。登录后可在服务、我的和订单页面之间直接切换。</p>
      <p>链接仅能使用一次，10 分钟有效，请勿转发；网站会话有效期 24 小时。</p>
      <a href="https://t.me/haotianzhaopian_bot" target="_blank" rel="noreferrer">前往机器人获取登录链接</a>
      <div className="membership-actions"><button type="button" onClick={() => window.location.reload()}>重新读取账户</button></div>
    </>}
  </section>
}

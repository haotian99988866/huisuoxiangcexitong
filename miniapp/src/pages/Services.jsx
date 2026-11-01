import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import ModuleIcon from '../components/ModuleIcon'
import { bindBackButton } from '../utils/twa'
import { useModuleScroll } from '../utils/moduleState'
import './Membership.css'

export default function Services() {
  useModuleScroll('services', true)
  useEffect(() => bindBackButton(null), [])
  return <main className="account-page membership-page services-page">
    <header className="membership-header">
      <span className="membership-tag">昊天生活通</span>
      <h1>服务中心</h1>
      <p>选服务、看价格，购买进度在「我的订单」查看。</p>
    </header>
    <section className="membership-panel" aria-labelledby="service-membership">
      <h2 id="service-membership">Telegram会员</h2>
      <Link className="service-product" to="/membership">
        <span className="service-product-icon"><ModuleIcon name="membership" /></span>
        <div><h3>Telegram Premium</h3><p>3 / 6 / 12个月 · 为自己或朋友开通</p><strong>$15.99 起</strong></div>
        <span aria-hidden="true">→</span>
      </Link>
      <p>余额付款后由昊天手动开通，进度在「我的订单」查看。</p>
    </section>
    <section className="membership-panel" aria-labelledby="service-coming">
      <h2 id="service-coming">更多便民服务</h2>
      <p>手机话费等服务筹备中，开放时再公布运营商、面额、售价和处理规则。</p>
      <span className="membership-tag">暂未开放下单</span>
    </section>
    <section className="membership-panel">
      <div className="membership-row"><h2>已有订单？</h2><Link to="/orders">查看订单 →</Link></div>
      <p>余额充值、资金明细和人工帮助，统一在「我的」管理。</p>
      <Link to="/personal">前往我的账户 →</Link>
    </section>
  </main>
}

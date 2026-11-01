import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import ModuleIcon from '../components/ModuleIcon'
import BrandArtwork from '../components/BrandArtwork'
import { bindBackButton } from '../utils/twa'
import { useModuleScroll } from '../utils/moduleState'
import './Membership.css'

export default function Services() {
  useModuleScroll('services', true)
  useEffect(() => bindBackButton(null), [])
  return <main className="account-page membership-page services-page">
    <header className="membership-header">
      <h1>服务中心</h1>
      <p>精选服务，轻松开通</p>
    </header>
    <section className="membership-panel service-hero" aria-labelledby="service-membership">
      <BrandArtwork />
      <span className="membership-tag" id="service-membership">TELEGRAM PREMIUM</span>
      <Link className="service-product" to="/membership">
        <span className="service-product-icon"><ModuleIcon name="membership" /></span>
        <div><h3>Telegram会员</h3><p>3 / 6 / 12个月 · 支持赠送</p><strong>$15.99 <small>起</small></strong></div>
        <span aria-hidden="true">→</span>
      </Link>
      <Link className="service-cta" to="/membership"><span>选择套餐</span><span>人工开通 →</span></Link>
    </section>
    <section className="membership-panel" aria-labelledby="service-coming">
      <h2 id="service-coming">更多便民服务</h2>
      <p>手机话费等 · 敬请期待</p>
    </section>
    <section className="membership-panel">
      <div className="membership-row"><h2>已有订单？</h2><Link to="/orders">查看订单 →</Link></div>
      <div className="account-menu"><Link to="/personal">钱包与账户</Link></div>
    </section>
  </main>
}

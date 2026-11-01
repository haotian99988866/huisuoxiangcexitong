import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import ModuleIcon from '../components/ModuleIcon'
import BrandArtwork from '../components/BrandArtwork'
import AccountHeader from '../components/AccountHeader'
import { bindBackButton } from '../utils/twa'
import { useModuleScroll } from '../utils/moduleState'
import './Membership.css'

export default function Services() {
  useModuleScroll('services', true)
  useEffect(() => bindBackButton(null), [])
  return <main className="account-page membership-page services-page">
    <AccountHeader><header className="membership-header">
      <h1>服务中心</h1>
    </header></AccountHeader>
    <p className="consumption-intro">精选服务，轻松开通</p>
    <section className="membership-panel service-hero" aria-labelledby="service-membership">
      <BrandArtwork />
      <span className="membership-tag" id="service-membership">TELEGRAM PREMIUM</span>
      <Link className="service-product" to="/membership">
        <span className="service-product-icon"><ModuleIcon name="membership" /></span>
        <div><h3>Telegram会员</h3><p>3 / 6 / 12个月 · 支持赠送</p><strong>$15.99 <small>起</small></strong></div>
      </Link>
      <Link className="service-cta" to="/membership"><span>选择套餐</span><span>人工开通</span></Link>
    </section>
    <section className="membership-panel" aria-labelledby="service-phone">
      <Link className="service-product" to="/phone-topup">
        <span className="service-product-icon"><ModuleIcon name="phone" /></span>
        <div><h3 id="service-phone">柬埔寨话费代充</h3><p>$5–$100 · 每单手续费 $1</p><strong>人工代充</strong></div>
      </Link>
      <Link className="service-cta" to="/phone-topup"><span>填写手机号</span><span>去充值</span></Link>
    </section>
  </main>
}

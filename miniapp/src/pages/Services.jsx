import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import ModuleIcon from '../components/ModuleIcon'
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
    <section className="membership-panel" aria-labelledby="service-subscriptions">
      <Link className="service-product" to="/subscriptions">
        <span className="service-product-icon"><ModuleIcon name="announcements" /></span>
        <div><h3 id="service-subscriptions">订阅服务</h3>
          <p>会所、外送、大事件 · 多位人员出勤提醒</p>
          <span className="service-inline-action">添加订阅 · 查看详情 · 随时取消</span>
        </div>
      </Link>
    </section>
    <section className="membership-panel service-news" aria-labelledby="service-news">
      <Link className="service-product" to="/announcements">
        <span className="service-product-icon"><ModuleIcon name="announcements" /></span>
        <div><h3 id="service-news">新闻资讯 <span className="service-news-tag">重点关注</span></h3>
          <p>东南亚大事件 · 多频道新闻汇集</p>
          <span className="service-inline-action">查看最新资讯</span>
        </div>
      </Link>
    </section>
    <section className="membership-panel" aria-labelledby="service-short-dramas">
      <Link className="service-product" to="/short-dramas">
        <span className="service-product-icon"><ModuleIcon name="movies" /></span>
        <div><h3 id="service-short-dramas">短剧合集</h3><p>热门短剧合集 · 无需选集</p>
          <span className="service-inline-action">挑选短剧 · Telegram 播放</span>
        </div>
      </Link>
    </section>
    <section className="membership-panel service-hero" aria-labelledby="service-membership">
      <Link className="service-product" to="/membership">
        <span className="service-product-icon"><ModuleIcon name="membership" /></span>
        <div><h3 id="service-membership">Telegram会员</h3><p>3 / 6 / 12个月 · 支持赠送</p>
          <span className="service-inline-row"><strong>$15.99 <small>起</small></strong><span>选择套餐 · 人工开通</span></span>
        </div>
      </Link>
    </section>
    <section className="membership-panel" aria-labelledby="service-phone">
      <Link className="service-product" to="/phone-topup">
        <span className="service-product-icon"><ModuleIcon name="phone" /></span>
        <div><h3 id="service-phone">柬埔寨话费代充</h3><p>$5–$100 · 每单手续费 $1</p><span className="service-inline-action">填写手机号 · 人工代充</span></div>
      </Link>
    </section>
    <section className="membership-panel" aria-labelledby="service-hotels">
      <Link className="service-product" to="/hotels">
        <span className="service-product-icon"><ModuleIcon name="location" /></span>
        <div><h3 id="service-hotels">酒店推荐</h3><p>门头照片 · 地图位置 · 前台Telegram</p><span className="service-inline-action">查看酒店 · 联系前台</span></div>
      </Link>
    </section>
  </main>
}

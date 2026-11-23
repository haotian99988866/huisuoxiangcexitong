import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import ModuleIcon from '../components/ModuleIcon'
import AccountHeader from '../components/AccountHeader'
import '../components/ui/ui.css'
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
    <p className="consumption-intro">娱乐、资讯、订阅与充值服务，一站直达</p>
    <section className="membership-panel service-quick-grid" aria-label="全部服务">
      {[
        ['movies', '观影', '/movies'],
        ['movies', '短剧', '/short-dramas'],
        ['music', '音乐', '/music'],
        ['location', '酒店', '/hotels'],
        ['announcements', '资讯', '/announcements'],
        ['announcements', '订阅', '/subscriptions'],
        ['membership', '会员', '/membership'],
        ['phone', '话费', '/phone-topup'],
      ].map(([icon, label, path]) => (
        <Link key={path} to={path}>
          <ModuleIcon name={icon} />
          <span>{label}</span>
        </Link>
      ))}
    </section>
  </main>
}

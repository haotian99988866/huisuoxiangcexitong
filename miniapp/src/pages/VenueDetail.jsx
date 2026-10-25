import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { sendData } from '../utils/twa'
import ModuleIcon from '../components/ModuleIcon'

export default function VenueDetail() {
  const { id } = useParams()
  const [v, setV] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    setV(null)
    setError('')
    const fn = e => {
      if (e.data?.type !== 'bot_response') return
      if (e.data.payload?.error) {
        setError(e.data.payload.error)
        return
      }
      setV(e.data.payload)
    }
    window.addEventListener('message', fn)
    sendData({ action: 'get_venue_detail', id })
    return () => window.removeEventListener('message', fn)
  }, [id])

  if (error) {
    return <div className="container loading"><p>{error}</p><Link to="/">返回会所列表</Link></div>
  }

  if (!v) return <div className="container loading"><div className="spinner"></div><p>加载中...</p></div>

  return (
    <div className="container">
      <Link to="/" className="back-link">← 返回会所列表</Link>
      
      <h1 style={{fontSize:24, fontWeight:700, marginBottom:24}}>{v.name}</h1>
      
      <div className="detail-card">
        <div className="venue-card__row">
          <span className="venue-card__icon"><ModuleIcon name="location" /></span>
          <div>
            <div className="venue-card__label">详细地址</div>
            <div className="venue-card__value">{v.address || '暂无地址'}</div>
          </div>
        </div>

        <div className="venue-card__row">
          <span className="venue-card__icon"><ModuleIcon name="clock" /></span>
          <div>
            <div className="venue-card__label">营业时间</div>
            <div className="venue-card__value">{v.business_hours || '暂无营业时间'}</div>
          </div>
        </div>

        <div className="venue-card__row">
          <span className="venue-card__icon"><ModuleIcon name="phone" /></span>
          <div>
            <div className="venue-card__label">咨询热线</div>
            <div className="venue-card__value">{v.phone || '暂无电话'}</div>
          </div>
        </div>
      </div>

      <div className="detail-card">
        <h3>会所介绍</h3>
        <p style={{color:'#4a4a4a', lineHeight:1.8}}>{v.description || '暂无介绍'}</p>
      </div>

      <Link to={`/venue/${id}/technicians`} className="btn btn-outline" style={{marginBottom:12}}>
        查看技师团队
      </Link>

      <a href={v.phone ? `tel:${v.phone}` : undefined} className="btn btn-success">
        立即致电预约
      </a>
    </div>
  )
}
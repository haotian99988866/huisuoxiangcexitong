import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { sendData } from '../utils/twa'

export default function VenueList() {
  const [list, setList] = useState([])

  useEffect(() => {
    sendData({ action: 'get_venues' })
    const fn = e => {
      if (e.data?.type === 'bot_response' && e.data.payload?.list) {
        setList(e.data.payload.list)
      }
    }
    window.addEventListener('message', fn)
    return () => window.removeEventListener('message', fn)
  }, [])

  return (
    <div className="container">
      <div className="header">
        <h1>🏪 会所一览</h1>
        <p>精选品质 · 舒适体验</p>
      </div>

      {list.length === 0 ? (
        <div className="loading">
          <div className="spinner"></div>
          <p>正在加载...</p>
        </div>
      ) : (
        list.map(v => (
          <Link key={v.id} to={`/venue/${v.id}`}>
            <div className="venue-card">
              <h2 className="venue-card__name">{v.name}</h2>
              
              <div className="venue-card__row">
                <span className="venue-card__icon">📍</span>
                <div>
                  <div className="venue-card__label">地址</div>
                  <div className="venue-card__value">{v.address}</div>
                </div>
              </div>

              <div className="venue-card__row">
                <span className="venue-card__icon">🕐</span>
                <div>
                  <div className="venue-card__label">营业时间</div>
                  <div className="venue-card__value">{v.business_hours}</div>
                </div>
              </div>

              <div className="venue-card__row">
                <span className="venue-card__icon">📞</span>
                <div>
                  <div className="venue-card__label">联系我们</div>
                  <div className="venue-card__value">{v.phone}</div>
                </div>
              </div>
            </div>
          </Link>
        ))
      )}
    </div>
  )
}
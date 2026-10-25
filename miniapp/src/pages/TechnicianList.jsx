import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { sendData } from '../utils/twa'

export default function TechnicianList() {
  const { venue_id } = useParams()
  const [list, setList] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setList([])
    setLoading(true)
    setError('')
    const fn = e => {
      if (e.data?.type !== 'bot_response') return
      if (e.data.payload?.error) {
        setError(e.data.payload.error)
      } else if (Array.isArray(e.data.payload?.list)) {
        setList(e.data.payload.list)
      }
      setLoading(false)
    }
    window.addEventListener('message', fn)
    sendData({ action: 'get_technicians', venue_id })
    return () => window.removeEventListener('message', fn)
  }, [venue_id])

  if (loading) return <div className="container loading"><div className="spinner"></div><p>加载中...</p></div>

  return (
    <div className="container">
      <Link to={`/venue/${venue_id}`} className="back-link">← 返回会所详情</Link>
      
      <h1 style={{fontSize:24, fontWeight:700, marginBottom:24}}>技师团队</h1>

      {error ? (
        <p role="alert">{error}</p>
      ) : list.length === 0 ? (
        <p>暂无技师信息</p>
      ) : null}
      
      {list.map(t => (
        <div key={t.id} className="tech-card">
          <div className="tech-card__header">
            {t.avatar_link && (
              <img
                src={t.avatar_link}
                alt=""
                style={{ width: 48, height: 48, borderRadius: '50%', objectFit: 'cover' }}
              />
            )}
            <span className="tech-card__name">{t.name}</span>
            <span className={`status-badge ${t.status === 'on_duty' ? 'status-on' : 'status-off'}`}>
              {t.status === 'on_duty' ? '在岗' : '休息'}
            </span>
          </div>
          
          {t.specialty && (
            <p style={{color:'#627264', fontSize:14, marginBottom:6}}>{t.specialty}</p>
          )}
        </div>
      ))}
    </div>
  )
}
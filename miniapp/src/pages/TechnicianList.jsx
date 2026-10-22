import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { sendData } from '../utils/twa'

export default function TechnicianList() {
  const { venue_id } = useParams()
  const [list, setList] = useState([])

  useEffect(() => {
    sendData({ action: 'get_technicians', venue_id })
    const fn = e => {
      if (e.data?.type === 'bot_response' && e.data.payload?.list) {
        setList(e.data.payload.list)
      }
    }
    window.addEventListener('message', fn)
    return () => window.removeEventListener('message', fn)
  }, [venue_id])

  if (!list.length) return <div className="container loading"><div className="spinner"></div><p>加载中...</p></div>

  return (
    <div className="container">
      <Link to={`/venue/${venue_id}`} className="back-link">← 返回会所详情</Link>
      
      <h1 style={{fontSize:24, fontWeight:700, marginBottom:24}}>👩‍⚕️ 技师团队</h1>
      
      {list.map(t => (
        <div key={t.id} className="tech-card">
          <div className="tech-card__header">
            <span className="tech-card__name">{t.name}</span>
            <span className={`status-badge ${t.status === 'on_duty' ? 'status-on' : 'status-off'}`}>
              {t.status === 'on_duty' ? '🟢 在岗' : '⚪ 休息'}
            </span>
          </div>
          
          <p style={{color:'#8a8a8a', fontSize:14, marginBottom:6}}>✨ {t.specialty}</p>
          <p style={{fontSize:14, marginBottom:6}}>📞 {t.phone}</p>
          <p style={{color:'#6b7280', fontSize:13}}>{t.description}</p>
        </div>
      ))}
    </div>
  )
}
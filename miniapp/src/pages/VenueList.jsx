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
    <div style={{padding:'20px', maxWidth:'480px', margin:'0 auto'}}>
      <h1 style={{fontSize:'22px', fontWeight:'bold', margin:'20px 0'}}>🏪 会所一览</h1>
      {list.length === 0 ? <p>加载中...</p> : list.map(v => (
        <Link key={v.id} to={`/venue/${v.id}`} style={{display:'block', border:'1px solid #eee', borderRadius:'12px', padding:'15px', marginBottom:'10px', background:'#fff'}}>
          <h3 style={{fontWeight:'bold'}}>{v.name}</h3>
          <p style={{fontSize:'14px', color:'#666', margin:'4px 0'}}>📍 {v.address}</p>
          <p style={{fontSize:'14px'}}>🕐 {v.business_hours}</p>
          <p style={{fontSize:'14px'}}>📞 {v.phone}</p>
        </Link>
      ))}
    </div>
  )
}
import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { sendData } from '../utils/twa'

export default function VenueDetail() {
  const { id } = useParams()
  const [v, setV] = useState(null)

  useEffect(() => {
    sendData({ action: 'get_venue_detail', id })
    const fn = e => {
      if (e.data?.type === 'bot_response') setV(e.data.payload)
    }
    window.addEventListener('message', fn)
    return () => window.removeEventListener('message', fn)
  }, [id])

  if (!v) return <div style={{padding:'20px'}}>加载中...</div>

  return (
    <div style={{padding:'20px', maxWidth:'480px', margin:'0 auto'}}>
      <Link to="/" style={{color:'#007bff'}}>← 返回列表</Link>
      <h1 style={{fontSize:'22px', fontWeight:'bold', margin:'15px 0'}}>{v.name}</h1>
      <div style={{background:'#fff', borderRadius:'12px', padding:'15px', marginBottom:'10px'}}>
        <p style={{marginBottom:'8px'}}><b>📍 地址：</b>{v.address}</p>
        <p style={{marginBottom:'8px'}}><b>🕐 营业时间：</b>{v.business_hours}</p>
        <p style={{marginBottom:'8px'}}><b>📞 电话：</b>{v.phone}</p>
      </div>
      <div style={{background:'#fff', borderRadius:'12px', padding:'15px'}}>
        <h3 style={{fontWeight:'bold', marginBottom:'8px'}}>📋 介绍</h3>
        <p>{v.description || '暂无介绍'}</p>
      </div>
      <a href={`tel:${v.phone}`} style={{display:'block', textAlign:'center', background:'#28a745', color:'#fff', borderRadius:'10px', padding:'12px', marginTop:'20px', fontWeight:'bold'}}>📞 立即致电</a>
    </div>
  )
}
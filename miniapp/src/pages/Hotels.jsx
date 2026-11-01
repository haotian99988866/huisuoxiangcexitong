import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import AccountHeader from '../components/AccountHeader'
import ModuleIcon from '../components/ModuleIcon'
import { API_BASE_URL, bindBackButton, openTelegramLink } from '../utils/twa'
import { mediaUrl } from '../utils/tenant'
import { requestHotels } from '../utils/hotels'
import { useModuleScroll } from '../utils/moduleState'
import './Membership.css'
import './Hotels.css'

export default function Hotels() {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [failedImages, setFailedImages] = useState(new Set())
  useModuleScroll('hotels', Boolean(data))
  useEffect(() => bindBackButton(() => navigate('/services', { replace: true })), [navigate])
  useEffect(() => {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 15000)
    let active = true
    setData(null); setError('')
    requestHotels(page, controller.signal).then(result => { if (active) setData(result) }).catch(failure => {
      console.error('[Hotels]', failure)
      if (active) setError(failure.name === 'AbortError' ? '酒店推荐读取超时，请重试' : failure.message)
    }).finally(() => clearTimeout(timeout))
    return () => { active = false; clearTimeout(timeout); controller.abort() }
  }, [page, reload])
  return <main className="account-page membership-page hotels-page">
    <AccountHeader><header className="membership-header"><h1>酒店推荐</h1></header></AccountHeader>
    <p className="consumption-intro">查看门头照片、地图位置，直接联系酒店前台。</p>
    {error && <p className="membership-error" role="alert">{error}
      <button type="button" onClick={() => setReload(value => value + 1)}>重新读取</button></p>}
    {!data && !error && <p role="status">正在读取酒店推荐…</p>}
    {data && <>
      {!data.hotels.length && <section className="membership-panel"><h2>暂无酒店推荐</h2><p className="admin-hint">酒店资料整理后会在这里展示。</p></section>}
      <div className="hotel-list">{data.hotels.map(hotel => <article className="hotel-card" key={hotel.id}>
        {failedImages.has(hotel.image_url) ? <div className="hotel-photo-placeholder" role="alert">门头照片暂不可用
          <button type="button" onClick={() => setFailedImages(current => {
            const next = new Set(current); next.delete(hotel.image_url); return next
          })}>重试照片</button></div> :
          <img className="hotel-photo" src={mediaUrl(`${API_BASE_URL}${hotel.image_url}`)} alt={`${hotel.name}门头照片`}
            loading="lazy" onError={() => setFailedImages(current => new Set([...current, hotel.image_url]))} />}
        <div className="hotel-card-body"><h2>{hotel.name}</h2>
          <p>前台 Telegram：@{hotel.telegram_username}</p>
          <div className="hotel-card-actions">
            <a href={hotel.map_url} target="_blank" rel="noopener noreferrer"><ModuleIcon name="location" />查看地图</a>
            <button type="button" onClick={() => openTelegramLink(`https://t.me/${hotel.telegram_username}`)}>
              <ModuleIcon name="contact" />联系前台</button>
          </div>
        </div>
      </article>)}</div>
      {data.total > 20 && <nav className="hotel-pages" aria-label="酒店列表分页">
        <button type="button" disabled={page === 1} onClick={() => setPage(value => value - 1)}>上一页</button>
        <span>{page} / {Math.ceil(data.total / 20)}</span>
        <button type="button" disabled={page * 20 >= data.total} onClick={() => setPage(value => value + 1)}>下一页</button>
      </nav>}
      {data.hotels.length > 0 && <p className="admin-hint">房价、空房及入住安排请直接向酒店前台核实，平台不代收住宿费用。</p>}
    </>}
  </main>
}

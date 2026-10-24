import { useEffect, useRef, useState, useMemo } from 'react'
import { API_BASE_URL, bindBackButton, closeMiniApp, hasTelegramBackButton, sendData, subscribeFullscreen } from '../utils/twa'

const ANNOUNCEMENT_TEXT = '暂无公告'

// 首页默认显示“名玺水汇会所”，找不到时退回列表第一个
const HOME_VENUE_NAME = '名玺水汇会所'
const pickHomeVenue = venues => venues.find(venue => venue.name === HOME_VENUE_NAME) || venues[0]


// 进入可视区附近才开始加载，并在加载完成后淡入
function LazyMedia({ as: Tag = 'img', src, style, ...rest }) {
  const nodeRef = useRef(null)
  const [inView, setInView] = useState(typeof IntersectionObserver === 'undefined')
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    if (inView) return undefined
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setInView(true)
        observer.disconnect()
      }
    }, { rootMargin: '300px' })
    observer.observe(nodeRef.current)
    return () => observer.disconnect()
  }, [inView])

  const loadProps = Tag === 'video'
    ? { onLoadedData: () => setLoaded(true) }
    : { onLoad: () => setLoaded(true) }

  return (
    <Tag
      ref={nodeRef}
      {...rest}
      {...(inView ? { src, ...loadProps, onError: () => setLoaded(true) } : {})}
      style={{ ...style, opacity: loaded ? 1 : 0, transition: 'opacity 0.35s ease' }}
    />
  )
}

const lazyFillStyle = {
  position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover'
}

const contactButtonStyle = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: '5px',
  padding: '8px 18px',
  borderRadius: '20px',
  background: '#80643c',
  color: '#fff',
  fontSize: '13px',
  fontWeight: 600,
  textDecoration: 'none',
  whiteSpace: 'nowrap'
}

function ContactButton() {
  return (
    <a
      href="https://t.me/haotian99988866"
      target="_blank"
      rel="noreferrer"
      style={contactButtonStyle}
    >
      <span aria-hidden="true">✈</span>联系昊天
    </a>
  )
}

function NoticeCard({ icon, title, updatedAt, children }) {
  return (
    <div style={{
      marginBottom: '24px',
      padding: '28px 18px',
      textAlign: 'center',
      background: '#fff9e6',
      border: '1px solid #f0dfb7',
      borderRadius: '14px',
      color: '#9a6b18'
    }}>
      <div style={{ fontSize: '34px', marginBottom: '8px' }}>{icon}</div>
      <div style={{ fontSize: '16px', fontWeight: 700, marginBottom: '8px' }}>{title}</div>
      <p style={{ margin: '0 0 16px', fontSize: '13px', lineHeight: 1.7, color: '#987f54' }}>
        {children}
      </p>
      <ContactButton />
      {updatedAt && (
        <div style={{ marginTop: '14px', fontSize: '11px', color: '#a89574' }}>
          最近更新：{updatedAt}
        </div>
      )}
    </div>
  )
}

const photoViewerButtonStyle = {
  width: '44px',
  height: '44px',
  border: '1px solid rgba(255, 255, 255, 0.35)',
  borderRadius: '50%',
  background: 'rgba(255, 255, 255, 0.12)',
  color: '#fff',
  fontSize: '26px',
  cursor: 'pointer'
}

const attendancePanelStyle = {
  marginBottom: '20px',
  background: '#fff',
  borderRadius: '12px',
  padding: '16px'
}

const greenSectionHeadingStyle = {
  fontSize: '15px',
  color: '#2f855a',
  margin: 0,
  fontWeight: 600
}

const ktvBookingButtonStyle = {
  display: 'inline-block',
  marginTop: '6px',
  padding: '6px 14px',
  borderRadius: '16px',
  background: '#80643c',
  color: '#fff',
  fontSize: '13px',
  textDecoration: 'none',
  cursor: 'pointer'
}

const DELIVERY_VENUE_IDS = new Set([7, 8, 9, 10])

const deliveryCategoryKey = venue => venue.categoryKey || String(venue.id)

const deliveryCategoryLabel = venue => venue.categoryLabel
  || venue.name.replace(/^西港/, '').replace(/外送$/, '')

// 小苹果/红苹果共用同一个频道，按内容文字里的类别分到各自的按钮下；未判断出类别的两边都显示
const expandDeliveryCategories = delivery => delivery.flatMap(venue => {
  if (Number(venue.id) !== 10) return [venue]
  const posts = venue.delivery_posts || []
  const pick = category => posts.filter(post => !post.category || post.category === category)
  return [
    { ...venue, categoryKey: '10-small', categoryLabel: '小苹果', delivery_posts: pick('small') },
    { ...venue, categoryKey: '10-red', categoryLabel: '红苹果', delivery_posts: pick('red') }
  ]
})

// 西港170/180/160 与小苹果/红苹果外送合并为“西港外送”，原会所作为其下的类别
const mergeDeliveryVenues = venues => {
  const delivery = venues.filter(venue => DELIVERY_VENUE_IDS.has(Number(venue.id)))
  if (delivery.length === 0) return venues
  const latest = delivery
    .map(venue => venue.last_channel_message_at)
    .filter(Boolean)
    .sort()
    .pop() || null
  const mergedVenue = {
    ...delivery[0],
    name: '西港外送',
    delivery_categories: expandDeliveryCategories(delivery),
    last_channel_message_at: latest
  }
  const result = []
  let inserted = false
  venues.forEach(venue => {
    if (!DELIVERY_VENUE_IDS.has(Number(venue.id))) {
      result.push(venue)
    } else if (!inserted) {
      result.push(mergedVenue)
      inserted = true
    }
  })
  return result
}

const ktvVideoInfo = video => {
  const lines = (video.caption || '')
    .split('\n')
    .map(line => line
      .replace(/[\p{Extended_Pictographic}\u200d\ufe0f]/gu, '')
      .replace(/[\u00a0\s]+/g, ' ')
      .trim())
    .filter(Boolean)
  const hall = lines.find(line => line.includes('·')) || ''
  return {
    hall: hall.replace(/\s*\d{3,4}$/, '') || (video.room_number ? `${video.room_number}号房` : '包房'),
    type: lines.find(line => line !== hall) || ''
  }
}

const ktvPriceLines = text => (text || '')
  .split(/\n-{5,}/)[0]
  .split('\n')
  .map(line => line.trim())
  .filter(line => line && !/^10\s*[:：]/.test(line))

const ktvBookingUrl = video => {
  const room = video.room_number ? `${video.room_number}号房` : '包房'
  return `https://t.me/haotian99988866?text=${encodeURIComponent(`你好，我想预定名玺水汇KTV ${room}`)}`
}

const attendanceHeadingRowStyle = {
  display: 'flex',
  alignItems: 'baseline',
  justifyContent: 'space-between',
  flexWrap: 'wrap',
  columnGap: '0',
  rowGap: '4px'
}

const recentUpdateStyle = {
  color: '#888',
  fontSize: '10px',
  whiteSpace: 'nowrap'
}

const attendanceInlineHeadingStyle = {
  ...greenSectionHeadingStyle,
  fontSize: '14px'
}

const technicianGridStyle = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fill, minmax(52px, 1fr))',
  gap: '8px'
}

const mingxiBoardPrices = [
  { board: '1号牌', member: 269, half: 139 },
  { board: '2号牌', member: 299, half: 149 },
  { board: '3号牌', member: 329, half: 169 },
  { board: '5号牌', member: 369, half: 189 },
  { board: '6号牌', member: 399, half: 199 },
  { board: '7号牌', member: 439, half: 219 },
  { board: '8号牌', member: 469, half: 239 },
  { board: '9号牌', member: 499, half: 249 },
  { board: '名玺牌', member: 599, half: 299 }
]

const kaidiTechnicianCategories = [
  { name: '5牌', codes: ['530', '508', '567', '513'], price: 99 },
  { name: '6牌', codes: ['628', '669', '616'], price: 159 },
  { name: '7牌', codes: ['707', '712', '733', '789'], price: 179 },
  { name: '8牌', codes: ['898', '886', '822', '899'], price: 209 },
  { name: '9牌', codes: ['986', '901', '909', '908'], price: 239 },
  { name: '1牌', codes: ['109', '102', '155', '139', '107'], price: 269 },
  { name: '0牌', codes: ['068', '039', '022'], price: 299 },
  { name: '2牌', codes: [], price: 339 },
  { name: '车模', codes: ['6666', '7777'], price: 369 },
  { name: '日韩', codes: [] }
]

const atlanticTechnicianCategories = [
  { name: '至尊', price: 99 },
  { name: '佳丽', price: 139 },
  { name: '车模', price: 169 },
  { name: '皇贵妃', price: 199 },
  { name: '皇后', price: 229 },
  { name: '1牌', codes: ['100', '199', '150'], price: 269 },
  { name: '3牌', price: 299 },
  { name: '0牌', price: 329 },
  { name: '豹子', price: 359 },
  { name: '日韩', price: 499 }
]

const dihaoTechnicianCategories = [
  { name: '0牌', price: 99 },
  { name: '1牌', price: 149 },
  { name: '2牌', price: 189 },
  { name: '6牌艺人', price: 229 },
  { name: '7牌公主', price: 269 },
  { name: '8牌佳丽', price: 329 },
  { name: '9牌贵妃', price: 369 },
  { name: '韩国牌', codes: ['韩国08'] }
]

const formatChannelMessageTime = timestamp => {
  if (!timestamp) return '暂无记录'

  const messageDate = new Date(timestamp)
  if (Number.isNaN(messageDate.getTime())) return '更新时间不可用'

  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23'
  }).format(messageDate)
}

export default function VenueList() {
  const [isFullscreen, setIsFullscreen] = useState(false)
  useEffect(() => subscribeFullscreen(setIsFullscreen), [])
  const [list, setList] = useState([])
  const [selectedId, setSelectedId] = useState('')
  const [deliveryCategoryId, setDeliveryCategoryId] = useState('')
  const [deliveryViewer, setDeliveryViewer] = useState(null)
  const [deliveryIndex, setDeliveryIndex] = useState(0)
  const deliveryTouchX = useRef(0)
  const stepDeliveryViewer = step => setDeliveryIndex(index => {
    const total = deliveryViewer?.media.length || 1
    return (index + step + total) % total
  })
  const openDeliveryViewer = post => {
    setDeliveryIndex(0)
    setDeliveryViewer(post)
  }

  useEffect(() => {
    if (!deliveryViewer) return undefined
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = previous }
  }, [deliveryViewer])
  const [selectedTechnicianCode, setSelectedTechnicianCode] = useState('')
  const [photoViewerIndex, setPhotoViewerIndex] = useState(null)
  const [ktvVideo, setKtvVideo] = useState(null)

  const closeKtvVideo = () => {
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {})
    setKtvVideo(null)
  }

  useEffect(() => {
    const onFullscreenChange = () => {
      if (!document.fullscreenElement) setKtvVideo(null)
    }
    document.addEventListener('fullscreenchange', onFullscreenChange)
    return () => document.removeEventListener('fullscreenchange', onFullscreenChange)
  }, [])
  const [photoZoom, setPhotoZoom] = useState(1)
  const [photoLoadState, setPhotoLoadState] = useState('idle')
  const [photoRetry, setPhotoRetry] = useState(0)
  const [widePhotos, setWidePhotos] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setSelectedTechnicianCode('')
    setPhotoViewerIndex(null)
    setPhotoLoadState('idle')
    setPhotoRetry(0)
  }, [selectedId])

  useEffect(() => {
    const fn = (e) => {
      if (e.data?.type !== 'bot_response') return
      if (e.data.payload?.error) {
        setError(e.data.payload.error)
        setLoading(false)
        return
      }
      const data = e.data.payload?.list || e.data.payload?.venues || e.data.payload
      if (Array.isArray(data)) {
        const merged = mergeDeliveryVenues(data)
        setList(merged)
        setSelectedId(currentId => currentId || String(pickHomeVenue(merged)?.id ?? ''))
      }
      setLoading(false)
    }
    window.addEventListener('message', fn)
    sendData({ action: 'get_venues' })
    return () => window.removeEventListener('message', fn)
  }, [])

  const baseVenue = useMemo(() => {
    const target = list.find(v => String(v.id) === selectedId)
    if (!target && list.length > 0) {
      const home = pickHomeVenue(list)
      setSelectedId(String(home.id))
      return home
    }
    return target
  }, [list, selectedId])

  const deliveryCategories = baseVenue?.delivery_categories || []
  const selectedVenue = deliveryCategories.length > 0
    ? deliveryCategories.find(venue => deliveryCategoryKey(venue) === deliveryCategoryId)
      || deliveryCategories[0]
    : baseVenue

  const onDutyCount = deliveryCategories.length > 0
    ? deliveryCategories.reduce((sum, venue) => sum + (venue.delivery_posts || []).length, 0)
    : selectedVenue?.on_duty_count ?? 0
  const technicianList = selectedVenue?.technician_list || []
  const currentShift = selectedVenue?.current_shift || null
  const isDeliveryDayShift = deliveryCategories.length > 0 && currentShift === '白班'
  const dutyShiftToday = currentShift || selectedVenue?.duty_shift_today
  const dutyLabel = currentShift ? `${currentShift}出勤` : '今日出勤'
  const serviceItems = selectedVenue?.service_items || []
  const technicianPhotos = selectedVenue?.technician_photos?.[selectedTechnicianCode] || []
  const technicianProfile = selectedVenue?.technician_profiles?.[selectedTechnicianCode] || {}
  const attendanceGroups = selectedVenue?.attendance_groups || []
  const rosterGroups = attendanceGroups.filter(group => group.group_type === 'roster')
  const attendanceServiceGroups = attendanceGroups.filter(group => group.group_type === 'service')

  // 从最上层详情开始，一层层退回
  const goBack = () => {
    if (ktvVideo) {
      closeKtvVideo()
    } else if (deliveryViewer) {
      setDeliveryViewer(null)
    } else if (photoViewerIndex !== null) {
      setPhotoViewerIndex(null)
      setPhotoZoom(1)
      setPhotoLoadState('idle')
    } else if (selectedTechnicianCode) {
      setSelectedTechnicianCode('')
    } else {
      closeMiniApp()
    }
  }
  const goBackRef = useRef(goBack)
  goBackRef.current = goBack
  useEffect(() => bindBackButton(() => goBackRef.current()), [])
  useEffect(() => {
    if (!selectedTechnicianCode) return undefined
    const closeOnEscape = event => {
      if (event.key === 'Escape') {
        if (photoViewerIndex !== null) {
          setPhotoViewerIndex(null)
          setPhotoZoom(1)
          setPhotoLoadState('idle')
        } else {
          setSelectedTechnicianCode('')
        }
      }
      if (photoViewerIndex === null || technicianPhotos.length === 0) return
      if (event.key === 'ArrowLeft') {
        setPhotoViewerIndex(index => (index - 1 + technicianPhotos.length) % technicianPhotos.length)
        setPhotoZoom(1)
        setPhotoLoadState('loading')
      }
      if (event.key === 'ArrowRight') {
        setPhotoViewerIndex(index => (index + 1) % technicianPhotos.length)
        setPhotoZoom(1)
        setPhotoLoadState('loading')
      }
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [photoViewerIndex, selectedTechnicianCode, technicianPhotos])

  // 按项目分类分组
  const serviceGroups = useMemo(() => {
    const groups = {}
    serviceItems.forEach(item => {
      if (!groups[item.category]) {
        groups[item.category] = { name: item.category_name, items: [] }
      }
      groups[item.category].items.push(item)
    })
    return groups
  }, [serviceItems])

  const technicianServiceCategories = selectedTechnicianCode
    ? [
        ...attendanceServiceGroups
          .filter(group => group.technician_codes.includes(selectedTechnicianCode))
          .map(group => group.group_name),
        ...[
        { key: 'small', title: '小项' },
        { key: 'medium', title: '中项' },
        { key: 'high', title: '高项' },
        { key: 'tcm', title: '中医按摩' },
        { key: 'pedicure', title: '修脚' }
        ]
        .filter(({ key }) => serviceGroups[key]?.items.some(item => (
          item.technician_codes?.split(',').map(code => code.trim())
            .includes(selectedTechnicianCode)
        )))
        .map(({ title }) => title)
      ]
    : []

  const getKaidiCategoryCodes = category => {
    const prefix = category.name.match(/^\d/)?.[0]
    const photoCodes = Object.keys(selectedVenue?.technician_photos || {}).filter(code => (
      category.name === '车模'
        ? ['6666', '7777'].includes(code)
        : prefix !== undefined && code.length <= 3 && code.startsWith(prefix)
    ))
    return [...new Set(photoCodes)]
  }

  const getAtlanticCategoryCodes = category => {
    const photoCodes = selectedVenue?.technician_categories
      ?.find(item => item.group_name === category.name)
      ?.technician_codes || []
    return [...new Set(photoCodes)]
  }

  const getDihaoCategoryCodes = category => (
    selectedVenue?.technician_categories
      ?.find(item => item.group_name === category.name)
      ?.technician_codes || []
  )

  const contactManagerUrl = selectedTechnicianCode && selectedVenue
    ? (() => {
        const photoLinks = selectedVenue.technician_photo_links?.[selectedTechnicianCode] || []
        const message = [
          '你好，我想咨询这位技师：',
          `会所：${selectedVenue.name}`,
          `工号：${selectedTechnicianCode}`,
          ['4', '5', '6'].includes(String(selectedVenue.id))
            ? '来源：会所频道照片'
            : `出勤：${dutyShiftToday || '今日在岗'}`,
          ...(technicianServiceCategories.length > 0
            ? [`可服务类别：${technicianServiceCategories.join('、')}`]
            : []),
          ...(photoLinks.length > 0
            ? ['', '频道照片原帖：', ...photoLinks.map((link, index) => `${index + 1}. ${link}`)]
            : ['技师暂无照片'])
        ].join('\n')
        return `https://t.me/haotian99988866?text=${encodeURIComponent(message)}`
      })()
    : 'https://t.me/haotian99988866'

  const renderTechnicianTile = code => {
    const photoIds = selectedVenue?.technician_photos?.[code] || []
    return (
      <button
        key={code}
        type="button"
        onClick={() => setSelectedTechnicianCode(code)}
        aria-label={`查看技师 ${code} 的详情${photoIds.length > 0 ? `，${photoIds.length}张照片` : '，暂无照片'}`}
        style={{
          position: 'relative',
          display: 'flex',
          alignItems: 'flex-end',
          justifyContent: 'center',
          minWidth: 0,
          minHeight: '72px',
          aspectRatio: '0.82',
          padding: '8px 4px',
          overflow: 'hidden',
          border: photoIds.length > 0
            ? '1px solid rgba(60, 45, 35, 0.12)'
            : '1px solid #dcebe2',
          borderRadius: '12px',
          backgroundColor: photoIds.length > 0 ? '#e9e2d9' : '#eaf4ee',
          backgroundImage: photoIds.length > 0
            ? 'none'
            : 'linear-gradient(145deg, #f1f8f3, #e4f0e8)',
          color: photoIds.length > 0 ? '#fff' : '#36734f',
          fontSize: '14px',
          fontWeight: 700,
          textShadow: photoIds.length > 0 ? '0 1px 3px rgba(0, 0, 0, 0.5)' : 'none',
          cursor: 'pointer',
          boxShadow: '0 2px 5px rgba(40, 65, 48, 0.08)'
        }}
      >
        {photoIds.length > 0 && (
          <>
            <LazyMedia
              alt=""
              decoding="async"
              src={`${API_BASE_URL}/photos/${photoIds[0]}/image`}
              style={lazyFillStyle}
            />
            <span style={{
              position: 'absolute', inset: 0,
              background: 'linear-gradient(to top, rgba(20, 20, 20, 0.72), rgba(20, 20, 20, 0.04) 58%)'
            }} />
          </>
        )}
        <span style={{ position: 'relative', zIndex: 1 }}>{code}</span>
      </button>
    )
  }

  const renderServiceItem = item => (
    <div key={item.id} style={{
      background: '#fff',
      borderRadius: '10px',
      padding: '14px 16px'
    }}>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'flex-start',
        gap: '8px'
      }}>
        <div style={{ fontSize: '14px', fontWeight: 600, color: '#333' }}>
          {item.name}
        </div>
        <div style={{
          fontSize: '15px',
          fontWeight: 700,
          color: '#c53030',
          flexShrink: 0
        }}>
          {item.price != null ? `$${Number(item.price).toFixed(2)}` : item.duration || '面议'}
        </div>
      </div>
      {item.description && (
        <div style={{ fontSize: '12px', color: '#888', marginTop: '4px' }}>
          {item.description}
        </div>
      )}
      {item.price && (
        <div style={{ fontSize: '12px', color: '#666', marginTop: '6px' }}>
          ⏱ {item.duration}
        </div>
      )}
    </div>
  )

  const renderCategoryTechnicians = (items, title) => {
    const technicianCodes = [...new Set(
      items.flatMap(item => item.technician_codes
        ? item.technician_codes.split(',').map(code => code.trim()).filter(Boolean)
        : [])
    )]

    return (
      <div style={{ marginBottom: '10px' }}>
        <div style={{
          display: 'flex',
          alignItems: 'baseline',
          marginBottom: technicianCodes.length > 0 ? '6px' : 0
        }}>
          <h3 style={{
            fontSize: '15px',
            color: '#b7791f',
            margin: 0,
            fontWeight: 600
          }}>
            {title}
          </h3>
        </div>
        {technicianCodes.length > 0 && (
          <div style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: '6px',
            margin: '0 16px'
          }}>
            {technicianCodes.map(code => {
            const photoIds = selectedVenue?.technician_photos?.[code] || []
            return (
              <button
                key={code}
                type="button"
                onClick={() => setSelectedTechnicianCode(code)}
                aria-label={`查看技师 ${code} 的详情${photoIds.length > 0 ? `，${photoIds.length}张照片` : '，暂无照片'}`}
                style={{
                  display: 'flex',
                  flex: '0 0 24px',
                  width: '24px',
                  height: '24px',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: 0,
                  overflow: 'hidden',
                  border: photoIds.length > 0
                    ? '1px solid rgba(60, 45, 35, 0.12)'
                    : '1px solid #dcebe2',
                  borderRadius: '50%',
                  backgroundColor: photoIds.length > 0 ? '#e9e2d9' : '#eaf4ee',
                  position: 'relative',
                  backgroundImage: photoIds.length > 0
                    ? 'none'
                    : 'linear-gradient(145deg, #f1f8f3, #e4f0e8)',
                  color: photoIds.length > 0 ? '#fff' : '#36734f',
                  fontSize: '8px',
                  fontWeight: 700,
                  textShadow: photoIds.length > 0 ? '0 1px 2px rgba(0, 0, 0, 0.7)' : 'none',
                  cursor: 'pointer',
                  boxShadow: '0 1px 3px rgba(40, 65, 48, 0.08)'
                }}
              >
                {photoIds.length > 0 && (
                  <>
                    <LazyMedia
                      alt=""
                      decoding="async"
                      src={`${API_BASE_URL}/photos/${photoIds[0]}/image`}
                      style={lazyFillStyle}
                    />
                    <span style={{
                      position: 'absolute', inset: 0, background: 'rgba(20, 20, 20, 0.25)'
                    }} />
                  </>
                )}
                <span style={{ position: 'relative' }}>{code}</span>
              </button>
            )
            })}
          </div>
        )}
      </div>
    )
  }

  return (
    <div style={{
      minHeight: '100vh',
      background: '#faf6f0',
      padding: '12px',
      boxSizing: 'border-box'
    }}>
      {/* 头部区域 */}
      <div style={{
        position: 'sticky',
        top: 0,
        zIndex: 20,
        padding: '10px 12px 10px',
        margin: '-12px -12px 14px',
        background: 'linear-gradient(180deg, #fffdf8 0%, #faf6f0 100%)',
        backdropFilter: 'blur(12px)',
        borderBottom: '1px solid rgba(158, 132, 101, 0.16)',
        boxShadow: '0 4px 14px rgba(120, 90, 50, 0.07)'
      }}>
        {isFullscreen && (
          <div style={{
            margin: '0 -12px 4px',
            paddingTop: 'var(--tg-safe-area-inset-top, 0px)',
            boxSizing: 'border-box'
          }}>
            <div
              aria-label="公告"
              style={{
                height: 'max(var(--tg-content-safe-area-inset-top, 0px), 44px)',
                padding: '0 64px',
                boxSizing: 'border-box',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '6px',
                fontSize: '13px',
                color: '#9a6b18',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis'
              }}
            >
              <span aria-hidden="true">📢</span>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{ANNOUNCEMENT_TEXT}</span>
            </div>
          </div>
        )}
        <div style={{
          display: 'grid',
          gap: '7px'
        }}>
          <div style={{
            padding: '0 2px'
          }}>
            <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            flexWrap: 'nowrap'
          }}>
            <select
              value={selectedId}
              onChange={e => setSelectedId(e.target.value)}
              aria-label="选择会所"
              style={{
                flex: '1 1 0',
                minWidth: '0',
                height: '40px',
                padding: '0 30px 0 12px',
                border: '1px solid rgba(174, 151, 122, 0.32)',
                borderRadius: '12px',
                color: '#5a4126',
                fontSize: '14px',
                fontWeight: 600,
                outline: 'none',
                appearance: 'none',
                WebkitAppearance: 'none',
                background: "#fff url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath d='M1 1l5 5 5-5' fill='none' stroke='%23a07a45' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E\") no-repeat right 12px center",
                boxShadow: '0 1px 3px rgba(120, 90, 50, 0.06)'
              }}
            >
              {loading ? (
                <option value="">加载中...</option>
              ) : error ? (
                <option value="">{error}</option>
              ) : (
                list.map(v => (
                  <option key={v.id} value={String(v.id)}>
                    🏠 {v.name}
                  </option>
                ))
              )}
            </select>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '6px',
              flex: '0 0 auto',
              minWidth: '104px',
              height: '40px',
              padding: '0 10px',
              background: 'rgba(56, 131, 93, 0.1)',
              color: '#28734d',
              border: '1px solid rgba(56, 131, 93, 0.18)',
              borderRadius: '12px',
              fontSize: '11px',
              fontWeight: 600,
              whiteSpace: 'nowrap'
            }}>
              <span aria-hidden="true">✓</span>
              {String(selectedVenue?.id) === '3' ? (
                <>
                  包房 <strong style={{ fontSize: '15px' }}>{(selectedVenue.ktv_videos || []).length}</strong> 间
                </>
              ) : isDeliveryDayShift ? (
                <>白班请单独联系</>
              ) : (
                <>
                  {dutyLabel} <strong style={{ fontSize: '15px' }}>{onDutyCount}</strong>{' '}
                  {['4', '5', '6'].includes(String(selectedVenue?.id)) ? '张照片' : '人'}
                </>
              )}
            </div>
          </div>
          </div>
          {!isFullscreen && (
            <div
              aria-label="公告"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                padding: '8px 12px',
                background: '#fff',
                border: '1px solid rgba(154, 107, 24, 0.18)',
                borderLeft: '3px solid #c9a24d',
                borderRadius: '10px',
                boxShadow: '0 1px 3px rgba(120, 90, 50, 0.05)',
                fontSize: '12px',
                color: '#9a6b18',
                lineHeight: 1.4
              }}
            >
              <span aria-hidden="true">📢</span>
              <span style={{ fontWeight: 700 }}>公告</span>
              <span style={{ width: '1px', height: '12px', background: '#e6d3a3' }} />
              <span style={{ minWidth: 0, color: '#987f54', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {ANNOUNCEMENT_TEXT}
              </span>
            </div>
          )}
          {deliveryCategories.length > 0 && !isDeliveryDayShift && (
            <div style={{
              display: 'flex',
              gap: '8px',
              overflowX: 'auto',
              marginTop: '8px',
              scrollbarWidth: 'none',
            }}>
              {deliveryCategories.map(venue => {
                const active = deliveryCategoryKey(venue) === deliveryCategoryKey(selectedVenue)
                return (
                  <button
                    key={deliveryCategoryKey(venue)}
                    type="button"
                    onClick={() => setDeliveryCategoryId(deliveryCategoryKey(venue))}
                    style={{
                      flex: '1 1 0', minWidth: 0, whiteSpace: 'nowrap', padding: '7px 2px', borderRadius: '18px', cursor: 'pointer',
                      border: active ? '1px solid #80643c' : '1px solid #eadfca',
                      background: active ? '#80643c' : '#fff',
                      color: active ? '#fff' : '#80643c',
                      fontSize: '14px', fontWeight: 600
                    }}
                  >
                    {deliveryCategoryLabel(venue)}
                  </button>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: '#999' }}>
          加载中...
        </div>
      ) : error ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: '#c53030' }}>
          {error}
        </div>
      ) : list.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: '#999' }}>
          <div style={{ fontSize: '40px', marginBottom: '12px' }}>🔍</div>
          <p>暂无会所数据</p>
        </div>
      ) : !selectedVenue ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: '#999' }}>
          加载中...
        </div>
      ) : (
        <div>
          {/* 今日在岗技师 / 未发布提示 */}
          {isDeliveryDayShift ? (
            <NoticeCard icon="☀️" title="西港外送只展示晚班出勤">
              当前为白班时间（06:00–18:00），白班外送请单独联系昊天，<br />
              晚班（18:00 起）内容将在此展示。
            </NoticeCard>
          ) : Array.isArray(selectedVenue.delivery_posts) ? (
            <div style={{ marginBottom: '24px' }}>
              <div style={{ ...attendanceHeadingRowStyle, marginBottom: '12px' }}>
                <h3 style={attendanceInlineHeadingStyle}>📸 最新上新</h3>
                <span style={recentUpdateStyle}>
                  最近更新：{formatChannelMessageTime(selectedVenue.last_channel_message_at)}
                </span>
              </div>
              {selectedVenue.delivery_posts.length > 0 ? (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '10px' }}>
                  {selectedVenue.delivery_posts.map(post => {
                    const description = (post.description || '').trim()
                    const cover = post.media.find(media => media.type !== 'video') || post.media[0]
                    return (
                      <div
                        key={post.id}
                        onClick={() => openDeliveryViewer(post)}
                        style={{
                          background: '#fff',
                          borderRadius: '14px',
                          overflow: 'hidden',
                          boxShadow: '0 2px 10px rgba(80, 60, 30, 0.1)',
                          cursor: 'pointer',
                        }}
                      >
                        <div style={{ position: 'relative',                         aspectRatio: '3 / 4', background: cover.type === 'video' ? '#1d1a16' : '#f3ede4' }}>
                                                  {cover.type === 'video' && (
                                                    <span style={{
                                                      position: 'absolute', inset: 0, display: 'flex',
                                                      alignItems: 'center', justifyContent: 'center',
                                                      color: 'rgba(255,255,255,0.8)', fontSize: '44px', pointerEvents: 'none',
                                                    }}>▶</span>
                                                  )}
                                                  {cover.type === 'video' ? (
                                                    <LazyMedia
                              as="video"
                              muted
                              playsInline
                              preload="metadata"
                              src={`${API_BASE_URL}/delivery-media/${cover.id}#t=0.5`}
                              style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                            />
                          ) : (
                            <LazyMedia
                              alt=""
                              decoding="async"
                              src={`${API_BASE_URL}/delivery-media/${cover.id}`}
                              style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                            />
                          )}
                          {post.media.length > 1 && (
                            <span style={{
                              position: 'absolute', right: '8px', bottom: '8px',
                              background: 'rgba(0,0,0,0.55)', color: '#fff',
                              fontSize: '12px', padding: '2px 8px', borderRadius: '10px',
                            }}>
                              🖼 {post.media.length}
                            </span>
                          )}
                          {post.price && (
                            <span style={{
                              position: 'absolute', left: '8px', bottom: '8px',
                              background: '#c9a24d', color: '#fff',
                              fontSize: '13px', fontWeight: 700, padding: '2px 9px', borderRadius: '10px',
                            }}>
                              {post.price}$
                            </span>
                          )}
                          {post.media.some(media => media.type === 'video') && (
                            <span style={{
                              position: 'absolute', left: '8px', top: '8px',
                              background: 'rgba(0,0,0,0.55)', color: '#fff',
                              fontSize: '12px', padding: '2px 8px', borderRadius: '10px',
                            }}>
                              ▶ 视频
                            </span>
                          )}
                        </div>
                        {description && (
                          <div style={{
                            padding: '8px 10px',
                            fontSize: '13px',
                            lineHeight: 1.5,
                            color: '#4a3b28',
                            display: '-webkit-box',
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: 'vertical',
                            overflow: 'hidden',
                            wordBreak: 'break-word',
                          }}>
                            {description}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              ) : (
                <NoticeCard icon="⏳" title="暂无内容">
                  新内容发布后将在此展示，<br />如需了解详情请直接联系昊天。
                </NoticeCard>
              )}
              {deliveryViewer && (
                <div
                  style={{
                    position: 'fixed',
                    inset: 0,
                    zIndex: 1000,
                    background: '#000',
                    display: 'flex',
                    flexDirection: 'column',
                    paddingTop: 'calc(var(--tg-safe-area-inset-top, 0px) + var(--tg-content-safe-area-inset-top, 0px))',
                    boxSizing: 'border-box',
                  }}
                >
                  <div
                    onTouchStart={event => { deliveryTouchX.current = event.touches[0].clientX }}
                    onTouchEnd={event => {
                      const delta = event.changedTouches[0].clientX - deliveryTouchX.current
                      if (Math.abs(delta) > 50) stepDeliveryViewer(delta < 0 ? 1 : -1)
                    }}
                    style={{
                      flex: 1,
                      minHeight: 0,
                      position: 'relative',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      overflow: 'hidden',
                      touchAction: 'pan-y',
                    }}
                  >
                    {(() => {
                      const media = deliveryViewer.media[deliveryIndex]
                      return media.type === 'video' ? (
                        <video
                          key={media.id}
                          controls
                          playsInline
                          preload="metadata"
                          src={`${API_BASE_URL}/delivery-media/${media.id}`}
                          style={{ maxWidth: '100%', maxHeight: '100%' }}
                        />
                      ) : (
                        <img
                          key={media.id}
                          alt=""
                          src={`${API_BASE_URL}/delivery-media/${media.id}`}
                          style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
                        />
                      )
                    })()}
                    {deliveryViewer.media.length > 1 && ['‹', '›'].map((arrow, index) => (
                      <button
                        key={arrow}
                        type="button"
                        aria-label={index === 0 ? '上一张' : '下一张'}
                        onClick={() => stepDeliveryViewer(index === 0 ? -1 : 1)}
                        style={{
                          position: 'absolute',
                          top: '50%',
                          [index === 0 ? 'left' : 'right']: '8px',
                          transform: 'translateY(-50%)',
                          width: '38px',
                          height: '38px',
                          borderRadius: '50%',
                          border: 'none',
                          background: 'rgba(0,0,0,0.4)',
                          color: '#fff',
                          fontSize: '26px',
                          lineHeight: '34px',
                          padding: 0,
                        }}
                      >
                        {arrow}
                      </button>
                    ))}
                    {deliveryViewer.media.length > 1 && (
                      <span style={{
                        position: 'absolute',
                        top: 'max(18px, env(safe-area-inset-top))',
                        left: '14px',
                        background: 'rgba(0,0,0,0.5)',
                        color: '#fff',
                        fontSize: '13px',
                        padding: '3px 10px',
                        borderRadius: '12px',
                      }}>
                        {deliveryIndex + 1} / {deliveryViewer.media.length}
                      </span>
                    )}
                  </div>
                  {deliveryViewer.media.length > 1 && (
                    <div style={{
                      display: 'flex',
                      gap: '8px',
                      justifyContent: 'center',
                      padding: '8px 12px',
                      background: '#000',
                    }}>
                      {deliveryViewer.media.map((media, index) => {
                        const thumbStyle = {
                          width: '52px',
                          height: '52px',
                          objectFit: 'cover',
                          borderRadius: '8px',
                          opacity: index === deliveryIndex ? 1 : 0.5,
                          border: index === deliveryIndex ? '2px solid #c9a24d' : '2px solid transparent',
                          boxSizing: 'border-box',
                          background: '#222',
                        }
                        return media.type === 'video' ? (
                          <div
                            key={media.id}
                            onClick={() => setDeliveryIndex(index)}
                            style={{ position: 'relative', width: '52px', height: '52px' }}
                          >
                            <LazyMedia
                              as="video"
                              muted
                              playsInline
                              preload="metadata"
                              src={`${API_BASE_URL}/delivery-media/${media.id}#t=0.5`}
                              style={{ ...thumbStyle, pointerEvents: 'none' }}
                            />
                            <span style={{
                              position: 'absolute', inset: 0, display: 'flex',
                              alignItems: 'center', justifyContent: 'center',
                              color: '#fff', fontSize: '16px', pointerEvents: 'none',
                            }}>▶</span>
                          </div>
                        ) : (
                          <LazyMedia
                            key={media.id}
                            alt=""
                            decoding="async"
                            onClick={() => setDeliveryIndex(index)}
                            src={`${API_BASE_URL}/delivery-media/${media.id}`}
                            style={thumbStyle}
                          />
                        )
                      })}
                    </div>
                  )}
                  <div style={{
                    padding: '12px 16px max(14px, env(safe-area-inset-bottom))',
                    background: '#1b1610',
                    color: '#fff',
                  }}>
                    {(deliveryViewer.work_no || deliveryViewer.price) && (
                      <div style={{
                        display: 'flex', gap: '12px', alignItems: 'center',
                        fontSize: '15px', fontWeight: 700, marginBottom: '8px',
                      }}>
                        {deliveryViewer.work_no && <span>工号 {deliveryViewer.work_no}</span>}
                        {deliveryViewer.price && (
                          <span style={{ color: '#e8c46a' }}>{deliveryViewer.price}$</span>
                        )}
                      </div>
                    )}
                    {deliveryViewer.description && (
                      <div style={{
                        fontSize: '15px',
                        lineHeight: 1.6,
                        maxHeight: '22vh',
                        overflowY: 'auto',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        marginBottom: '10px',
                      }}>
                        {deliveryViewer.description}
                      </div>
                    )}
                    <a
                      href={`https://t.me/haotian99988866?text=${encodeURIComponent(
                        `你好，我想预约${baseVenue?.name || '西港外送'}：${[
                          deliveryViewer.work_no && `工号${deliveryViewer.work_no}`,
                          deliveryViewer.price && `${deliveryViewer.price}$`,
                          (deliveryViewer.description || '').slice(0, 40),
                        ].filter(Boolean).join(' ')}`
                      )}`}
                      target="_blank"
                      rel="noreferrer"
                      style={{
                        display: 'block',
                        textAlign: 'center',
                        background: '#c9a24d',
                        color: '#fff',
                        fontWeight: 700,
                        fontSize: '16px',
                        padding: '12px',
                        borderRadius: '12px',
                        textDecoration: 'none',
                      }}
                    >
                      联系预约
                    </a>
                  </div>
                  {!hasTelegramBackButton && (
                  <button
                    type="button"
                    aria-label="关闭"
                    onClick={() => setDeliveryViewer(null)}
                    style={{
                      position: 'absolute',
                      top: 'max(12px, env(safe-area-inset-top))',
                      right: '12px',
                      width: '40px',
                      height: '40px',
                      borderRadius: '50%',
                      border: 'none',
                      background: 'rgba(255,255,255,0.25)',
                      color: '#fff',
                      fontSize: '22px',
                    }}
                  >
                    ✕
                  </button>
                  )}
                </div>
              )}
            </div>
          ) : String(selectedVenue.id) === '3' ? (
            <div style={{ ...attendancePanelStyle, marginBottom: '24px' }}>
              <div style={{ ...attendanceHeadingRowStyle, marginBottom: '12px' }}>
                <h3 style={attendanceInlineHeadingStyle}>🎤 KTV包房视频</h3>
                <span style={recentUpdateStyle}>
                  最近更新：{formatChannelMessageTime(selectedVenue.last_channel_message_at)}
                </span>
              </div>
              {(selectedVenue.ktv_videos || []).length > 0 ? (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
                  {selectedVenue.ktv_videos.map(video => {
                    const info = ktvVideoInfo(video)
                    return (
                    <div key={video.id} style={{
                      background: '#fff', borderRadius: '14px', overflow: 'hidden',
                      border: '1px solid #eee3cf', boxShadow: '0 4px 14px rgba(128, 100, 60, 0.12)'
                    }}>
                      <div
                        onClick={() => setKtvVideo(video)}
                        style={{
                          position: 'relative', aspectRatio: '16 / 10',
                          background: '#2b2b2b', cursor: 'pointer', overflow: 'hidden'
                        }}
                      >
                        <LazyMedia
                          as="video"
                          preload="metadata"
                          muted
                          playsInline
                          src={`${API_BASE_URL}/ktv-videos/${video.id}#t=0.5`}
                          style={{ width: '100%', height: '100%', objectFit: 'cover', pointerEvents: 'none' }}
                        />
                        <div style={{
                          position: 'absolute', inset: 0,
                          background: 'linear-gradient(to top, rgba(0,0,0,0.55), rgba(0,0,0,0) 55%)'
                        }} />
                        {video.room_number && (
                          <span style={{
                            position: 'absolute', top: '8px', left: '8px', padding: '2px 9px',
                            borderRadius: '10px', background: 'rgba(128, 100, 60, 0.92)',
                            color: '#fff', fontSize: '12px', fontWeight: 600
                          }}>
                            {video.room_number}
                          </span>
                        )}
                        <span style={{
                          position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%, -50%)',
                          width: '42px', height: '42px', borderRadius: '50%',
                          background: 'rgba(255,255,255,0.88)', color: '#80643c', fontSize: '16px',
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          paddingLeft: '3px', boxShadow: '0 2px 8px rgba(0,0,0,0.3)'
                        }}>▶</span>
                        {info.type && (
                          <span style={{
                            position: 'absolute', left: '8px', bottom: '8px',
                            color: '#fff', fontSize: '12px', textShadow: '0 1px 3px rgba(0,0,0,0.6)'
                          }}>
                            {info.type}
                          </span>
                        )}
                      </div>
                      <div style={{
                        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                        gap: '6px', padding: '10px'
                      }}>
                        <div style={{
                          fontSize: '13px', fontWeight: 600, color: '#5a4630', minWidth: 0,
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
                        }}>
                          {info.hall}{video.room_number ? ` ${video.room_number}` : ''}
                        </div>
                        <a
                          href={ktvBookingUrl(video)}
                          target="_blank"
                          rel="noreferrer"
                          style={{ ...ktvBookingButtonStyle, marginTop: 0, padding: '5px 12px', fontSize: '12px', flexShrink: 0 }}
                        >
                          预定
                        </a>
                      </div>
                    </div>
                    )
                  })}
                </div>
              ) : (
                <p style={{ margin: 0, color: '#888', fontSize: '13px' }}>暂无包房视频</p>
              )}
              {ktvVideo && (
                <div
                  ref={node => {
                    if (node && !document.fullscreenElement) {
                      node.requestFullscreen?.().catch(() => {})
                    }
                  }}
                  onClick={() => closeKtvVideo()}
                  style={{
                    position: 'fixed', inset: 0, zIndex: 1000, background: '#000',
                    display: 'flex', flexDirection: 'column', justifyContent: 'center'
                  }}
                >
                  {!hasTelegramBackButton && (
                  <button
                    type="button"
                    aria-label="关闭"
                    onClick={event => { event.stopPropagation(); closeKtvVideo() }}
                    style={{
                      position: 'absolute',
                      top: 'calc(var(--tg-safe-area-inset-top, 0px) + var(--tg-content-safe-area-inset-top, 0px) + 14px)',
                      right: '14px', zIndex: 2,
                      width: '40px', height: '40px', borderRadius: '50%', border: 0,
                      background: 'rgba(0,0,0,0.6)', color: '#fff', fontSize: '22px', cursor: 'pointer'
                    }}
                  >
                    ✕
                  </button>
                  )}
                  <video
                    autoPlay
                    controls
                    controlsList="nofullscreen"
                    playsInline
                    src={`${API_BASE_URL}/ktv-videos/${ktvVideo.id}`}
                    onClick={event => event.stopPropagation()}
                    style={{ width: '100%', maxHeight: '80vh', background: '#000' }}
                  />
                  <div
                    onClick={event => event.stopPropagation()}
                    style={{ display: 'flex', gap: '12px', justifyContent: 'center', padding: '14px' }}
                  >
                    <a href={ktvBookingUrl(ktvVideo)} target="_blank" rel="noreferrer" style={ktvBookingButtonStyle}>
                      包房预定
                    </a>
                    <button
                      type="button"
                      onClick={() => closeKtvVideo()}
                      style={{ ...ktvBookingButtonStyle, background: '#555', border: 0 }}
                    >
                      关闭
                    </button>
                  </div>
                </div>
              )}
              {(selectedVenue.ktv_texts || []).length > 0 && (
                <div style={{ marginTop: '20px' }}>
                  <h3 style={{ ...attendanceInlineHeadingStyle, marginBottom: '10px' }}>💰 消费模式及价格</h3>
                  <div style={{
                    background: '#fffaf0', border: '1px solid #eee3cf',
                    borderRadius: '14px', padding: '6px 14px'
                  }}>
                    {selectedVenue.ktv_texts.flatMap(ktvPriceLines).map((line, index) => (
                      <p key={index} style={{
                        margin: 0, padding: '8px 0', fontSize: '13px', lineHeight: 1.6,
                        color: line.includes('消费明细') ? '#80643c' : '#555',
                        fontWeight: line.includes('消费明细') ? 700 : 400,
                        borderBottom: '1px dashed #eadfca'
                      }}>
                        {line}
                      </p>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : ['4', '5', '6'].includes(String(selectedVenue.id)) && onDutyCount > 0 ? (
            <div style={{ ...attendancePanelStyle, marginBottom: '24px' }}>
              <div style={{ ...attendanceHeadingRowStyle, marginBottom: '12px' }}>
                <h3 style={attendanceInlineHeadingStyle}>
                  👥 {dutyLabel}技师 ({onDutyCount}张照片)
                </h3>
                <span style={recentUpdateStyle}>
                  最近更新：{formatChannelMessageTime(selectedVenue.last_channel_message_at)}
                </span>
              </div>
              {(String(selectedVenue.id) === '5'
                ? atlanticTechnicianCategories
                : String(selectedVenue.id) === '6'
                  ? dihaoTechnicianCategories
                  : kaidiTechnicianCategories
              ).filter(category => (
                (String(selectedVenue.id) === '5'
                  ? getAtlanticCategoryCodes(category)
                  : String(selectedVenue.id) === '6'
                    ? getDihaoCategoryCodes(category)
                    : getKaidiCategoryCodes(category)
                ).length > 0
              )).map(category => (
                <div key={category.name} style={{ marginBottom: '14px' }}>
                  <h4 style={{
                    margin: '0 0 8px',
                    color: '#80643c',
                    fontSize: '13px',
                    fontWeight: 600
                  }}>
                    {category.name}
                    {(category.price != null) && (
                      <span style={{ marginLeft: '8px', color: '#888', fontWeight: 400 }}>
                        价格 ${category.price} · 90分钟
                      </span>
                    )}
                  </h4>
                  {(String(selectedVenue.id) === '5'
                    ? getAtlanticCategoryCodes(category)
                    : String(selectedVenue.id) === '6'
                      ? getDihaoCategoryCodes(category)
                      : getKaidiCategoryCodes(category)
                  ).length > 0 ? (
                    <div style={technicianGridStyle}>
                      {(String(selectedVenue.id) === '5'
                        ? getAtlanticCategoryCodes(category)
                        : String(selectedVenue.id) === '6'
                          ? getDihaoCategoryCodes(category)
                          : getKaidiCategoryCodes(category)
                      ).map(renderTechnicianTile)}
                    </div>
                  ) : (
                    <p style={{ margin: 0, color: '#888', fontSize: '13px' }}>
                      暂无技师工号
                    </p>
                  )}
                </div>
              ))}
            </div>
          ) : technicianList.length > 0 ? (
            <div style={attendancePanelStyle}>
              <div style={{ ...attendanceHeadingRowStyle, marginBottom: '12px' }}>
                <h3 style={attendanceInlineHeadingStyle}>
                  👥 {dutyLabel}技师 ({technicianList.length}人)
                  {!currentShift && dutyShiftToday && (
                    <span style={{ fontSize: '11px', color: '#666', marginLeft: '8px' }}>
                      （{dutyShiftToday}）
                    </span>
                  )}
                </h3>
                <span style={recentUpdateStyle}>
                  最近更新：{formatChannelMessageTime(selectedVenue.last_channel_message_at)}
                </span>
              </div>
              {rosterGroups.length > 0 ? (
                rosterGroups.map(group => (
                  <div key={group.group_name} style={{ marginBottom: '14px' }}>
                    <h4 style={{
                      margin: '0 0 8px',
                      color: '#80643c',
                      fontSize: '13px',
                      fontWeight: 600
                    }}>
                      {group.group_name}
                      {String(selectedVenue.id) === '2' && (() => {
                        const boardNumber = group.group_name.match(/(\d+)\s*号牌/)?.[1]
                        const price = mingxiBoardPrices.find(item => (
                          item.board === `${boardNumber}号牌`
                        ))
                        return price && (
                          <span style={{ marginLeft: '8px', color: '#888', fontWeight: 400 }}>
                            会员 ${price.member} / 90分钟 · 半价 ${price.half} / 45分钟
                          </span>
                        )
                      })()}
                    </h4>
                    <div style={technicianGridStyle}>
                      {group.technician_codes.map(renderTechnicianTile)}
                    </div>
                  </div>
                ))
              ) : (
                <div style={technicianGridStyle}>
                  {technicianList.map(renderTechnicianTile)}
                </div>
              )}
            </div>
          ) : (
            <NoticeCard
              icon="⏳"
              title={`${dutyLabel}尚未发布`}
              updatedAt={formatChannelMessageTime(selectedVenue.last_channel_message_at)}
            >
              {currentShift === '白班'
                ? '白班出勤名单与照片发布后将在此展示，'
                : '晚班出勤名单与照片发布后将在此展示，'}
              <br />如需了解详情请直接联系昊天。
            </NoticeCard>
          )}

          {/* Services grouped by category */}
          {attendanceServiceGroups.length > 0 ? (
            <div style={{
              marginBottom: '24px',
              background: '#fff',
              borderRadius: '12px',
              padding: '16px'
            }}>
              {attendanceServiceGroups.map(group => (
                <div key={group.group_name} style={{ marginBottom: '14px' }}>
                  <h3 style={{
                    margin: '0 0 8px',
                    color: '#b7791f',
                    fontSize: '15px',
                    fontWeight: 600
                  }}>
                    {group.group_name}
                  </h3>
                  {group.technician_codes.length > 0 ? (
                    <div style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fill, minmax(52px, 1fr))',
                      gap: '8px'
                    }}>
                      {group.technician_codes.map(renderTechnicianTile)}
                    </div>
                  ) : (
                    <p style={{ margin: 0, color: '#888', fontSize: '13px' }}>暂无关联技师</p>
                  )}
                </div>
              ))}
            </div>
          ) : Object.keys(serviceGroups).length > 0 && (
            <div style={{ marginBottom: '24px' }}>
              {[
                { key: 'small', title: '⭐️ 小项' },
                { key: 'medium', title: '⭐️ 中项' },
                { key: 'high', title: '⭐️ 高项' },
                { key: 'tcm', title: '🤩 中医按摩' },
                { key: 'pedicure', title: '🌸 修脚师' }
              ].map(({ key, title }) => serviceGroups[key] && (
                <div key={key} style={{ marginBottom: '18px' }}>
                  {renderCategoryTechnicians(serviceGroups[key].items, title)}
                  <div style={{ display: 'grid', gap: '8px' }}>
                    {serviceGroups[key].items.map(renderServiceItem)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {selectedTechnicianCode && selectedVenue && (
        <div
          role="presentation"
          onClick={() => setSelectedTechnicianCode('')}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 1000,
            display: 'flex',
            alignItems: 'flex-end',
            justifyContent: 'center',
            background: 'rgba(0, 0, 0, 0.48)'
          }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="technician-detail-title"
            onClick={event => event.stopPropagation()}
            style={{
              width: '100%',
              maxWidth: '520px',
              maxHeight: '82vh',
              overflowY: 'auto',
              padding: '20px 20px calc(24px + env(safe-area-inset-bottom))',
              boxSizing: 'border-box',
              borderRadius: '20px 20px 0 0',
              background: '#fff'
            }}
          >
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '16px'
            }}>
              <h2
                id="technician-detail-title"
                style={{ margin: 0, fontSize: '20px', color: '#2c7a56' }}
              >
                技师 {selectedTechnicianCode}
              </h2>
              <button
                type="button"
                onClick={() => setSelectedTechnicianCode('')}
                aria-label="关闭技师详情"
                style={{
                  width: '36px',
                  height: '36px',
                  border: 0,
                  borderRadius: '50%',
                  background: '#f2f2f2',
                  color: '#555',
                  fontSize: '22px',
                  cursor: 'pointer'
                }}
              >
                ×
              </button>
            </div>

            {(selectedVenue.technician_photos?.[selectedTechnicianCode] || []).length > 0 ? (
              <div style={{
                display: 'flex',
                gap: '10px',
                overflowX: 'auto',
                marginBottom: '18px'
              }}>
                {technicianPhotos.map((photoId, index) => (
                  <button
                    key={photoId}
                    type="button"
                    onClick={() => {
                      setPhotoViewerIndex(index)
                      setPhotoZoom(1)
                      setPhotoLoadState('loading')
                      setPhotoRetry(0)
                    }}
                    aria-label={`放大查看第 ${index + 1} 张照片`}
                    style={{
                      position: 'relative',
                      padding: 0,
                      border: 0,
                      borderRadius: '12px',
                      background: '#f2f2f2',
                      overflow: 'hidden',
                      cursor: 'zoom-in',
                      flex: '0 0 auto'
                    }}
                  >
                    <img
                      src={`${API_BASE_URL}/photos/${photoId}/image`}
                      alt={`工号 ${selectedTechnicianCode} 的第 ${index + 1} 张照片`}
                      loading="lazy"
                      onLoad={event => {
                        const image = event.currentTarget
                        if (image.naturalWidth > image.naturalHeight) {
                          setWidePhotos(current => (
                            current[photoId]
                              ? current
                              : { ...current, [photoId]: true }
                          ))
                        }
                      }}
                      style={{
                        display: 'block',
                        width: widePhotos[photoId]
                          ? 'min(88vw, 420px)'
                          : 'min(48vw, 220px)',
                        height: '280px',
                        objectFit: widePhotos[photoId] ? 'contain' : 'cover'
                      }}
                    />
                    <span style={{
                      position: 'absolute',
                      right: '8px',
                      bottom: '8px',
                      padding: '4px 8px',
                      borderRadius: '12px',
                      background: 'rgba(0, 0, 0, 0.58)',
                      color: '#fff',
                      fontSize: '12px'
                    }}>
                      点击放大
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <div style={{
                marginBottom: '18px',
                padding: '24px 12px',
                borderRadius: '12px',
                background: '#f7f7f7',
                color: '#888',
                textAlign: 'center',
                fontSize: '14px'
              }}>
                暂无已确认照片
              </div>
            )}

            {Object.keys(technicianProfile).length > 0 && (
              <div style={{ marginBottom: '18px' }}>
                <h3 style={{
                  margin: '0 0 9px',
                  paddingLeft: '8px',
                  borderLeft: '3px solid #4a8b68',
                  fontSize: '15px',
                  lineHeight: 1.2,
                  color: '#34483c'
                }}>
                  技师基本信息
                </h3>
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
                  padding: '4px 12px',
                  border: '1px solid #e7eee9',
                  borderRadius: '12px',
                  background: '#f8faf8'
                }}>
                  {[
                    ['technician_code', '工号'],
                    ['age', '年龄'],
                    ['height', '身高'],
                    ['bust', '胸围'],
                    ['weight', '体重'],
                    ['origin', ['4', '5', '6'].includes(String(selectedVenue.id)) ? '产地' : '籍贯']
                  ].filter(([field]) => technicianProfile[field]).map(([field, label]) => (
                    <div
                      key={field}
                      style={{
                        display: 'flex',
                        alignItems: 'baseline',
                        gap: '8px',
                        minWidth: 0,
                        padding: '9px 2px',
                        borderBottom: '1px solid #e8eeea'
                      }}
                    >
                      <span style={{
                        flexShrink: 0,
                        color: '#829087',
                        fontSize: '12px'
                      }}>{label}</span>
                      <strong style={{
                        minWidth: 0,
                        color: '#34483c',
                        fontSize: '14px',
                        fontWeight: 650,
                        overflowWrap: 'anywhere'
                      }}>
                        {technicianProfile[field]}
                        {field === 'height' ? ' cm' : field === 'age' ? ' 岁' : ''}
                      </strong>
                    </div>
                  ))}
                </div>
                {technicianProfile.service_description && (
                  <div style={{ marginTop: '14px' }}>
                    <h3 style={{ margin: '0 0 8px', fontSize: '15px', color: '#444' }}>
                      服务介绍
                    </h3>
                    <p style={{
                      margin: 0,
                      padding: '12px',
                      borderRadius: '10px',
                      background: '#f7f8f7',
                      color: '#666',
                      fontSize: '13px',
                      lineHeight: 1.7,
                      whiteSpace: 'pre-wrap',
                      overflowWrap: 'anywhere'
                    }}>
                      {technicianProfile.service_description}
                    </p>
                  </div>
                )}
              </div>
            )}

            <div style={{ marginBottom: '16px' }}>
              <h3 style={{ margin: '0 0 6px', fontSize: '15px', color: '#444' }}>
                信息来源
              </h3>
              <div style={{ fontSize: '14px', color: '#666' }}>
                {['4', '5', '6'].includes(String(selectedVenue.id))
                  ? '会所频道照片'
                  : technicianList.includes(selectedTechnicianCode)
                    ? `在岗${dutyShiftToday ? ` · ${dutyShiftToday}` : ''}`
                    : `不在${dutyLabel}名单`}
              </div>
            </div>

            {technicianServiceCategories.length > 0 && (
              <div>
                <h3 style={{ margin: '0 0 8px', fontSize: '15px', color: '#444' }}>
                  可服务类别
                </h3>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  {technicianServiceCategories.map(category => (
                    <span
                      key={category}
                      style={{
                        padding: '6px 11px',
                        borderRadius: '16px',
                        background: '#f0f7f4',
                        color: '#2c7a56',
                        fontSize: '13px'
                      }}
                    >
                      {category}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <a
              href={contactManagerUrl}
              target="_blank"
              rel="noreferrer"
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '8px',
                minHeight: '46px',
                marginTop: '20px',
                borderRadius: '12px',
                background: 'linear-gradient(135deg, #38835d, #4a9870)',
                color: '#fff',
                fontSize: '15px',
                fontWeight: 700,
                textDecoration: 'none'
              }}
            >
              <span aria-hidden="true">✈</span>
              联系业务经理昊天
            </a>
            <div style={{
              marginTop: '7px',
              color: '#888',
              fontSize: '12px',
              textAlign: 'center'
            }}>
              将在 Telegram 中预填技师信息和频道照片原帖链接
            </div>
          </section>
        </div>
      )}
      {photoViewerIndex !== null && technicianPhotos[photoViewerIndex] && (
        <div
          role="presentation"
          onClick={() => {
            setPhotoViewerIndex(null)
            setPhotoZoom(1)
            setPhotoLoadState('idle')
          }}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 1100,
            display: 'flex',
            flexDirection: 'column',
            background: 'rgba(0, 0, 0, 0.96)',
            color: '#fff'
          }}
        >
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: 'max(16px, env(safe-area-inset-top)) 16px 12px'
          }}>
            <span style={{ fontSize: '14px' }}>
              工号 {selectedTechnicianCode} · {photoViewerIndex + 1}/{technicianPhotos.length}
            </span>
            <button
              type="button"
              onClick={() => {
                setPhotoViewerIndex(null)
                setPhotoZoom(1)
                setPhotoLoadState('idle')
              }}
              aria-label="关闭照片查看"
              style={{
                width: '40px',
                height: '40px',
                border: 0,
                borderRadius: '50%',
                background: 'rgba(255, 255, 255, 0.16)',
                color: '#fff',
                fontSize: '24px',
                cursor: 'pointer'
              }}
            >
              ×
            </button>
          </div>

          <div
            onClick={event => event.stopPropagation()}
            style={{
              position: 'relative',
              flex: 1,
              minHeight: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              overflow: 'auto',
              padding: '8px 12px'
            }}
          >
            <img
              key={`${technicianPhotos[photoViewerIndex]}-${photoRetry}`}
              src={`${API_BASE_URL}/photos/${technicianPhotos[photoViewerIndex]}/image${photoRetry ? `?retry=${photoRetry}` : ''}`}
              alt={`工号 ${selectedTechnicianCode} 的放大照片`}
              onLoad={() => setPhotoLoadState('loaded')}
              onError={() => setPhotoLoadState('error')}
              style={{
                display: 'block',
                width: `${Math.min(92 * photoZoom, 300)}vw`,
                height: `${Math.min(82 * photoZoom, 120)}vh`,
                maxWidth: 'none',
                maxHeight: 'none',
                objectFit: 'contain',
                opacity: photoLoadState === 'loaded' ? 1 : 0
              }}
            />
            {photoLoadState !== 'loaded' && (
              <div style={{
                position: 'absolute',
                inset: 0,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '14px',
                color: '#fff',
                textAlign: 'center',
                fontSize: '14px'
              }}>
                <span>
                  {photoLoadState === 'error' ? '照片加载失败，请检查网络后重试' : '正在加载照片…'}
                </span>
                {photoLoadState === 'error' && (
                  <button
                    type="button"
                    onClick={() => {
                      setPhotoLoadState('loading')
                      setPhotoRetry(retry => retry + 1)
                    }}
                    style={{
                      padding: '9px 16px',
                      border: '1px solid rgba(255, 255, 255, 0.6)',
                      borderRadius: '18px',
                      background: 'rgba(255, 255, 255, 0.14)',
                      color: '#fff',
                      fontSize: '14px',
                      cursor: 'pointer'
                    }}
                  >
                    重新加载
                  </button>
                )}
              </div>
            )}
          </div>

          <div
            onClick={event => event.stopPropagation()}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '16px',
              padding: '12px 16px max(20px, env(safe-area-inset-bottom))'
            }}
          >
            {technicianPhotos.length > 1 && (
              <button
                type="button"
                onClick={() => {
                  setPhotoViewerIndex(index => (index - 1 + technicianPhotos.length) % technicianPhotos.length)
                  setPhotoZoom(1)
                  setPhotoLoadState('loading')
                  setPhotoRetry(0)
                }}
                aria-label="上一张照片"
                style={photoViewerButtonStyle}
              >
                ‹
              </button>
            )}
            <button
              type="button"
              onClick={() => setPhotoZoom(zoom => Math.max(1, zoom - 0.5))}
              aria-label="缩小照片"
              style={photoViewerButtonStyle}
            >
              −
            </button>
            <span style={{ minWidth: '48px', textAlign: 'center', fontSize: '14px' }}>
              {Math.round(photoZoom * 100)}%
            </span>
            <button
              type="button"
              onClick={() => setPhotoZoom(zoom => Math.min(3, zoom + 0.5))}
              aria-label="放大照片"
              style={photoViewerButtonStyle}
            >
              +
            </button>
            {technicianPhotos.length > 1 && (
              <button
                type="button"
                onClick={() => {
                  setPhotoViewerIndex(index => (index + 1) % technicianPhotos.length)
                  setPhotoZoom(1)
                  setPhotoLoadState('loading')
                  setPhotoRetry(0)
                }}
                aria-label="下一张照片"
                style={photoViewerButtonStyle}
              >
                ›
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
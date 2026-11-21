import { useEffect, useRef, useState, useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useModuleState, useModuleScroll } from '../utils/moduleState'
import ModuleIcon from '../components/ModuleIcon'
import StaffGallery from '../components/StaffGallery'
import VenueRecommendations from '../components/VenueRecommendations'
import DeliveryArchive from '../components/DeliveryArchive'
import VenueChrome from '../components/venue/VenueChrome'
import KtvBoard from '../components/venue/KtvBoard'
import TechnicianBoard from '../components/venue/TechnicianBoard'
import DeliveryBoard from '../components/delivery/DeliveryBoard'
import { useAnalyticsScreen } from '../utils/analytics'
import { bookingUrl } from '../utils/booking'
import { contactUrl, mediaUrl } from '../utils/tenant'
import {
  API_BASE_URL,
  bindBackButton,
  errorText,
  sendData,
  subscribeFullscreen,
} from '../utils/twa'
import {
  DELIVERY_VENUE_IDS,
  PHOTO_FIRST_VENUE_IDS,
  STAFF_ARCHIVE_VENUE_IDS,
  deliveryCategoryKey,
  mergeDeliveryVenues,
  pickHomeVenue,
  roomyTechnicianGridStyle,
  technicianGridStyle,
} from '../utils/venueHome'

export default function VenueList() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const requestedModule = searchParams.get('module')
  const [isFullscreen, setIsFullscreen] = useState(() => Boolean(window.Telegram?.WebApp?.isFullscreen))
  useEffect(() => subscribeFullscreen(setIsFullscreen), [])
  const [list, setList] = useState([])
  const [showStaffArchive, setShowStaffArchive] = useState(false)
  const [selectedId, setSelectedId] = useModuleState('venue.selection', '')
  const [lastVenueId, setLastVenueId] = useModuleState('venue.lastClub', '')
  const [deliveryCategoryId, setDeliveryCategoryId] = useModuleState('delivery.category', '')
  const [deliveryViewer, setDeliveryViewer] = useState(null)
  const [deliveryIndex, setDeliveryIndex] = useState(0)
  const openDeliveryViewer = post => {
    setDeliveryIndex(0)
    setDeliveryViewer(post)
  }
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
  useModuleScroll(requestedModule === 'delivery' ? 'delivery' : 'venue', !loading)

  useEffect(() => {
    if (!list.length || !requestedModule) return
    setSelectedId(current => {
      const selected = list.find(venue => String(venue.id) === current)
      const wantsDelivery = requestedModule === 'delivery'
      if (selected && Boolean(selected.delivery_categories) === wantsDelivery) return current
      const candidates = list.filter(venue => Boolean(venue.delivery_categories) === wantsDelivery)
      if (!wantsDelivery && candidates.some(venue => String(venue.id) === lastVenueId)) return lastVenueId
      return String(pickHomeVenue(candidates)?.id || current)
    })
  }, [requestedModule, list, setSelectedId, lastVenueId])

  useEffect(() => {
    const selected = list.find(venue => String(venue.id) === selectedId)
    if (selected && !selected.delivery_categories) setLastVenueId(selectedId)
  }, [selectedId, list, setLastVenueId])

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
        setError(errorText(e.data.payload.error))
        setLoading(false)
        return
      }
      const data = e.data.payload?.list || e.data.payload?.venues || e.data.payload
      if (Array.isArray(data)) {
        const merged = mergeDeliveryVenues(data)
        setError('')
        setList(merged)
        setSelectedId(currentId => currentId || String(pickHomeVenue(merged)?.id ?? ''))
      }
      setLoading(false)
    }
    window.addEventListener('message', fn)
    sendData({ action: 'get_venues' })
    const refresh = () => {
      if (document.visibilityState === 'visible') sendData({ action: 'get_venues' })
    }
    const timer = setInterval(refresh, 60000)
    document.addEventListener('visibilitychange', refresh)
    window.addEventListener('focus', refresh)
    return () => {
      window.removeEventListener('message', fn)
      clearInterval(timer)
      document.removeEventListener('visibilitychange', refresh)
      window.removeEventListener('focus', refresh)
    }
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
  const isPhotoFirst = PHOTO_FIRST_VENUE_IDS.includes(Number(selectedVenue?.id))
  const rosterGridStyle = technicianList.length <= 9
    ? roomyTechnicianGridStyle
    : technicianGridStyle
  const currentShift = selectedVenue?.current_shift || null
  const isDelivery = DELIVERY_VENUE_IDS.has(selectedVenue?.id)
  const hasStaffArchive = STAFF_ARCHIVE_VENUE_IDS.includes(selectedVenue?.id) || isDelivery
  const attendanceViewLabel = isDelivery ? '晚班出勤' : currentShift === '晚班' ? '今晚出勤' : '今日出勤'
  useEffect(() => {
    setShowStaffArchive(false)
    setSelectedTechnicianCode('')
  }, [selectedVenue?.id, selectedVenue?.categoryKey])
  const isDeliveryDayShift = deliveryCategories.length > 0 && currentShift === '白班'
  const dutyShiftToday = currentShift || selectedVenue?.duty_shift_today
  const dutyLabel = currentShift ? `${currentShift}出勤` : '今日出勤'
  const serviceItems = selectedVenue?.service_items || []
  const technicianPhotos = selectedVenue?.technician_photos?.[selectedTechnicianCode] || []
  const technicianProfile = selectedVenue?.technician_profiles?.[selectedTechnicianCode] || {}
  const attendanceGroups = selectedVenue?.attendance_groups || []
  const rosterGroups = attendanceGroups.filter(group => group.group_type === 'roster')
  const attendanceServiceGroups = attendanceGroups.filter(group => group.group_type === 'service')

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
    }
  }
  const goBackRef = useRef(goBack)
  goBackRef.current = goBack
  const hasDetail = Boolean(ktvVideo || deliveryViewer || photoViewerIndex !== null || selectedTechnicianCode)
  useAnalyticsScreen(ktvVideo ? 'ktv_player' : deliveryViewer || photoViewerIndex !== null ? 'media_viewer' : selectedTechnicianCode ? 'technician_detail' : null)
  useEffect(() => bindBackButton(hasDetail ? () => goBackRef.current() : null), [hasDetail])
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

  const deliveryBookingUrl = deliveryViewer ? bookingUrl({
    title: `你好，我想预约${baseVenue?.name || '西港外送'}`,
    links: deliveryViewer.link ? [deliveryViewer.link] : deliveryViewer.media.map(media => (
      new URL(mediaUrl(`${API_BASE_URL}/delivery-media/${media.id}`), window.location.origin).href
    )),
    details: [
      deliveryViewer.work_no && `工号：${deliveryViewer.work_no}`,
      deliveryViewer.price && `价格：${deliveryViewer.price}$`,
      deliveryViewer.description,
    ],
  }) : ''

  const contactManagerUrl = selectedTechnicianCode && selectedVenue
    ? (() => {
        const originalLinks = selectedVenue.technician_photo_links?.[selectedTechnicianCode] || []
        const photoLinks = originalLinks.length ? originalLinks : technicianPhotos.map(id => (
          new URL(mediaUrl(`${API_BASE_URL}/photos/${id}/image`), window.location.origin).href
        ))
        return bookingUrl({
          title: `你好，我想咨询这位技师：${selectedVenue.name} · 工号${selectedTechnicianCode}`,
          links: photoLinks,
          details: [
          `会所：${selectedVenue.name}`,
          `工号：${selectedTechnicianCode}`,
          ...[['age', '年龄'], ['height', '身高'], ['bust', '胸围'], ['weight', '体重'], ['origin', '产地']]
            .filter(([field]) => technicianProfile[field])
            .map(([field, label]) => `${label}：${technicianProfile[field]}${field === 'height' ? ' cm' : field === 'age' ? ' 岁' : ''}`),
          ...(technicianProfile.service_description
            ? [`资料描述：${technicianProfile.service_description}`] : []),
          ['4', '5', '6'].includes(String(selectedVenue.id))
            ? '来源：会所频道照片'
            : `出勤：${dutyShiftToday || '今日在岗'}`,
          ...(technicianServiceCategories.length > 0
            ? [`可服务类别：${technicianServiceCategories.join('、')}`]
            : []),
          ...(photoLinks.length ? [] : ['该技师暂无可用照片链接']),
          ],
        })
      })()
    : contactUrl()

  return (
    <div className="home-page" style={{
      minHeight: '100vh',
      background: 'transparent',
      padding: '12px',
      boxSizing: 'border-box'
    }}>
      <VenueChrome
        isFullscreen={isFullscreen}
        loading={loading}
        error={error}
        list={list}
        selectedId={selectedId}
        setSelectedId={setSelectedId}
        navigate={navigate}
        deliveryCategories={deliveryCategories}
        deliveryCategoryId={deliveryCategoryId}
        setDeliveryCategoryId={setDeliveryCategoryId}
        selectedVenue={selectedVenue}
        isPhotoFirst={isPhotoFirst}
        showStaffArchive={showStaffArchive}
        setShowStaffArchive={setShowStaffArchive}
        setSelectedTechnicianCode={setSelectedTechnicianCode}
        hasStaffArchive={hasStaffArchive}
        attendanceViewLabel={attendanceViewLabel}
        isDeliveryDayShift={isDeliveryDayShift}
        dutyLabel={dutyLabel}
        onDutyCount={onDutyCount}
      />

      {loading ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: '#999' }}>
          正在读取…
        </div>
      ) : error ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: '#c53030' }}>
          {error}
        </div>
      ) : list.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: '#999' }}>
          <div className="home-notice-icon"><ModuleIcon name="search" /></div>
          <p>暂无会所数据</p>
        </div>
      ) : !selectedVenue ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: '#999' }}>
          正在读取…
        </div>
      ) : (
        <div id="venue-personnel-content">
          {isPhotoFirst && <VenueRecommendations venueId={selectedVenue.id} />}
          {isPhotoFirst
            ? isDelivery
              ? <DeliveryArchive key={`${deliveryCategoryKey(selectedVenue)}-${showStaffArchive}`} venue={selectedVenue} photoFirst attendanceOnly={!showStaffArchive} />
              : <StaffGallery key={`${selectedVenue.id}-${showStaffArchive}`} venue={selectedVenue} photoFirst attendanceOnly={!showStaffArchive} />
            : hasStaffArchive && showStaffArchive
            ? isDelivery
              ? <DeliveryArchive key={deliveryCategoryKey(selectedVenue)} venue={selectedVenue} />
              : <StaffGallery key={selectedVenue.id} venue={selectedVenue} />
            : <>
          {isDeliveryDayShift || Array.isArray(selectedVenue.delivery_posts) ? (
            <DeliveryBoard
              selectedVenue={selectedVenue}
              isDeliveryDayShift={isDeliveryDayShift}
              deliveryViewer={deliveryViewer}
              setDeliveryViewer={setDeliveryViewer}
              deliveryIndex={deliveryIndex}
              setDeliveryIndex={setDeliveryIndex}
              openDeliveryViewer={openDeliveryViewer}
              deliveryBookingUrl={deliveryBookingUrl}
            />
          ) : String(selectedVenue.id) === '3' ? (
            <KtvBoard
              selectedVenue={selectedVenue}
              ktvVideo={ktvVideo}
              setKtvVideo={setKtvVideo}
              closeKtvVideo={closeKtvVideo}
            />
          ) : (
            <TechnicianBoard
              selectedVenue={selectedVenue}
              onDutyCount={onDutyCount}
              dutyLabel={dutyLabel}
              currentShift={currentShift}
              dutyShiftToday={dutyShiftToday}
              technicianList={technicianList}
              rosterGridStyle={rosterGridStyle}
              rosterGroups={rosterGroups}
              attendanceServiceGroups={attendanceServiceGroups}
              serviceGroups={serviceGroups}
              selectedTechnicianCode={selectedTechnicianCode}
              setSelectedTechnicianCode={setSelectedTechnicianCode}
              technicianPhotos={technicianPhotos}
              technicianProfile={technicianProfile}
              technicianServiceCategories={technicianServiceCategories}
              contactManagerUrl={contactManagerUrl}
              photoViewerIndex={photoViewerIndex}
              setPhotoViewerIndex={setPhotoViewerIndex}
              photoZoom={photoZoom}
              setPhotoZoom={setPhotoZoom}
              photoLoadState={photoLoadState}
              setPhotoLoadState={setPhotoLoadState}
              photoRetry={photoRetry}
              setPhotoRetry={setPhotoRetry}
              widePhotos={widePhotos}
              setWidePhotos={setWidePhotos}
            />
          )}
          </>}
        </div>
      )}
    </div>
  )
}
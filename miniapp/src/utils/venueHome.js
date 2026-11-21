import venuePrices from '../../../shared/venue-prices.json'
import { bookingUrl } from './booking'
import { mediaUrl } from './tenant'
import { API_BASE_URL } from './twa'

// 首页默认显示“名玺水汇会所”，找不到时退回列表第一个
export const HOME_VENUE_NAME = '名玺水汇会所'
export const pickHomeVenue = venues => venues.find(venue => venue.name === HOME_VENUE_NAME) || venues[0]

export const lazyFillStyle = {
  position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover'
}

export const photoViewerButtonStyle = {
  width: '44px',
  height: '44px',
  border: '1px solid rgba(255, 255, 255, 0.35)',
  borderRadius: '50%',
  background: 'rgba(255, 255, 255, 0.12)',
  color: '#fff',
  fontSize: '26px',
  cursor: 'pointer'
}

export const attendancePanelStyle = {
  marginBottom: '20px',
  background: 'var(--home-surface)',
  border: '1px solid var(--ink-line)',
  boxShadow: 'var(--shadow)',
  borderRadius: '12px',
  padding: '16px'
}

export const greenSectionHeadingStyle = {
  fontSize: '15px',
  color: 'var(--ink-accent)',
  margin: 0,
  fontWeight: 600
}

export const ktvBookingButtonStyle = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  minHeight: '44px',
  marginTop: '6px',
  padding: '6px 14px',
  borderRadius: '16px',
  background: 'var(--ink-accent)',
  color: '#fffef5',
  fontSize: '13px',
  textDecoration: 'none',
  cursor: 'pointer'
}

export const DELIVERY_VENUE_IDS = new Set([7, 8, 9, 10])

export const deliveryCategoryKey = venue => venue.categoryKey || String(venue.id)

export const deliveryCategoryLabel = venue => venue.categoryLabel
  || venue.name.replace(/^西港/, '').replace(/外送$/, '')

// 小苹果/红苹果共用同一个频道，按内容文字里的类别分到各自的按钮下；未判断出类别的两边都显示
export const expandDeliveryCategories = delivery => delivery.flatMap(venue => {
  if (Number(venue.id) !== 10) return [venue]
  const posts = venue.delivery_posts || []
  const pick = category => posts.filter(post => !post.category || post.category === category)
  return [
    { ...venue, categoryKey: '10-small', categoryLabel: '小苹果', delivery_posts: pick('small') },
    { ...venue, categoryKey: '10-red', categoryLabel: '红苹果', delivery_posts: pick('red') }
  ]
})

// 西港170/180/160 与小苹果/红苹果外送合并为“西港外送”，原会所作为其下的类别
export const mergeDeliveryVenues = venues => {
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

export const ktvVideoInfo = video => {
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

export const ktvPriceLines = text => (text || '')
  .split(/\n-{5,}/)[0]
  .split('\n')
  .map(line => line.trim())
  .filter(line => line && !/^10\s*[:：]/.test(line))

export const ktvBookingUrl = video => {
  const room = video.room_number ? `${video.room_number}号房` : '包房'
  return bookingUrl({
    title: `你好，我想预定名玺水汇KTV ${room}`,
    links: [new URL(mediaUrl(`${API_BASE_URL}/ktv-videos/${video.id}`), window.location.origin).href],
    details: [video.caption],
  })
}

export const attendanceHeadingRowStyle = {
  display: 'flex',
  alignItems: 'baseline',
  justifyContent: 'space-between',
  flexWrap: 'wrap',
  columnGap: '0',
  rowGap: '4px'
}

export const recentUpdateStyle = {
  color: '#888',
  fontSize: '10px',
  whiteSpace: 'nowrap'
}

export const attendanceInlineHeadingStyle = {
  ...greenSectionHeadingStyle,
  fontSize: '14px'
}

export const technicianGridStyle = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fill, minmax(52px, 1fr))',
  gap: '8px'
}

export const roomyTechnicianGridStyle = {
  display: 'grid',
  gridTemplateColumns: 'repeat(3, 1fr)',
  gap: '12px'
}

export const {
  mingxiBoardPrices,
  kaidiTechnicianCategories,
  atlanticTechnicianCategories,
  dihaoTechnicianCategories
} = venuePrices

export const formatChannelMessageTime = timestamp => {
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

export const getKaidiCategoryCodes = (selectedVenue, category) => {
  const prefix = category.name.match(/^\d/)?.[0]
  const photoCodes = Object.keys(selectedVenue?.technician_photos || {}).filter(code => (
    category.name === '车模'
      ? ['6666', '7777'].includes(code)
      : prefix !== undefined && code.length <= 3 && code.startsWith(prefix)
  ))
  return [...new Set(photoCodes)]
}

export const getAtlanticCategoryCodes = (selectedVenue, category) => {
  const photoCodes = selectedVenue?.technician_categories
    ?.find(item => item.group_name === category.name)
    ?.technician_codes || []
  return [...new Set(photoCodes)]
}

export const getDihaoCategoryCodes = (selectedVenue, category) => (
  selectedVenue?.technician_categories
    ?.find(item => item.group_name === category.name)
    ?.technician_codes || []
)

export const PHOTO_FIRST_VENUE_IDS = [2, 4, 5, 6, 7, 8, 9, 10]
export const STAFF_ARCHIVE_VENUE_IDS = [1, 2, 4, 5, 6]
export const CHANNEL_PHOTO_VENUE_IDS = ['4', '5', '6']
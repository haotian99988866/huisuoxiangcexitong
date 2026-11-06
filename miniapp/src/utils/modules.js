import { contactName, contactUrl, getTenant, hasAnnouncementAccess, hasDeliveryAccess, hasVenueAccess } from './tenant.js'

export const moduleGroups = [
  { key: 'content', label: '常用内容' },
  { key: 'convenience', label: '便民服务' },
  { key: 'personal', label: '个人与帮助' },
]

export const modules = [
  { key: 'media', label: '影音', path: '/movies', startParam: 'media', group: 'content', description: '电影与音乐，一站浏览' },
  { key: 'venue', label: '会所', icon: '🏠', path: '/?module=venue', startParam: 'venue', group: 'content', common: true, description: '当班出勤与技师资料' },
  { key: 'delivery', label: '外送', icon: '🛵', path: '/?module=delivery', startParam: 'delivery', group: 'content', common: true, description: '西港外送实时信息' },
  { key: 'movies', label: '观影', icon: '🎬', path: '/movies', startParam: 'movies', group: 'content', common: true, description: '分类找剧与剧集浏览' },
  { key: 'music', label: '音乐', icon: '🎵', path: '/music', startParam: 'music', group: 'content', common: true, description: '授权音乐 · 在线播放' },
  { key: 'announcements', label: '东南亚大事件', icon: '📢', path: '/announcements', startParam: 'announcements', group: 'content', description: '最新公告与历史消息' },
  { key: 'services', label: '服务中心', path: '/services', startParam: 'services', group: 'convenience', description: '会员套餐与便民服务' },
  { key: 'shortDramas', label: '短剧合集', path: '/short-dramas', startParam: 'short_dramas', group: 'convenience', description: '短剧合集 · Telegram 播放' },
  { key: 'recharge', label: '余额充值', icon: '💳', path: '/recharge', startParam: 'recharge', group: 'personal', description: 'USDT / ABA · 人工核款入账' },
  { key: 'membership', label: 'Telegram会员', icon: '⭐', path: '/membership', startParam: 'membership', group: 'convenience', description: '选套餐 · 确认余额购买' },
  { key: 'phone', label: '柬埔寨话费充值', icon: '📱', path: '/phone-topup', startParam: 'phone', group: 'convenience', description: '填写手机号 · 人工代充' },
  { key: 'hotels', label: '酒店推荐', icon: '🏨', path: '/hotels', startParam: 'hotels', group: 'convenience', description: '门头照片 · 地图与前台联系方式' },
  { key: 'personal', label: '我的', icon: '👤', path: '/personal', startParam: 'personal', group: 'personal', description: '钱包、订单、个人内容与帮助' },
  { key: 'admin', label: '运营管理台', path: '/admin', startParam: 'admin', adminOnly: true },
  { key: 'contact', label: '联系昊天', icon: '💬', url: 'https://t.me/haotian99988866', group: 'personal', description: '咨询与人工帮助' },
]

export function displayModules() {
  if (!getTenant()) return modules.filter(item => !item.adminOnly)
  return modules.filter(item => ['movies', 'contact'].includes(item.key) ||
    (item.key === 'announcements' && hasAnnouncementAccess()) ||
    (item.key === 'venue' && hasVenueAccess()) ||
    (item.key === 'delivery' && hasDeliveryAccess())).map(item => (
    item.key === 'contact' ? { ...item, label: `联系${contactName()}`, url: contactUrl() } : item
  ))
}

import { contactName, contactUrl, getTenant } from './tenant'

export const moduleGroups = [
  { key: 'content', label: '常用内容' },
  { key: 'convenience', label: '便民服务' },
  { key: 'personal', label: '个人与帮助' },
]

export const modules = [
  { key: 'venue', label: '会所', icon: '🏠', path: '/?module=venue', group: 'content', common: true, description: '当班出勤与技师资料' },
  { key: 'delivery', label: '外送', icon: '🛵', path: '/?module=delivery', group: 'content', common: true, description: '西港外送实时信息' },
  { key: 'movies', label: '观影', icon: '🎬', path: '/movies', startParam: 'movies', group: 'content', common: true, description: '分类找剧与剧集浏览' },
  { key: 'recharge', label: '充值中心', icon: '💳', path: '/recharge', startParam: 'recharge', group: 'convenience', description: '余额充值 · 商品筹备中' },
  { key: 'personal', label: '个人中心', icon: '👤', path: '/personal', startParam: 'personal', group: 'personal', description: '账户余额、充值记录与帮助' },
  { key: 'contact', label: '联系昊天', icon: '💬', url: 'https://t.me/haotian99988866', group: 'personal', description: '咨询与人工帮助' },
]

export function displayModules() {
  if (!getTenant()) return modules
  return modules.filter(item => ['venue', 'movies', 'contact'].includes(item.key)).map(item => (
    item.key === 'contact' ? { ...item, label: `联系${contactName()}`, url: contactUrl() } : item
  ))
}

import { API_BASE_URL } from './twa'
import { useEffect } from 'react'

const listeners = new Set()
let preferences = null
let pending
const screens = []
const screenListeners = new Set()
export const analyticsScreen = () => screens.at(-1)?.page || null
export function subscribeAnalyticsScreen(listener) {
  screenListeners.add(listener)
  return () => screenListeners.delete(listener)
}
export function useAnalyticsScreen(page) {
  useEffect(() => {
    if (!page) return
    const entry = { page }
    screens.push(entry)
    for (const listener of screenListeners) listener(analyticsScreen())
    return () => {
      const index = screens.indexOf(entry)
      if (index >= 0) screens.splice(index, 1)
      for (const listener of screenListeners) listener(analyticsScreen())
    }
  }, [page])
}

export const analyticsId = () => crypto.randomUUID().replaceAll('-', '')
export const analyticsPreferences = () => preferences
export function subscribeAnalytics(listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export async function analyticsRequest(action, fields = {}, keepalive = false) {
  const initData = window.Telegram?.WebApp?.initData
  if (!initData) throw new Error('请从 Telegram 主入口打开')
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15000)
  try {
    const response = await fetch(`${API_BASE_URL}/webhook`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...fields, action, init_data: initData }),
      cache: 'no-store', keepalive, signal: controller.signal,
    })
    const result = await response.json()
    if (!response.ok) throw new Error(result.error || '浏览统计请求失败')
    return result
  } finally { clearTimeout(timer) }
}

export async function loadAnalyticsPreferences(recording) {
  if (recording === undefined && preferences) return preferences
  if (recording === undefined && pending) return pending
  const request = analyticsRequest('analytics_preferences',
    recording === undefined ? {} : { recording })
    .then(result => {
      preferences = result
      for (const listener of listeners) listener(result)
      return result
    })
  if (recording === undefined) {
    pending = request
    try { return await request }
    finally { pending = undefined }
  }
  return request
}

export function analyticsPage(path, search) {
  if (path === '/') return new URLSearchParams(search).get('module') === 'delivery' ? 'delivery' : 'venue'
  if (/^\/venue\/[^/]+\/technicians$/.test(path)) return 'technicians'
  if (/^\/venue\/[^/]+$/.test(path)) return 'venue_detail'
  if (/^\/announcements\/[^/]+$/.test(path)) return 'announcement_detail'
  if (/^\/orders\/[^/]+$/.test(path)) return 'order_detail'
  return ({
    '/movies': 'movies', '/media': 'movies', '/music': 'music',
    '/announcements': 'announcements', '/services': 'services', '/hotels': 'hotels', '/personal': 'personal',
    '/recharge': 'recharge', '/membership': 'membership', '/phone-topup': 'phone_topup', '/orders': 'orders',
    '/wallet-history': 'wallet_history', '/admin': 'admin', '/consumption': 'consumption',
  })[path] || 'other'
}

export const pageLabels = {
  venue: '会所', delivery: '外送', venue_detail: '会所详情', technicians: '技师团队',
  movies: '观影', music: '音乐', announcements: '东南亚大事件',
  announcement_detail: '资讯详情', services: '服务', hotels: '酒店推荐', personal: '我的', recharge: '余额充值',
  membership: '会员套餐', phone_topup: '柬埔寨话费代充', orders: '订单', order_detail: '订单详情',
  wallet_history: '钱包明细', consumption: '我的消费', admin: '管理台', other: '其他页面',
  technician_detail: '技师资料', movie_detail: '影视选集', media_viewer: '照片 / 外送详情',
  ktv_player: 'KTV视频', music_player: '完整音乐播放器',
}

export function duration(ms) {
  const seconds = Math.floor(Number(ms || 0) / 1000)
  if (seconds < 60) return `${seconds}秒`
  const minutes = Math.floor(seconds / 60)
  return minutes < 60 ? `${minutes}分${seconds % 60}秒` : `${Math.floor(minutes / 60)}小时${minutes % 60}分`
}

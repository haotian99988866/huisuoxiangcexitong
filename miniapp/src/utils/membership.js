import { API_BASE_URL } from './twa'
import { isTenantEntry } from './tenant'

const SESSION_KEY = 'member-browser-session'
let loginPromise
let loginToken

export function isTelegramMemberEntry() {
  return Boolean(window.Telegram?.WebApp?.initData)
}

export async function requestMember(action, fields = {}) {
  if (isTenantEntry()) throw new Error('会所入口不开放账户或会员购买。')
  const initData = window.Telegram?.WebApp?.initData
  const session = sessionStorage.getItem(SESSION_KEY)
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 20000)
  try {
    const response = await fetch(`${API_BASE_URL}/webhook`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(session ? { Authorization: `Bearer ${session}` } : {}) },
      body: JSON.stringify({ ...fields, action, ...(initData ? { init_data: initData } : {}) }),
      cache: 'no-store',
      referrerPolicy: 'no-referrer',
      signal: controller.signal,
    })
    const result = await response.json()
    if (!response.ok) {
      if (response.status === 401) sessionStorage.removeItem(SESSION_KEY)
      const error = new Error(result.error || '会员请求失败，请先核实订单状态。')
      error.status = response.status
      throw error
    }
    return result
  } catch (failure) {
    if (failure.name === 'AbortError') throw new Error('账户请求超时；交易结果未确认时请先查询记录，勿重复付款或重新下单。')
    throw failure
  } finally { clearTimeout(timer) }
}

export function initializeMemberSession() {
  if (isTelegramMemberEntry()) return Promise.resolve(true)
  const token = new URLSearchParams(window.location.hash.slice(1)).get('login')
  if (token && token !== loginToken) {
    loginPromise = undefined
    loginToken = token
  }
  if (loginPromise) return loginPromise
  loginPromise = (async () => {
    if (token) {
      window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`)
      sessionStorage.removeItem(SESSION_KEY)
      const result = await requestMember('member_login', { token })
      sessionStorage.setItem(SESSION_KEY, result.session)
    }
    return Boolean(sessionStorage.getItem(SESSION_KEY))
  })()
  return loginPromise
}

export async function logoutMember() {
  await requestMember('member_logout')
  sessionStorage.removeItem(SESSION_KEY)
  loginPromise = undefined
  loginToken = undefined
}

export function newMemberOrderId() {
  return [...crypto.getRandomValues(new Uint8Array(16))]
    .map(value => value.toString(16).padStart(2, '0')).join('')
}

export const memberStatuses = {
  paid: '余额已扣除，等待开通',
  delivering: '会员充值中，请等待',
  delivered: '会员已开通',
  refund_pending: '开通失败，余额退款处理中',
  refunded: '已退回账户余额',
  delivery_unknown: '开通结果核实中，请勿重复购买',
}

export function resolveTenant(search, startParam) {
  const params = new URLSearchParams(search)
  const explicit = params.has('tenant') ? params.get('tenant') : null
  const started = typeof startParam === 'string' && startParam.startsWith('tenant_')
    ? startParam.slice(7) : null
  if (explicit !== null && started !== null && explicit !== started) {
    throw new Error('租户入口参数冲突，请从专属链接重新打开')
  }
  const slug = explicit !== null ? explicit : started
  if (slug !== null && !/^[a-z0-9][a-z0-9-]{2,31}$/.test(slug)) {
    throw new Error('租户入口无效，请从专属链接重新打开')
  }
  return slug
}

let tenant = null
let slug = null

export function initializeTenant() {
  slug = resolveTenant(
    window.location.search,
    window.Telegram?.WebApp?.initDataUnsafe?.start_param,
  )
  return slug
}

export function setTenant(config) {
  if (slug !== null && (!config || config.slug !== slug ||
      typeof config.brand_name !== 'string' || typeof config.contact_name !== 'string' ||
      !/^[A-Za-z][A-Za-z0-9_]{4,31}$/.test(config.contact_username) ||
      !Array.isArray(config.venue_ids))) {
    throw new Error('租户配置无效，请联系服务提供方')
  }
  tenant = config
}

export const getTenant = () => tenant
export const hasDeliveryAccess = () => Boolean(tenant?.venue_ids.some(id => [7, 8, 9, 10].includes(id)))
export const hasVenueAccess = () => Boolean(tenant?.venue_ids.some(id => [1, 2, 3, 4, 5, 6].includes(id)))
export const isTenantEntry = () => slug !== null
export const tenantHeaders = () => slug !== null ? { 'X-Tenant': slug } : {}

export function tenantPath(href) {
  if (slug === null) return href
  const url = new URL(href, window.location.origin)
  url.searchParams.set('tenant', slug)
  return `${url.pathname}${url.search}${url.hash}`
}

export function mediaUrl(href) {
  if (slug === null) return href
  const url = new URL(href, window.location.origin)
  url.searchParams.set('tenant', slug)
  return url.href
}

export const contactName = () => tenant?.contact_name || '昊天'
export const contactUrl = () => `https://t.me/${tenant?.contact_username || 'haotian99988866'}`
export const announcementText = () => tenant
  ? `${tenant.brand_name} · ${hasDeliveryAccess() ? '会所、外送与观影' : '会所与观影'}信息服务`
  : '充值模块开发中，商品购买暂未开放'

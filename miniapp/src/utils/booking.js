import { contactUrl } from './tenant.js'

const MAX_DRAFT_LENGTH = 4096

export function bookingUrl({ title, links = [], details = [] }) {
  const sources = [...new Set(links.filter(Boolean))]
  // Keep source links ahead of optional descriptions when Telegram truncates drafts.
  const header = [title, ...(sources.length ? ['原帖 / 图片链接：', ...sources] : [])].join('\n')
  const message = [header, ...details.filter(Boolean)].join('\n')
  const text = Array.from(message).slice(0, MAX_DRAFT_LENGTH).join('')
  return `${contactUrl()}?text=${encodeURIComponent(text)}`
}

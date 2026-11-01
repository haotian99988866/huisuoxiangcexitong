import { contactUrl, isTenantEntry } from './tenant'
import { initializeMemberSession, requestMember } from './membership'

let lastAttempt = 0

export function notifyContactProfile(href) {
  if (isTenantEntry()) return
  const target = new URL(href)
  const contact = new URL(contactUrl())
  if (target.origin !== contact.origin || target.pathname.toLowerCase() !== contact.pathname.toLowerCase()) return
  if (Date.now() - lastAttempt < 5000) return
  lastAttempt = Date.now()
  Promise.resolve().then(async () => {
    if (!await initializeMemberSession()) return
    await requestMember('customer_contact')
  }).catch(error => {
    console.error('[Customer contact reminder]', error)
    window.dispatchEvent(new CustomEvent('contact-profile-error', {
      detail: '联系入口已打开，但客户档案提醒未能确认发送。可正常聊天，请联系昊天人工核对档案。',
    }))
  })
}

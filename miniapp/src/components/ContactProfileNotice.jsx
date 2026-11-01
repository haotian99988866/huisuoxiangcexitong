import { useEffect, useState } from 'react'
import { notifyContactProfile } from '../utils/contactProfile'
import './CustomerRecords.css'

export default function ContactProfileNotice() {
  const [message, setMessage] = useState('')
  useEffect(() => {
    const notice = event => setMessage(event.detail)
    const contact = event => notifyContactProfile(event.detail)
    window.addEventListener('contact-profile-error', notice)
    window.addEventListener('customer-contact-open', contact)
    return () => {
      window.removeEventListener('contact-profile-error', notice)
      window.removeEventListener('customer-contact-open', contact)
    }
  }, [])
  return message ? <aside className="crm-notification" role="alert">
    <p>{message}</p><button type="button" onClick={() => setMessage('')}>知道了</button>
  </aside> : null
}

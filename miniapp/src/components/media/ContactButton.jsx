import CopyBooking from '../CopyBooking'
import { bookingUrl } from '../../utils/booking'
import { contactName } from '../../utils/tenant'

export default function ContactButton({ title, contactOnly = false }) {
  return <CopyBooking url={bookingUrl({
    title: `你好，我想咨询：${title}`,
    links: [window.location.href],
  })} compact={contactOnly} label={contactOnly ? `联系${contactName()}` : null}
    showCopySuccess={!contactOnly} />
}
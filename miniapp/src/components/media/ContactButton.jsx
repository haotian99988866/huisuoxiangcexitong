import CopyBooking from '../CopyBooking'
import { bookingUrl } from '../../utils/booking'

export default function ContactButton({ title }) {
  return <CopyBooking url={bookingUrl({
    title: `你好，我想咨询：${title}`,
    links: [window.location.href],
  })} />
}
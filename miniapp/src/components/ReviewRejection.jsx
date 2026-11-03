import { openTelegramLink } from '../utils/twa'

export default function ReviewRejection({ reason }) {
  return <div className="review-rejection">
    <p>拒绝原因：{reason || '此历史记录未填写原因，请联系管理员核实。'}</p>
    <button type="button" onClick={() => openTelegramLink('https://t.me/haotian99988866')}>有疑问，联系管理员昊天</button>
  </div>
}

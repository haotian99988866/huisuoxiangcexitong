import { useEffect, useState } from 'react'
import { requestMember } from '../utils/membership'
import { openTelegramLink } from '../utils/twa'
import { consumptionDate } from '../utils/customerRecords'
import './CouponPanel.css'

const time = consumptionDate
const invitationState = {
  pending: '留存中', qualified: '已达标', left: '未满7天退出',
  review: '留存待人工核实', invalid: '未通过核实',
}

export default function CouponPanel({ userId }) {
  const [data, setData] = useState(null)
  const [page, setPage] = useState(1)
  const [voucherPage, setVoucherPage] = useState(1)
  const [reload, setReload] = useState(0)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    requestMember('coupon_mine', { page, voucher_page: voucherPage })
      .then(result => { if (active) setData(result) })
      .catch(failure => { if (active) { setData(null); setError(failure.message) } })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [userId, page, voucherPage, reload])

  async function share() {
    setBusy(true)
    setError('')
    try {
      const { invite_link: link } = await requestMember('coupon_invite')
      setReload(value => value + 1)
      openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent('邀请你加入频道')}`)
    } catch (failure) {
      setError(failure.message)
    } finally { setBusy(false) }
  }

  return <section className="membership-panel coupon-panel" aria-labelledby="my-coupons" aria-busy={loading}>
    <div className="membership-row">
      <h2 id="my-coupons">邀请好友 · 赢 $20</h2>
      <button className="coupon-refresh" type="button" disabled={loading} onClick={() => setReload(value => value + 1)}>刷新</button>
    </div>
    <p className="coupon-intro">好友留存7天并通过核验，每5人得一张代金券。</p>
    {error && <p className="membership-error" role="alert">{error}
      <button type="button" onClick={() => setReload(value => value + 1)}>重新读取</button></p>}
    {loading && <p role="status">{data ? '正在更新邀请进度…' : '正在读取邀请进度…'}</p>}
    {data && <>
      {!data.enabled && <p role="status">活动暂未开放，已获得的代金券仍可查看。</p>}
      <div className="coupon-summary" aria-label="本轮邀请进度">
        <div><strong>{data.eligible}</strong><span>已达标</span></div>
        <div><strong>{data.retaining}</strong><span>留存中</span></div>
        <div><strong>{Math.max(0, 5 - data.eligible - data.retaining)}</strong><span>还需邀请</span></div>
        <div><strong>{Number.isInteger(data.available_voucher_total) ? data.available_voucher_total : '—'}</strong><span>有效券</span></div>
      </div>
      {!Number.isInteger(data.available_voucher_total) && <p role="status">有效券数量暂不可用，请稍后重试。</p>}
      <p className="coupon-hint">还需邀请按留存好友均通过核验计算；退出或未通过核验可能增加人数。</p>
      {data.enabled && <div className="coupon-actions">
        <button className="coupon-action" type="button" onClick={() => openTelegramLink('https://t.me/haotianyl99988866')}>加入频道</button>
        <button className="coupon-action coupon-action-primary" type="button" disabled={busy} onClick={share}>
          {busy ? '正在核实…' : '分享邀请链接'}
        </button>
      </div>}
      <details className="coupon-details">
        <summary>查看代金券、好友和活动规则</summary>
        <p>新券有效期2个月，会所及外送可用（不含名玺水汇按摩）；符合条件的新消费自动抵扣，每次最多1张，不可叠加。</p>
        <h3>我的代金券</h3>
        {data.vouchers.length ? data.vouchers.map(voucher => {
          const status = voucher.status === 'available' && new Date(voucher.expires_at) <= new Date()
            ? '已过期' : ({ available: '可使用', redeemed: '已核销', invalid: '已失效' }[voucher.status] || '待核实')
          return <article className="coupon-item" key={voucher.id}>
            <strong>$20 · {status}</strong><span>有效至 {time(voucher.expires_at)}</span>
            <small>券号 {voucher.id}</small>
          </article>
        }) : <p>暂无代金券。</p>}
        {data.voucher_total > 20 && <div className="coupon-pager">
          <button type="button" disabled={loading || voucherPage <= 1} onClick={() => setVoucherPage(value => value - 1)}>上一页</button>
          <span>{voucherPage} / {Math.ceil(data.voucher_total / 20)}</span>
          <button type="button" disabled={loading || voucherPage * 20 >= data.voucher_total} onClick={() => setVoucherPage(value => value + 1)}>下一页</button>
        </div>}
        <h3>邀请记录 · {data.invite_total}人</h3>
        {data.invites.length ? data.invites.map(invite => <article className="coupon-item" key={invite.invitee_id}>
          <strong>{invite.display_name || (invite.username ? `@${invite.username}` : '未记录名称')}</strong>
          <span>{invitationState[invite.state] || '待核实'} · 加入 {time(invite.joined_at)}</span>
          {invite.left_at && <span>退出 {time(invite.left_at)}</span>}
        </article>) : <p>暂无通过专属链接加入的好友。</p>}
        {data.invite_total > 20 && <div className="coupon-pager">
          <button type="button" disabled={loading || page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button>
          <span>{page} / {Math.ceil(data.invite_total / 20)}</span>
          <button type="button" disabled={loading || page * 20 >= data.invite_total} onClick={() => setPage(value => value + 1)}>下一页</button>
        </div>}
      </details>
    </>}
  </section>
}

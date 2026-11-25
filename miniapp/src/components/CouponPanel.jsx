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
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setError('')
    requestMember('coupon_mine', { page, voucher_page: voucherPage })
      .then(result => { if (active) setData(result) })
      .catch(failure => { if (active) setError(failure.message) })
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

  return <section className="membership-panel coupon-panel" aria-labelledby="my-coupons">
    <div className="membership-row"><h2 id="my-coupons">邀请好友 · 我的代金券</h2></div>
    <p>先加入频道，再分享专属链接邀请好友；每位连续留存7天，每5人获得一张 $20 代金券。</p>
    <p>发券后一个月内使用，适用于会所和外送，不含名玺水汇按摩；登记新消费时自动抵用一张。</p>
    {error && <p className="membership-error" role="alert">{error}
      <button type="button" onClick={() => setReload(value => value + 1)}>重新读取</button></p>}
    {!data && !error && <p role="status">正在读取邀请进度…</p>}
    {data && <>
      {!data.enabled && <p role="status">活动暂未开放，已获得的代金券仍可查看。</p>}
      {data.enabled && <button className="coupon-action" type="button" onClick={() => openTelegramLink('https://t.me/haotianyl99988866')}>① 加入频道</button>}
      {data.enabled && <button className="coupon-action" type="button" disabled={busy} onClick={share}>
        {busy ? '正在核实并准备链接…' : '② 参加活动 · 分享专属邀请链接'}
      </button>}
      <p>已达标、尚未兑换：{data.eligible} 人 · 邀请记录：{data.invite_total} 人</p>
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
        <button type="button" disabled={voucherPage <= 1} onClick={() => setVoucherPage(value => value - 1)}>上一页</button>
        <span>{voucherPage} / {Math.ceil(data.voucher_total / 20)}</span>
        <button type="button" disabled={voucherPage * 20 >= data.voucher_total} onClick={() => setVoucherPage(value => value + 1)}>下一页</button>
      </div>}
      <h3>③ 邀请状态</h3>
      {data.invites.length ? data.invites.map(invite => <article className="coupon-item" key={invite.invitee_id}>
        <strong>{invite.display_name || (invite.username ? `@${invite.username}` : '未记录名称')}</strong>
        <span>{invitationState[invite.state] || '待核实'} · 加入 {time(invite.joined_at)}</span>
        {invite.left_at && <span>退出 {time(invite.left_at)}</span>}
      </article>) : <p>暂无通过专属链接加入的好友。</p>}
      {data.invite_total > 20 && <div className="coupon-pager">
        <button type="button" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button>
        <span>{page} / {Math.ceil(data.invite_total / 20)}</span>
        <button type="button" disabled={page * 20 >= data.invite_total} onClick={() => setPage(value => value + 1)}>下一页</button>
      </div>}
    </>}
  </section>
}

const detailStyle = { color: '#627264', fontSize: '14px', lineHeight: 1.8 }
const statuses = { pending: '待核款', approved: '已入账', rejected: '已拒绝' }

export default function DepositRecords({ deposits }) {
  if (!deposits.length) return <p style={detailStyle}>暂无充值记录</p>
  return deposits.map(deposit => (
    <div key={deposit.id} style={{ padding: '12px 0', borderBottom: '1px solid #eee', overflowWrap: 'anywhere' }}>
      <p>#{deposit.id} · {statuses[deposit.status] || deposit.status}</p>
      <p style={detailStyle}>申报 {deposit.claimed} U{deposit.credited && ` · 入账 ${deposit.credited} U`}</p>
      <p style={detailStyle}>{deposit.created_at}</p>
      <p style={detailStyle}>TXID：{deposit.txid}</p>
    </div>
  ))
}

const detailStyle = { color: '#627264', fontSize: '14px', lineHeight: 1.8 }
const statuses = { pending: '待核款', approved: '已入账', rejected: '已拒绝' }

export default function DepositRecords({ deposits }) {
  if (!deposits.length) return <p style={detailStyle}>暂无充值记录</p>
  return deposits.map(deposit => (
    <div key={deposit.id} style={{ padding: '12px 0', borderBottom: '1px solid #eee', overflowWrap: 'anywhere' }}>
      <p>#{deposit.id} · {statuses[deposit.status] || deposit.status}</p>
      <p style={detailStyle}>申报 {deposit.claimed} USD{deposit.credited && ` · 入账 ${deposit.credited} USD`}</p>
      <p style={detailStyle}>{deposit.created_at}</p>
      <p style={detailStyle}>{deposit.txid.startsWith('manual:') ? 'ABA 金额申请已提交，等待昊天复核' : deposit.txid.startsWith('proof:') ? '付款凭证已提交，等待人工核实' : `${deposit.method === 'aba' ? 'ABA 交易编号' : 'USDT 交易编号'}：${deposit.txid.replace(/^aba:/, '')}`}</p>
    </div>
  ))
}

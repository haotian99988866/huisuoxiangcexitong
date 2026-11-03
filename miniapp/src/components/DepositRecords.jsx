import ReviewRejection from './ReviewRejection'

const statuses = { pending: '待核款', approved: '已入账', rejected: '已拒绝' }

export default function DepositRecords({ deposits }) {
  if (!deposits.length) return <p>暂无充值记录</p>
  return deposits.map(deposit => (
    <article key={deposit.id} className="membership-order">
      <div className="membership-row"><strong>{deposit.method.toUpperCase()} · #{deposit.id}</strong><span className="account-status">{statuses[deposit.status] || deposit.status}</span></div>
      <p>申报 {deposit.claimed} USD{deposit.credited && ` · 入账 ${deposit.credited} USD`}</p>
      <small>{deposit.created_at}</small>
      {deposit.status === 'rejected' && <ReviewRejection reason={deposit.rejection_reason} />}
      {!deposit.txid.startsWith('manual:') && <details className="account-details"><summary>付款参考信息</summary>
        <p className="membership-reference">{deposit.txid.startsWith('proof:') ? '付款凭证已提交' : `${deposit.method === 'aba' ? 'ABA' : 'USDT'} 交易编号：${deposit.txid.replace(/^aba:/, '')}`}</p>
      </details>}
    </article>
  ))
}

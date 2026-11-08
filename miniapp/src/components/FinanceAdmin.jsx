import { useEffect, useState } from 'react'
import { requestAdmin } from '../utils/admin'
import { newMemberOrderId } from '../utils/membership'
import './FinanceAdmin.css'

const today = () => {
  const date = new Date()
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
const money = value => Number(value || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const emptyEntry = () => ({
  kind: 'expense', amount: '', principal: '', interest: '0', happened_on: today(),
  account_id: '', target_account_id: '', category_id: '', debt_id: '',
  counterparty: '', due_on: '', note: '',
})

export default function FinanceAdmin() {
  const [data, setData] = useState(null)
  const [month, setMonth] = useState(today().slice(0, 7))
  const [entry, setEntry] = useState(emptyEntry)
  const [budgets, setBudgets] = useState({})
  const [commissions, setCommissions] = useState([])
  const [account, setAccount] = useState({ name: '', account_type: 'cash' })
  const [category, setCategory] = useState({ name: '', kind: 'expense' })
  const [goal, setGoal] = useState(() => ({
    name: '', target: '', target_date: '', request_id: newMemberOrderId(),
  }))
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const pendingTransactionKey = `finance-transaction-${window.Telegram?.WebApp?.initDataUnsafe?.user?.id || 'admin'}`
  const pendingGoalKey = `finance-goal-${window.Telegram?.WebApp?.initDataUnsafe?.user?.id || 'admin'}`

  useEffect(() => {
    const pending = sessionStorage.getItem(pendingTransactionKey)
    if (pending) {
      try {
        const restored = JSON.parse(pending)
        if (!/^[0-9a-f]{32}$/.test(restored.request_id)) throw new Error('invalid request id')
        setEntry({ ...emptyEntry(), ...restored })
        setNotice('已恢复上次未确认的记账内容。请使用原内容重试，或先刷新明细核对，勿重复新建。')
      } catch (failure) {
        console.error('[Restore finance transaction]', failure)
        setError('上次记账暂存数据无效，请先核对明细后再继续。')
      }
    }
    const pendingGoal = sessionStorage.getItem(pendingGoalKey)
    if (!pendingGoal) return
    try {
      const restored = JSON.parse(pendingGoal)
      if (!/^[0-9a-f]{32}$/.test(restored.request_id)) throw new Error('invalid goal id')
      setGoal(restored)
      setNotice('已恢复上次未确认的储蓄目标。请使用原内容重试，或先刷新核对。')
    } catch (failure) {
      console.error('[Restore finance goal]', failure)
      setError('上次储蓄目标暂存数据无效，请先核对后再继续。')
    }
  }, [])

  const refresh = async selectedMonth => {
    setError('')
    const result = await requestAdmin('admin_finance_dashboard', { month: selectedMonth || month })
    setData(result)
    setBudgets(Object.fromEntries(result.budgets.map(row => [row.id, String(row.budget)])))
    setCommissions(result.commission_items.map(row => ({ ...row })))
  }
  useEffect(() => {
    let active = true
    requestAdmin('admin_finance_dashboard', { month })
      .then(result => {
        if (!active) return
        setData(result)
        setBudgets(Object.fromEntries(result.budgets.map(row => [row.id, String(row.budget)])))
        setCommissions(result.commission_items.map(row => ({ ...row })))
      })
      .catch(failure => { if (active) setError(failure.message) })
    return () => { active = false }
  }, [month])

  const perform = async (action, payload, success) => {
    if (busy) return { ok: false, status: 409 }
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await requestAdmin(action, payload)
      setNotice(success)
      await refresh()
      return { ok: true }
    } catch (failure) {
      setError(failure.message)
      return { ok: false, status: failure.status }
    } finally {
      setBusy(false)
    }
  }
  const changeEntry = (key, value) => setEntry(current => ({ ...current, [key]: value }))
  const saveEntry = async event => {
    event.preventDefault()
    const previous = sessionStorage.getItem(pendingTransactionKey)
    let requestId = newMemberOrderId()
    if (previous) {
      try { requestId = JSON.parse(previous).request_id } catch {
        setError('上次记账暂存数据无效，请刷新核对财务明细后再继续。')
        return
      }
    }
    const payload = { ...entry, request_id: requestId }
    if (['income', 'expense', 'transfer', 'borrow', 'lend'].includes(entry.kind)) {
      payload.amount = entry.amount.trim()
    }
    if (['debt_repay', 'debt_collect'].includes(entry.kind)) {
      payload.principal = entry.principal.trim()
      payload.interest = entry.interest.trim() || '0'
    }
    if (entry.account_id) payload.account_id = Number(entry.account_id)
    if (entry.target_account_id) payload.target_account_id = Number(entry.target_account_id)
    if (entry.category_id) payload.category_id = Number(entry.category_id)
    if (entry.debt_id) payload.debt_id = entry.debt_id
    if (previous && JSON.stringify(payload) !== previous) {
      setError('上次记账结果未确认，请用原内容重试或刷新核对，不要更改后重复提交。')
      return
    }
    sessionStorage.setItem(pendingTransactionKey, JSON.stringify(payload))
    const result = await perform('admin_finance_transaction', payload, '记账已保存。')
    if (result.ok) {
      sessionStorage.removeItem(pendingTransactionKey)
      setEntry(emptyEntry())
    } else {
      if ([400, 401, 403, 409].includes(result.status)) {
        sessionStorage.removeItem(pendingTransactionKey)
      } else {
        setNotice('记账结果未确认。请使用相同表单重试，或先刷新明细核对；勿新建重复流水。')
      }
    }
  }
  const saveBudget = (event, item) => {
    event.preventDefault()
    perform('admin_finance_budget', {
      month, category_id: item.id, amount: budgets[item.id] || '0',
    }, '月度限额已保存。')
  }
  const saveCommissions = event => {
    event.preventDefault()
    perform('admin_finance_commissions', {
      items: commissions.map(row => ({
        venue_id: row.venue_id, service_key: row.service_key,
        commission_amount: row.commission_amount,
      })),
    }, '各项目固定提成已保存；已有消费保留登记时的提成快照。')
  }
  const saveGoal = async event => {
    event.preventDefault()
    const payload = { ...goal, name: goal.name.trim(), target: goal.target.trim() }
    const previous = sessionStorage.getItem(pendingGoalKey)
    if (previous && JSON.stringify(payload) !== previous) {
      setError('上次储蓄目标结果未确认，请使用原内容重试或先刷新核对。')
      return
    }
    sessionStorage.setItem(pendingGoalKey, JSON.stringify(payload))
    const result = await perform('admin_finance_goal', payload, '储蓄目标已添加。')
    if (result.ok) {
      sessionStorage.removeItem(pendingGoalKey)
      setGoal({ name: '', target: '', target_date: '', request_id: newMemberOrderId() })
    } else if ([400, 401, 403, 409].includes(result.status)) {
      sessionStorage.removeItem(pendingGoalKey)
    }
  }
  const kind = entry.kind
  const visibleCategories = data?.categories.filter(row => row.kind === kind) || []

  return <section className="admin-finance">
    <div className="admin-header">
      <div><h2>个人财务</h2><p className="admin-hint">个人财务只对当前管理员可见。客人消费按登记项目固定提成入账。</p></div>
      <label className="finance-month">月份<input type="month" value={month} onChange={event => setMonth(event.target.value)} /></label>
    </div>
    {error && <p role="alert" className="admin-error">{error}</p>}
    {notice && <p role="status" className="admin-notice">{notice}</p>}
    {!data && !error && <p role="status">正在读取财务数据…</p>}
    {data && <>
      <section className="admin-stats finance-stats" aria-label="月度财务概览">
        {[
          ['本月收入（含提成）', `$${money(data.summary.income)}`],
          ['本月支出', `$${money(data.summary.expense)}`],
          ['本月结余', `$${money(data.summary.net)}`],
          ['储蓄账户余额', `$${money(data.summary.saved)}`],
          ['待还借款', `$${money(data.summary.payable)}`],
          ['待收借款', `$${money(data.summary.receivable)}`],
        ].map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}
      </section>

      <section className="admin-card">
        <h3>快捷记账</h3>
        <form className="finance-form" onSubmit={saveEntry}>
          <label>类型<select value={kind} onChange={event => changeEntry('kind', event.target.value)}>
            <option value="expense">支出</option><option value="income">收入</option>
            <option value="transfer">账户转账 / 储蓄</option><option value="borrow">借入</option>
            <option value="lend">借出</option><option value="debt_repay">偿还借款</option>
            <option value="debt_collect">收回借款</option>
          </select></label>
          <label>日期<input required type="date" value={entry.happened_on}
            onChange={event => changeEntry('happened_on', event.target.value)} /></label>
          {['debt_repay', 'debt_collect'].includes(kind) ? <>
            <label>借款记录<select required value={entry.debt_id}
              onChange={event => changeEntry('debt_id', event.target.value)}>
              <option value="">选择往来款</option>
              {data.debts.filter(row => row.direction === (kind === 'debt_repay' ? 'payable' : 'receivable'))
                .map(row => <option key={row.id} value={row.id}>
                  {row.counterparty} · 剩余 ${money(row.outstanding)}
                </option>)}
            </select></label>
            <label>归还本金 USD<input required inputMode="decimal" value={entry.principal}
              onChange={event => changeEntry('principal', event.target.value)} /></label>
            <label>利息 USD<input inputMode="decimal" value={entry.interest}
              onChange={event => changeEntry('interest', event.target.value)} /></label>
          </> : <>
            {['borrow', 'lend'].includes(kind) && <label>往来对象<input required maxLength={128}
              value={entry.counterparty} onChange={event => changeEntry('counterparty', event.target.value)} /></label>}
            <label>金额 USD<input required inputMode="decimal" value={entry.amount}
              onChange={event => changeEntry('amount', event.target.value)} /></label>
          </>}
          {['income', 'expense'].includes(kind) && <label>分类<select required value={entry.category_id}
            onChange={event => changeEntry('category_id', event.target.value)}>
            <option value="">选择分类</option>
            {visibleCategories.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
          </select></label>}
          {['income', 'expense', 'transfer', 'borrow', 'lend', 'debt_repay', 'debt_collect'].includes(kind) &&
            <label>账户<select required value={entry.account_id}
              onChange={event => changeEntry('account_id', event.target.value)}>
              <option value="">选择账户</option>
              {data.accounts.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
            </select></label>}
          {kind === 'transfer' && <label>转入账户<select required value={entry.target_account_id}
            onChange={event => changeEntry('target_account_id', event.target.value)}>
            <option value="">选择目标账户</option>
            {data.accounts.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
          </select></label>}
          {['borrow', 'lend'].includes(kind) && <label>到期日（选填）<input type="date" value={entry.due_on}
            onChange={event => changeEntry('due_on', event.target.value)} /></label>}
          <label className="finance-wide">备注<input maxLength={1000} value={entry.note}
            onChange={event => changeEntry('note', event.target.value)} /></label>
          <button type="submit" disabled={busy}>{busy ? '保存中…' : '保存记录'}</button>
        </form>
        <p className="admin-hint">借入/借出本金不计为收入或支出，只有利息计入；账户转账只改变资金存放位置。</p>
      </section>

      <section className="admin-card">
        <h3>账户</h3>
        <div className="finance-list">{data.accounts.map(row => <p key={row.id}>
          {row.name} · {row.account_type === 'savings' ? '储蓄' : '日常'} · 余额 ${money(row.balance)}
        </p>)}</div>
        <form className="finance-inline-form" onSubmit={event => {
          event.preventDefault()
          perform('admin_finance_account', account, '账户已添加。')
          setAccount({ name: '', account_type: 'cash' })
        }}>
          <input required maxLength={80} aria-label="新账户名称" placeholder="账户名称" value={account.name}
            onChange={event => setAccount({ ...account, name: event.target.value })} />
          <select aria-label="账户类型" value={account.account_type}
            onChange={event => setAccount({ ...account, account_type: event.target.value })}>
            <option value="cash">日常账户</option><option value="savings">储蓄账户</option>
          </select><button disabled={busy}>添加账户</button>
        </form>
      </section>

      <section className="admin-card">
        <h3>借款与债务</h3>
        {data.debts.map(row => <p className="finance-debt" key={row.id}>
          <strong>{row.direction === 'payable' ? '我借入' : '我借出'} · {row.counterparty}</strong>
          <span>本金 ${money(row.principal)} · 未还 ${money(row.outstanding)} · 到期 {row.due_on || '未设置'}</span>
          {row.note && <small>{row.note}</small>}
        </p>)}
        {!data.debts.length && <p className="admin-hint">当前没有未结清借款。</p>}
        <p className="admin-hint">使用上方“借入 / 借出”登记本金；还款、收款时分别选择对应借款并拆分本金和利息。</p>
      </section>

      <section className="admin-card">
        <h3>自定义分类</h3>
        <form className="finance-inline-form" onSubmit={event => {
          event.preventDefault()
          perform('admin_finance_category', category, '分类已添加。')
          setCategory({ ...category, name: '' })
        }}>
          <input required maxLength={48} aria-label="新分类名称" placeholder="分类名称"
            value={category.name} onChange={event => setCategory({ ...category, name: event.target.value })} />
          <select aria-label="分类类型" value={category.kind}
            onChange={event => setCategory({ ...category, kind: event.target.value })}>
            <option value="expense">支出分类</option><option value="income">收入分类</option>
          </select><button disabled={busy}>添加分类</button>
        </form>
      </section>

      <section className="admin-card">
        <h3>预算与储蓄目标 · {month}</h3>
        {data.budgets.map(row => <form className="finance-budget" key={row.id}
          onSubmit={event => saveBudget(event, row)}>
          <label>{row.name}<input inputMode="decimal" aria-label={`${row.name}月度限额`}
            value={budgets[row.id] ?? '0'} onChange={event =>
              setBudgets(current => ({ ...current, [row.id]: event.target.value }))} /></label>
          <span>已用 ${money(row.spent)} / 限额</span><button disabled={busy}>保存</button>
          {Number(row.budget) > 0 && <progress max={Number(row.budget)} value={Math.min(Number(row.spent), Number(row.budget))} />}
          {Number(row.budget) > 0 && Number(row.spent) > Number(row.budget) &&
            <strong className="finance-over">超过限额 ${money(Number(row.spent) - Number(row.budget))}</strong>}
        </form>)}
        <div className="finance-goals">{data.goals.map(row => {
          const remaining = Math.max(0, Number(row.target) - Number(row.saved))
          return <div key={row.id}>
            <p>{row.name}：已存 ${money(row.saved)} / 目标 ${money(row.target)} ·
              {remaining ? `还差 $${money(remaining)}` : '已达成'} · {row.target_date || '未设日期'}</p>
            <progress max={Number(row.target)} value={Math.min(Number(row.saved), Number(row.target))} />
          </div>
        })}</div>
        <form className="finance-inline-form" onSubmit={saveGoal}>
          <input required maxLength={80} aria-label="储蓄目标名称" placeholder="目标名称" value={goal.name}
            onChange={event => setGoal({ ...goal, name: event.target.value })} />
          <input required inputMode="decimal" aria-label="储蓄目标金额" placeholder="目标金额 USD" value={goal.target}
            onChange={event => setGoal({ ...goal, target: event.target.value })} />
          <input type="date" aria-label="目标日期" value={goal.target_date}
            onChange={event => setGoal({ ...goal, target_date: event.target.value })} />
          <button disabled={busy}>添加目标</button>
        </form>
      </section>

      <section className="admin-card">
        <h3>各项目固定提成</h3>
        <p className="admin-hint">金额单位为美元；修改只影响新登记或更换项目后的消费，已登记消费保留当时快照。退款金额直接从快照提成扣减，最低为 $0。</p>
        <form onSubmit={saveCommissions}>
          <div className="finance-commission-list">{commissions.map((row, index) => <label key={`${row.venue_id}:${row.service_key}`}>
            <span>{row.venue_name} · {row.service_name}{row.listed_price ? `（菜单 $${money(row.listed_price)}）` : ''}</span>
            <input required inputMode="decimal" aria-label={`${row.venue_name} ${row.service_name}固定提成`}
              value={row.commission_amount} onChange={event => setCommissions(current =>
                current.map((item, itemIndex) => itemIndex === index
                  ? { ...item, commission_amount: event.target.value } : item))} />
          </label>)}</div>
          <button disabled={busy}>保存提成设置</button>
        </form>
      </section>

      <section className="admin-card">
        <h3>本月收支明细</h3>
        <div className="finance-transactions">{data.transactions.map((row, index) => <article key={row.id || `${row.kind}-${index}`}>
          <div><strong>{row.kind === 'commission' ? '客人提成' : row.category_name || row.kind}</strong>
            <small>{row.display_date} · {row.account_name || row.source_name || ''}</small>
            {row.note && <small>{row.note}</small>}</div>
          <strong>{row.kind === 'transfer' ? '↔' :
            ['expense', 'debt_repay', 'lend'].includes(row.kind) ? '−' : '+'}${money(row.amount)}</strong>
          {row.kind !== 'commission' && <button type="button" disabled={busy} onClick={() => {
            if (window.confirm('确定作废这条记录？作废后将从统计中排除。')) {
              perform('admin_finance_void', { transaction_id: row.id }, '该记录已作废。')
            }
          }}>作废</button>}
        </article>)}</div>
        {!data.transactions.length && <p className="admin-hint">本月暂无收支记录。</p>}
      </section>
    </>}
  </section>
}

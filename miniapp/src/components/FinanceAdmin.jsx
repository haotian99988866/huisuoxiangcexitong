import { useEffect, useRef, useState } from 'react'
import { requestAdmin } from '../utils/admin'
import { newMemberOrderId } from '../utils/membership'
import { financeCategoryAnalysis } from '../utils/financeAnalysis'
import MusicDialog from './MusicDialog'
import MusicIcon from './MusicIcon'
import './FinanceAdmin.css'

const cambodiaToday = () => new Date(Date.now() + 7 * 60 * 60 * 1000).toISOString().slice(0, 10)
const today = () => cambodiaToday()
const ENTRY_TITLES = { expense: '支出', income: '收入', transfer: '转账' }
const emptyEntry = () => ({
  kind: 'expense', amount: '', principal: '', interest: '0', happened_on: today(),
  account_id: '', target_account_id: '', category_id: '', debt_id: '',
  counterparty: '', due_on: '', note: '',
})

export default function FinanceAdmin({ initialView = 'overview', initialEntryKind = null }) {
  const [amountsVisible, setAmountsVisible] = useState(false)
  const [data, setData] = useState(null)
  const [month, setMonth] = useState(today().slice(0, 7))
  const [activeView, setActiveView] = useState(initialView)
  const alwaysShowAmounts = ['records', 'today', 'analysis', 'planning', 'settings'].includes(activeView)
  const money = value => amountsVisible || alwaysShowAmounts
    ? Number(value || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : '••••'
  const [dailySummary, setDailySummary] = useState(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [entryOpen, setEntryOpen] = useState(false)
  const [entry, setEntry] = useState(emptyEntry)
  const [budgets, setBudgets] = useState({})
  const [budgetEditing, setBudgetEditing] = useState(false)
  const [openingBalances, setOpeningBalances] = useState({})
  const [commissions, setCommissions] = useState([])
  const [commissionSearch, setCommissionSearch] = useState('')
  const planningErrorRef = useRef(null)
  const [account, setAccount] = useState({ name: '', account_type: 'cash', opening_balance: '0' })
  const [category, setCategory] = useState({ name: '', kind: 'expense' })
  const [categoryEdits, setCategoryEdits] = useState({})
  const [editingCategoryId, setEditingCategoryId] = useState(null)
  const [goal, setGoal] = useState(() => ({
    name: '', target: '', target_date: '', request_id: newMemberOrderId(),
  }))
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const entryFormRef = useRef(null)
  const pendingTransactionKey = `finance-transaction-${window.Telegram?.WebApp?.initDataUnsafe?.user?.id || 'admin'}`
  const pendingGoalKey = `finance-goal-${window.Telegram?.WebApp?.initDataUnsafe?.user?.id || 'admin'}`
  const defaultAccountId = data?.accounts.find(row => row.name === '日常账户')?.id ??
    data?.accounts.find(row => row.account_type === 'cash')?.id
  const defaultSavingsId = data?.accounts.find(row => row.account_type === 'savings')?.id
  const categoryForKind = (selectedKind, categories = data?.categories || []) => {
    const options = categories.filter(row => row.kind === selectedKind)
    return options.find(row => row.name === (selectedKind === 'expense' ? '其他' : '其他收入')) || options[0]
  }

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
    setOpeningBalances(Object.fromEntries(result.accounts.map(row => [row.id, String(row.opening_balance)])))
    setCategoryEdits(Object.fromEntries(result.all_categories.map(row => [row.id, {
      name: row.name, kind: row.kind,
    }])))
    setCommissions(result.commission_items.map(row => ({ ...row })))
    setLastUpdated(new Date())
  }
  useEffect(() => {
    let active = true
    setRefreshing(true)
    setError('')
    const reloadWhenVisible = () => {
      if (document.visibilityState === 'visible') setRefreshKey(value => value + 1)
    }
    requestAdmin('admin_finance_dashboard', { month })
      .then(result => {
        if (!active) return
        setData(result)
        setBudgets(Object.fromEntries(result.budgets.map(row => [row.id, String(row.budget)])))
        setOpeningBalances(Object.fromEntries(result.accounts.map(row => [row.id, String(row.opening_balance)])))
        setCategoryEdits(Object.fromEntries(result.all_categories.map(row => [row.id, {
          name: row.name, kind: row.kind,
        }])))
        setCommissions(result.commission_items.map(row => ({ ...row })))
        setLastUpdated(new Date())
      })
      .catch(failure => { if (active) setError(failure.message) })
      .finally(() => { if (active) setRefreshing(false) })
    document.addEventListener('visibilitychange', reloadWhenVisible)
    return () => {
      active = false
      document.removeEventListener('visibilitychange', reloadWhenVisible)
    }
  }, [month, refreshKey])

  useEffect(() => {
    if (activeView !== 'today') return undefined
    let active = true
    setDailySummary(null)
    requestAdmin('admin_finance_daily_summary', { day: cambodiaToday() })
      .then(result => { if (active) setDailySummary(result) })
      .catch(failure => { if (active) setError(failure.message) })
    return () => { active = false }
  }, [activeView, refreshKey])

  useEffect(() => {
    if (!data || sessionStorage.getItem(pendingTransactionKey)) return
    setEntry(current => {
      const currentCategory = data.categories.find(row => String(row.id) === current.category_id)
      const defaultCategory = categoryForKind(current.kind, data.categories)
      const targetAccountId = current.target_account_id ||
        (defaultSavingsId && String(defaultSavingsId) !== current.account_id ? String(defaultSavingsId) : '')
      return {
        ...current,
        account_id: current.account_id || (defaultAccountId ? String(defaultAccountId) : ''),
        target_account_id: targetAccountId,
        category_id: currentCategory?.kind === current.kind
          ? current.category_id : defaultCategory ? String(defaultCategory.id) : '',
      }
    })
  }, [data])

  useEffect(() => {
    if (error && activeView === 'planning') {
      planningErrorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [error, activeView])

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
      return { ok: false, status: failure.status, message: failure.message }
    } finally {
      setBusy(false)
    }
  }
  const changeEntry = (key, value) => setEntry(current => {
    if (key === 'kind') {
      const currentCategory = data?.categories.find(row => String(row.id) === current.category_id)
      const nextCategory = currentCategory?.kind === value ? currentCategory : categoryForKind(value)
      return {
        ...current, kind: value, category_id: nextCategory ? String(nextCategory.id) : '',
        debt_id: ['debt_repay', 'debt_collect'].includes(value) ? current.debt_id : '',
      }
    }
    return { ...current, [key]: value }
  })
  const startEntry = selectedKind => {
    if (sessionStorage.getItem(pendingTransactionKey)) {
      setNotice('有一笔记账结果尚未确认，请先用原内容重试或核对记录。')
      setEntryOpen(true)
      return
    }
    changeEntry('kind', selectedKind)
    setError('')
    setNotice('')
    setEntryOpen(true)
  }
  const closeEntry = () => {
    if (busy) return
    const webApp = window.Telegram?.WebApp
    if (webApp?.initData && typeof webApp.close === 'function') {
      webApp.close()
      return
    }
    setEntryOpen(false)
  }
  const initialEntryStarted = useRef(false)
  useEffect(() => {
    if (!initialEntryKind || initialEntryStarted.current) return
    initialEntryStarted.current = true
    startEntry(initialEntryKind)
  }, [initialEntryKind])
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
    if (['income', 'expense', 'transfer', 'borrow', 'lend', 'balance_add'].includes(entry.kind)) {
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
      setEntry(current => ({
        ...emptyEntry(), kind: current.kind, account_id: current.account_id,
        target_account_id: current.target_account_id, category_id: current.category_id,
      }))
      setEntryOpen(false)
    } else {
      if ([400, 401, 403, 409].includes(result.status)) {
        sessionStorage.removeItem(pendingTransactionKey)
      } else {
        setNotice('记账结果未确认。请使用相同表单重试，或先刷新明细核对；勿新建重复流水。')
      }
    }
  }
  const budgetRows = (data?.budgets || []).filter(row =>
    !(row.kind === 'expense' && ['账单', '利息'].includes(row.name)))
  const incomeBudgetRows = budgetRows.filter(row => row.kind === 'income')
  const expenseBudgetRows = budgetRows.filter(row => row.kind === 'expense')
  const budgetTotal = rows => rows.reduce((total, row) => {
    const amount = Number(budgets[row.id] ?? row.budget ?? 0)
    return total + (Number.isFinite(amount) && amount > 0 ? amount : 0)
  }, 0)
  const saveBudgets = async event => {
    event.preventDefault()
    setError('')
    const invalidBudget = budgetRows.find(row =>
      !/^\d{1,10}(?:\.\d{1,2})?$/.test((budgets[row.id] ?? '0').trim()))
    if (invalidBudget) {
      setError(`${invalidBudget.name}限额格式不正确，请输入整数或最多两位小数的金额。`)
      return
    }
    const result = await perform('admin_finance_budget', {
      month,
      items: budgetRows.map(row => ({
        category_id: row.id, kind: row.kind,
        amount: (budgets[row.id] ?? '0').trim() || '0',
      })),
    }, '所有月度限额已统一保存。')
    if (result.ok) setBudgetEditing(false)
    else if (result.message) setError(`预算保存失败：${result.message}`)
  }
  const cancelBudgetEdit = () => {
    setBudgets(Object.fromEntries((data?.budgets || []).map(row => [row.id, String(row.budget)])))
    setBudgetEditing(false)
    setError('')
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
  const categoryRows = (data?.all_categories || []).filter(row => row.active)
  const deletedCategoryRows = (data?.all_categories || []).filter(row => !row.active)
  const renderCategory = row => {
    const values = categoryEdits[row.id] || { name: row.name, kind: row.kind }
    const protectedCategory = (row.kind === 'expense' && row.name === '利息') ||
      (row.kind === 'income' && row.name === '利息收入')
    const editing = editingCategoryId === row.id
    const changed = values.name.trim() !== row.name || values.kind !== row.kind
    return <article className={`finance-category-row${editing ? ' is-editing' : ''}`} key={row.id}>
      {editing ? <form onSubmit={async event => {
        event.preventDefault()
        const result = await perform('admin_finance_category_update', {
          category_id: row.id, ...values, name: values.name.trim(),
        }, '分类已修改。')
        if (result.ok) setEditingCategoryId(null)
        else if (result.message) setError(`保存失败：${result.message}`)
      }}>
        <label>分类名称<input required maxLength={48} autoFocus value={values.name}
          onChange={event => setCategoryEdits(current => ({
            ...current, [row.id]: { ...values, name: event.target.value },
          }))} /></label>
        <label>收支类型<select value={values.kind}
          onChange={event => setCategoryEdits(current => ({
            ...current, [row.id]: { ...values, kind: event.target.value },
          }))}>
          <option value="expense">支出分类</option><option value="income">收入分类</option>
        </select></label>
        <div className="finance-category-actions">
          <button type="button" disabled={busy} onClick={() => {
            setCategoryEdits(current => ({ ...current, [row.id]: { name: row.name, kind: row.kind } }))
            setEditingCategoryId(null)
          }}>取消</button>
          <button className="is-primary" disabled={busy || !changed}>保存修改</button>
        </div>
        {values.kind !== row.kind && <small className="finance-category-hint">
          已有流水或预算的分类不能更改收支类型。
        </small>}
      </form> : <>
        <div className="finance-category-info">
          <strong>{row.name}</strong>
          <span className={`finance-category-kind ${row.kind}`}>
            {row.kind === 'income' ? '收入' : '支出'}
          </span>
          {protectedCategory && <small>借款利息专用</small>}
        </div>
        <div className="finance-category-actions">
          {!protectedCategory && <button type="button" disabled={busy}
            onClick={() => setEditingCategoryId(row.id)}>编辑</button>}
          {!protectedCategory && <button className="finance-category-delete" type="button" disabled={busy}
            onClick={() => {
              if (window.confirm(`删除“${row.name}”？历史流水会保留，可在“已删除分类”中恢复。`)) {
                perform('admin_finance_category_delete', { category_id: row.id }, '分类已删除。')
              }
            }}>删除</button>}
        </div>
      </>}
    </article>
  }
  const commissionGroups = commissions.reduce((groups, row) => {
    const venue = row.venue_name || '其他会所'
    if (!groups[venue]) groups[venue] = []
    groups[venue].push(row)
    return groups
  }, {})
  const filteredCommissionGroups = Object.entries(commissionGroups).map(([venue, rows]) => [
    venue,
    rows.filter(row => `${row.venue_name} ${row.service_name}`.toLocaleLowerCase()
      .includes(commissionSearch.trim().toLocaleLowerCase())),
  ]).filter(([, rows]) => rows.length)
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
  const transactionRows = data?.transactions || []
  const dailyTransactions = activeView === 'today'
    ? transactionRows.filter(row => String(row.display_date || '').slice(0, 10) === cambodiaToday())
    : transactionRows
  const categoryAnalysis = financeCategoryAnalysis(data)
  const ledgerSummary = activeView === 'today' ? dailySummary : data?.summary
  const transactionLabel = row => {
    if (row.kind === 'balance_add') return '补充账户金额'
    if (row.kind === 'commission') {
      return Number(row.amount) === 0 && !row.commission_service_key
        ? '消费记录（未匹配提成）' : '客人提成（自动入账）'
    }
    return ({
      borrow: '借入',
      lend: '借出',
      debt_repay: '偿还借款',
      debt_collect: '收回借款',
    }[row.kind] || row.category_name || ({
      income: '其他收入', expense: '支出', transfer: '账户转账',
    }[row.kind] || row.kind))
  }
  const transactionDescription = row => {
    if (row.kind === 'commission') {
      return [String(row.display_date || '').slice(11, 16), row.source_name, row.service,
        row.staff_code ? `工号${row.staff_code}` : row.delivery_person_name || '未关联人员']
        .filter(Boolean).join(' · ')
    }
    if (row.kind === 'transfer') return `${row.account_name || '账户'} → ${row.target_account_name || '账户'}`
    const description = [row.counterparty, row.account_name || row.source_name].filter(Boolean)
    if (['debt_repay', 'debt_collect'].includes(row.kind)) {
      description.push(`本金 $${money(row.principal)}`, `利息 $${money(row.interest)}`)
    }
    return description.join(' · ')
  }
  const transactionGroups = dailyTransactions.reduce((groups, row, index) => {
    const date = String(row.display_date || '').slice(0, 10)
    if (!groups[date]) groups[date] = []
    groups[date].push({ ...row, key: row.id || `${row.kind}-${index}` })
    return groups
  }, {})
  const displayDay = date => {
    const [year, monthNumber, day] = date.split('-').map(Number)
    if (!year || !monthNumber || !day) return date
    const weekday = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][new Date(year, monthNumber - 1, day).getDay()]
    return `${monthNumber}月${day}日 ${weekday}`
  }
  const renderTransaction = row => <article className={`finance-transaction${row.status === 'void' ? ' is-void' : ''}`}
    key={row.key}>
    <span className={`finance-transaction-mark ${row.kind === 'commission' || ['income', 'borrow', 'debt_collect', 'balance_add'].includes(row.kind) ? 'is-inflow' :
      ['expense', 'debt_repay', 'lend'].includes(row.kind) ? 'is-outflow' : ''}`} aria-hidden="true">
      {row.kind === 'transfer' ? '↔' : ['expense', 'debt_repay', 'lend'].includes(row.kind) ? '−' : '+'}
    </span>
    <div className="finance-transaction-copy">
      <strong>{transactionLabel(row)}</strong>
      {row.status === 'void' && <span className="finance-void-badge">已作废</span>}
      <small>{transactionDescription(row)}</small>
      {row.note && <small>{row.note}</small>}
    </div>
    <div className="finance-transaction-side">
      <strong className={['expense', 'debt_repay', 'lend'].includes(row.kind) ? 'is-outflow' :
        ['income', 'borrow', 'debt_collect', 'balance_add'].includes(row.kind) ||
          row.kind === 'commission' && Number(row.amount) > 0 ? 'is-inflow' : ''}>
        {row.kind === 'transfer' ? '' : ['expense', 'debt_repay', 'lend'].includes(row.kind) ? '−' : '+'}
        ${money(row.amount)}
      </strong>
      {row.kind !== 'commission' && row.status !== 'void' && <button type="button" disabled={busy} onClick={() => {
        if (window.confirm('确定作废这条记录？作废后将从统计中排除。')) {
          perform('admin_finance_void', { transaction_id: row.id }, '该记录已作废。')
        }
      }}>作废</button>}
    </div>
  </article>
  const transactionList = <div className="finance-ledger">
    {Object.entries(transactionGroups).map(([date, rows]) => <section className="finance-ledger-day" key={date}>
      <h3>{displayDay(date)}</h3>
      <div>{rows.map(renderTransaction)}</div>
    </section>)}
    {!dailyTransactions.length && <p className="finance-empty">
      {activeView === 'today' ? '今天还没有收支记录' : '这个月还没有收支记录'}
    </p>}
  </div>
  const refreshButton = <button type="button" className={`finance-refresh${refreshing ? ' is-refreshing' : ''}`}
    aria-label={refreshing ? '正在刷新财务数据' : '刷新财务数据'} title={refreshing ? '正在刷新' : '刷新'}
    disabled={busy || refreshing} onClick={() => setRefreshKey(value => value + 1)}>
    <MusicIcon name="refresh" size={19} />
  </button>
  const monthPicker = <label className="finance-month"><span>账单月份</span>
    <input type="month" value={month} onChange={event => setMonth(event.target.value)} />
  </label>
  const viewTitle = {
    analysis: '分类分析',
    today: '今日收支',
    records: '本月账单',
  }[activeView] || '个人财务总览'
  const privacyButton = <button type="button" className="finance-privacy"
    aria-pressed={!amountsVisible} onClick={() => setAmountsVisible(value => !value)}>
    <MusicIcon name={amountsVisible ? 'eye' : 'eyeOff'} size={18} />
    {amountsVisible ? '隐藏金额' : '显示金额'}
  </button>

  return <section className={`admin-finance${initialEntryKind ? ' is-direct-entry' : ''}`}>
    {activeView !== 'overview' && !alwaysShowAmounts && <div className="finance-privacy-bar">{privacyButton}<span>金额单位 USD</span></div>}
    {['records', 'today'].includes(activeView) ? <header className="finance-record-page-header">
      <button type="button" aria-label="返回财务总览" onClick={() => setActiveView('overview')}>
        <MusicIcon name="back" size={20} />
      </button>
      <div><h2>{viewTitle}</h2><p>收入、支出和自动同步提成都在这里</p></div>
      {refreshButton}
      <div className="finance-record-period">{activeView === 'today' ? cambodiaToday() : monthPicker}
        <span>{lastUpdated ? `更新于 ${lastUpdated.toLocaleTimeString('zh-CN', { hour12: false })}` : '正在同步…'}</span>
      </div>
    </header> : <header className="finance-main-header">
      <div className="finance-main-title">
        <div><h2>{viewTitle}</h2><p>提成自动计入日常账户余额</p></div>
        {refreshButton}
      </div>
      {activeView === 'overview' && data && <section className="finance-wealth" aria-label="资产总览">
        <span>个人净资产 · USD</span>
        <button type="button" className="finance-wealth-amount"
          aria-label={amountsVisible ? '隐藏金额' : '显示金额'}
          aria-pressed={amountsVisible} onClick={() => setAmountsVisible(value => !value)}>
          <strong>${money(data.summary.net_worth)}</strong>
          <MusicIcon name={amountsVisible ? 'eye' : 'eyeOff'} size={21} />
        </button>
        <p>点击金额显示或隐藏 · 账户余额 + 待收 − 待还</p>
        <div><span>账户总余额</span>
          <button type="button" className="finance-wealth-balance"
            aria-label={amountsVisible ? '隐藏账户金额' : '显示账户金额'}
            onClick={() => setAmountsVisible(value => !value)}>
            ${money(data.summary.account_balance)}
          </button>
        </div>
      </section>}
      <div className="finance-main-period">{monthPicker}
        <span>{lastUpdated ? `更新于 ${lastUpdated.toLocaleTimeString('zh-CN', { hour12: false })}` : '正在同步…'}</span>
      </div>
    </header>}
    <nav className="finance-tabs" aria-label="个人财务功能">
      {[
        ['overview', '总览'], ['records', '本月'],
        ['today', '今日'], ['analysis', '分类分析'],
        ['planning', '预算债务'], ['settings', '账户设置'],
      ].map(([id, label]) => <button key={id} type="button" aria-pressed={activeView === id}
        onClick={() => {
          if (id === 'today') setMonth(cambodiaToday().slice(0, 7))
          setActiveView(id)
        }}>{label}</button>)}
    </nav>
    {error && activeView !== 'planning' && <p role="alert" className="admin-error">{error}</p>}
    {notice && <p role="status" className="admin-notice">{notice}</p>}
    {!data && !error && <p role="status">正在读取财务数据…</p>}
    {data && <>
      {activeView === 'overview' && <>
        <section className="finance-overview-grid">
          <article className="admin-card">
            <h3>账户与往来</h3>
            <div className="finance-balance-grid">
              <div><span>日常账户</span><strong>${money(data.accounts.find(row => row.name === '日常账户')?.balance)}</strong></div>
              <div><span>储蓄账户</span><strong>${money(data.summary.saved)}</strong></div>
              <div><span>待还借款</span><strong>${money(data.summary.payable)}</strong></div>
              <div><span>待收借款</span><strong>${money(data.summary.receivable)}</strong></div>
            </div>
            <button type="button" className="finance-account-topup" onClick={() => startEntry('balance_add')}>
              ＋ 补充账户金额
            </button>
          </article>
          <article className="admin-card">
            <h3>{month.replace('-', '年')}月收支</h3>
            <section className="finance-ledger-summary" aria-label="月度财务概览">
              <div><span>收入</span><strong className="is-inflow">${money(data.summary.income)}</strong></div>
              <div><span>支出</span><strong className="is-outflow">${money(data.summary.expense)}</strong></div>
              <div><span>结余</span><strong>${money(data.summary.net)}</strong></div>
            </section>
            <p className="admin-hint">自动提成 ${money(data.summary.commission)} · 其他收入 ${money(data.summary.other_income)}</p>
            <button type="button" className="finance-account-topup" onClick={() => setActiveView('analysis')}>
              查看收支分析
            </button>
          </article>
        </section>
        <section className="finance-quick-actions" aria-label="快速记账">
          <button className="finance-add-entry" type="button" onClick={() => startEntry('expense')}>
            <span className="finance-add-icon" aria-hidden="true">＋</span>
            <span><strong>记一笔</strong><small>收支 / 借还款</small></span>
          </button>
          <button className="finance-transfer-entry" type="button" onClick={() => startEntry('transfer')}>账户转账</button>
        </section>
        <button type="button" className="finance-view-ledger" onClick={() => setActiveView('records')}>
          <span><strong>收支明细</strong><small>查看本月所有收入、支出和提成记录</small></span>
          <span aria-hidden="true">查看全部　›</span>
        </button>
      </>}

      {['records', 'today'].includes(activeView) && (ledgerSummary ? <>
        <section className="finance-ledger-summary" aria-label={activeView === 'today' ? '今日收支汇总' : `${month} 收支汇总`}>
          <div><span>收入</span><strong className="is-inflow">${money(ledgerSummary.income)}</strong></div>
          <div><span>支出</span><strong className="is-outflow">${money(ledgerSummary.expense)}</strong></div>
          <div><span>结余</span><strong>${money(ledgerSummary.net)}</strong></div>
        </section>
        <p className="finance-ledger-note">其中自动入账提成 ${money(ledgerSummary.commission)}</p>
        <p className="finance-ledger-note">借入、借出及归还本金不会计入收入或支出，但会保留在下方流水中；余额补充也不计收入；利息计入对应收支。</p>
        <div className="finance-ledger-heading"><h3>{activeView === 'today' ? '今日流水' : `${month.replace('-', '年')}月账单`}</h3>
          <button type="button" onClick={() => startEntry('expense')}>＋ 记一笔</button></div>
        {transactionList}
      </> : !error && <p role="status">正在读取今日收支…</p>)}

      {activeView === 'today' && <>
        {dailySummary && <section className="admin-card">
          <h3>账户与净资产</h3>
          <p>账户余额 <strong>${money(dailySummary.account_balance)}</strong></p>
          <p>个人净资产 <strong>${money(dailySummary.net_worth)}</strong></p>
        </section>}
        {dailySummary && <section className="admin-card">
          <h3>今日支出分类</h3>
          {dailySummary.categories.length
            ? dailySummary.categories.map(row => <p className="finance-analysis-item" key={row.name}>
              <span>{row.name}</span><strong>${money(row.amount)}</strong>
            </p>)
            : <p className="admin-hint">今天暂无支出记录。</p>}
        </section>}
      </>}

      {activeView === 'analysis' && <>
        {categoryAnalysis.map(group => <section className="admin-card" key={group.kind}>
          <div className="finance-ledger-heading"><h3>{group.title} · {month}</h3>
            <strong>${money(group.total)}</strong></div>
          {group.rows.length
            ? <div className="finance-analysis-list">{group.rows.map(row => {
              const share = group.total ? Number(row.spent) / group.total * 100 : 0
              return <div className="finance-analysis-row" key={row.id}>
                <div className="finance-analysis-item">
                  <span>{row.name}</span><strong>${money(row.spent)}</strong>
                </div>
                <progress max="100" value={share} aria-label={`${row.name} ${share.toFixed(1)}%`} />
              </div>
            })}</div>
            : <p className="admin-hint">本月暂无{group.title.slice(0, 2)}记录。</p>}
        </section>)}
      </>}

      {entryOpen && <MusicDialog
        className={`account-sheet finance-entry-sheet${initialEntryKind ? ' finance-entry-direct' : ''}`}
        title={`${ENTRY_TITLES[kind] || '记账'}记账`}
        headerContent={!alwaysShowAmounts && <div className="finance-privacy-bar">{privacyButton}<span>编辑金额正常显示</span></div>}
        scrollBody onClose={closeEntry}
        footer={<div className="finance-entry-actions">
          <button type="button" disabled={busy} onClick={closeEntry}>关闭</button>
          <button className="finance-save" type="button" disabled={busy}
            onClick={() => entryFormRef.current?.requestSubmit()}>
            {busy ? '保存中…' : '保存记录'}
          </button>
        </div>}>
      {error && <p role="alert" className="admin-error">{error}</p>}
      {notice && <p role="status" className="admin-notice">{notice}</p>}
      <section className="admin-card">
        <p className="finance-entry-hint">
          {kind === 'expense' ? '记下今天的花销，默认使用日常账户和“其他”分类。' :
            kind === 'income' ? '这里只记其他收入；客人消费提成已自动同步，不要重复录入。' :
              kind === 'transfer' ? '账户之间转账或存钱，不会计入收入和支出。' :
                kind === 'borrow' ? '收到借款计入账户余额，但不计为收入。' :
                kind === 'lend' ? '借钱给他人会从账户余额扣除，并计入待收借款；不计为支出。' :
                  kind === 'balance_add' ? '把已有的私人资金补入所选账户，只增加账户余额，不计为收入。' :
                    '还款或收款时拆分填写本金和利息，本金不计入收入或支出。'}
        </p>
        <form ref={entryFormRef} className="finance-form" onSubmit={saveEntry}>
          <div className="finance-kind-options">
            {[
              ['expense', '支出'], ['income', '收入'], ['transfer', '转账'],
            ].map(([value, label]) => <button key={value} type="button" aria-pressed={kind === value}
              onClick={() => changeEntry('kind', value)}>{label}</button>)}
          </div>
          <label>借款与余额<select value={['borrow', 'lend', 'debt_repay', 'debt_collect', 'balance_add'].includes(kind) ? kind : ''}
            onChange={event => event.target.value && changeEntry('kind', event.target.value)}>
            <option value="">不涉及借款</option><option value="borrow">我借入</option>
            <option value="lend">我借出</option><option value="debt_repay">偿还借款</option>
            <option value="debt_collect">收回借款</option><option value="balance_add">补充账户金额</option>
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
            <label>归还本金 USD<input required inputMode="decimal" placeholder="例如 100.00"
              pattern="\d{1,10}(\.\d{1,2})?" value={entry.principal}
              onChange={event => changeEntry('principal', event.target.value)} /></label>
            <label>利息 USD<input inputMode="decimal" placeholder="没有利息填 0"
              pattern="\d{1,10}(\.\d{1,2})?" value={entry.interest}
              onChange={event => changeEntry('interest', event.target.value)} /></label>
                {!data.debts.some(row => row.direction === (kind === 'debt_repay' ? 'payable' : 'receivable')) &&
                  <p className="finance-entry-hint finance-wide">
                    {kind === 'debt_repay' ? '目前没有待还借款，请先登记“我借入”。' : '目前没有待收借款，请先登记“我借出”。'}
                  </p>}
              </> : <>
            {['borrow', 'lend'].includes(kind) && <label>往来对象<input required maxLength={128}
              value={entry.counterparty} onChange={event => changeEntry('counterparty', event.target.value)} /></label>}
            <label>金额 USD<input required inputMode="decimal" placeholder="例如 12.50"
              pattern="\d{1,10}(\.\d{1,2})?" value={entry.amount}
              onChange={event => changeEntry('amount', event.target.value)} /></label>
          </>}
          {['income', 'expense'].includes(kind) && <label>分类<select required value={entry.category_id}
            onChange={event => changeEntry('category_id', event.target.value)}>
            <option value="">选择分类</option>
            {visibleCategories.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
          </select></label>}
          {['income', 'expense', 'transfer', 'borrow', 'lend', 'debt_repay', 'debt_collect', 'balance_add'].includes(kind) &&
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
        </form>
        {!initialEntryKind && <p className="admin-hint">保存后可以继续登记下一笔；账户和分类会保留，方便连续记账。</p>}
      </section>
      </MusicDialog>}

      {activeView === 'settings' && <>
      <section className="admin-card finance-settings-card">
        <details className="finance-settings-details">
          <summary><span><strong>账户</strong><small>{data.accounts.length} 个账户</small></span><span className="finance-settings-toggle">管理　⌄</span></summary>
          <div className="finance-settings-content">
            <div className="finance-account-list">{data.accounts.map(row => <form className="finance-account-row"
              key={row.id} onSubmit={async event => {
                event.preventDefault()
                const result = await perform('admin_finance_account_opening', {
                  account_id: row.id, opening_balance: openingBalances[row.id] ?? '0',
                }, '账户期初余额已保存。')
                if (!result.ok && result.message) setError(`保存失败：${result.message}`)
              }}>
              <div><strong>{row.name}</strong><span>{row.account_type === 'savings' ? '储蓄' : '日常'} · 当前余额 ${money(row.balance)}</span></div>
              <label>期初余额<input required inputMode="decimal" pattern="\d{1,10}(\.\d{1,2})?"
                value={openingBalances[row.id] ?? '0'} onChange={event => setOpeningBalances(current =>
                  ({ ...current, [row.id]: event.target.value }))} /></label>
              <button disabled={busy}>保存期初金额</button>
            </form>)}</div>
            <form className="finance-inline-form" onSubmit={async event => {
              event.preventDefault()
              const result = await perform('admin_finance_account', account, '账户已添加。')
              if (result.ok) setAccount({ name: '', account_type: 'cash', opening_balance: '0' })
            }}>
              <input required maxLength={80} aria-label="新账户名称" placeholder="账户名称" value={account.name}
                onChange={event => setAccount({ ...account, name: event.target.value })} />
              <select aria-label="账户类型" value={account.account_type}
                onChange={event => setAccount({ ...account, account_type: event.target.value })}>
                <option value="cash">日常账户</option><option value="savings">储蓄账户</option>
              </select>
              <input required inputMode="decimal" aria-label="账户期初余额" placeholder="期初余额 USD"
                pattern="\d{1,10}(\.\d{1,2})?" value={account.opening_balance}
                onChange={event => setAccount({ ...account, opening_balance: event.target.value })} />
              <button disabled={busy}>添加账户</button>
            </form>
          </div>
        </details>
      </section>
      </>}

      {activeView === 'planning' && <>
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
      </>}

      {activeView === 'settings' && <>
      <section className="admin-card finance-settings-card">
        <details className="finance-settings-details" open>
          <summary><span><strong>自定义分类</strong><small>{(data.all_categories || []).length} 个分类</small></span><span className="finance-settings-toggle">管理　⌄</span></summary>
          <div className="finance-settings-content finance-category-settings">
            <p className="finance-category-intro">收入和支出分类分开展示，删除分类不会影响已有账目。</p>
            {['expense', 'income'].map(kind => {
              const rows = categoryRows.filter(row => row.kind === kind)
              return <section className="finance-category-group" key={kind}>
                <h4>{kind === 'expense' ? '支出分类' : '收入分类'}<span>{rows.length}</span></h4>
                <div className="finance-category-list">{rows.map(renderCategory)}</div>
              </section>
            })}
            {deletedCategoryRows.length > 0 && <details className="finance-deleted-categories">
              <summary>已删除分类 <span>{deletedCategoryRows.length}</span></summary>
              <div>{deletedCategoryRows.map(row => <div className="finance-deleted-category" key={row.id}>
                <span>{row.name} · {row.kind === 'income' ? '收入' : '支出'}</span>
                <button type="button" disabled={busy} onClick={() =>
                  perform('admin_finance_category_restore', { category_id: row.id }, '分类已恢复。')}>
                  恢复
                </button>
              </div>)}</div>
            </details>}
            <form className="finance-category-add" onSubmit={async event => {
              event.preventDefault()
              const result = await perform('admin_finance_category', category, '分类已添加。')
              if (result.ok) setCategory(current => ({ ...current, name: '' }))
            }}>
              <h4>新增分类</h4>
              <div className="finance-category-add-fields">
                <input required maxLength={48} aria-label="新分类名称" placeholder="输入分类名称"
                  value={category.name} onChange={event => setCategory({ ...category, name: event.target.value })} />
                <select aria-label="新分类收支类型" value={category.kind}
                  onChange={event => setCategory({ ...category, kind: event.target.value })}>
                  <option value="expense">支出</option><option value="income">收入</option>
                </select>
                <button disabled={busy || !category.name.trim()}>添加分类</button>
              </div>
            </form>
          </div>
        </details>
      </section>
      </>}

      {activeView === 'planning' && <>
      <section className="admin-card">
        <div className="finance-budget-header">
          <div><h3>月度预算 · {month}</h3>
            <div className="finance-budget-totals">
              <p>收入限额 <strong>${money(budgetTotal(incomeBudgetRows))}</strong></p>
              <p>支出限额 <strong>${money(budgetTotal(expenseBudgetRows))}</strong></p>
            </div>
          </div>
          <div className="finance-budget-actions">
            {budgetEditing && <button className="finance-cancel-budgets" type="button" disabled={busy}
              onClick={cancelBudgetEdit}>取消</button>}
            {budgetEditing
              ? <button className="finance-save-budgets" form="finance-budgets-form" disabled={busy}>
                {busy ? '保存中…' : '统一保存预算'}
              </button>
              : <button className="finance-save-budgets" type="button"
                onClick={() => setBudgetEditing(true)}>修改预算</button>}
          </div>
        </div>
        {error && <p ref={planningErrorRef} role="alert" className="admin-error finance-inline-error">{error}</p>}
        <form id="finance-budgets-form" className="finance-budgets-form" onSubmit={saveBudgets}>
          {[
            ['收入预算', incomeBudgetRows],
            ['支出预算', expenseBudgetRows],
          ].map(([title, rows]) => <section className="finance-budget-group" key={title}>
            <h4>{title}</h4>
            {rows.map(row => {
              const amount = Number(budgets[row.id] ?? row.budget ?? 0)
              return <div className={`finance-budget${budgetEditing ? ' is-editing' : ''}`} key={row.id}>
                <label>{row.name}
                  {budgetEditing
                    ? <input inputMode="decimal" aria-label={`${row.name}月度限额`}
                      pattern="\d{1,10}(\.\d{1,2})?" title="请输入整数或最多两位小数的金额"
                      value={budgets[row.id] ?? '0'} onChange={event =>
                        setBudgets(current => ({ ...current, [row.id]: event.target.value }))} />
                    : <strong className="finance-budget-value">${money(amount)}</strong>}
                </label>
                <span>已用 ${money(row.spent)}{budgetEditing ? ' / 限额' : ''}</span>
                {budgetEditing && Number(row.budget) > 0 &&
                  <progress max="100" value={Math.min(Number(row.spent) / Number(row.budget) * 100, 100)} />}
                {budgetEditing && Number(row.budget) > 0 && Number(row.spent) > Number(row.budget) &&
                  <strong className="finance-over">超过限额 ${money(Number(row.spent) - Number(row.budget))}</strong>}
              </div>
            })}
          </section>)}
        </form>
        <div className="finance-goals">
          <h3>储蓄目标</h3>
          {data.goals.map(row => {
          const remaining = Math.max(0, Number(row.target) - Number(row.saved))
          return <div key={row.id}>
            <p>{row.name}：已存 ${money(row.saved)} / 目标 ${money(row.target)} ·
              {remaining ? `还差 $${money(remaining)}` : '已达成'} · {row.target_date || '未设日期'}</p>
            <progress max="100" value={Number(row.target) > 0 ? Math.max(0, Math.min(Number(row.saved) / Number(row.target) * 100, 100)) : 0} />
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
      </>}

      {activeView === 'settings' && <>
      <section className="admin-card finance-settings-card">
        <details className="finance-settings-details">
          <summary><span><strong>各项目固定提成</strong><small>{commissions.length} 个项目 · 按会所分组</small></span><span className="finance-settings-toggle">管理　⌄</span></summary>
          <div className="finance-settings-content">
            <p className="admin-hint">金额单位为美元。修改只影响之后登记的消费；退款按已登记的提成快照扣减，最低为 $0。</p>
            <label className="finance-commission-search">搜索项目
              <input type="search" value={commissionSearch} placeholder="输入会所或服务名称"
                onChange={event => setCommissionSearch(event.target.value)} />
            </label>
            <form onSubmit={saveCommissions}>
              <div className="finance-commission-groups">
                {filteredCommissionGroups.map(([venue, rows]) => <details className="finance-commission-venue" key={venue}>
                  <summary><strong>{venue}</strong><span>{rows.length} 项　⌄</span></summary>
                  <div className="finance-commission-list">{rows.map(row => <label key={`${row.venue_id}:${row.service_key}`}>
                    <span>{row.service_name}{row.listed_price ? `（菜单 $${money(row.listed_price)}）` : ''}</span>
                    <input required inputMode="decimal" aria-label={`${row.venue_name} ${row.service_name}固定提成`}
                      value={row.commission_amount} onChange={event => setCommissions(current =>
                        current.map(item => item.venue_id === row.venue_id && item.service_key === row.service_key
                          ? { ...item, commission_amount: event.target.value } : item))} />
                  </label>)}</div>
                </details>)}
                {!filteredCommissionGroups.length && <p className="admin-hint">没有找到匹配的项目。</p>}
              </div>
              <button disabled={busy || !commissions.length}>保存提成设置</button>
            </form>
          </div>
        </details>
      </section>
      </>}
    </>}
  </section>
}

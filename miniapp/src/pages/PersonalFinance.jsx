import { useSearchParams } from 'react-router-dom'
import FinanceAdmin from '../components/FinanceAdmin'
import './Admin.css'

const ENTRY_KINDS = new Set(['expense', 'income', 'transfer'])
const VIEWS = {
  today: 'today',
  month: 'records',
  analysis: 'analysis',
  overview: 'overview',
}

export default function PersonalFinance() {
  const [searchParams] = useSearchParams()
  const requestedView = searchParams.get('view')
  const requestedKind = searchParams.get('kind')
  const initialEntryKind = requestedView === 'entry' && ENTRY_KINDS.has(requestedKind)
    ? requestedKind : null
  const initialView = VIEWS[requestedView] || 'overview'

  return <main className="admin-console personal-finance-page" aria-label="个人财务">
    <FinanceAdmin
      key={`${requestedView || 'overview'}:${initialEntryKind || ''}`}
      initialView={initialView}
      initialEntryKind={initialEntryKind}
    />
  </main>
}

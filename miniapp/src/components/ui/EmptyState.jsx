import { Button } from './Button'
import './ui.css'

export function EmptyState({ children, actionLabel, onAction }) {
  return (
    <div className="ui-empty" role="status">
      <div>{children}</div>
      {actionLabel && onAction ? (
        <div className="ui-empty__action">
          <Button variant="ghost" onClick={onAction}>
            {actionLabel}
          </Button>
        </div>
      ) : null}
    </div>
  )
}

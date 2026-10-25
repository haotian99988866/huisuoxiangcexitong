import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { moduleGroups, displayModules } from '../utils/modules'
import { getTenant, tenantPath } from '../utils/tenant'
import { registerBackOverlay } from '../utils/twa'
import { rememberModuleScroll } from '../utils/moduleState'
import './ModuleTabs.css'

export default function ModuleTabs({ active }) {
  const modules = displayModules()
  const navigate = useNavigate()
  const [expanded, setExpanded] = useState(false)
  const trigger = useRef(null)
  const dialog = useRef(null)
  const current = modules.find(item => item.key === active)

  useEffect(() => {
    if (!expanded) return
    const previousOverflow = document.body.style.overflow
    const unregisterBack = registerBackOverlay(() => setExpanded(false))
    document.body.style.overflow = 'hidden'
    dialog.current?.querySelector('button')?.focus()
    const onKey = event => {
      if (event.key === 'Escape') setExpanded(false)
      if (event.key !== 'Tab') return
      const focusable = dialog.current?.querySelectorAll('button, a[href]')
      if (!focusable?.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = previousOverflow
      unregisterBack()
      document.removeEventListener('keydown', onKey)
      trigger.current?.focus()
    }
  }, [expanded])

  const selectModule = item => {
    rememberModuleScroll(active)
    setExpanded(false)
    if (item.key !== active) navigate(tenantPath(item.path), { replace: true })
  }

  return (
    <nav aria-label="模块导航" className="module-navigation">
      {getTenant() && <div className="module-current">{getTenant().brand_name}</div>}
      <div className="module-shortcuts">
        {modules.filter(item => item.common).map(item => (
          <button
            key={item.key}
            type="button"
            aria-current={active === item.key ? 'page' : undefined}
            className={active === item.key ? 'module-shortcut selected' : 'module-shortcut'}
            onClick={() => selectModule(item)}
          >
            <span aria-hidden="true">{item.icon}</span> {item.label}
          </button>
        ))}
        <button ref={trigger} type="button" className={`module-shortcut module-all${current && !current.common ? ' selected' : ''}`}
          aria-haspopup="dialog" aria-expanded={expanded} onClick={() => setExpanded(true)}>
          全部 ▾
        </button>
      </div>
      {current && !current.common && <div className="module-current">当前位置：{current.label}</div>}
      {expanded && createPortal(
        <div className="module-backdrop" onClick={event => {
          if (event.target === event.currentTarget) setExpanded(false)
        }}>
          <section ref={dialog} role="dialog" aria-modal="true" aria-labelledby="module-panel-title" className="module-panel">
            <header className="module-panel-header">
              <h2 id="module-panel-title">全部功能</h2>
              <button type="button" aria-label="关闭全部功能" onClick={() => setExpanded(false)}>✕</button>
            </header>
            {moduleGroups.map(group => {
              const items = modules.filter(item => item.group === group.key)
              if (!items.length) return null
              return (
                <section key={group.key} className="module-group">
                  <h3>{group.label}</h3>
                  <div className="module-grid">
                    {items.map(item => {
                      const content = <><span aria-hidden="true">{item.icon}</span><strong>{item.label}</strong><small>{item.description}</small></>
                      return item.url ? (
                        <a key={item.key} href={item.url} target="_blank" rel="noreferrer" className="module-tile">{content}</a>
                      ) : (
                        <button key={item.key} type="button" className={`module-tile${active === item.key ? ' selected' : ''}`}
                          aria-current={active === item.key ? 'page' : undefined} onClick={() => selectModule(item)}>
                          {content}
                        </button>
                      )
                    })}
                  </div>
                </section>
              )
            })}
          </section>
        </div>,
        document.body,
      )}
    </nav>
  )
}

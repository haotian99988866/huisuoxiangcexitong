import { useEffect, useRef } from 'react'
import { registerBackOverlay } from '../utils/twa'
import MusicIcon from './MusicIcon'
import './MusicDialog.css'

export default function MusicDialog({ title, onClose, children, full = false, className = '', headerContent, footer, scrollBody = false }) {
  const ref = useRef(null)
  const headingRef = useRef(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    const dialog = ref.current
    const previousFocus = document.activeElement
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    dialog.showModal()
    if (scrollBody) headingRef.current?.focus({ preventScroll: true })
    const unbind = registerBackOverlay(() => closeRef.current())
    return () => {
      unbind()
      document.body.style.overflow = overflow
      dialog.close()
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus({ preventScroll: true })
    }
  }, [])
  const heading = <header className="music-section-heading">
    <h2 ref={headingRef} tabIndex={scrollBody ? -1 : undefined}>{title}</h2>
    <button type="button" className="music-icon-button" aria-label={full ? '收起播放器' : '关闭面板'} onClick={onClose}>
      <MusicIcon name={full ? 'down' : 'close'} />
    </button>
  </header>
  return (
    <dialog ref={ref} className={`music-sheet${full ? ' music-full-player' : ''}${scrollBody ? ' dialog-panel' : ''}${className ? ` ${className}` : ''}`} aria-label={title}
      onClose={event => { if (!event.currentTarget.open) closeRef.current() }}
      onCancel={event => { event.preventDefault(); closeRef.current() }}
      onClick={event => { if (event.target === event.currentTarget && !full) closeRef.current() }}>
      <div className="music-sheet-content">
        {!full && <div className="music-sheet-handle" aria-hidden="true" />}
        {scrollBody ? <div className="dialog-panel-header">{heading}{headerContent}</div> : <>{heading}{headerContent}</>}
        {scrollBody ? <div className="dialog-panel-body">{children}</div> : children}
        {scrollBody && footer ? <div className="dialog-panel-footer">{footer}</div> : footer}
      </div>
    </dialog>
  )
}

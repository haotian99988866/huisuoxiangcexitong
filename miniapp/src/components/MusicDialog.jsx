import { useEffect, useRef } from 'react'
import { registerBackOverlay } from '../utils/twa'
import MusicIcon from './MusicIcon'

export default function MusicDialog({ title, onClose, children, full = false }) {
  const ref = useRef(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    const dialog = ref.current
    const previousFocus = document.activeElement
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    dialog.showModal()
    const unbind = registerBackOverlay(() => closeRef.current())
    return () => {
      unbind()
      document.body.style.overflow = overflow
      dialog.close()
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus({ preventScroll: true })
    }
  }, [])
  return (
    <dialog ref={ref} className={`music-sheet${full ? ' music-full-player' : ''}`} aria-label={title}
      onClose={event => { if (!event.currentTarget.open) closeRef.current() }}
      onCancel={event => { event.preventDefault(); closeRef.current() }}
      onClick={event => { if (event.target === event.currentTarget && !full) closeRef.current() }}>
      <div className="music-sheet-content">
        {!full && <div className="music-sheet-handle" aria-hidden="true" />}
        <header className="music-section-heading">
          <h2>{title}</h2>
          <button type="button" className="music-icon-button" aria-label={full ? '收起播放器' : '关闭面板'} onClick={onClose}>
            <MusicIcon name={full ? 'down' : 'close'} />
          </button>
        </header>
        {children}
      </div>
    </dialog>
  )
}

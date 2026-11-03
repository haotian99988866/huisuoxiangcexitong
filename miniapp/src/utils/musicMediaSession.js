export function bindMusicMediaSession(session, handlers) {
  if (!session || typeof session.setActionHandler !== 'function') return () => {}
  const registered = []
  for (const [action, handler] of Object.entries(handlers)) {
    try {
      session.setActionHandler(action, handler)
      registered.push(action)
    } catch (error) {
      console.warn('[Music media session action]', action, error)
    }
  }
  return () => {
    for (const action of registered) {
      try {
        session.setActionHandler(action, null)
      } catch (error) {
        console.warn('[Music media session cleanup]', action, error)
      }
    }
  }
}

export function updateMusicMediaPosition(session, duration, position, playbackRate = 1) {
  if (!session || typeof session.setPositionState !== 'function') return
  try {
    if (!Number.isFinite(duration) || duration <= 0) {
      session.setPositionState()
      return
    }
    session.setPositionState({
      duration,
      position: Number.isFinite(position) ? Math.max(0, Math.min(duration, position)) : 0,
      playbackRate: Number.isFinite(playbackRate) && playbackRate > 0 ? playbackRate : 1,
    })
  } catch (error) {
    console.warn('[Music media session position]', error)
  }
}

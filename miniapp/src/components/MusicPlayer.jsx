import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { API_BASE_URL } from '../utils/twa'
import { requestMusic } from '../utils/music'
import { nextTrack } from '../utils/musicPlayback'
import './MusicPlayer.css'

const MusicContext = createContext(null)
export const useMusic = () => useContext(MusicContext)
export const musicTime = seconds => {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00'
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
}

export default function MusicPlayer({ children }) {
  const audioRef = useRef(null)
  const requestId = useRef(0)
  const currentRef = useRef(null)
  const controller = useRef(null)
  const [tracks, setTracks] = useState([])
  const [status, setStatus] = useState('idle')
  const [catalogError, setCatalogError] = useState('')
  const [current, setCurrent] = useState(null)
  const [playing, setPlaying] = useState(false)
  const [waiting, setWaiting] = useState(false)
  const [error, setError] = useState('')
  const [position, setPosition] = useState(0)
  const [duration, setDuration] = useState(0)
  const [profile, setProfile] = useState(null)
  const [playlists, setPlaylists] = useState([])
  const [accountError, setAccountError] = useState('')
  const [busy, setBusy] = useState(false)
  const [queueIds, setQueueIds] = useState(null)
  const accountChain = useRef(Promise.resolve())
  const restored = useRef(false)
  const pendingSeek = useRef(null)
  const lastSaved = useRef('')
  const profileRef = useRef(null)
  const mounted = useRef(true)
  const [volume, setVolume] = useState(1)
  const location = useLocation()
  const mode = profile?.mode || 'sequential'
  const queue = queueIds === null ? tracks : queueIds.map(id => tracks.find(track => track.id === id)).filter(Boolean)

  const accountRequest = useCallback((action, fields = {}) => {
    const task = accountChain.current.then(async () => {
      if (mounted.current) {
        setBusy(true)
        if (!['recent', 'resume'].includes(fields.operation)) setAccountError('')
      }
      try {
        const result = await requestMusic(action, fields)
        if (mounted.current) {
          profileRef.current = result.profile
          setProfile(result.profile)
          setPlaylists(result.playlists)
        }
        return result
      } catch (failure) {
        console.error('[Music account]', failure)
        if (mounted.current) setAccountError(failure.message)
        return null
      } finally { if (mounted.current) setBusy(false) }
    })
    accountChain.current = task
    return task
  }, [])
  const loadAccount = useCallback(() => accountRequest('music_profile'), [accountRequest])
  const saveProgress = useCallback((clear = false) => {
    if (!profileRef.current) return
    const resume = !clear && currentRef.current
      ? { id: currentRef.current.id, position: Math.floor(audioRef.current.currentTime || 0) } : null
    const key = JSON.stringify(resume)
    if (key === lastSaved.current) return
    lastSaved.current = key
    accountRequest('music_update', { operation: 'resume', resume }).then(result => {
      if (!result) lastSaved.current = ''
    })
  }, [accountRequest])

  useEffect(() => {
    mounted.current = true
    if (window.Telegram?.WebApp?.initData) loadAccount()
    const timer = setInterval(() => {
      if (audioRef.current && !audioRef.current.paused) saveProgress()
    }, 10000)
    const saveOnHide = () => { if (document.visibilityState === 'hidden') saveProgress() }
    const saveOnExit = () => saveProgress()
    document.addEventListener('visibilitychange', saveOnHide)
    window.addEventListener('pagehide', saveOnExit)
    return () => {
      mounted.current = false
      clearInterval(timer)
      document.removeEventListener('visibilitychange', saveOnHide)
      window.removeEventListener('pagehide', saveOnExit)
    }
  }, [loadAccount, saveProgress])

  useEffect(() => {
    if (!profile || status !== 'ready' || restored.current) return
    restored.current = true
    if (currentRef.current || !profile.resume) return
    const track = tracks.find(item => item.id === profile.resume.id)
    if (!track) return
    currentRef.current = track
    setCurrent(track)
    setPosition(profile.resume.position)
    pendingSeek.current = profile.resume.position
    audioRef.current.src = `${API_BASE_URL}/music-audio/${track.id}`
  }, [profile, tracks, status])

  const load = useCallback(async () => {
    controller.current?.abort()
    const active = new AbortController()
    controller.current = active
    setStatus('loading')
    setCatalogError('')
    const timer = setTimeout(() => active.abort(), 15000)
    try {
      const response = await fetch(`${API_BASE_URL}/webhook`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'get_music' }), signal: active.signal,
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.error || '音乐列表加载失败')
      if (!Array.isArray(data.tracks)) throw new Error('音乐列表格式无效')
      if (controller.current !== active) return
      setTracks(data.tracks)
      setStatus('ready')
    } catch (failure) {
      if (controller.current !== active) return
      console.error('[Music catalog]', failure)
      setCatalogError(failure.name === 'AbortError' ? '音乐列表加载超时，请重试' : failure.message)
      setStatus('error')
    } finally { clearTimeout(timer) }
  }, [])

  useEffect(() => {
    const audio = audioRef.current
    const pauseForOtherMedia = event => {
      if (event.target !== audio && event.target instanceof HTMLMediaElement) {
        requestId.current += 1
        audio.pause()
        setWaiting(false)
      }
    }
    document.addEventListener('play', pauseForOtherMedia, true)
    return () => {
      document.removeEventListener('play', pauseForOtherMedia, true)
      controller.current?.abort()
      controller.current = null
      requestId.current += 1
      audio.pause()
      audio.removeAttribute('src')
      audio.load()
    }
  }, [])

  const start = async (track, selectedQueue) => {
    const audio = audioRef.current
    const id = ++requestId.current
    setError('')
    setWaiting(true)
    if (selectedQueue) setQueueIds(selectedQueue.map(item => item.id))
    if (currentRef.current?.id !== track.id) {
      saveProgress()
      pendingSeek.current = null
      audio.pause()
      audio.src = `${API_BASE_URL}/music-audio/${track.id}`
      currentRef.current = track
      setCurrent(track)
      setPosition(0)
      setDuration(0)
    } else if (audio.error) {
      audio.load()
    }
    document.querySelectorAll('video, audio').forEach(media => {
      if (media !== audio) media.pause()
    })
    try {
      await audio.play()
      if (requestId.current === id && profileRef.current) {
        accountRequest('music_update', { operation: 'recent', track_id: track.id })
        saveProgress()
      }
    } catch (failure) {
      if (requestId.current !== id) return
      console.error('[Music playback]', failure)
      setWaiting(false)
      setError(failure.name === 'NotAllowedError'
        ? '客户端阻止了播放，请再次点击播放按钮。'
        : '音频无法播放，请重试；若仍失败，请联系管理员检查文件和格式。')
    }
  }
  const pause = () => {
    requestId.current += 1
    audioRef.current.pause()
    setWaiting(false)
    saveProgress()
  }
  const toggle = () => {
    if (playing || waiting) pause()
    else if (current) start(current)
  }
  const step = direction => {
    const track = nextTrack(queue, currentRef.current?.id, direction, mode)
    if (track) start(track)
  }
  const stop = () => {
    pause()
    audioRef.current.removeAttribute('src')
    audioRef.current.load()
    currentRef.current = null
    pendingSeek.current = null
    saveProgress(true)
    setCurrent(null)
    setPosition(0)
    setDuration(0)
    setError('')
  }
  const seek = value => {
    if (!duration) return
    audioRef.current.currentTime = Math.max(0, Math.min(duration, Number(value)))
    setPosition(audioRef.current.currentTime)
    saveProgress()
  }
  const changeVolume = value => {
    audioRef.current.volume = Number(value)
    setVolume(Number(value))
  }
  const failed = () => {
    if (!currentRef.current) return
    setWaiting(false)
    setPlaying(false)
    setError('音频加载失败或歌曲已下架，请重试或选择其他歌曲。')
  }
  const value = {
    tracks, status, catalogError, load, current, playing, waiting, error,
    position, duration, mode, volume, start, pause, toggle, step, stop, seek,
    changeVolume, profile, playlists, accountError, busy, accountRequest, loadAccount,
    queue, setQueueIds,
    changeMode: mode => accountRequest('music_update', { operation: 'mode', mode }),
    favorite: (id, enabled) => accountRequest('music_update', { operation: 'favorite', track_id: id, enabled }),
  }

  return (
    <MusicContext.Provider value={value}>
      <div className={current ? 'music-layout music-layout-active' : 'music-layout'}>{children}</div>
      <audio ref={audioRef} preload="metadata" loop={mode === 'single'}
        onPlay={() => setPlaying(true)}
        onPlaying={() => setWaiting(false)}
        onPause={() => { setPlaying(false); setWaiting(false); saveProgress() }}
        onWaiting={() => setWaiting(true)}
        onTimeUpdate={event => setPosition(event.currentTarget.currentTime)}
        onDurationChange={event => setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0)}
        onLoadedMetadata={event => {
          const audio = event.currentTarget
          setDuration(Number.isFinite(audio.duration) ? audio.duration : 0)
          if (pendingSeek.current !== null && Number.isFinite(audio.duration)) {
            audio.currentTime = Math.min(pendingSeek.current, Math.max(0, audio.duration - 0.1))
            setPosition(audio.currentTime)
            pendingSeek.current = null
          }
        }}
        onError={failed}
        onEnded={() => { setPlaying(false); setWaiting(false); step(1) }} />
      {current && location.pathname !== '/music' && (
        <aside className="music-mini" aria-label="迷你音乐播放器">
          <Link to="/music" className="music-mini-title">{current.title}<small>{current.artist}</small></Link>
          <button type="button" onClick={toggle} aria-label={playing || waiting ? '暂停音乐' : '播放音乐'}>
            {playing || waiting ? '暂停' : '播放'}
          </button>
          <button type="button" onClick={() => step(1)} disabled={!queue.length} aria-label="下一首音乐">下一首</button>
          <button type="button" onClick={stop} aria-label="关闭音乐播放器">×</button>
          {error && <p role="alert">{error}</p>}
          {accountError && <p role="alert">{accountError}</p>}
        </aside>
      )}
    </MusicContext.Provider>
  )
}

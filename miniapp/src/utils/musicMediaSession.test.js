import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { bindMusicMediaSession, updateMusicMediaPosition } from './musicMediaSession.js'

test('unsupported clients do not prevent ordinary music playback', () => {
  bindMusicMediaSession(undefined, { play() {} })()
  bindMusicMediaSession({}, { play() {} })()
  updateMusicMediaPosition(undefined, 100, 20)
  updateMusicMediaPosition({}, 100, 20)
})

test('system controls invoke supplied actions and cleanup removes them', () => {
  const registered = new Map()
  const session = { setActionHandler: (action, handler) => registered.set(action, handler) }
  const calls = []
  const cleanup = bindMusicMediaSession(session, {
    play: () => calls.push('play'),
    pause: () => calls.push('pause'),
    nexttrack: () => calls.push('next'),
    seekto: details => calls.push(details.seekTime),
  })
  registered.get('play')()
  registered.get('pause')()
  registered.get('nexttrack')()
  registered.get('seekto')({ seekTime: 40 })
  assert.deepEqual(calls, ['play', 'pause', 'next', 40])
  cleanup()
  assert.ok([...registered.values()].every(handler => handler === null))
})

test('unsupported individual actions are logged and do not disable remaining controls', () => {
  const warnings = []
  const original = console.warn
  console.warn = (...args) => warnings.push(args)
  try {
    const registered = []
    const cleanup = bindMusicMediaSession({
      setActionHandler(action) {
        if (action === 'seekto') throw new Error('Unsupported action')
        registered.push(action)
      },
    }, { seekto() {}, pause() {} })
    cleanup()
    assert.deepEqual(registered, ['pause', 'pause'])
    assert.equal(warnings.length, 1)
  } finally {
    console.warn = original
  }
})

test('system position is bounded and unknown duration clears previous song progress', () => {
  const states = []
  const session = { setPositionState: state => states.push(state) }
  updateMusicMediaPosition(session, 100, 150)
  updateMusicMediaPosition(session, 100, -10)
  updateMusicMediaPosition(session, 100, NaN, 0)
  updateMusicMediaPosition(session, 0, 50)
  updateMusicMediaPosition(session, Infinity, 50)
  assert.deepEqual(states, [
    { duration: 100, position: 100, playbackRate: 1 },
    { duration: 100, position: 0, playbackRate: 1 },
    { duration: 100, position: 0, playbackRate: 1 },
    undefined, undefined,
  ])
})

test('music lock-screen controls disable seek shortcuts and preserve track navigation', () => {
  const source = readFileSync(new URL('../components/MusicPlayer.jsx', import.meta.url), 'utf8')
  assert.match(source, /seekbackward:\s*null/)
  assert.match(source, /seekforward:\s*null/)
  assert.match(source, /previoustrack:\s*\(\) => mediaControls\.current\.step\(-1\)/)
  assert.match(source, /nexttrack:\s*\(\) => mediaControls\.current\.step\(1\)/)
  const actions = new Map()
  const calls = []
  const cleanup = bindMusicMediaSession({
    setActionHandler: (action, handler) => actions.set(action, handler),
  }, {
    previoustrack: () => calls.push('previous'),
    nexttrack: () => calls.push('next'),
    seekbackward: null,
    seekforward: null,
  })
  assert.equal(actions.get('seekbackward'), null)
  assert.equal(actions.get('seekforward'), null)
  actions.get('previoustrack')()
  actions.get('nexttrack')()
  assert.deepEqual(calls, ['previous', 'next'])
  cleanup()
  assert.ok([...actions.values()].every(handler => handler === null))
})

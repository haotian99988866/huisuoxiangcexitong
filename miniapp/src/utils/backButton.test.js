import test from 'node:test'
import assert from 'node:assert/strict'
import { bindBackButton, registerBackOverlay } from './backButton.js'

function mockTelegram() {
  const listeners = new Set()
  const button = {
    visible: false,
    show() { this.visible = true },
    hide() { this.visible = false },
    onClick(handler) { listeners.add(handler) },
    offClick(handler) { listeners.delete(handler) },
  }
  let closed = 0
  globalThis.window = { Telegram: { WebApp: { BackButton: button, close() { closed++ } } } }
  return { button, listeners, click: () => [...listeners].forEach(handler => handler()), closed: () => closed }
}

test('root hides back; nested overlays return one layer at a time without closing the app', () => {
  const tg = mockTelegram()
  const cleanups = []
  try {
    cleanups.push(bindBackButton(null))
    assert.equal(tg.button.visible, false)
    let pageBacks = 0
    const detail = bindBackButton(() => pageBacks++)
    cleanups.push(detail)
    assert.equal(tg.button.visible, true)
    let outerBacks = 0
    const outer = registerBackOverlay(() => outerBacks++)
    cleanups.push(outer)
    let innerBacks = 0
    const inner = registerBackOverlay(() => innerBacks++)
    cleanups.push(inner)
    assert.equal(tg.listeners.size, 1)
    tg.click()
    assert.deepEqual([innerBacks, outerBacks, pageBacks], [1, 0, 0])
    inner()
    tg.click()
    assert.deepEqual([innerBacks, outerBacks, pageBacks], [1, 1, 0])
    outer()
    tg.click()
    assert.equal(pageBacks, 1)
    detail()
    assert.equal(tg.button.visible, false)
    const rootOverlay = registerBackOverlay(() => outerBacks++)
    cleanups.push(rootOverlay)
    assert.equal(tg.button.visible, true)
    tg.click()
    rootOverlay()
    assert.equal(tg.button.visible, false)
    assert.equal(tg.closed(), 0)
  } finally {
    cleanups.reverse().forEach(cleanup => cleanup())
    assert.equal(tg.listeners.size, 0)
    delete globalThis.window
  }
})

test('route cleanup cannot hide an active overlay or remove a newer page handler', () => {
  const tg = mockTelegram()
  const cleanups = []
  try {
    const old = bindBackButton(() => assert.fail('Old page must not receive back'))
    cleanups.push(old)
    const root = bindBackButton(null)
    cleanups.push(root)
    old()
    assert.equal(tg.button.visible, false)
    let returned = 0
    const overlay = registerBackOverlay(() => returned++)
    cleanups.push(overlay)
    root()
    assert.equal(tg.button.visible, true)
    tg.click()
    assert.equal(returned, 1)
    overlay()
    assert.equal(tg.button.visible, false)
  } finally {
    cleanups.reverse().forEach(cleanup => cleanup())
    delete globalThis.window
  }
})

test('browser without Telegram supports registration and cleanup', () => {
  globalThis.window = {}
  try {
    const root = bindBackButton(null)
    const overlay = registerBackOverlay(() => {})
    overlay()
    root()
  } finally { delete globalThis.window }
})

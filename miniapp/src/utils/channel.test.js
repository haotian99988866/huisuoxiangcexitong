import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'

const html = readFileSync(new URL('../../public/channel.html', import.meta.url), 'utf8')
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1]

test('channel page opens the fixed channel and keeps manual fallback', () => {
  for (const search of ['', '?target=contact', '?target=https://example.com']) {
  for (const mode of ['telegram', 'browser', 'failure']) {
    let click
    let opened
    let prevented = false
    const status = {}
    const link = { addEventListener(_, handler) { click = handler } }
    const tg = mode === 'browser' ? undefined : {
      ready() {},
      openTelegramLink(url) {
        if (mode === 'failure') throw new Error('unsupported')
        opened = url
      },
    }
    runInNewContext(script, {
      window: { Telegram: { WebApp: tg }, location: { search } },
      URLSearchParams,
      document: {
        getElementById(id) {
          return id === 'status' ? status : id === 'open' ? link : {}
        },
      },
      console: { error() {} },
    })
    click({ preventDefault() { prevented = true } })
    assert.equal(prevented, mode === 'telegram')
    const expected = search === '?target=contact' ? 'https://t.me/haotian99988866' : 'https://t.me/haotianyl99988866'
    if (mode === 'telegram') assert.equal(opened, expected)
    assert.equal(link.href, expected)
    if (mode === 'failure') assert.ok(status.textContent.includes('未成功'))
  }
  }
})

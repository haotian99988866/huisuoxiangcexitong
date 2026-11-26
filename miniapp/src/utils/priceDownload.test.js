import test from 'node:test'
import assert from 'node:assert/strict'
import { telegramDownload } from './priceDownload.js'

test('Telegram native download requests an HTTPS file and reports rejection', async () => {
  const calls = []
  const tg = {
    isVersionAtLeast: version => version === '8.0',
    downloadFile: (params, callback) => { calls.push(params); callback(true) },
  }
  await telegramDownload(tg, 'https://example.test/temporary', '价目.xlsx')
  assert.deepEqual(calls, [{ url: 'https://example.test/temporary', file_name: '价目.xlsx' }])
  tg.downloadFile = (_params, callback) => callback(false)
  await assert.rejects(telegramDownload(tg, 'https://example.test/temporary', '价目.xlsx'), /未确认/)
  assert.throws(() => telegramDownload({}, 'https://example.test/temporary', '价目.xlsx'), /更新 Telegram/)
  assert.throws(() => telegramDownload(tg, 'http://example.test/temporary', '价目.xlsx'), /HTTPS/)
})

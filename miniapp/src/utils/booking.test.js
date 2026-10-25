import test from 'node:test'
import assert from 'node:assert/strict'
import { bookingUrl } from './booking.js'

test('draft preserves descriptions and source links', () => {
  const source = 'https://t.me/c/12345/67'
  const url = bookingUrl({ title: '预约工号508', links: [source], details: ['描述：温柔大方\n身高161'] })
  const native = new URL(url)
  assert.equal(native.pathname, '/haotian99988866')
  assert.ok(native.searchParams.get('text').includes(source))
  assert.ok(native.searchParams.get('text').includes('描述：温柔大方'))
})

test('source remains without description and ahead of truncated long content', () => {
  const source = 'https://app.haotianzhushou.com/api/photos/123/image'
  for (const details of [[], ['长'.repeat(6000)]]) {
    const text = new URL(bookingUrl({ title: '预约', links: [source], details })).searchParams.get('text')
    assert.ok(text.includes(source))
    assert.ok(Array.from(text).length <= 4096)
  }
})

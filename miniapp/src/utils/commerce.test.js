import test from 'node:test'
import assert from 'node:assert/strict'
import { money, navigationSection, orderMatches, orderGuidance, validPurchaseAttempt } from './commerce.js'

test('navigation groups old and new deep links under the right main destination', () => {
  for (const path of ['/', '/venue/7', '/venue/7/technicians']) assert.equal(navigationSection(path), 'home')
  for (const path of ['/media', '/movies', '/movies/12']) assert.equal(navigationSection(path), 'movies')
  for (const path of ['/music', '/music/12']) assert.equal(navigationSection(path), 'music')
  for (const path of ['/announcements', '/announcements/12']) assert.equal(navigationSection(path), 'news')
  for (const path of ['/services', '/membership']) assert.equal(navigationSection(path), 'services')
  for (const path of ['/personal', '/recharge', '/orders', '/orders/abc', '/wallet-history', '/admin']) {
    assert.equal(navigationSection(path), 'personal')
  }
  assert.equal(navigationSection('/membership-other'), 'home')
  assert.equal(navigationSection('/music-other'), 'home')
  assert.equal(navigationSection('/movies-other'), 'home')
})

test('order filters keep refunds and uncertain outcomes distinct from completion', () => {
  const expected = {
    manual_pending: 'processing', paid: 'processing', delivering: 'processing', delivered: 'completed',
    refund_pending: 'attention', refunded: 'attention', delivery_unknown: 'attention',
  }
  for (const [status, filter] of Object.entries(expected)) {
    assert.equal(orderMatches({ status }, filter), true)
    assert.equal(orderMatches({ status }, 'all'), true)
    for (const other of ['processing', 'completed', 'attention'].filter(item => item !== filter)) {
      assert.equal(orderMatches({ status }, other), false)
    }
  }
  assert.match(orderGuidance('delivery_unknown'), /不要重复购买/)
  assert.match(orderGuidance('refunded'), /不是退回/)
})

test('money preserves six decimal ledger precision and uses at least two decimals', () => {
  assert.equal(money('15.990000'), '15.99')
  assert.equal(money('-0.000001'), '-0.000001')
  assert.equal(money('0'), '0.00')
})

test('restored pending orders require a valid stable id, recipient and supported plan', () => {
  const attempt = { order_id: 'a'.repeat(32), recipient_username: '@receiver', months: 3 }
  assert.equal(validPurchaseAttempt(attempt), true)
  for (const bad of [null, {}, { ...attempt, order_id: 'x' }, { ...attempt, months: 1 },
    { ...attempt, recipient_username: '' }, { ...attempt, recipient_username: 123 }]) {
    assert.equal(validPurchaseAttempt(bad), false)
  }
})

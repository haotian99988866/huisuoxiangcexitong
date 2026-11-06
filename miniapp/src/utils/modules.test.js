import test from 'node:test'
import assert from 'node:assert/strict'
import { modules } from './modules.js'

test('home menu Mini App links resolve to their intended pages', () => {
  const routes = {
    venue: '/?module=venue',
    delivery: '/?module=delivery',
    hotels: '/hotels',
    movies: '/movies',
    short_dramas: '/short-dramas',
    music: '/music',
    announcements: '/announcements',
    membership: '/membership',
    phone: '/phone-topup',
    recharge: '/recharge',
    personal: '/personal',
    services: '/services',
  }
  for (const [param, path] of Object.entries(routes)) {
    const targets = modules.filter(item => item.startParam === param)
    assert.equal(targets.length, 1, param)
    assert.equal(targets[0].path, path, param)
    assert.ok(!targets[0].adminOnly, param)
  }
})

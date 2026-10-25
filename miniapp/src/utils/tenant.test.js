import test from 'node:test'
import assert from 'node:assert/strict'
import { resolveTenant, initializeTenant, setTenant, tenantPath, mediaUrl, contactUrl, tenantHeaders, announcementText, hasDeliveryAccess, hasVenueAccess } from './tenant.js'
import { bookingUrl } from './booking.js'
import { displayModules } from './modules.js'

test('entry parsing preserves owner and rejects missing or conflicting tenants', () => {
  assert.equal(resolveTenant('', 'movies'), null)
  assert.equal(resolveTenant('?tenant=shop-one', ''), 'shop-one')
  assert.equal(resolveTenant('', 'tenant_shop-one'), 'shop-one')
  assert.equal(resolveTenant('?tenant=shop-one', 'tenant_shop-one'), 'shop-one')
  for (const [search, start] of [['?tenant=', ''], ['', 'tenant_'], ['?tenant=bad/slash', ''], ['?tenant=shop-two', 'tenant_shop-one']]) {
    assert.throws(() => resolveTenant(search, start))
  }
})

test('scoped routes, media, contacts and booking messages preserve tenant context', () => {
  globalThis.window = {
    location: { origin: 'https://example.com', search: '?tenant=shop-one' },
  }
  try {
    initializeTenant()
    assert.throws(() => setTenant(null))
    setTenant({
      slug: 'shop-one', brand_name: '测试品牌', contact_name: '客服甲',
      contact_username: 'contact_one', venue_ids: [3],
    })
    assert.equal(tenantPath('/movies'), '/movies?tenant=shop-one')
    const media = new URL(mediaUrl('/api/ktv-videos/1?thumb=1#t=0.5'))
    assert.equal(media.searchParams.get('tenant'), 'shop-one')
    assert.equal(media.searchParams.get('thumb'), '1')
    assert.equal(media.hash, '#t=0.5')
    assert.deepEqual(tenantHeaders(), { 'X-Tenant': 'shop-one' })
    assert.equal(contactUrl(), 'https://t.me/contact_one')
    assert.equal(hasDeliveryAccess(), false)
    assert.equal(hasVenueAccess(), true)
    assert.equal(displayModules().some(item => item.key === 'music'), false)
    assert.ok(announcementText().includes('测试品牌'))
    const booking = new URL(bookingUrl({ title: '预约', links: [media.href] }))
    assert.equal(booking.pathname, '/contact_one')
    assert.ok(booking.searchParams.get('text').includes('tenant=shop-one'))
    setTenant({
      slug: 'shop-one', brand_name: '测试品牌', contact_name: '客服甲',
      contact_username: 'contact_one', venue_ids: [7, 10],
    })
    assert.equal(hasDeliveryAccess(), true)
    assert.equal(hasVenueAccess(), false)
    const delivery = new URL(mediaUrl('/api/delivery-media/1'))
    assert.equal(delivery.searchParams.get('tenant'), 'shop-one')
    window.location.search = ''
    initializeTenant()
    setTenant(null)
    assert.equal(tenantPath('/movies'), '/movies')
    assert.equal(contactUrl(), 'https://t.me/haotian99988866')
    assert.deepEqual(displayModules().filter(item => item.common).map(item => item.key), ['venue', 'delivery', 'movies', 'music'])
  } finally {
    delete globalThis.window
  }
})

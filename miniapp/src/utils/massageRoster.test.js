import { strict as assert } from 'node:assert'
import { test } from 'node:test'
import { massageRosterMembers, massageRosterPhoto, massageRosterServices } from './massageRoster.js'

test('massage roster uses public archive covers and leaves missing photos empty', () => {
  const members = massageRosterMembers([
    { code: '002', cover: 10, photo_count: 2 },
    { code: '011', cover: 11, photo_count: 1 },
    { code: '035', cover: null, photo_count: 0 },
  ])
  assert.deepEqual(massageRosterPhoto('002', members), { id: 10, kind: 'staff-photos', count: 2 })
  assert.deepEqual(massageRosterPhoto('011', members), { id: 11, kind: 'staff-photos', count: 1 })
  assert.equal(massageRosterPhoto('035', members), null)
  assert.equal(massageRosterPhoto('999', members), null)
  assert.equal(members.get('002').code, '002')
  assert.equal(members.has('2'), false)
})

test('massage services match the exact published technician code', () => {
  const items = [
    { id: 1, technician_codes: '002, 011', price: '29.00', duration: '30分钟' },
    { id: 2, technician_codes: '2,012', price: '99.00', duration: '面议' },
    { id: 3, technician_codes: '' },
    { id: 4, technician_codes: null },
  ]
  assert.deepEqual(massageRosterServices('002', items), [items[0]])
  assert.deepEqual(massageRosterServices('2', items), [items[1]])
  assert.deepEqual(massageRosterServices('999', items), [])
})

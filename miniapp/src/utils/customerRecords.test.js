import test from 'node:test'
import assert from 'node:assert/strict'
import { consumptionCommission, consumptionProjects } from './customerRecords.js'

test('consumption projects show only the selected venue and preserve unmatched legacy names', () => {
  const items = [
    { venue_id: 2, service_key: 'member', service_name: '1号牌·会员' },
    { venue_id: 6, service_key: '0牌', service_name: '0牌' },
  ]
  const selected = consumptionProjects(items, '2', '1号牌·会员')
  assert.deepEqual(selected.options, [items[0]])
  assert.equal(selected.selected, items[0])
  assert.equal(consumptionProjects(items, '2', '90分钟').selected, undefined)
  assert.equal(consumptionProjects(items, '6', '1号牌·会员').selected, undefined)
  assert.deepEqual(consumptionProjects(items, '', '').options, [])
})

test('commission preview uses the saved snapshot or delivery fallback without guessing retired defaults', () => {
  const items = [
    { venue_id: 2, service_name: '1号牌·会员', service_key: 'member', commission_amount: '50.00' },
    { venue_id: 7, service_name: '外送订单', service_key: 'delivery-order', commission_amount: '40.00' },
  ]
  const record = { venue_id: '2', service_kind: 'venue', service: '1号牌·会员' }
  const prior = { venue_id: 2, service: record.service, commission_service_key: 'member', commission_base_amount: '45.00' }
  assert.equal(consumptionCommission(items, record, prior), '45.00')
  assert.equal(consumptionCommission(items, record, null), '50.00')
  assert.equal(consumptionCommission(items, { ...record, revision: 1, service: '90分钟' }, prior), null)
  assert.equal(consumptionCommission(items, { ...record, service: '90分钟' }, prior), 0)
  assert.equal(consumptionCommission(items, { venue_id: '7', service_kind: 'delivery', service: '其他' }), '40.00')
  assert.equal(consumptionCommission(items, { venue_id: '10', service_kind: 'delivery', service: '其他' }), null)
})

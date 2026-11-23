import test from 'node:test'
import assert from 'node:assert/strict'
import { listingInfo, listingValue } from './serviceListing.js'

test('housing summaries use explicit fields without inventing area or monthly units', () => {
  const item = { title: '地址：TL社区', attributes: [
    { label: '地址', value: 'TL社区' }, { label: '户型', value: '#4房 # 排屋' },
    { label: '租金', value: '# 800$' },
  ] }
  assert.deepEqual(listingInfo(item), {
    title: '4房 排屋', location: 'TL社区', price: '800$', priceLabel: '租金', features: [],
  })
  assert.equal(listingInfo({ title: '特惠单间', attributes: [] }).price, '')
})

test('vehicle summaries retain source model and distinguish explicit year from age', () => {
  const item = { title: '车型：传奇', attributes: [
    { label: '车型', value: '#传奇' }, { label: '年龄', value: '2024年' },
    { label: '出租', value: '1300$ 店里 现车' },
  ] }
  const info = listingInfo(item, true)
  assert.equal(info.title, '传奇')
  assert.equal(info.price, '1300$ 店里 现车')
  assert.deepEqual(info.features, [{ label: '年份', value: '2024年' }])
  assert.equal(listingInfo({ ...item, attributes: [{ label: '年龄', value: '3年' }] }, true)
    .features[0].label, '年龄')
})

test('conflicting prices never become a guessed single price or sale-price fallback', () => {
  const item = { title: '多套介绍', attributes: [
    { label: '租金', value: '260$' }, { label: '租金', value: '280$' }, { label: '售价', value: '10000$' },
  ] }
  assert.equal(listingValue(item, '租金', '售价'), '')
  assert.equal(listingInfo(item).price, '')
})

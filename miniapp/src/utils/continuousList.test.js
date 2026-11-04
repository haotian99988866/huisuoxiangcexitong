import test from 'node:test'
import assert from 'node:assert/strict'
import { appendUnique, pullDistance } from './continuousList.js'

test('append keeps earlier batches and removes duplicates within or across incoming batches', () => {
  const first = [{ id: 1 }, { id: 2 }]
  assert.deepEqual(appendUnique(first, [{ id: 2 }, { id: 3 }, { id: 3 }], row => row.id),
    [{ id: 1 }, { id: 2 }, { id: 3 }])
  assert.deepEqual(first, [{ id: 1 }, { id: 2 }])
})

test('movie category and series name together identify a card', () => {
  const key = row => JSON.stringify([row.category, row.name])
  assert.equal(appendUnique([{ category: '电影', name: '同名' }],
    [{ category: '电视剧', name: '同名' }], key).length, 2)
})

test('pull refresh requires a downward primarily vertical gesture and caps travel', () => {
  const start = { x: 10, y: 10 }
  assert.equal(pullDistance(start, { x: 10, y: 154 }), 72)
  assert.equal(pullDistance(start, { x: 110, y: 154 }), 0)
  assert.equal(pullDistance(start, { x: 10, y: 0 }), 0)
  assert.equal(pullDistance(start, { x: 10, y: 1000 }), 110)
})

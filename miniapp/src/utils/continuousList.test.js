import test from 'node:test'
import assert from 'node:assert/strict'
import { appendUnique } from './continuousList.js'

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

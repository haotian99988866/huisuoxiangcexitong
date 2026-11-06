import test from 'node:test'
import assert from 'node:assert/strict'
import { readSubscriptionOperations } from './subscriptionOperations.js'

const payload = { kind: 'person', id: 12, enabled: true, revision: 3, request_id: 'a'.repeat(32) }
const storage = value => ({ getItem: () => value })

test('pending retries preserve exact request ids and revisions', () => {
  assert.deepEqual(readSubscriptionOperations(storage(JSON.stringify([payload])), 'key'), [payload])
  assert.deepEqual(readSubscriptionOperations(storage(null), 'key'), [])
})

test('corrupt or conflicting pending operations are never silently replayed', () => {
  for (const value of ['{}', 'null', '[null]', '{broken',
    JSON.stringify([{ ...payload, id: -1 }]),
    JSON.stringify([{ ...payload, revision: undefined }]),
    JSON.stringify([{ ...payload, enabled: 'true' }]),
    JSON.stringify([payload, payload])]) {
    assert.throws(() => readSubscriptionOperations(storage(value), 'key'))
  }
})

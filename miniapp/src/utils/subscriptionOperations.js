export function readSubscriptionOperations(storage, key) {
  const operations = JSON.parse(storage.getItem(key) || '[]')
  const kinds = new Set(['venue', 'delivery', 'news', 'staff', 'person'])
  if (!Array.isArray(operations) || operations.some(item =>
    !item || !kinds.has(item.kind) || !Number.isSafeInteger(item.id) || item.id < 0 ||
    typeof item.enabled !== 'boolean' || !/^[0-9a-f]{32}$/.test(item.request_id) ||
    (['staff', 'person'].includes(item.kind) && (!Number.isInteger(item.revision) || item.revision < 0)))) {
    throw new Error('暂存订阅操作损坏，请联系管理员核对，不要重复提交')
  }
  if (new Set(operations.map(item => item.request_id)).size !== operations.length) {
    throw new Error('暂存订阅编号重复，请联系管理员核对')
  }
  return operations
}

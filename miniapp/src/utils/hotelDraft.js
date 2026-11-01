const databaseName = 'hotel-admin-pending'

export async function hotelDraft(key, operation, value) {
  const database = await new Promise((resolve, reject) => {
    const request = indexedDB.open(databaseName, 1)
    request.onupgradeneeded = () => request.result.createObjectStore('pending')
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
    request.onblocked = () => reject(new Error('酒店暂存被其他页面占用，请关闭其他管理页后重试'))
  })
  try {
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction('pending', operation === 'get' ? 'readonly' : 'readwrite')
      const store = transaction.objectStore('pending')
      const request = operation === 'get' ? store.get(key) : operation === 'put' ? store.put(value, key) : store.delete(key)
      transaction.oncomplete = () => resolve(request.result)
      transaction.onerror = () => reject(transaction.error)
      transaction.onabort = () => reject(transaction.error || new Error('酒店暂存操作中断'))
    })
  } finally { database.close() }
}

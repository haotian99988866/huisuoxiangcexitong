import { useCallback, useEffect, useRef, useState } from 'react'
import { API_BASE_URL } from './twa'
import { tenantHeaders } from './tenant'
import { rememberModuleScroll, useModuleState } from './moduleState'
import { appendUnique } from './continuousList'

export default function usePagedCatalog(module, fields, itemField, itemKey) {
  const signature = JSON.stringify(fields)
  const [cache, setCache] = useModuleState(`${module}.catalog`, null)
  const snapshot = useRef(cache)
  snapshot.current = cache
  const request = useRef(null)
  const failed = useRef(null)
  const [operation, setOperation] = useState('')
  const busy = operation !== ''
  const [error, setError] = useState('')
  const ready = cache?.signature === signature
  const load = useCallback(async (page, replace = false) => {
    if (request.current && !replace) return
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setOperation(replace ? 'replace' : 'append')
    setError('')
    const timer = setTimeout(() => controller.abort(), 15000)
    try {
      const response = await fetch(`${API_BASE_URL}/webhook`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...tenantHeaders() },
        body: JSON.stringify({ ...JSON.parse(signature), page }), signal: controller.signal,
      })
      const result = await response.json()
      if (!response.ok) throw new Error(result.error || '列表加载失败，请重试')
      if (!Array.isArray(result[itemField]) || typeof result.has_more !== 'boolean') {
        throw new Error('列表返回格式无效，请重试')
      }
      if (result.has_more && !result[itemField].length) throw new Error('列表未返回下一批内容，请重试')
      if (request.current !== controller) return
      failed.current = null
      const previous = !replace && snapshot.current?.signature === signature
        ? snapshot.current.data[itemField] : []
      setCache({ signature, page, data: {
        ...result, [itemField]: appendUnique(previous, result[itemField], itemKey),
      } })
    } catch (failure) {
      if (request.current === controller) {
        console.error('[Continuous catalog]', failure)
        failed.current = { signature, page, replace }
        setError(failure.name === 'AbortError' ? '列表加载超时，请重试' : failure.message)
      }
    } finally {
      clearTimeout(timer)
      if (request.current === controller) { request.current = null; setOperation('') }
    }
  }, [signature, itemField, itemKey, setCache])
  useEffect(() => {
    if (snapshot.current?.signature !== signature) {
      window.scrollTo(0, 0)
      rememberModuleScroll(module)
      load(1, true)
    }
    return () => { request.current?.abort(); request.current = null }
  }, [signature, load, module])
  const refresh = () => {
    window.scrollTo(0, 0)
    rememberModuleScroll(module)
    return load(1, true)
  }
  return {
    data: ready ? cache.data : null, ready, busy, refreshing: operation === 'replace',
    loading: !ready && !error, error,
    refresh, loadMore: () => failed.current?.signature === signature
      ? failed.current.replace ? refresh() : load(failed.current.page)
      : load(ready ? cache.page + 1 : 1, !ready),
  }
}

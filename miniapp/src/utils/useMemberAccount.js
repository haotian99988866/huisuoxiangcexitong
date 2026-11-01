import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { initializeMemberSession, logoutMember, requestMember } from './membership'

export default function useMemberAccount({ withOrders = false, withCatalog = false } = {}) {
  const { hash } = useLocation()
  const [account, setAccount] = useState(null)
  const [orders, setOrders] = useState([])
  const [catalog, setCatalog] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const generation = useRef(0)
  const requestId = useRef(0)

  const reportFailure = useCallback(failure => {
    if (failure.status === 401) {
      generation.current += 1
      setAccount(null)
      setOrders([])
      setCatalog(null)
    }
    setError(failure.message)
  }, [])

  const refresh = useCallback(async () => {
    const currentGeneration = generation.current
    const id = ++requestId.current
    const [wallet, list, plans] = await Promise.all([
      requestMember('member_account'),
      withOrders ? requestMember('member_orders') : Promise.resolve(null),
      withCatalog ? requestMember('member_catalog') : Promise.resolve(null),
    ])
    if (currentGeneration !== generation.current || id !== requestId.current) return
    setAccount(wallet)
    if (list) setOrders(list.orders)
    if (plans) setCatalog(plans)
    setError('')
    return wallet
  }, [withOrders, withCatalog])

  useEffect(() => {
    let active = true
    generation.current += 1
    setLoading(true)
    setAccount(null)
    setOrders([])
    setCatalog(null)
    setError('')
    async function load() {
      try {
        if (await initializeMemberSession()) await refresh()
      } catch (failure) {
        if (active) reportFailure(failure)
      } finally {
        if (active) setLoading(false)
      }
    }
    load()
    return () => { active = false; generation.current += 1 }
  }, [hash, refresh, reportFailure])

  const needsPolling = Boolean(account?.deposits.some(item => item.status === 'pending')
    || orders.some(item => ['paid', 'delivering', 'refund_pending', 'delivery_unknown'].includes(item.status)))
  useEffect(() => {
    if (!account || !needsPolling) return undefined
    let active = true
    let polling = false
    const timer = window.setInterval(async () => {
      if (polling || document.visibilityState === 'hidden') return
      polling = true
      try { await refresh() }
      catch (failure) { if (active) reportFailure(failure) }
      finally { polling = false }
    }, 5000)
    return () => { active = false; window.clearInterval(timer) }
  }, [Boolean(account), needsPolling, refresh, reportFailure])

  const logout = async () => {
    await logoutMember()
    generation.current += 1
    setAccount(null)
    setOrders([])
    setCatalog(null)
  }
  return { account, orders, catalog, loading, error, refresh, reportFailure, logout }
}

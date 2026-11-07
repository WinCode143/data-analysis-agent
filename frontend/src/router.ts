import { useEffect, useState } from 'react'

export type Route =
  | { view: 'home' }
  | { view: 'chat'; id: string | null; nonce: number }
  | { view: 'dashboard'; id: string }
  | { view: 'data' }
  | { view: 'settings' }
  | { view: 'automations' }
  | { view: 'inbox' }
  | { view: 'sql' }

let nonce = 0

function parse(hash: string): Route {
  const [, view, id] = hash.replace(/^#/, '').split('/')
  if (view === 'dashboard' && id) return { view: 'dashboard', id }
  if (view === 'data' || view === 'metrics') return { view: 'data' }
  if (view === 'settings') return { view: 'settings' }
  if (view === 'automations') return { view: 'automations' }
  if (view === 'inbox') return { view: 'inbox' }
  if (view === 'sql') return { view: 'sql' }
  if (view === 'chat') return { view: 'chat', id: id || null, nonce: ++nonce }
  return { view: 'home' }
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(window.location.hash))
  useEffect(() => {
    const on = () => setRoute(parse(window.location.hash))
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}

export const go = (hash: string) => {
  window.location.hash = hash
}

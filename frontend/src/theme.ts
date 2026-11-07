import { useEffect, useState } from 'react'

export type ThemePref = 'system' | 'light' | 'dark'
const KEY = 'insight.theme'

function read(): ThemePref {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'light' || v === 'dark' ? v : 'system'
  } catch {
    return 'system'
  }
}

function apply(pref: ThemePref) {
  const root = document.documentElement
  if (pref === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', pref)
}

apply(read())

export function useThemePref(): [ThemePref, (p: ThemePref) => void] {
  const [pref, setPref] = useState<ThemePref>(read)
  useEffect(() => {
    apply(pref)
    try {
      localStorage.setItem(KEY, pref)
    } catch {
      /* storage unavailable */
    }
  }, [pref])
  return [pref, setPref]
}

/** Effective scheme (light/dark), reacting to the system and to the manual switch. */
export function useColorScheme(): 'light' | 'dark' {
  const compute = (): 'light' | 'dark' => {
    const forced = document.documentElement.getAttribute('data-theme')
    if (forced === 'light' || forced === 'dark') return forced
    return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  const [scheme, setScheme] = useState(compute)
  useEffect(() => {
    const on = () => setScheme(compute())
    const mq = matchMedia('(prefers-color-scheme: dark)')
    mq.addEventListener('change', on)
    const mo = new MutationObserver(on)
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => {
      mq.removeEventListener('change', on)
      mo.disconnect()
    }
  }, [])
  return scheme
}

import { useEffect, useRef, useState, type ReactNode } from 'react'

/** Dropdown menu that closes on an outside click or with Escape. */
export default function Menu({ trigger, children, align = 'right' }: {
  trigger: (open: boolean, toggle: () => void) => ReactNode
  children: (close: () => void) => ReactNode
  align?: 'left' | 'right'
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  return (
    <div className="menu-wrap" ref={ref}>
      {trigger(open, () => setOpen((v) => !v))}
      {open && <div className="menu" style={align === 'left' ? { left: 0, right: 'auto' } : undefined}>{children(() => setOpen(false))}</div>}
    </div>
  )
}

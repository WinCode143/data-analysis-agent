import { AlertTriangle, CheckCircle2, Info } from 'lucide-react'
import { useEffect, useState } from 'react'
import { onToast, type ToastKind } from '../events'

export default function Toasts() {
  const [items, setItems] = useState<{ id: number; message: string; kind: ToastKind }[]>([])
  useEffect(() => onToast(({ message, kind }) => {
    const id = Date.now() + Math.random()
    setItems((prev) => [...prev, { id, message, kind }])
    setTimeout(() => setItems((prev) => prev.filter((t) => t.id !== id)), 3500)
  }), [])
  return (
    <div className="toasts" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={'toast ' + t.kind}>
          {t.kind === 'success' ? <CheckCircle2 size={16} /> : t.kind === 'error' ? <AlertTriangle size={16} /> : <Info size={16} />}
          {t.message}
        </div>
      ))}
    </div>
  )
}

import { ArrowUp, Square } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import ModelPicker from './ModelPicker'

interface Props {
  onSend: (text: string) => void
  busy?: boolean
  onStop?: () => void
  choice?: { provider: string; model: string } | null
  onChoice?: (c: { provider: string; model: string }) => void
  placeholder?: string
  autoFocus?: boolean
}

export default function Composer({ onSend, busy, onStop, choice, onChoice, placeholder, autoFocus }: Props) {
  const [text, setText] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  }, [text])
  const send = () => {
    if (!text.trim() || busy) return
    onSend(text.trim())
    setText('')
  }
  return (
    <div className="composer-box">
      <textarea
        ref={ref}
        rows={1}
        autoFocus={autoFocus}
        placeholder={placeholder ?? 'Ask something about your data…'}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            send()
          }
        }}
      />
      <div className="composer-foot">
        <ModelPicker value={choice} onChange={onChoice} />
        {busy ? (
          <button className="btn send-btn" title="Stop" onClick={onStop}><Square size={13} fill="currentColor" /></button>
        ) : (
          <button className="btn primary send-btn" title="Send (Enter)" disabled={!text.trim()} onClick={send}><ArrowUp size={17} /></button>
        )}
      </div>
    </div>
  )
}

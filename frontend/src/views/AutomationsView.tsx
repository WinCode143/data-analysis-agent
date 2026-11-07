import { AlertTriangle, BellRing, CalendarClock, CheckCircle2, Loader2, Pencil, Play, Plus, Trash2, XCircle } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type Automation } from '../api'
import { AlertForm, ReportForm } from '../components/AutomationForms'
import Modal from '../components/Modal'
import { subscribe, toast } from '../events'

const STATUS: Record<string, { label: string; cls: string; icon: typeof CheckCircle2 }> = {
  ok: { label: 'Nothing new', cls: 'good', icon: CheckCircle2 },
  sent: { label: 'Sent', cls: 'good', icon: CheckCircle2 },
  triggered: { label: 'Triggered', cls: 'warn', icon: AlertTriangle },
  error: { label: 'Error', cls: 'bad', icon: XCircle },
}

const when = (iso?: string | null) => iso ? new Date(iso).toLocaleString('en-US', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—'

export default function AutomationsView() {
  const [items, setItems] = useState<Automation[] | null>(null)
  const [editing, setEditing] = useState<{ kind: 'report' | 'alert'; item?: Automation } | null>(null)

  const load = useCallback(() => { api.automations().then((r) => setItems(r.items)).catch((e) => toast(e.message, 'error')) }, [])
  useEffect(() => {
    load()
    const unsub = subscribe('automations', load)
    const t = setInterval(load, 10000)
    return () => { unsub(); clearInterval(t) }
  }, [load])

  const run = async (a: Automation) => {
    const r = await api.runAutomation(a.id)
    toast(r.started ? `Running “${a.name}”… the result will show up in Notifications` : r.message ?? 'Already running', 'info')
    setTimeout(load, 800)
  }
  const toggle = async (a: Automation) => { await api.patchAutomation(a.id, { enabled: !a.enabled }); load() }
  const remove = async (a: Automation) => {
    if (!confirm(`Delete “${a.name}”?`)) return
    await api.deleteAutomation(a.id)
    load()
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Automations</h1>
          <p>Reports that send themselves and alerts that watch your metrics. You can also ask for them in the chat: “let me know if…”.</p>
        </div>
        <div className="row">
          <button className="btn" onClick={() => setEditing({ kind: 'alert' })}><BellRing size={14} /> New alert</button>
          <button className="btn primary" onClick={() => setEditing({ kind: 'report' })}><CalendarClock size={14} /> Schedule report</button>
        </div>
      </div>
      <div className="page-body">
        <div className="container">
          {!items ? <div className="skeleton" style={{ height: 160 }} /> : items.length === 0 ? (
            <div className="empty card">
              <div className="empty-icon"><CalendarClock size={24} /></div>
              <h2>Nothing scheduled yet</h2>
              <p>Schedule a dashboard to go out every Monday, or create an alert that tells you when a metric changes.</p>
              <div className="row" style={{ justifyContent: 'center', marginTop: 14 }}>
                <button className="btn" onClick={() => setEditing({ kind: 'alert' })}><Plus size={14} /> New alert</button>
                <button className="btn primary" onClick={() => setEditing({ kind: 'report' })}><Plus size={14} /> Schedule report</button>
              </div>
            </div>
          ) : (
            <div className="stack" style={{ gap: 10 }}>
              {items.map((a) => {
                const st = a.running ? null : a.last_status ? STATUS[a.last_status] : null
                return (
                  <div key={a.id} className="card automation-row" style={{ opacity: a.enabled ? 1 : 0.6 }}>
                    <span className="empty-icon" style={{ width: 38, height: 38, borderRadius: 11, flexShrink: 0 }}>
                      {a.kind === 'alert' ? <BellRing size={18} /> : <CalendarClock size={18} />}
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
                        <strong>{a.name}</strong>
                        {a.running && <span className="badge accent"><Loader2 size={11} className="spin" /> Running</span>}
                        {st && <span className={`badge ${st.cls === 'good' ? 'good' : ''}`} style={st.cls === 'bad' ? { background: 'var(--critical-soft)', color: 'var(--critical)' } : st.cls === 'warn' ? { background: '#fff4dc', color: '#8a5a00' } : undefined}><st.icon size={11} /> {st.label}</span>}
                        {!a.enabled && <span className="badge">Paused</span>}
                      </div>
                      <div className="secondary" style={{ fontSize: 13 }}>{a.description}</div>
                      <div className="muted" style={{ fontSize: 12 }}>
                        Next: {a.enabled ? when(a.next_run) : '—'} · Last: {when(a.last_run)} · Channels: {a.channels.join(', ')}
                        {a.last_message && <> · {a.last_message}</>}
                      </div>
                    </div>
                    <div className="row" style={{ gap: 2 }}>
                      <button className="btn small" onClick={() => run(a)} disabled={a.running} title="Run now"><Play size={13} /> Now</button>
                      <label className="switch" title={a.enabled ? 'Pause' : 'Enable'}>
                        <input type="checkbox" checked={a.enabled} onChange={() => toggle(a)} /><span />
                      </label>
                      <button className="icon-btn" title="Edit" onClick={() => setEditing({ kind: a.kind, item: a })}><Pencil size={14} /></button>
                      <button className="icon-btn" title="Delete" onClick={() => remove(a)}><Trash2 size={14} /></button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
      {editing && (
        <Modal title={editing.item ? `Edit “${editing.item.name}”` : editing.kind === 'alert' ? 'New alert' : 'Schedule report'} onClose={() => setEditing(null)}>
          {editing.kind === 'alert'
            ? <AlertForm initial={editing.item} onDone={() => { setEditing(null); load() }} />
            : <ReportForm initial={editing.item} onDone={() => { setEditing(null); load() }} />}
        </Modal>
      )}
    </>
  )
}

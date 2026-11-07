import { AlertTriangle, BellRing, CheckCheck, CheckCircle2, ExternalLink, FileText, Info, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, type AppNotification } from '../api'
import { notifyAutomationsChanged, toast } from '../events'
import { go } from '../router'

const ICON = { info: FileText, warning: AlertTriangle, error: XCircle, success: CheckCircle2 }

export default function InboxView() {
  const [items, setItems] = useState<AppNotification[] | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const load = () => api.notifications().then((r) => { setItems(r.items); setOpenId((cur) => cur ?? r.items[0]?.id ?? null) })
  useEffect(() => { load().catch((e) => toast(e.message, 'error')) }, [])

  const open = async (n: AppNotification) => {
    setOpenId(n.id)
    if (!n.read) {
      await api.readNotifications([n.id])
      setItems((xs) => xs?.map((x) => (x.id === n.id ? { ...x, read: true } : x)) ?? null)
      notifyAutomationsChanged()
    }
  }
  const readAll = async () => { await api.readNotifications(); load(); notifyAutomationsChanged() }
  const current = items?.find((n) => n.id === openId)

  return (
    <>
      <div className="page-header">
        <div><h1>Notifications</h1><p>Results of your scheduled reports and alerts.</p></div>
        <button className="btn" onClick={readAll}><CheckCheck size={14} /> Mark all as read</button>
      </div>
      <div className="page-body">
        {!items ? <div className="skeleton" style={{ height: 200 }} /> : items.length === 0 ? (
          <div className="empty card"><div className="empty-icon"><BellRing size={24} /></div><h2>No notifications</h2>
            <p>When an alert fires or a report goes out, it shows up here.</p>
            <button className="btn primary" style={{ marginTop: 12 }} onClick={() => go('#/automations')}>Ir a Automatizaciones</button></div>
        ) : (
          <div className="inbox">
            <div className="card list-card inbox-list">
              {items.map((n) => {
                const Icon = ICON[n.level] ?? Info
                return (
                  <div key={n.id} className={'list-row inbox-item' + (n.id === openId ? ' active' : '') + (n.read ? '' : ' unread')} onClick={() => open(n)}>
                    <Icon size={16} className={`lvl-${n.level}`} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="inbox-title">{n.title}</div>
                      <div className="muted" style={{ fontSize: 12 }}>{new Date(n.created_at).toLocaleString('en-US', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</div>
                    </div>
                    {!n.read && <span className="unread-dot" />}
                  </div>
                )
              })}
            </div>
            {current && (
              <div className="card inbox-body fade-up" key={current.id}>
                <div className="msg-text"><Markdown remarkPlugins={[remarkGfm]} components={{ a: ({ href, children }) => <a href={href?.includes('#/') ? '#/' + href.split('#/')[1] : href} target={href?.includes('#/') ? undefined : '_blank'} rel="noreferrer">{children}</a> }}>{current.body}</Markdown></div>
                <div className="row" style={{ marginTop: 16, flexWrap: 'wrap', gap: 6 }}>
                  {current.link && <button className="btn small" onClick={() => go(current.link!)}><ExternalLink size={13} /> Open</button>}
                  {Object.entries(current.delivered).map(([ch, st]) => (
                    <span key={ch} className={'badge' + (st === 'ok' ? ' good' : '')} title={st}>{ch}: {st === 'ok' ? 'delivered' : 'failed'}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </>
  )
}

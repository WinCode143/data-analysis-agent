import { ArrowRight, Check, Database, LayoutDashboard, MessageSquare, Plus, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type ChartType, type ConversationSummary, type DashboardSummary, type Status } from '../api'
import Composer from '../components/Composer'
import ProviderLogo from '../components/ProviderLogo'
import { askAgent, onSettingsChanged, subscribe } from '../events'
import { go } from '../router'

/** Abstract dashboard thumbnail based on the chart types it contains. */
function Thumb({ types }: { types: ChartType[] }) {
  const cells = (types.length ? types : ['kpi', 'kpi', 'line', 'bar'] as ChartType[]).slice(0, 8)
  return (
    <div className="dash-thumb" aria-hidden>
      {cells.map((t, i) => (
        <i key={i} style={{ gridColumn: t === 'kpi' ? 'span 1' : 'span 2' }}>
          {t === 'kpi' ? <b style={{ height: 4, background: 'var(--text-muted)', flex: 'none', width: '60%' }} /> :
            [0.5, 0.8, 0.6, 1, 0.7].map((h, j) => (
              <b key={j} style={{ height: `${h * 100}%`, background: `var(--series-${(i % 3) + 1})` }} />
            ))}
        </i>
      ))}
    </div>
  )
}

function timeAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60) return 'now'
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`
  return new Date(iso).toLocaleDateString('en-US', { day: 'numeric', month: 'short' })
}

export default function HomeView() {
  const [status, setStatus] = useState<Status | null>(null)
  const [dashboards, setDashboards] = useState<DashboardSummary[]>([])
  const [convs, setConvs] = useState<ConversationSummary[]>([])

  useEffect(() => {
    const load = () => {
      api.status().then(setStatus).catch(() => {})
      api.dashboards().then(setDashboards).catch(() => {})
      api.conversations().then(setConvs).catch(() => {})
    }
    load()
    const a = subscribe('dashboards', load)
    const b = subscribe('data', load)
    const c = onSettingsChanged(load)
    return () => { a(); b(); c() }
  }, [])

  const steps = [
    { done: (status?.models ?? 0) > 0, title: 'Connect your data', text: status?.models ? `${status.models} models ready` : 'CSV, Excel or Postgres', go: () => go('#/data') },
    { done: !!status?.configured, title: 'Pick your AI', text: status?.configured ? `${status.provider_name} · ${status.model}` : 'Claude, OpenAI, Gemini, Ollama…', go: () => go('#/settings') },
    { done: dashboards.length > 0, title: 'Create a dashboard', text: dashboards.length ? `${dashboards.length} created` : 'The AI builds it for you', go: () => askAgent('Create an executive dashboard with the most relevant data and explain the main findings.') },
  ]

  return (
    <div className="page-body">
      <div className="container stack" style={{ gap: 26 }}>
        <section className="hero fade-up">
          <span className="badge accent" style={{ marginBottom: 12 }}><Sparkles size={11} /> AI data analyst</span>
          <h1>Ask your data</h1>
          <p>Connect files or databases and ask for what you need in plain language: analysis, the causes of a change, charts or a whole dashboard.</p>
          <Composer onSend={(t) => askAgent(t)} placeholder="E.g. Why did sales drop last month?" />
        </section>

        <section className="checklist">
          {steps.map((s, i) => (
            <button key={s.title} className={'card check-item' + (s.done ? ' done' : '')} onClick={s.go}>
              <span className="check-num">{s.done ? <Check size={14} /> : i + 1}</span>
              <div style={{ minWidth: 0 }}>
                <strong>{s.title}</strong>
                <span>{s.text}</span>
              </div>
              <ArrowRight size={15} className="muted" style={{ marginLeft: 'auto', alignSelf: 'center' }} />
            </button>
          ))}
        </section>

        <section>
          <div className="row" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
            <p className="section-title" style={{ margin: 0 }}><LayoutDashboard size={15} /> Dashboards</p>
            <button className="btn small" onClick={() => askAgent('Create a new dashboard. Ask me first what I want to see if it isn\'t clear.')}><Plus size={13} /> New with AI</button>
          </div>
          {dashboards.length === 0 ? (
            <div className="card empty" style={{ padding: 32 }}>
              <p style={{ margin: 0 }}>No dashboards yet. Ask the AI for one or add charts from the chat.</p>
            </div>
          ) : (
            <div className="gallery">
              {dashboards.map((d) => (
                <button key={d.id} className="card dash-card" onClick={() => go(`#/dashboard/${d.id}`)}>
                  <Thumb types={d.types} />
                  <div className="dash-card-body">
                    <strong>{d.title}</strong>
                    <span>{d.charts} charts · {timeAgo(d.updated_at)}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="checklist" style={{ gridTemplateColumns: '2fr 1fr' }}>
          <div className="card list-card">
            <p className="section-title" style={{ padding: '8px 10px 0' }}><MessageSquare size={15} /> Recent conversations</p>
            {convs.length === 0 && <p className="muted" style={{ padding: '0 10px', fontSize: 13 }}>No conversations yet.</p>}
            {convs.slice(0, 6).map((c) => (
              <div key={c.id} className="list-row" onClick={() => go(`#/chat/${c.id}`)}>
                <ProviderLogo id={c.provider} size={20} />
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.title}</span>
                <span className="muted" style={{ fontSize: 12 }}>{timeAgo(c.updated_at)}</span>
              </div>
            ))}
          </div>
          <button className="card check-item" style={{ flexDirection: 'column' }} onClick={() => go('#/data')}>
            <span className="empty-icon" style={{ width: 40, height: 40, borderRadius: 12 }}><Database size={20} /></span>
            <strong style={{ marginTop: 6 }}>Data and semantic layer</strong>
            <span>{status ? `${status.models} models · metrics and relationships the AI uses to answer precisely` : '…'}</span>
          </button>
        </section>
      </div>
    </div>
  )
}

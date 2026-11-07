import {
  AlertTriangle, ArrowRightLeft, BookmarkCheck, CalendarClock, Code2, Brain, Check, ChevronRight, Copy, CornerDownRight, LayoutDashboard, Loader2, RotateCcw, Sparkles, X,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, streamChat, type AgentEvent, type Insights } from '../api'
import ChartCard from '../components/ChartCard'
import Composer from '../components/Composer'
import DataTable from '../components/DataTable'
import { FindingCard } from '../components/InsightsPanel'
import { notifyConversationsChanged, notifyDashboardsChanged, notifyDataChanged, openInEditor, takePendingPrompt, toast } from '../events'
import { go } from '../router'

const TOOL_LABELS: Record<string, string> = {
  describe_model: 'Exploring the model',
  run_query: 'Querying data',
  create_chart: 'Creating chart',
  create_dashboard: 'Creating dashboard',
  update_dashboard: 'Updating dashboard',
  list_dashboards: 'Reviewing dashboards',
  get_chart: 'Reading chart',
  edit_semantic_layer: 'Improving the semantic layer',
  run_python: 'Running Python',
  write_dashboard_insights: 'Saving conclusions',
  query_metrics: 'Querying metrics',
  save_query: 'Saving query',
  check_data_quality: 'Checking data quality',
  preview_cleaning_step: 'Previewing cleanup',
  add_cleaning_step: 'Cleaning data',
  remove_cleaning_step: 'Undoing cleanup',
  list_saved_queries: 'Reviewing saved queries',
  define_metric: 'Defining metric',
  schedule_report: 'Scheduling report',
  create_alert: 'Creating alert',
  list_automations: 'Reviewing automations',
  semantic_layer: 'Reading the semantic layer',
}

const FOLLOWUP_RE = /\n*\**Follow-up questions:?\**:?\s*(.+)\s*$/i

type Step = { kind: 'tool'; id: string; name: string; input: Record<string, unknown>; done: boolean; ok: boolean; error?: string | null; extras: AgentEvent[] }
type Item =
  | { kind: 'user'; text: string }
  | { kind: 'text'; text: string }
  | { kind: 'thinking'; text: string }
  | Step
  | { kind: 'chart'; ev: Extract<AgentEvent, { type: 'chart' }> }
  | { kind: 'dashboard'; id: string; title: string }
  | { kind: 'error'; message: string }
  | { kind: 'notice'; message: string }
  | { kind: 'automation'; id: string; name: string; description: string; next_run?: string | null }
  | { kind: 'saved_query'; id: string; title: string }
  | { kind: 'quality'; model: string; score: number; issues: number }
  | { kind: 'cleaning'; model: string; description: string; rows_changed?: number | null }
  | { kind: 'usage'; usage: Record<string, number> }
  | { kind: 'insights'; dashboardId: string; title: string; insights: Insights }

/** Reduces the event stream to interface items. */
function reduce(items: Item[], ev: AgentEvent): Item[] {
  const last = items[items.length - 1]
  switch (ev.type) {
    case 'user':
      return [...items, { kind: 'user', text: ev.text }]
    case 'text':
      if (last?.kind === 'text') return [...items.slice(0, -1), { ...last, text: last.text + ev.delta }]
      return [...items, { kind: 'text', text: ev.delta }]
    case 'thinking':
      if (last?.kind === 'thinking') return [...items.slice(0, -1), { ...last, text: last.text + ev.delta }]
      return [...items, { kind: 'thinking', text: ev.delta }]
    case 'tool_start':
      return [...items, { kind: 'tool', id: ev.id, name: ev.name, input: ev.input, done: false, ok: true, extras: [] }]
    case 'tool_end':
      return items.map((it) => (it.kind === 'tool' && it.id === ev.id ? { ...it, done: true, ok: ev.ok, error: ev.error } : it))
    case 'query':
    case 'python':
      return items.map((it) => (it.kind === 'tool' && it.id === ev.tool_id ? { ...it, extras: [...it.extras, ev] } : it))
    case 'chart':
      return [...items, { kind: 'chart', ev }]
    case 'dashboard':
      return [...items, { kind: 'dashboard', id: ev.id, title: ev.title }]
    case 'error':
      return [...items, { kind: 'error', message: ev.message }]
    case 'notice':
      return [...items, { kind: 'notice', message: ev.message }]
    case 'saved_query':
      return [...items, { kind: 'saved_query', id: ev.id, title: ev.title }]
    case 'quality':
      return [...items, { kind: 'quality', model: ev.model, score: ev.score, issues: ev.issues }]
    case 'cleaning':
      return [...items, { kind: 'cleaning', model: ev.model, description: ev.description, rows_changed: ev.rows_changed }]
    case 'automation':
      return [...items, { kind: 'automation', id: ev.id, name: ev.name, description: ev.description, next_run: ev.next_run }]
    case 'done':
      return [...items, { kind: 'usage', usage: ev.usage }]
    case 'insights':
      return [...items, { kind: 'insights', dashboardId: ev.dashboard_id, title: ev.title, insights: ev.insights }]
    default:
      return items
  }
}

function splitFollowups(text: string): [string, string[]] {
  const m = text.match(FOLLOWUP_RE)
  if (!m) return [text, []]
  return [text.slice(0, m.index).trimEnd(), m[1].split('|').map((s) => s.trim().replace(/^[-*•]\s*/, '')).filter(Boolean).slice(0, 3)]
}

const fmtTokens = (n: number) => (n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n))

function ToolStep({ step }: { step: Step }) {
  const sql = typeof step.input.sql === 'string' ? step.input.sql : null
  const detail = step.name === 'describe_model' ? String(step.input.model ?? '') : step.name === 'create_chart' ? String(step.input.title ?? '') : step.name === 'edit_semantic_layer' ? `${step.input.action} · ${step.input.model}` : ''
  return (
    <details className="step">
      <summary>
        {!step.done ? <Loader2 size={14} className="spin" /> : step.ok ? <Check size={14} className="ok" /> : <X size={14} className="fail" />}
        <span>{TOOL_LABELS[step.name] ?? step.name}</span>
        {detail && <span className="muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>· {detail}</span>}
        <ChevronRight size={14} className="chev" />
      </summary>
      <div className="step-body">
        {sql ? <pre>{sql}</pre> : <pre>{JSON.stringify(step.input, null, 2)}</pre>}
        {sql && <div><button className="btn small" onClick={() => openInEditor(sql)}><Code2 size={13} /> Open in the SQL editor</button></div>}
        {step.error && <div className="error-box"><AlertTriangle size={14} />{step.error}</div>}
        {step.extras.map((ex, i) =>
          ex.type === 'query' ? (
            <div key={i}>
              <DataTable data={ex} maxHeight={240} limit={100} />
              <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{ex.row_count} rows{ex.truncated ? ' (truncated)' : ''}</div>
            </div>
          ) : ex.type === 'python' ? (
            <div key={i}>
              <pre>{ex.code}</pre>
              <pre>{ex.stdout || ex.stderr || '(no output)'}</pre>
            </div>
          ) : null,
        )}
      </div>
    </details>
  )
}

export default function ChatView({ conversationId }: { conversationId: string | null }) {
  const [items, setItems] = useState<Item[]>([])
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [locked, setLocked] = useState<{ provider: string; model: string } | null>(null)
  const [choice, setChoice] = useState<{ provider: string; model: string } | null>(null)
  const [suggestions, setSuggestions] = useState<string[]>([])
  const convRef = useRef<string | null>(conversationId)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)

  useEffect(() => {
    convRef.current = conversationId
    setItems([])
    setLoadError(null)
    setLocked(null)
    setChoice(null)
    if (!conversationId) {
      api.suggestions().then(setSuggestions).catch(() => {})
      return
    }
    api.conversation(conversationId)
      .then((c) => {
        setLocked({ provider: c.provider, model: c.model })
        setItems(c.ui.reduce(reduce, [] as Item[]).map((it) => (it.kind === 'tool' ? { ...it, done: true } : it)))
      })
      .catch((e) => setLoadError(e.message))
  }, [conversationId])

  useEffect(() => {
    const el = scrollRef.current
    if (el && stickRef.current) el.scrollTop = el.scrollHeight
  }, [items])

  const send = useCallback(async (text: string) => {
    const message = text.trim()
    if (!message || busy) return
    setBusy(true)
    stickRef.current = true
    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      await streamChat(message, convRef.current, (ev) => {
        if (ev.type === 'conversation') {
          setLocked({ provider: ev.provider, model: ev.model })
          setChoice(null)
          if (convRef.current !== ev.id) {
            convRef.current = ev.id
            history.replaceState(null, '', `#/chat/${ev.id}`)
            notifyConversationsChanged()
          }
          return
        }
        if (ev.type === 'dashboard') notifyDashboardsChanged()
        if (ev.type === 'semantic_updated') notifyDataChanged()
        setItems((prev) => reduce(prev, ev))
      }, ctrl.signal, choice ?? undefined)
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setItems((prev) => [...prev, { kind: 'error', message: (e as Error).message }])
    } finally {
      setBusy(false)
      abortRef.current = null
      setItems((prev) => prev.map((it) => (it.kind === 'tool' && !it.done ? { ...it, done: true } : it)))
    }
  }, [busy, choice])

  useEffect(() => {
    const p = takePendingPrompt()
    if (p) send(p)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const onScroll = () => {
    const el = scrollRef.current!
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  const lastUser = [...items].reverse().find((i) => i.kind === 'user') as { text: string } | undefined
  const lastTextIdx = items.map((i) => i.kind).lastIndexOf('text')
  const lastIsThinking = busy && items[items.length - 1]?.kind === 'thinking'

  return (
    <div className="chat">
      <div className="chat-scroll" ref={scrollRef} onScroll={onScroll}>
        <div className="chat-inner">
          {loadError && <div className="error-box"><AlertTriangle size={15} />{loadError}</div>}
          {items.length === 0 && !loadError && !conversationId && (
            <div className="empty fade-up">
              <div className="empty-icon"><Sparkles size={24} /></div>
              <h2>What do you want to know about your data?</h2>
              <p>The analyst queries the semantic layer, creates charts and builds dashboards for you.</p>
              <div className="suggestions">
                {suggestions.map((s) => (
                  <button key={s} className="suggestion" onClick={() => send(s)}><CornerDownRight size={15} />{s}</button>
                ))}
              </div>
            </div>
          )}
          {items.map((it, i) => {
            switch (it.kind) {
              case 'user':
                return <div key={i} className="msg-user">{it.text}</div>
              case 'text': {
                const isLastText = i === lastTextIdx
                const [body, followups] = splitFollowups(it.text)
                return (
                  <div key={i} className="msg-block">
                    <div className="msg-text"><Markdown remarkPlugins={[remarkGfm]}>{body}</Markdown></div>
                    <div className="msg-tools">
                      <button className="icon-btn" title="Copy" onClick={() => { navigator.clipboard.writeText(body); toast('Copied', 'success') }}><Copy size={14} /></button>
                    </div>
                    {isLastText && !busy && followups.length > 0 && (
                      <div className="followups" style={{ marginTop: 10 }}>
                        {followups.map((f) => <button key={f} className="chip-btn" onClick={() => send(f)}><CornerDownRight size={13} />{f}</button>)}
                      </div>
                    )}
                  </div>
                )
              }
              case 'thinking':
                return (
                  <details key={i} className="step" open={lastIsThinking && i === items.length - 1}>
                    <summary><Brain size={14} /> <span>Reasoning</span><ChevronRight size={14} className="chev" /></summary>
                    <div className="step-body thinking">{it.text}</div>
                  </details>
                )
              case 'tool':
                return <ToolStep key={i} step={it} />
              case 'chart':
                return <div key={i} className="fade-up"><ChartCard spec={it.ev.spec} data={it.ev.data} allowPin /></div>
              case 'dashboard':
                return (
                  <button key={i} className="card dash-link nav-item fade-up" onClick={() => go(`#/dashboard/${it.id}`)}>
                    <span className="empty-icon" style={{ width: 34, height: 34, borderRadius: 10 }}><LayoutDashboard size={17} /></span>
                    <span>Dashboard ready: <strong>{it.title}</strong></span>
                    <ChevronRight size={16} style={{ marginLeft: 'auto' }} />
                  </button>
                )
              case 'insights':
                return (
                  <div key={i} className="card insights fade-up" style={{ marginBottom: 0 }}>
                    <div className="insights-head">
                      <p className="section-title" style={{ margin: 0 }}><Sparkles size={15} /> Conclusions · {it.title}</p>
                      <button className="btn small" onClick={() => go(`#/dashboard/${it.dashboardId}`)}>View dashboard <ChevronRight size={13} /></button>
                    </div>
                    <div className="insights-summary msg-text"><Markdown>{it.insights.summary}</Markdown></div>
                    <div className="findings">{it.insights.findings.map((f, j) => <FindingCard key={j} f={f} />)}</div>
                  </div>
                )
              case 'quality':
                return (
                  <button key={i} className="card dash-link nav-item fade-up" onClick={() => go('#/data/quality')}>
                    <span className="score-pill" style={{ fontSize: 14, padding: '4px 10px', color: it.score >= 85 ? 'var(--good)' : it.score >= 60 ? '#d99a00' : 'var(--critical)' }}>{it.score}</span>
                    <span>Quality of <strong>{it.model}</strong>: {it.issues} issues found</span>
                    <ChevronRight size={16} style={{ marginLeft: 'auto' }} />
                  </button>
                )
              case 'cleaning':
                return (
                  <div key={i} className="muted row fade-up" style={{ fontSize: 12.5 }}>
                    <Check size={13} className="ok" style={{ color: 'var(--good)' }} /> {it.model}: {it.description}{it.rows_changed != null && ` · ${it.rows_changed.toLocaleString('en-US')} rows`}
                  </div>
                )
              case 'saved_query':
                return (
                  <button key={i} className="card dash-link nav-item fade-up" onClick={() => go('#/sql')}>
                    <span className="empty-icon" style={{ width: 34, height: 34, borderRadius: 10 }}><BookmarkCheck size={17} /></span>
                    <span>Query saved: <strong>{it.title}</strong> <span className="muted">· in the SQL editor</span></span>
                    <ChevronRight size={16} style={{ marginLeft: 'auto' }} />
                  </button>
                )
              case 'automation':
                return (
                  <button key={i} className="card dash-link nav-item fade-up" onClick={() => go('#/automations')}>
                    <span className="empty-icon" style={{ width: 34, height: 34, borderRadius: 10 }}><CalendarClock size={17} /></span>
                    <span style={{ minWidth: 0, whiteSpace: 'normal' }}><strong>{it.name}</strong><br /><span className="muted" style={{ fontSize: 12.5 }}>{it.description}{it.next_run && ` · next: ${new Date(it.next_run).toLocaleString('en-US', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}`}</span></span>
                    <ChevronRight size={16} style={{ marginLeft: 'auto' }} />
                  </button>
                )
              case 'notice':
                return <div key={i} className="muted row" style={{ fontSize: 12.5, justifyContent: 'center' }}><ArrowRightLeft size={13} /> {it.message}</div>
              case 'error':
                return (
                  <div key={i} className="error-box">
                    <AlertTriangle size={15} /><span>{it.message}{/key|model|URL/i.test(it.message) && <> · <a href="#/settings">Open settings</a></>}</span>
                    {!busy && lastUser && i === items.length - 1 && (
                      <button className="btn small" onClick={() => send(lastUser.text)}><RotateCcw size={13} /> Retry</button>
                    )}
                  </div>
                )
              case 'usage':
                return (it.usage.input_tokens || it.usage.output_tokens) ? (
                  <div key={i} className="usage" title="Tokens for this turn">
                    <span>↑ {fmtTokens(it.usage.input_tokens ?? 0)}</span><span>↓ {fmtTokens(it.usage.output_tokens ?? 0)} tokens</span>
                  </div>
                ) : null
            }
          })}
          {busy && !lastIsThinking && items[items.length - 1]?.kind !== 'tool' && (
            <div className="muted row"><Loader2 size={14} className="spin" /> Analyzing…</div>
          )}
        </div>
      </div>
      <div className="composer">
        <Composer onSend={send} busy={busy} onStop={() => abortRef.current?.abort()} choice={choice ?? locked} onChoice={setChoice} autoFocus />
      </div>
    </div>
  )
}

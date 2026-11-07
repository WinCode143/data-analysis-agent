import { AlertTriangle, BellRing, CheckCircle2, Loader2, Mail, MessageSquare, Save, Send, Webhook } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type Automation, type Channel, type DashboardSummary, type Frequency, type Mdl, type Metric } from '../api'
import { notifyAutomationsChanged, toast } from '../events'
import { pctChange, valueFormatter } from '../format'
import { go } from '../router'

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
export const PERIOD_OPTIONS: [string, string][] = [
  ['last_7_days', 'Last 7 days'], ['last_30_days', 'Last 30 days'], ['last_90_days', 'Last 90 days'],
  ['last_12_months', 'Last 12 months'], ['mtd', 'Month to date'], ['qtd', 'Quarter to date'], ['ytd', 'Year to date'],
  ['last_month', 'Last full month'], ['last_quarter', 'Last full quarter'], ['last_year', 'Last full year'],
]
const CHANNELS: { id: Channel; label: string; icon: typeof Mail }[] = [
  { id: 'app', label: 'In-app inbox', icon: BellRing },
  { id: 'email', label: 'Email', icon: Mail },
  { id: 'slack', label: 'Slack', icon: MessageSquare },
  { id: 'webhook', label: 'Webhook', icon: Webhook },
]

function FrequencyPicker({ value, onChange, allowHourly }: { value: Frequency; onChange: (f: Frequency) => void; allowHourly?: boolean }) {
  const opts: [Frequency['every'], string][] = [...(allowHourly ? [['hour', 'Every hour'] as [Frequency['every'], string]] : []),
    ['day', 'Every day'], ['week', 'Every week'], ['month', 'Every month']]
  return (
    <div>
      <label className="label">Frequency</label>
      <div className="row" style={{ flexWrap: 'wrap' }}>
        <select className="select" style={{ width: 'auto' }} value={value.every} onChange={(e) => onChange({ ...value, every: e.target.value as Frequency['every'] })}>
          {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        {value.every === 'week' && (
          <select className="select" style={{ width: 'auto' }} value={value.weekday ?? 0} onChange={(e) => onChange({ ...value, weekday: Number(e.target.value) })}>
            {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
          </select>
        )}
        {value.every === 'month' && (
          <select className="select" style={{ width: 'auto' }} value={value.day ?? 1} onChange={(e) => onChange({ ...value, day: Number(e.target.value) })}>
            {Array.from({ length: 28 }, (_, i) => <option key={i} value={i + 1}>day {i + 1}</option>)}
          </select>
        )}
        {value.every === 'hour' ? (
          <span className="secondary" style={{ fontSize: 13 }}>at minute
            <input className="input" style={{ width: 64, marginLeft: 6 }} value={value.at.slice(3)} onChange={(e) => onChange({ ...value, at: '00:' + e.target.value.replace(/\D/g, '').slice(0, 2).padStart(2, '0') })} />
          </span>
        ) : (
          <input className="input" type="time" style={{ width: 120 }} value={value.at} onChange={(e) => onChange({ ...value, at: e.target.value })} />
        )}
      </div>
    </div>
  )
}

function ChannelPicker({ channels, recipients, available, onChange }: {
  channels: Channel[]; recipients: string[]; available: Channel[]
  onChange: (c: Channel[], r: string[]) => void
}) {
  const toggle = (c: Channel) => onChange(channels.includes(c) ? channels.filter((x) => x !== c) : [...channels, c], recipients)
  return (
    <div>
      <label className="label">Send to</label>
      <div className="row" style={{ flexWrap: 'wrap' }}>
        {CHANNELS.map(({ id, label, icon: Icon }) => {
          const off = !available.includes(id)
          return (
            <button key={id} type="button" disabled={id === 'app'} className={'chip-btn' + (channels.includes(id) || id === 'app' ? ' on' : '')}
              onClick={() => toggle(id)} title={off ? 'Not configured: Settings → Notifications' : undefined} style={off ? { opacity: 0.55 } : undefined}>
              <Icon size={13} /> {label}{off && ' · not configured'}
            </button>
          )
        })}
      </div>
      {channels.includes('email') && (
        <input className="input" style={{ marginTop: 8 }} placeholder="name1@company.com, name2@company.com" value={recipients.join(', ')}
          onChange={(e) => onChange(channels, e.target.value.split(',').map((x) => x.trim()).filter(Boolean))} />
      )}
      {channels.some((c) => c !== 'app' && !available.includes(c)) && (
        <p className="hint"><a href="#/settings" onClick={() => go('#/settings')}>Set up the channels in Settings → Notifications</a>. Until then, it goes to the in-app inbox.</p>
      )}
    </div>
  )
}

const DEFAULT_FREQ: Frequency = { every: 'week', at: '09:00', weekday: 0, day: 1 }

export function ReportForm({ initial, dashboardId, onDone }: { initial?: Automation; dashboardId?: string; onDone: () => void }) {
  const [dashboards, setDashboards] = useState<DashboardSummary[]>([])
  const [available, setAvailable] = useState<Channel[]>(['app'])
  const [a, setA] = useState<Partial<Automation>>(initial ?? {
    kind: 'report', name: '', dashboard_id: dashboardId, frequency: DEFAULT_FREQ, channels: ['app'], recipients: [], refresh_insights: true,
  })
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    api.dashboards().then((d) => {
      setDashboards(d)
      if (!a.dashboard_id && d[0]) setA((x) => ({ ...x, dashboard_id: d[0].id }))
      const title = d.find((x) => x.id === (a.dashboard_id ?? d[0]?.id))?.title
      if (!a.name && title) setA((x) => ({ ...x, name: `Weekly report · ${title}` }))
    })
    api.automations().then((r) => setAvailable(r.available_channels))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const save = async () => {
    setBusy(true)
    try {
      if (initial) await api.patchAutomation(initial.id, a)
      else await api.createAutomation(a)
      toast(initial ? 'Report updated' : 'Report scheduled', 'success')
      notifyAutomationsChanged()
      onDone()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="stack" style={{ gap: 14, maxWidth: 620 }}>
      <div><label className="label">Dashboard</label>
        <select className="select" value={a.dashboard_id ?? ''} onChange={(e) => setA({ ...a, dashboard_id: e.target.value })}>
          {dashboards.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
        </select>
      </div>
      <div><label className="label">Name</label><input className="input" value={a.name ?? ''} onChange={(e) => setA({ ...a, name: e.target.value })} /></div>
      <FrequencyPicker value={a.frequency ?? DEFAULT_FREQ} onChange={(frequency) => setA({ ...a, frequency })} />
      <label className="row" style={{ fontSize: 13, cursor: 'pointer' }}>
        <input type="checkbox" checked={a.refresh_insights ?? true} onChange={(e) => setA({ ...a, refresh_insights: e.target.checked })} />
        Regenerate the conclusions with the AI before each send (uses your AI provider)
      </label>
      <ChannelPicker channels={a.channels ?? ['app']} recipients={a.recipients ?? []} available={available}
        onChange={(channels, recipients) => setA({ ...a, channels, recipients })} />
      <div className="row" style={{ justifyContent: 'flex-end' }}>
        <button className="btn primary" disabled={busy || !a.name || !a.dashboard_id} onClick={save}>
          {busy ? <Loader2 size={14} className="spin" /> : <Save size={14} />} {initial ? 'Save' : 'Schedule report'}
        </button>
      </div>
    </div>
  )
}

function AlertFilter({ metric, value, onChange }: { metric?: Metric; value: Automation['filters']; onChange: (f: Automation['filters']) => void }) {
  const [mdl, setMdl] = useState<Mdl | null>(null)
  const [options, setOptions] = useState<string[]>([])
  const current = value[0]
  useEffect(() => { api.semantic().then(setMdl).catch(() => {}) }, [])
  useEffect(() => {
    if (!metric || !current?.column) { setOptions([]); return }
    api.columnValues(metric.model, current.column).then((v) => setOptions(v.map(String))).catch(() => setOptions([]))
  }, [metric, current?.column])
  const columns = mdl?.models.find((m) => m.name === metric?.model)?.columns
    .filter((c) => !c.relationship && /^(varchar|text|char|string)/.test(c.type)).map((c) => c.name) ?? []
  if (!columns.length) return null
  const toggle = (v: string) => {
    const vals = current.values.includes(v) ? current.values.filter((x) => x !== v) : [...current.values, v]
    onChange(vals.length ? [{ column: current.column, values: vals }] : [{ column: current.column, values: [] }])
  }
  return (
    <div>
      <label className="label">Only for (optional)</label>
      <div className="row" style={{ flexWrap: 'wrap' }}>
        <select className="select" style={{ width: 'auto' }} value={current?.column ?? ''}
          onChange={(e) => onChange(e.target.value ? [{ column: e.target.value, values: [] }] : [])}>
          <option value="">All data</option>
          {columns.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {current && options.slice(0, 30).map((o) => (
          <button key={o} type="button" className={'chip-btn' + (current.values.includes(o) ? ' on' : '')} onClick={() => toggle(o)}>{o}</button>
        ))}
      </div>
    </div>
  )
}

export function AlertForm({ initial, onDone }: { initial?: Automation; onDone: () => void }) {
  const [metrics, setMetrics] = useState<Metric[]>([])
  const [available, setAvailable] = useState<Channel[]>(['app'])
  const [a, setA] = useState<Partial<Automation>>(initial ?? {
    kind: 'alert', name: '', metric: '', period: 'last_7_days', compare: 'previous_period', explain: true,
    condition: { type: 'change_pct', op: '<', threshold: -15 }, frequency: { every: 'day', at: '08:00' }, channels: ['app'], recipients: [], filters: [],
  })
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof api.previewAlert>> | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const metric = metrics.find((m) => m.name === a.metric)

  useEffect(() => {
    api.metrics().then((m) => {
      setMetrics(m)
      if (!a.metric && m[0]) setA((x) => ({ ...x, metric: m[0].name, name: x.name || `${m[0].label} drop` }))
    })
    api.automations().then((r) => setAvailable(r.available_channels))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!a.metric || !a.condition) return
    const t = setTimeout(() => {
      api.previewAlert({ ...a, filters: (a.filters ?? []).filter((f) => f.values.length) }).then((p) => { setPreview(p); setPreviewError(null) }).catch((e) => { setPreview(null); setPreviewError(e.message) })
    }, 350)
    return () => clearTimeout(t)
  }, [a])

  const save = async () => {
    setBusy(true)
    try {
      const clean = { ...a, filters: (a.filters ?? []).filter((f) => f.values.length) }
      if (initial) await api.patchAutomation(initial.id, clean)
      else await api.createAutomation(clean)
      toast(initial ? 'Alert updated' : 'Alert created', 'success')
      notifyAutomationsChanged()
      onDone()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }

  if (metrics.length === 0) {
    return <div className="notice">Alerts watch <strong>official metrics</strong>. Define one in <a href="#/data">Data → Metrics</a> or ask the AI (“define the average order value metric”).</div>
  }
  const cond = a.condition!
  const fmt = metric ? valueFormatter({ value_format: metric.format, currency: metric.currency }) : (v: unknown) => String(v)
  return (
    <div className="stack" style={{ gap: 14, maxWidth: 620 }}>
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <div style={{ flex: 1 }}><label className="label">Metric</label>
          <select className="select" value={a.metric ?? ''} onChange={(e) => setA({ ...a, metric: e.target.value })}>
            {metrics.map((m) => <option key={m.name} value={m.name}>{m.label}{m.certified ? ' ✔' : ''}</option>)}
          </select>
        </div>
        <div style={{ flex: 1 }}><label className="label">Period evaluated</label>
          <select className="select" value={a.period} onChange={(e) => setA({ ...a, period: e.target.value })}>
            {PERIOD_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
      </div>
      <AlertFilter metric={metric} value={a.filters ?? []} onChange={(filters) => setA({ ...a, filters })} />
      <div>
        <label className="label">Condition</label>
        <div className="row" style={{ flexWrap: 'wrap' }}>
          <select className="select" style={{ width: 'auto' }} value={cond.type} onChange={(e) => setA({ ...a, condition: { ...cond, type: e.target.value as 'change_pct' | 'value' } })}>
            <option value="change_pct">Change (%) against</option>
            <option value="value">Absolute value</option>
          </select>
          {cond.type === 'change_pct' && (
            <select className="select" style={{ width: 'auto' }} value={a.compare ?? 'previous_period'} onChange={(e) => setA({ ...a, compare: e.target.value as Automation['compare'] })}>
              <option value="previous_period">the previous period</option>
              <option value="previous_year">the same period last year</option>
            </select>
          )}
          <select className="select" style={{ width: 'auto' }} value={cond.op} onChange={(e) => setA({ ...a, condition: { ...cond, op: e.target.value as '<' | '>' } })}>
            <option value="<">less than</option>
            <option value=">">greater than</option>
          </select>
          <input className="input" type="number" style={{ width: 110 }} value={cond.threshold} onChange={(e) => setA({ ...a, condition: { ...cond, threshold: Number(e.target.value) } })} />
          {cond.type === 'change_pct' && <span className="secondary">%</span>}
        </div>
        <p className="hint">{cond.type === 'change_pct' ? 'E.g. “less than −15 %” notifies you if it falls more than 15 %.' : 'E.g. “less than 1000” notifies you if it drops below 1000.'}</p>
      </div>
      {preview ? (
        <div className={'notice row'} style={{ gap: 10, alignItems: 'flex-start' }}>
          {preview.triggered ? <AlertTriangle size={16} color="var(--critical)" /> : <CheckCircle2 size={16} color="var(--good)" />}
          <div>
            <strong>{preview.triggered ? 'It would fire today.' : 'It wouldn\'t fire today.'}</strong>{' '}
            {metric?.label}: {fmt(preview.value)} ({preview.range[0]} → {preview.range[1]})
            {preview.previous != null && <> against {fmt(preview.previous)}{preview.var != null && ` (${pctChange(preview.var)})`}</>}.
          </div>
        </div>
      ) : previewError && <div className="error-box">{previewError}</div>}
      <div><label className="label">Name</label><input className="input" aria-label="Alert name" value={a.name ?? ''} onChange={(e) => setA({ ...a, name: e.target.value })} /></div>
      <FrequencyPicker value={a.frequency ?? { every: 'day', at: '08:00' }} onChange={(frequency) => setA({ ...a, frequency })} allowHourly />
      <label className="row" style={{ fontSize: 13, cursor: 'pointer' }}>
        <input type="checkbox" checked={a.explain ?? true} onChange={(e) => setA({ ...a, explain: e.target.checked })} />
        When it fires, have the AI investigate the cause and include it in the notification
      </label>
      <ChannelPicker channels={a.channels ?? ['app']} recipients={a.recipients ?? []} available={available}
        onChange={(channels, recipients) => setA({ ...a, channels, recipients })} />
      <div className="row" style={{ justifyContent: 'flex-end' }}>
        <button className="btn primary" disabled={busy || !a.name || !a.metric} onClick={save}>
          {busy ? <Loader2 size={14} className="spin" /> : <Send size={14} />} {initial ? 'Save' : 'Create alert'}
        </button>
      </div>
    </div>
  )
}

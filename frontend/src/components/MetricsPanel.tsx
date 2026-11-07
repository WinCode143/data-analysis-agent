import { ArrowDownRight, ArrowUpRight, BadgeCheck, Loader2, Plus, Save, Sparkles, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type Mdl, type Metric, type ValueFormat } from '../api'
import { askAgent, notifyDataChanged, subscribe, toast } from '../events'
import { pctChange, valueFormatter } from '../format'
import Modal from './Modal'

const slug = (s: string) => s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').replace(/^(\d)/, 'm_$1')

function MetricForm({ initial, mdl, onDone }: { initial?: Metric; mdl: Mdl; onDone: () => void }) {
  const [m, setM] = useState<Partial<Metric>>(initial ?? { label: '', name: '', model: mdl.models[0]?.name, expression: '', format: 'number', higher_is_better: true, certified: false, description: '' })
  const [busy, setBusy] = useState(false)
  const model = mdl.models.find((x) => x.name === m.model)
  const save = async () => {
    setBusy(true)
    try {
      await api.saveMetric({ ...m, name: m.name || slug(m.label ?? '') } as Metric)
      toast('Metric saved', 'success')
      notifyDataChanged()
      onDone()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  const remove = async () => {
    if (!initial || !confirm(`Delete the metric “${initial.label}”?`)) return
    await api.deleteMetric(initial.name)
    notifyDataChanged()
    onDone()
  }
  return (
    <div className="stack" style={{ gap: 12, maxWidth: 640 }}>
      <div className="row">
        <div style={{ flex: 2 }}><label className="label">Name</label><input className="input" value={m.label ?? ''} placeholder="Average order value"
          onChange={(e) => setM({ ...m, label: e.target.value, name: initial ? m.name : slug(e.target.value) })} /></div>
        <div style={{ flex: 1.4 }}><label className="label">Identifier</label><input className="input" value={m.name ?? ''} disabled={!!initial} onChange={(e) => setM({ ...m, name: slug(e.target.value) })} /></div>
      </div>
      <div><label className="label">Model</label>
        <select className="select" value={m.model} onChange={(e) => setM({ ...m, model: e.target.value })}>
          {mdl.models.map((x) => <option key={x.name}>{x.name}</option>)}
        </select>
      </div>
      <div>
        <label className="label">Formula (aggregate)</label>
        <input className="input" style={{ fontFamily: 'var(--mono)' }} value={m.expression ?? ''} placeholder="sum(net_revenue) / count(DISTINCT order_id)"
          onChange={(e) => setM({ ...m, expression: e.target.value })} />
        <p className="hint">Use aggregate functions: sum, count, avg, count(DISTINCT …), quantile_cont(x, 0.9)… For ratios, divide sums: <code>sum(a) / nullif(sum(b), 0)</code>.
          {model && <> Columns: {model.columns.filter((c) => !c.relationship).map((c) => c.name).join(', ')}.</>}</p>
      </div>
      <div className="row" style={{ flexWrap: 'wrap' }}>
        <div><label className="label">Format</label>
          <select className="select" style={{ width: 'auto' }} value={m.format} onChange={(e) => setM({ ...m, format: e.target.value as ValueFormat })}>
            <option value="number">Number</option><option value="integer">Integer</option><option value="currency">Currency</option><option value="percent">Percent (0-1)</option>
          </select>
        </div>
        {m.format === 'currency' && <div><label className="label">Currency</label><input className="input" style={{ width: 90 }} value={m.currency ?? 'USD'} onChange={(e) => setM({ ...m, currency: e.target.value.toUpperCase() })} /></div>}
        <div><label className="label">Reading</label>
          <select className="select" style={{ width: 'auto' }} value={m.higher_is_better ? 'up' : 'down'} onChange={(e) => setM({ ...m, higher_is_better: e.target.value === 'up' })}>
            <option value="up">Higher is better</option><option value="down">Lower is better</option>
          </select>
        </div>
      </div>
      <div><label className="label">Description</label><input className="input" value={m.description ?? ''} onChange={(e) => setM({ ...m, description: e.target.value })} placeholder="What it measures and how it's used" /></div>
      <label className="row" style={{ fontSize: 13, cursor: 'pointer' }}>
        <input type="checkbox" checked={m.certified ?? false} onChange={(e) => setM({ ...m, certified: e.target.checked })} />
        <BadgeCheck size={15} color="var(--accent)" /> Certified: reviewed, official definition
      </label>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        {initial ? <button className="btn danger" onClick={remove}><Trash2 size={14} /> Delete</button> : <span />}
        <button className="btn primary" disabled={busy || !m.label || !m.expression || !m.model} onClick={save}>
          {busy ? <Loader2 size={14} className="spin" /> : <Save size={14} />} Save (validated against the data)
        </button>
      </div>
    </div>
  )
}

function MetricCard({ m, onClick }: { m: Metric; onClick: () => void }) {
  const [v, setV] = useState<{ cur: number | null; prev: number | null; var: number | null; label: string } | null>(null)
  useEffect(() => {
    api.queryMetrics({ metrics: [m.name], period: 'ytd', compare: 'previous_year' })
      .then((r) => setV({ cur: r.rows[0]?.[0] as number, prev: r.rows[0]?.[1] as number, var: r.rows[0]?.[2] as number, label: r.range ? `${r.range[0]} → ${r.range[1]}` : '' }))
      .catch(() => api.queryMetrics({ metrics: [m.name] }).then((r) => setV({ cur: r.rows[0]?.[0] as number, prev: null, var: null, label: 'all time' })).catch(() => {}))
  }, [m])
  const fmt = valueFormatter({ value_format: m.format, currency: m.currency }, true)
  const good = v?.var != null && (v.var >= 0) === m.higher_is_better
  return (
    <button className="card metric-card" onClick={onClick}>
      <div className="row" style={{ gap: 6 }}>
        <strong style={{ fontSize: 13.5 }}>{m.label}</strong>
        {m.certified && <BadgeCheck size={15} color="var(--accent)" aria-label="Certified" />}
        <span className="pill" style={{ marginLeft: 'auto' }}>{m.model}</span>
      </div>
      <div className="metric-value">{v ? fmt(v.cur) : <span className="skeleton" style={{ display: 'inline-block', width: 90, height: 24 }} />}</div>
      {v?.var != null && (
        <div className="kpi-delta" style={{ marginTop: 0, color: good ? 'var(--good)' : 'var(--critical)' }}>
          {v.var >= 0 ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />} {pctChange(v.var)} <span className="muted" style={{ fontWeight: 400 }}>vs. previous year</span>
        </div>
      )}
      <div className="muted" style={{ fontSize: 11.5 }}>Year to date {v?.label && `· ${v.label}`}</div>
      <code style={{ fontSize: 11, color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.expression}</code>
    </button>
  )
}

export default function MetricsPanel({ mdl }: { mdl: Mdl }) {
  const [metrics, setMetrics] = useState<Metric[] | null>(null)
  const [editing, setEditing] = useState<{ item?: Metric } | null>(null)
  const load = useCallback(() => { api.metrics().then(setMetrics).catch(() => {}) }, [])
  useEffect(() => { load(); return subscribe('data', load) }, [load])
  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
        <p className="hint" style={{ margin: 0, maxWidth: 640 }}>Official metrics are defined once, and the AI, dashboards and alerts always compute them the same way. They support periods (year to date, last 12 months…) and comparisons year over year or against the previous period.</p>
        <div className="row">
          <button className="btn" onClick={() => askAgent('Suggest and define with define_metric the most useful business metrics for my data (ratios, averages per order, rates, times). Explain each one in one line.')}><Sparkles size={14} /> Suggest with AI</button>
          <button className="btn primary" onClick={() => setEditing({})}><Plus size={14} /> New metric</button>
        </div>
      </div>
      {!metrics ? <div className="skeleton" style={{ height: 120 }} /> : metrics.length === 0 ? (
        <div className="empty card" style={{ padding: 32 }}><p style={{ margin: 0 }}>No official metrics yet.</p></div>
      ) : (
        <div className="metric-grid">{metrics.map((m) => <MetricCard key={m.name + m.updated_at} m={m} onClick={() => setEditing({ item: m })} />)}</div>
      )}
      {editing && (
        <Modal title={editing.item ? `Metric: ${editing.item.label}` : 'New metric'} onClose={() => setEditing(null)}>
          <MetricForm initial={editing.item} mdl={mdl} onDone={() => { setEditing(null); load() }} />
        </Modal>
      )}
    </div>
  )
}

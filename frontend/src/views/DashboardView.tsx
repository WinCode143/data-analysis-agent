import { AlertTriangle, CalendarClock, CalendarDays, FileSpreadsheet, MousePointerClick, Type, Check, ListFilter, Pencil, Plus, Printer, RefreshCw, Sparkles, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, streamChat, type ChartWithData, type Dashboard, type DashboardFilters, type FilterOptions } from '../api'
import InsightsPanel from '../components/InsightsPanel'
import { ReportForm } from '../components/AutomationForms'
import Modal from '../components/Modal'
import ChartCard from '../components/ChartCard'
import Menu from '../components/Menu'
import { askAgent, notifyConversationsChanged, notifyDashboardsChanged, toast } from '../events'
import { go } from '../router'

const PROGRESS: Record<string, string> = {
  list_dashboards: 'Reading the dashboard charts…', run_query: 'Querying data', describe_model: 'Exploring',
  write_dashboard_insights: 'Writing conclusions…', create_chart: 'Creating a supporting chart',
}

function describeFilters(f: DashboardFilters): string {
  const parts: string[] = []
  if (f.date_from || f.date_to) parts.push(`dates ${f.date_from ?? 'start'} → ${f.date_to ?? 'today'}`)
  for (const d of f.dimensions) if (d.values.length) parts.push(`${d.column} ∈ {${d.values.join(', ')}}`)
  return parts.join('; ')
}

const iso = (d: Date) => d.toISOString().slice(0, 10)
const daysAgo = (n: number) => { const d = new Date(); d.setDate(d.getDate() - n); return iso(d) }
const PRESETS: { id: string; label: string; range: () => [string | null, string | null] }[] = [
  { id: 'all', label: 'All', range: () => [null, null] },
  { id: '30d', label: '30 days', range: () => [daysAgo(30), null] },
  { id: '90d', label: '90 days', range: () => [daysAgo(90), null] },
  { id: '12m', label: '12 months', range: () => [daysAgo(365), null] },
  { id: 'ytd', label: 'This year', range: () => [`${new Date().getFullYear()}-01-01`, null] },
]

function DimensionPicker({ options, onAdd }: { options: FilterOptions; onAdd: (column: string, values: string[]) => void }) {
  const [column, setColumn] = useState<string | null>(null)
  const [values, setValues] = useState<string[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [q, setQ] = useState('')
  const pick = async (col: string, model: string) => {
    setColumn(col)
    setSelected(new Set())
    setQ('')
    try {
      setValues((await api.columnValues(model, col)).map(String))
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  return (
    <Menu align="left" trigger={(_, toggle) => (
      <button className="chip-btn" onClick={() => { setColumn(null); toggle() }}><Plus size={13} /> Filter</button>
    )}>
      {(close) => column === null ? (
        <>
          <div className="menu-label">Filter by</div>
          {options.dimensions.length === 0 && <div className="menu-label">No text dimensions</div>}
          {options.dimensions.map((d) => (
            <button key={d.column} onClick={() => pick(d.column, d.models[0])}><ListFilter size={14} /> {d.column}</button>
          ))}
        </>
      ) : (
        <div style={{ width: 260 }}>
          <div className="menu-label">{column}</div>
          <div style={{ padding: '2px 6px 6px' }}><input className="input" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} autoFocus /></div>
          <div className="values-list">
            {values.filter((v) => v.toLowerCase().includes(q.toLowerCase())).map((v) => (
              <label key={v}>
                <input type="checkbox" checked={selected.has(v)} onChange={() => setSelected((s) => { const n = new Set(s); if (n.has(v)) n.delete(v); else n.add(v); return n })} />
                {v}
              </label>
            ))}
          </div>
          <hr />
          <div className="row" style={{ padding: 4, justifyContent: 'flex-end' }}>
            <button className="btn small primary" style={{ width: 'auto', color: 'var(--accent-ink)' }} disabled={!selected.size}
              onClick={() => { onAdd(column, [...selected]); close() }}>Apply</button>
          </div>
        </div>
      )}
    </Menu>
  )
}

function FilterBar({ options, filters, onChange }: { options: FilterOptions; filters: DashboardFilters; onChange: (f: DashboardFilters) => void }) {
  // The active preset is derived from the filters, so a range that comes from a chart click shows as “custom”.
  const matched = PRESETS.find((p) => { const [f, t] = p.range(); return (f ?? null) === (filters.date_from ?? null) && (t ?? null) === (filters.date_to ?? null) })
  const preset = matched?.id ?? 'custom'
  const [custom, setCustom] = useState<[string, string]>(['', ''])
  if (!options.has_time && options.dimensions.length === 0) return null
  return (
    <div className="filterbar no-print">
      {options.has_time && (
        <>
          <CalendarDays size={15} className="muted" />
          {PRESETS.map((p) => (
            <button key={p.id} className={'chip-btn' + (preset === p.id ? ' on' : '')} onClick={() => {
              const [from, to] = p.range()
              onChange({ ...filters, date_from: from, date_to: to })
            }}>{p.label}</button>
          ))}
          <Menu align="left" trigger={(_, toggle) => (
            <button className={'chip-btn' + (preset === 'custom' ? ' on' : '')} onClick={toggle}>
              {preset === 'custom' ? `${filters.date_from ?? '…'} → ${filters.date_to ?? '…'}` : 'Custom'}
            </button>
          )}>
            {(close) => (
              <div style={{ padding: 8, display: 'flex', flexDirection: 'column', gap: 8, width: 240 }}>
                <label className="label">From<input type="date" className="input" value={custom[0]} onChange={(e) => setCustom([e.target.value, custom[1]])} /></label>
                <label className="label">To<input type="date" className="input" value={custom[1]} onChange={(e) => setCustom([custom[0], e.target.value])} /></label>
                <button className="btn small primary" style={{ color: 'var(--accent-ink)' }} onClick={() => {
                  onChange({ ...filters, date_from: custom[0] || null, date_to: custom[1] || null })
                  close()
                }}><Check size={13} /> Apply</button>
              </div>
            )}
          </Menu>
          <span style={{ width: 1, height: 20, background: 'var(--border-strong)', margin: '0 4px' }} />
        </>
      )}
      {filters.dimensions.map((d) => (
        <span key={d.column} className="chip-btn on">
          {d.column}: {d.values.length > 2 ? `${d.values.slice(0, 2).join(', ')} +${d.values.length - 2}` : d.values.join(', ')}
          <button className="icon-btn" style={{ padding: 0 }} aria-label="Remove filter"
            onClick={() => onChange({ ...filters, dimensions: filters.dimensions.filter((x) => x.column !== d.column) })}><X size={13} /></button>
        </span>
      ))}
      <DimensionPicker options={options} onAdd={(column, values) =>
        onChange({ ...filters, dimensions: [...filters.dimensions.filter((x) => x.column !== column), { column, values }] })} />
    </div>
  )
}

export default function DashboardView({ id }: { id: string }) {
  const [dash, setDash] = useState<Dashboard | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [editingTitle, setEditingTitle] = useState<string | null>(null)
  const [filters, setFilters] = useState<DashboardFilters>({ dimensions: [] })
  const [dragId, setDragId] = useState<string | null>(null)
  const [overId, setOverId] = useState<string | null>(null)
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null)
  const [armedId, setArmedId] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)
  const [progress, setProgress] = useState('')
  const [scheduling, setScheduling] = useState(false)

  const drill = async (chartId: string, column: string | null | undefined, value: unknown, seriesCol?: string | null, seriesValue?: string) => {
    if (!dash || !column) return
    try {
      const r = await api.drill(dash.id, chartId, column, value)
      let next: DashboardFilters = { ...filters, dimensions: [...filters.dimensions] }
      const addDim = (col: string, v: string) => {
        const cur = next.dimensions.find((d) => d.column === col)
        next.dimensions = cur
          ? next.dimensions.map((d) => (d.column === col ? { column: col, values: d.values.includes(v) ? d.values : [...d.values, v] } : d))
          : [...next.dimensions, { column: col, values: [v] }]
      }
      if (r.kind === 'date') next = { ...next, date_from: r.date_from, date_to: r.date_to }
      else addDim(r.column, r.value)
      if (seriesCol && seriesValue) {
        const rs = await api.drill(dash.id, chartId, seriesCol, seriesValue).catch(() => null)
        if (rs?.kind === 'dimension') addDim(rs.column, rs.value)
      }
      changeFilters(next)
      toast(r.kind === 'date' ? `Filtered: ${r.date_from} → ${r.date_to}` : `Filtered: ${r.column} = ${r.value}`, 'info')
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const addText = async () => {
    if (!dash) return
    await api.addTextBlock(dash.id, '## New section\nDouble-click to edit this text.')
    load(filters)
  }

  const load = useCallback((f: DashboardFilters) => {
    setLoading(true)
    setError(null)
    api.renderDashboard(id, f)
      .then((d) => { setDash(d); setRefreshedAt(new Date()) })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [id])

  useEffect(() => {
    setDash(null)
    setFilters({ dimensions: [] })
    load({ dimensions: [] })
  }, [load])

  const changeFilters = (f: DashboardFilters) => {
    setFilters(f)
    load(f)
  }

  const patch = async (body: Record<string, unknown>) => {
    try {
      await api.patchDashboard(id, body)
      notifyDashboardsChanged()
      load(filters)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const patchChart = async (chartId: string, body: Record<string, unknown>) => {
    try {
      await api.patchChart(id, chartId, body)
      load(filters)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const generateInsights = async () => {
    if (!dash || generating) return
    const scope = describeFilters(filters)
    const prompt = `Write the conclusions of the dashboard “${dash.title}” (id ${dash.id}). ` +
      (scope ? `Analyze only this scope (apply it yourself in the WHERE of your queries): ${scope}. ` : '') +
      'Read the SQL of its charts with list_dashboards, investigate the data with extra queries (compare periods, ' +
      'break down by dimensions until you find the causes, look for concentrations and anomalies) and save the result with ' +
      'write_dashboard_insights, linking each finding to its chart_id. In the chat, answer only with a 2-3 line summary.'
    setGenerating(true)
    setProgress('Preparing the analysis…')
    try {
      await streamChat(prompt, null, (ev) => {
        if (ev.type === 'conversation') notifyConversationsChanged()
        else if (ev.type === 'tool_start') {
          const detail = typeof ev.input.sql === 'string' ? '' : typeof ev.input.model === 'string' ? ` · ${ev.input.model}` : ''
          const label = PROGRESS[ev.name] ?? ev.name
          setProgress(label.endsWith('…') ? label : label + detail + '…')
        } else if (ev.type === 'thinking') setProgress('Reasoning about the data…')
        else if (ev.type === 'insights' && ev.dashboard_id === id) setDash((d) => (d ? { ...d, insights: ev.insights } : d))
        else if (ev.type === 'error') toast(ev.message, 'error')
      })
      load(filters)
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setGenerating(false)
      setProgress('')
    }
  }

  const jumpTo = (chartId: string) => {
    const el = document.getElementById(`chart-${chartId}`)?.querySelector('.card')
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.remove('flash')
    void (el as HTMLElement).offsetWidth
    el.classList.add('flash')
  }

  const remove = async () => {
    if (!dash || !confirm(`Delete the dashboard “${dash.title}”?`)) return
    await api.deleteDashboard(id)
    notifyDashboardsChanged()
    toast('Dashboard deleted', 'success')
    go('#/')
  }

  const drop = (targetId: string) => {
    if (!dash || !dragId || dragId === targetId) return
    const ids = dash.charts.map((c) => c.spec.id)
    const from = ids.indexOf(dragId)
    const to = ids.indexOf(targetId)
    const reordered: ChartWithData[] = [...dash.charts]
    const [moved] = reordered.splice(from, 1)
    reordered.splice(to, 0, moved)
    setDash({ ...dash, charts: reordered })
    api.patchDashboard(id, { order: reordered.map((c) => c.spec.id) }).catch((e) => toast(e.message, 'error'))
  }

  if (error && !dash) return <div className="page-body"><div className="error-box"><AlertTriangle size={15} />{error}</div></div>

  return (
    <>
      <div className="page-header">
        <div style={{ minWidth: 0 }}>
          {editingTitle !== null ? (
            <div className="row">
              <input className="input" autoFocus value={editingTitle} onChange={(e) => setEditingTitle(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { patch({ title: editingTitle }); setEditingTitle(null) } if (e.key === 'Escape') setEditingTitle(null) }} />
              <button className="icon-btn" onClick={() => { patch({ title: editingTitle }); setEditingTitle(null) }}><Check size={16} /></button>
            </div>
          ) : (
            <div className="row">
              <h1>{dash?.title ?? <span className="skeleton" style={{ display: 'inline-block', width: 220, height: 22 }} />}</h1>
              {dash && <button className="icon-btn no-print" title="Rename" onClick={() => setEditingTitle(dash.title)}><Pencil size={14} /></button>}
            </div>
          )}
          <p>
            {dash?.description}
            {refreshedAt && <span className="muted">{dash?.description ? ' · ' : ''}Updated {refreshedAt.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}</span>}
          </p>
        </div>
        <div className="row no-print">
          <button className="btn" onClick={() => load(filters)} disabled={loading} title="Re-runs the queries with the current data">
            <RefreshCw size={14} className={loading ? 'spin' : undefined} /> Refresh
          </button>
          <button className="btn" onClick={() => window.print()} title="Export to PDF from the print dialog"><Printer size={14} /> PDF</button>
          <button className="btn" disabled={!dash} title="Excel: one sheet per chart + conclusions (with the active filters)"
            onClick={() => dash && api.exportDashboardXlsx(dash.id, filters, dash.title).catch((e) => toast(e.message, 'error'))}><FileSpreadsheet size={14} /> Excel</button>
          <button className="btn" onClick={() => setScheduling(true)} title="Send this dashboard periodically"><CalendarClock size={14} /> Schedule</button>
          <button className="btn" onClick={addText} disabled={!dash} title="Add a text block or section title"><Type size={14} /> Text</button>
          {dash && (
            <button className="btn primary" onClick={() => askAgent(
              `Review the dashboard “${dash.title}” (id ${dash.id}). Analyze what its charts say, spot what's most relevant or anomalous and suggest improvements (missing or unnecessary charts). Apply the improvements with update_dashboard.`,
            )}>
              <Sparkles size={14} /> Improve with AI
            </button>
          )}
          <button className="icon-btn" title="Delete dashboard" onClick={remove}><Trash2 size={16} /></button>
        </div>
      </div>
      {scheduling && dash && (
        <Modal title={`Schedule sending “${dash.title}”`} onClose={() => setScheduling(false)}>
          <ReportForm dashboardId={dash.id} onDone={() => setScheduling(false)} />
        </Modal>
      )}
      <div className="page-body">
        {error && <div className="error-box" style={{ marginBottom: 16 }}><AlertTriangle size={15} />{error}</div>}
        {dash && <FilterBar options={dash.filter_options} filters={filters} onChange={changeFilters} />}
        {dash && dash.charts.some((c) => ['bar', 'hbar', 'stacked_bar', 'line', 'area', 'heatmap'].includes(c.spec.type)) && (
          <p className="drill-hint no-print"><MousePointerClick size={13} /> Click a bar, point or cell to filter the whole dashboard by that value.</p>
        )}
        {dash && dash.charts.length > 0 && (
          <InsightsPanel title={dash.title} dashboardId={dash.id} filtersActive={!!(filters.date_from || filters.date_to || filters.dimensions.some((d) => d.values.length))} insights={dash.insights} generating={generating} progress={progress}
            onGenerate={generateInsights} onJump={jumpTo} />
        )}
        {!dash ? (
          <div className="grid12">
            {[3, 3, 3, 3, 12, 6, 6].map((w, i) => (
              <div key={i} style={{ gridColumn: `span ${w}` }}><div className="card chart-card"><div className="skeleton" style={{ height: 14, width: '50%', marginBottom: 14 }} /><div className="skeleton" style={{ height: w === 3 ? 60 : 240 }} /></div></div>
            ))}
          </div>
        ) : dash.charts.length === 0 ? (
          <div className="empty">
            <div className="empty-icon"><Sparkles size={24} /></div>
            <h2>Empty dashboard</h2>
            <p>Add charts from the chat (the ⋯ menu on each chart) or ask the AI to fill it in.</p>
          </div>
        ) : (
          <div className="grid12" style={{ opacity: loading ? 0.6 : 1, transition: 'opacity .15s' }}>
            {dash.charts.map((c) => (
              <div
                key={c.spec.id}
                id={`chart-${c.spec.id}`}
                style={{ gridColumn: `span ${c.spec.width}` }}
                draggable={armedId === c.spec.id}
                onDragStart={(e) => { setDragId(c.spec.id); e.dataTransfer.effectAllowed = 'move' }}
                onDragEnd={() => { setDragId(null); setOverId(null); setArmedId(null) }}
                onDragOver={(e) => { if (dragId) { e.preventDefault(); setOverId(c.spec.id) } }}
                onDrop={(e) => { e.preventDefault(); drop(c.spec.id) }}
              >
                <ChartCard
                  spec={c.spec}
                  data={c.data}
                  error={c.error}
                  className={(dragId === c.spec.id ? 'dragging ' : '') + (overId === c.spec.id && dragId !== c.spec.id ? 'drop-target' : '')}
                  dragHandle={{
                    onMouseDown: () => setArmedId(c.spec.id),
                    onMouseUp: () => setArmedId(null),
                  }}
                  onRemove={() => patch({ remove_chart_ids: [c.spec.id] })}
                  onPatch={(p) => patchChart(c.spec.id, p)}
                  onPointClick={(pt) => drill(c.spec.id, c.spec.type === 'heatmap' ? c.spec.x : c.spec.x, pt.x, c.spec.series, pt.series)}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  )
}

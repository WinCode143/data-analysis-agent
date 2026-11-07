import {
  AlertTriangle, BookOpen, ChartColumn, ChevronDown, ChevronRight, Clock, Code2, Download, FilePlus2, FileSpreadsheet,
  History, Loader2, Play, Save, Sparkles, Table2, Trash2, Wand2, Wrench,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import { api, type ChartSpec, type ChartType, type Mdl, type QueryRun, type SavedQuery, type ValueFormat } from '../api'
import AddToDashboard from '../components/AddToDashboard'
import Chart from '../components/Chart'
import DataTable from '../components/DataTable'
import Modal from '../components/Modal'
import SqlEditor, { type SqlEditorHandle } from '../components/SqlEditor'
import { takePendingSql, toast } from '../events'

const DRAFT_KEY = 'insight.sql.draft'
const HISTORY_KEY = 'insight.sql.history'
type HistoryItem = { sql: string; at: string; rows: number; ms: number }

function load<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key)
    return v ? (JSON.parse(v) as T) : fallback
  } catch {
    return fallback
  }
}
function store(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* storage unavailable */
  }
}

function toCsv(run: QueryRun): string {
  const esc = (v: unknown) => {
    const s = v == null ? '' : String(v)
    return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [run.columns.map((c) => esc(c.name)).join(','), ...run.rows.map((r) => r.map(esc).join(','))].join('\n')
}

function SchemaTree({ mdl, onInsert }: { mdl: Mdl; onInsert: (text: string) => void }) {
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const q = (n: string) => (/^[a-z_][a-z0-9_]*$/.test(n) ? n : `"${n}"`)
  return (
    <div className="schema-tree">
      {mdl.models.map((m) => (
        <div key={m.name}>
          <div className="tree-row model" onClick={() => setOpen({ ...open, [m.name]: !open[m.name] })} onDoubleClick={() => onInsert(q(m.name))}
            title="Click: show columns · double-click: insert">
            {open[m.name] ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            <Table2 size={13} /> <span>{m.name}</span>
          </div>
          {open[m.name] && m.columns.filter((c) => !c.relationship).map((c) => (
            <div key={c.name} className="tree-row col" onClick={() => onInsert(q(c.name))} title={c.expression ? `= ${c.expression}` : c.properties?.description || c.type}>
              <span>{c.name}</span><span className="muted">{c.isCalculated ? 'ƒ ' : ''}{c.type}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

function ChartBuilder({ run, sql }: { run: QueryRun; sql: string }) {
  const cols = run.columns.map((c) => c.name)
  const numeric = run.columns.filter((c) => /INT|DOUBLE|DECIMAL|FLOAT|REAL|NUMERIC|HUGEINT/i.test(c.type)).map((c) => c.name)
  const dims = cols.filter((c) => !numeric.includes(c))
  const [type, setType] = useState<ChartType>(dims.length ? 'bar' : 'kpi')
  const [x, setX] = useState(dims[0] ?? cols[0])
  const [y, setY] = useState<string[]>(numeric.slice(0, 1))
  const [series, setSeries] = useState('')
  const [format, setFormat] = useState<ValueFormat>('number')
  const [title, setTitle] = useState('')
  const [saved, setSaved] = useState<string | null>(null)
  const [pin, setPin] = useState(false)
  const spec: ChartSpec = { id: 'preview', title: title || 'Preview', type, sql, x: type === 'kpi' || type === 'table' ? null : x, y: type === 'table' ? [] : y, series: series || null, value_format: format, currency: 'USD', width: 6 }
  const valid = type === 'table' || (y.length > 0 && (type === 'kpi' || !!x))
  const save = async () => {
    try {
      const r = await api.createChart({ ...spec, id: undefined, title: title || y.join(', ') })
      setSaved(r.chart_id)
      setPin(true)
      toast('Chart saved', 'success')
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  return (
    <div className="chart-builder">
      <div className="builder-controls">
        <label className="label">Type
          <select className="select" value={type} onChange={(e) => setType(e.target.value as ChartType)}>
            {(['bar', 'hbar', 'stacked_bar', 'line', 'area', 'scatter', 'kpi', 'table'] as ChartType[]).map((t) => <option key={t}>{t}</option>)}
          </select>
        </label>
        {type !== 'kpi' && type !== 'table' && (
          <label className="label">X axis
            <select className="select" value={x} onChange={(e) => setX(e.target.value)}>{cols.map((c) => <option key={c}>{c}</option>)}</select>
          </label>
        )}
        {type !== 'table' && (
          <div>
            <span className="label">Values</span>
            <div className="row" style={{ flexWrap: 'wrap', gap: 4 }}>
              {numeric.map((c) => (
                <button key={c} className={'chip-btn' + (y.includes(c) ? ' on' : '')}
                  onClick={() => setY(y.includes(c) ? y.filter((v) => v !== c) : series ? [c] : [...y, c])}>{c}</button>
              ))}
            </div>
          </div>
        )}
        {['bar', 'stacked_bar', 'line', 'area'].includes(type) && (
          <label className="label">Series (optional)
            <select className="select" value={series} onChange={(e) => { setSeries(e.target.value); if (e.target.value) setY(y.slice(0, 1)) }}>
              <option value="">—</option>{dims.filter((d) => d !== x).map((c) => <option key={c}>{c}</option>)}
            </select>
          </label>
        )}
        <label className="label">Format
          <select className="select" value={format} onChange={(e) => setFormat(e.target.value as ValueFormat)}>
            <option value="number">Number</option><option value="integer">Integer</option><option value="currency">Currency ($)</option><option value="percent">Percent</option>
          </select>
        </label>
        <label className="label">Title<input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What does it show?" /></label>
        <div style={{ position: 'relative' }}>
          <button className="btn primary" style={{ width: '100%' }} disabled={!valid} onClick={save}><Save size={14} /> Save chart</button>
          {pin && saved && <AddToDashboard chartId={saved} onClose={() => setPin(false)} />}
        </div>
      </div>
      <div className="builder-preview card">
        {valid ? <Chart spec={spec} data={run} height={320} /> : <p className="muted">Pick at least one numeric value.</p>}
      </div>
    </div>
  )
}

export default function SqlView() {
  const editor = useRef<SqlEditorHandle>(null)
  const [mdl, setMdl] = useState<Mdl | null>(null)
  const [side, setSide] = useState<'schema' | 'saved' | 'history'>('schema')
  const [saved, setSaved] = useState<SavedQuery[]>([])
  const [historyItems, setHistoryItems] = useState<HistoryItem[]>(() => load(HISTORY_KEY, []))
  const [current, setCurrent] = useState<SavedQuery | null>(null)
  const [run, setRun] = useState<QueryRun | null>(null)
  const [ranSql, setRanSql] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [tab, setTab] = useState<'table' | 'chart' | 'planned'>('table')
  const [ask, setAsk] = useState('')
  const [explanation, setExplanation] = useState<string | null>(null)
  const [saving, setSaving] = useState<{ title: string; description: string; asNew: boolean } | null>(null)
  const [sort, setSort] = useState<{ col: number; desc: boolean } | null>(null)
  const initial = useMemo(() => takePendingSql() ?? load(DRAFT_KEY, 'SELECT *\nFROM sales\nLIMIT 100'), [])

  const loadSaved = useCallback(() => { api.queries().then(setSaved).catch(() => {}) }, [])
  useEffect(() => {
    api.semantic().then(setMdl).catch(() => {})
    loadSaved()
    const onOpen = () => { const s = takePendingSql(); if (s) { editor.current?.setValue(s); setCurrent(null) } }
    window.addEventListener('insight:open-sql', onOpen)
    return () => window.removeEventListener('insight:open-sql', onOpen)
  }, [loadSaved])

  const schema = useMemo(() => Object.fromEntries((mdl?.models ?? []).map((m) => [m.name, m.columns.filter((c) => !c.relationship).map((c) => c.name)])), [mdl])

  const execute = useCallback(async () => {
    const sql = (editor.current?.getSelection() || editor.current?.getValue() || '').trim()
    if (!sql) return
    setBusy('run')
    setError(null)
    setExplanation(null)
    try {
      const r = await api.runSql(sql, 10000)
      setRun(r)
      setRanSql(sql)
      setSort(null)
      const item = { sql, at: new Date().toISOString(), rows: r.row_count, ms: r.elapsed_ms }
      const next = [item, ...historyItems.filter((h) => h.sql !== sql)].slice(0, 40)
      setHistoryItems(next)
      store(HISTORY_KEY, next)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }, [historyItems])

  const assist = async (mode: 'generate' | 'fix' | 'explain') => {
    setBusy(mode)
    try {
      const r = await api.assistSql({ mode, prompt: ask, sql: editor.current?.getValue(), error: error ?? undefined })
      if (r.sql) {
        editor.current?.setValue(r.sql)
        setError(null)
      }
      setExplanation(r.text || null)
      if (mode !== 'explain' && r.sql) setTimeout(execute, 50)
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(null)
    }
  }

  const format = async () => {
    try {
      editor.current?.setValue((await api.formatSql(editor.current.getValue())).sql)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const saveQuery = async () => {
    if (!saving) return
    try {
      const q = await api.saveQuery({ id: saving.asNew ? undefined : current?.id, title: saving.title, description: saving.description, sql: editor.current?.getValue() })
      setCurrent(q)
      setSaving(null)
      loadSaved()
      toast('Query saved', 'success')
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  const sortedRun = useMemo(() => {
    if (!run || !sort) return run
    const rows = [...run.rows].sort((a, b) => {
      const x = a[sort.col], y = b[sort.col]
      if (x == null) return 1
      if (y == null) return -1
      const c = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), 'en')
      return sort.desc ? -c : c
    })
    return { ...run, rows }
  }, [run, sort])

  return (
    <>
      <div className="page-header">
        <div>
          <h1>{current ? current.title : 'SQL editor'}</h1>
          <p>{current?.description || 'Query the semantic models directly. Ctrl+Enter runs it (or just the selection).'}</p>
        </div>
        <div className="row">
          <button className="btn" onClick={() => { editor.current?.setValue(''); setCurrent(null); setRun(null); setError(null); editor.current?.focus() }}><FilePlus2 size={14} /> New</button>
          <button className="btn" onClick={() => setSaving({ title: current?.title ?? '', description: current?.description ?? '', asNew: !current })}><Save size={14} /> Save</button>
          <button className="btn" disabled={!ranSql || busy !== null} onClick={async () => { setBusy('xlsx'); try { await api.exportXlsx(ranSql, current?.title ?? 'Query') } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(null) } }}>
            {busy === 'xlsx' ? <Loader2 size={14} className="spin" /> : <FileSpreadsheet size={14} />} Excel
          </button>
        </div>
      </div>
      <div className="page-body sql-layout">
        <aside className="card sql-side">
          <div className="segmented" style={{ width: '100%' }}>
            <button className={side === 'schema' ? 'on' : ''} onClick={() => setSide('schema')} style={{ flex: 1, justifyContent: 'center' }}><Table2 size={13} /> Models</button>
            <button className={side === 'saved' ? 'on' : ''} onClick={() => setSide('saved')} style={{ flex: 1, justifyContent: 'center' }}><BookOpen size={13} /> Saved</button>
            <button className={side === 'history' ? 'on' : ''} onClick={() => setSide('history')} style={{ flex: 1, justifyContent: 'center' }} title="History"><History size={13} /></button>
          </div>
          <div className="sql-side-body">
            {side === 'schema' && mdl && <SchemaTree mdl={mdl} onInsert={(t) => editor.current?.insert(t)} />}
            {side === 'saved' && (saved.length === 0 ? <p className="muted" style={{ fontSize: 13, padding: 8 }}>No saved queries. The AI can save them too: “save this query”.</p> :
              saved.map((q) => (
                <div key={q.id} className={'list-row' + (current?.id === q.id ? ' active' : '')} onClick={() => { editor.current?.setValue(q.sql); setCurrent(q); setRun(null) }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 550, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{q.title}</div>
                    {q.description && <div className="muted" style={{ fontSize: 11.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{q.description}</div>}
                  </div>
                  <button className="icon-btn" title="Delete" onClick={async (e) => { e.stopPropagation(); if (confirm(`Delete “${q.title}”?`)) { await api.deleteQuery(q.id); if (current?.id === q.id) setCurrent(null); loadSaved() } }}><Trash2 size={13} /></button>
                </div>
              )))}
            {side === 'history' && (historyItems.length === 0 ? <p className="muted" style={{ fontSize: 13, padding: 8 }}>You haven't run any queries yet.</p> :
              historyItems.map((h, i) => (
                <div key={i} className="list-row" style={{ alignItems: 'flex-start' }} onClick={() => editor.current?.setValue(h.sql)}>
                  <Clock size={13} className="muted" style={{ marginTop: 3 }} />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <code style={{ fontSize: 11.5, display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.sql.replace(/\s+/g, ' ')}</code>
                    <span className="muted" style={{ fontSize: 11 }}>{new Date(h.at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })} · {h.rows} rows · {h.ms} ms</span>
                  </div>
                </div>
              )))}
          </div>
        </aside>

        <section className="sql-main">
          <div className="card sql-ask">
            <Sparkles size={16} color="var(--accent)" />
            <input className="input" style={{ border: 0, boxShadow: 'none', background: 'none' }} value={ask} onChange={(e) => setAsk(e.target.value)}
              placeholder="Describe what you want to query and the AI writes the SQL… (e.g. average order value by region and month in 2026)"
              onKeyDown={(e) => e.key === 'Enter' && ask.trim() && assist('generate')} />
            <button className="btn small primary" disabled={!ask.trim() || busy !== null} onClick={() => assist('generate')}>
              {busy === 'generate' ? <Loader2 size={13} className="spin" /> : <Wand2 size={13} />} Generate SQL
            </button>
          </div>

          <div className="card sql-editor-card">
            <SqlEditor ref={editor} initial={initial} schema={schema} onRun={execute} onChange={(v) => store(DRAFT_KEY, v)} />
          </div>

          <div className="row sql-toolbar">
            <button className="btn primary" onClick={execute} disabled={busy !== null}>{busy === 'run' ? <Loader2 size={14} className="spin" /> : <Play size={14} />} Run</button>
            <button className="btn" onClick={format}><Code2 size={14} /> Format</button>
            <button className="btn" onClick={() => assist('explain')} disabled={busy !== null}>{busy === 'explain' ? <Loader2 size={14} className="spin" /> : <BookOpen size={14} />} Explain</button>
            {run && !error && <span className="muted" style={{ marginLeft: 'auto', fontSize: 12.5 }}>{run.row_count.toLocaleString('en-US')} rows{run.truncated ? ' (first 10,000)' : ''} · {run.elapsed_ms} ms</span>}
          </div>

          {explanation && <div className="notice msg-text fade-up" style={{ fontSize: 13 }}><Markdown>{explanation}</Markdown></div>}
          {error && (
            <div className="error-box fade-up">
              <AlertTriangle size={15} /><span style={{ whiteSpace: 'pre-wrap' }}>{error}</span>
              <button className="btn small" onClick={() => assist('fix')} disabled={busy !== null}>{busy === 'fix' ? <Loader2 size={13} className="spin" /> : <Wrench size={13} />} Fix with AI</button>
            </div>
          )}

          {sortedRun && !error && (
            <div className="card sql-results fade-up">
              <div className="row" style={{ justifyContent: 'space-between', marginBottom: 10, flexWrap: 'wrap' }}>
                <div className="segmented">
                  <button className={tab === 'table' ? 'on' : ''} onClick={() => setTab('table')}><Table2 size={13} /> Results</button>
                  <button className={tab === 'chart' ? 'on' : ''} onClick={() => setTab('chart')}><ChartColumn size={13} /> Chart</button>
                  <button className={tab === 'planned' ? 'on' : ''} onClick={() => setTab('planned')}><Code2 size={13} /> Executed SQL</button>
                </div>
                <div className="row">
                  <button className="btn small" onClick={() => { const url = URL.createObjectURL(new Blob([toCsv(sortedRun)], { type: 'text/csv' })); const a = document.createElement('a'); a.href = url; a.download = `${current?.title ?? 'query'}.csv`; a.click() }}><Download size={13} /> CSV</button>
                  <button className="btn small" onClick={() => api.exportXlsx(ranSql, current?.title ?? 'Query').catch((e) => toast(e.message, 'error'))}><FileSpreadsheet size={13} /> Excel</button>
                </div>
              </div>
              {tab === 'table' && (
                <>
                  <div className="sort-hint muted">Click a column to sort</div>
                  <div className="table-wrap sortable" style={{ maxHeight: 460 }} onClick={(e) => {
                    const th = (e.target as HTMLElement).closest('th')
                    if (!th) return
                    const col = Array.from(th.parentElement!.children).indexOf(th)
                    setSort((s) => (s?.col === col ? { col, desc: !s.desc } : { col, desc: false }))
                  }}>
                    <DataTable data={sortedRun} maxHeight={10000} limit={2000} sort={sort} />
                  </div>
                </>
              )}
              {tab === 'chart' && <ChartBuilder key={ranSql} run={sortedRun} sql={ranSql} />}
              {tab === 'planned' && <pre className="sql-view" style={{ maxHeight: 420 }}>{sortedRun.planned_sql}</pre>}
            </div>
          )}
        </section>
      </div>
      {saving && (
        <Modal title={saving.asNew ? 'Save query' : 'Save changes'} onClose={() => setSaving(null)}>
          <div className="stack" style={{ gap: 12, maxWidth: 520 }}>
            <div><label className="label">Title</label><input className="input" autoFocus value={saving.title} onChange={(e) => setSaving({ ...saving, title: e.target.value })} /></div>
            <div><label className="label">Description</label><input className="input" value={saving.description} onChange={(e) => setSaving({ ...saving, description: e.target.value })} placeholder="What it's for" /></div>
            {current && (
              <label className="row" style={{ fontSize: 13 }}><input type="checkbox" checked={saving.asNew} onChange={(e) => setSaving({ ...saving, asNew: e.target.checked })} /> Save as a new query</label>
            )}
            <div className="row" style={{ justifyContent: 'flex-end' }}><button className="btn primary" disabled={!saving.title} onClick={saveQuery}><Save size={14} /> Save</button></div>
          </div>
        </Modal>
      )}
    </>
  )
}

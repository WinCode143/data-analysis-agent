import { AlertTriangle, Database, FileSpreadsheet, Globe, Layers, RefreshCw, ShieldCheck, Loader2, Plus, Sigma, Sparkles, Trash2, Upload, Wand2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Mdl, type MdlModel, type Profile, type Source } from '../api'
import DataTable from '../components/DataTable'
import DatabaseForm from '../components/DatabaseForm'
import Menu from '../components/Menu'
import MetricsPanel from '../components/MetricsPanel'
import QualityPanel from '../components/QualityPanel'
import UrlSourceForm from '../components/UrlSourceForm'
import { askAgent, notifyDataChanged, subscribe, toast } from '../events'
import { cellText } from '../format'

function Uploader({ onDone }: { onDone: (msg: string) => void }) {
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const upload = async (files: File[]) => {
    if (!files.length) return
    setBusy(true)
    setError(null)
    try {
      const r = await api.upload(files)
      onDone(`Imported: ${r.tables.join(', ')}`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div
        className={'dropzone' + (over ? ' over' : '')}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); upload([...e.dataTransfer.files]) }}
      >
        {busy ? <Loader2 className="spin" size={22} /> : <Upload size={22} />}
        <div style={{ marginTop: 8, fontWeight: 550, color: 'var(--text-primary)' }}>Drop files or click</div>
        <div style={{ fontSize: 12 }}>CSV, Excel, Parquet, JSON or a SQLite database</div>
        <input ref={inputRef} type="file" multiple hidden accept=".csv,.tsv,.txt,.xlsx,.xlsm,.xls,.parquet,.json,.jsonl,.ndjson,.db,.sqlite,.sqlite3"
          onChange={(e) => upload([...(e.target.files ?? [])])} />
      </div>
      {error && <div className="error-box" style={{ marginTop: 8 }}><AlertTriangle size={15} />{error}</div>}
    </div>
  )
}

function SchemaMenu({ source, onChanged }: { source: Source; onChanged: (msg: string) => void }) {
  const all = source.available_schemas ?? []
  const [sel, setSel] = useState<Set<string>>(new Set(source.schemas ?? all.map((s) => s.schema)))
  const label = source.schemas ? `${source.schemas.length}/${all.length}` : 'all'
  const save = async (close: () => void) => {
    const list = sel.size === all.length ? null : [...sel]
    const r = await api.setSchemas(source.alias!, list)
    close()
    onChanged(`Schemas updated: +${r.added.length} / −${r.removed.length} models`)
  }
  return (
    <Menu trigger={(_, toggle) => (
      <button className="pill" style={{ border: 0, cursor: 'pointer' }} title="Choose schemas" onClick={toggle}><Layers size={11} /> {label}</button>
    )}>
      {(close) => (
        <div style={{ width: 240 }}>
          <div className="menu-label">Schemas included</div>
          <div className="values-list">
            {all.map((s) => (
              <label key={s.schema}>
                <input type="checkbox" checked={sel.has(s.schema)} onChange={() => setSel((cur) => {
                  const n = new Set(cur); if (n.has(s.schema)) n.delete(s.schema); else n.add(s.schema); return n
                })} />
                <span style={{ flex: 1 }}>{s.schema}</span><span className="pill">{s.tables}</span>
              </label>
            ))}
          </div>
          <hr />
          <div className="row" style={{ padding: 4, justifyContent: 'flex-end' }}>
            <button className="btn small primary" style={{ width: 'auto' }} disabled={!sel.size} onClick={() => save(close)}>Apply</button>
          </div>
        </div>
      )}
    </Menu>
  )
}

function CalculatedFieldForm({ model, onDone }: { model: string; onDone: () => void }) {
  const [name, setName] = useState('')
  const [expression, setExpression] = useState('')
  const [type, setType] = useState('double')
  const [busy, setBusy] = useState(false)
  const submit = async () => {
    setBusy(true)
    try {
      await api.editSemantic({ action: 'add_calculated_field', model, name, expression, type })
      toast(`Field “${name}” created`, 'success')
      setName('')
      setExpression('')
      onDone()
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="row" style={{ flexWrap: 'wrap', marginTop: 12, alignItems: 'flex-end' }}>
      <div style={{ flex: '1 1 140px' }}><label className="label">Name</label><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="net_revenue" /></div>
      <div style={{ flex: '3 1 260px' }}><label className="label">SQL expression</label><input className="input" style={{ fontFamily: 'var(--mono)' }} value={expression} onChange={(e) => setExpression(e.target.value)} placeholder="quantity * unit_price" /></div>
      <div style={{ flex: '0 0 120px' }}><label className="label">Type</label>
        <select className="select" value={type} onChange={(e) => setType(e.target.value)}>
          {['double', 'bigint', 'varchar', 'date', 'boolean'].map((t) => <option key={t}>{t}</option>)}
        </select>
      </div>
      <button className="btn" disabled={!name || !expression || busy} onClick={submit}>{busy ? <Loader2 size={14} className="spin" /> : <Plus size={14} />} Add</button>
    </div>
  )
}

function ModelDetail({ model, onChanged }: { model: MdlModel; onChanged: () => void }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [desc, setDesc] = useState(model.properties?.description ?? '')

  useEffect(() => {
    setProfile(null)
    setError(null)
    setDesc(model.properties?.description ?? '')
    api.profile(model.name).then(setProfile).catch((e) => setError(e.message))
  }, [model.name, model.properties?.description])

  const saveDesc = async (value: string, column?: string) => {
    await api.setDescription(model.name, value, column)
    onChanged()
  }

  const stats = new Map(profile?.columns.map((c) => [c.column, c]))
  const ref = model.tableReference
  const derived = model.columns.filter((c) => c.isCalculated || c.relationship)

  return (
    <div className="stack">
      <div className="card" style={{ padding: 16 }}>
        <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 17 }}>{model.name}</h2>
            <div className="muted" style={{ fontSize: 12 }}>
              {ref.catalog}.{ref.schema}.{ref.table}
              {profile && ` · ${profile.row_count.toLocaleString('en-US')} rows`}
            </div>
          </div>
          <div className="row">
            <button className="btn" onClick={() => askAgent(
              `Document the model “${model.name}”: explore it with describe_model and save with edit_semantic_layer a description of the model and of each column (what it means, units, typical values). If you find relationships with other models, add them.`,
            )}><Wand2 size={14} /> Document with AI</button>
            <button className="btn primary" onClick={() => askAgent(
              `Create a complete dashboard from the model “${model.name}” (and related models if they help). Explore the data first, pick the most useful KPIs and charts for the business and explain the main findings.`,
            )}><Sparkles size={14} /> Generate dashboard</button>
          </div>
        </div>
        <label className="label" style={{ marginTop: 14 }}>Model description</label>
        <input className="input" value={desc} placeholder="What does each row represent?" onChange={(e) => setDesc(e.target.value)}
          onBlur={() => desc !== (model.properties?.description ?? '') && saveDesc(desc)} />
      </div>

      {error && <div className="error-box"><AlertTriangle size={15} />{error}</div>}

      <div className="card" style={{ padding: 16 }}>
        <p className="section-title">Columns</p>
        <div className="table-wrap" style={{ maxHeight: 420 }}>
          <table className="data">
            <thead><tr><th>Column</th><th>Type</th><th>Min</th><th>Max</th><th>Distinct</th><th>% null</th><th>Description</th></tr></thead>
            <tbody>
              {model.columns.filter((c) => !c.isCalculated && !c.relationship).map((c) => {
                const s = stats.get(c.name)
                return (
                  <tr key={c.name}>
                    <td><strong>{c.name}</strong></td>
                    <td className="muted">{c.type}</td>
                    <td>{cellText(s?.min)}</td>
                    <td>{cellText(s?.max)}</td>
                    <td className="num">{cellText(s?.distinct_approx)}</td>
                    <td className="num">{s ? cellText(s.null_pct) : '—'}</td>
                    <td style={{ minWidth: 220 }}>
                      <input className="input" style={{ padding: '3px 6px' }} defaultValue={c.properties?.description ?? ''}
                        onBlur={(e) => e.target.value !== (c.properties?.description ?? '') && saveDesc(e.target.value, c.name)} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ padding: 16 }}>
        <p className="section-title"><Sigma size={15} /> Calculated fields and relationships</p>
        {derived.length === 0 && <p className="hint" style={{ marginTop: -4 }}>Define reusable fields (e.g. net revenue) so the AI always computes them the same way. You can reference related models: <code>products.category</code>.</p>}
        <div className="stack" style={{ gap: 6 }}>
          {derived.map((c) => (
            <div key={c.name} className="row" style={{ fontSize: 13, flexWrap: 'wrap' }}>
              <strong>{c.name}</strong>
              <span className="pill">{c.relationship ? `relationship → ${c.type}` : c.type}</span>
              {c.expression && <code>{c.expression}</code>}
              {c.properties?.description && <span className="muted">{c.properties.description}</span>}
            </div>
          ))}
        </div>
        <CalculatedFieldForm model={model.name} onDone={onChanged} />
      </div>

      {profile && (
        <div className="card" style={{ padding: 16 }}>
          <p className="section-title">Sample</p>
          <DataTable data={{ columns: profile.sample.columns.map((n) => ({ name: n, type: '' })), rows: profile.sample.rows }} maxHeight={300} />
        </div>
      )}
    </div>
  )
}

export default function DataView() {
  const [sources, setSources] = useState<Source[]>([])
  const [mdl, setMdl] = useState<Mdl | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [tab, setTab] = useState<'models' | 'quality' | 'metrics'>(() => (window.location.hash.includes('metrics') ? 'metrics' : window.location.hash.includes('quality') ? 'quality' : 'models'))

  const load = useCallback(() => {
    api.sources().then(setSources)
    api.semantic().then((m) => {
      setMdl(m)
      setSelected((s) => (s && m.models.some((x) => x.name === s) ? s : m.models[0]?.name ?? null))
    })
  }, [])

  useEffect(() => {
    load()
    return subscribe('data', load)
  }, [load])

  const done = (msg: string) => {
    toast(msg, 'success')
    notifyDataChanged()
  }

  const removeSource = async (s: Source) => {
    const id = s.type === 'file' || s.type === 'url' || s.type === 'gsheet' ? s.table! : s.alias!
    if (!confirm(`Remove the source “${s.name}”?`)) return
    await api.removeSource(id)
    done('Source removed')
  }

  const model = mdl?.models.find((m) => m.name === selected)

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Data and semantic layer</h1>
          <p>Connected sources, the models the AI queries and the official metrics.</p>
        </div>
        <div className="segmented">
          <button className={tab === 'models' ? 'on' : ''} onClick={() => setTab('models')}><Database size={13} /> Sources and models</button>
          <button className={tab === 'quality' ? 'on' : ''} onClick={() => setTab('quality')}><ShieldCheck size={13} /> Quality</button>
          <button className={tab === 'metrics' ? 'on' : ''} onClick={() => setTab('metrics')}><Sigma size={13} /> Metrics</button>
        </div>
      </div>
      <div className="page-body">
        {tab === 'quality' ? (mdl ? <QualityPanel mdl={mdl} /> : null) : tab === 'metrics' ? (mdl ? <div className="container"><MetricsPanel mdl={mdl} /></div> : null) : (
        <div className="two-col">
          <div className="stack">
            <Uploader onDone={done} />
            <DatabaseForm onDone={done} />
            <UrlSourceForm onDone={done} />
            <div className="card" style={{ padding: 10 }}>
              <p className="section-title" style={{ padding: '4px 6px 0' }}>Sources</p>
              {sources.length === 0 && <p className="muted" style={{ padding: '0 6px', fontSize: 13 }}>No sources yet.</p>}
              {sources.map((s) => (
                <div key={(s.alias ?? '') + (s.table ?? '')} className="list-row" style={{ cursor: 'default' }}>
                  {s.type === 'file' ? <FileSpreadsheet size={15} /> : s.type === 'url' || s.type === 'gsheet' ? <Globe size={15} /> : <Database size={15} />}
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="row" style={{ gap: 6 }}>
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
                      {s.engine_name && <span className="pill">{s.engine_name}</span>}
                    </div>
                    <div className="muted" style={{ fontSize: 11.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {s.type === 'file' ? `table ${s.table}` : s.type === 'url' || s.type === 'gsheet'
                        ? `${s.type === 'gsheet' ? 'Google Sheets' : 'URL'} · ${s.refresh_minutes ? `reloads every ${s.refresh_minutes >= 60 ? s.refresh_minutes / 60 + ' h' : s.refresh_minutes + ' min'}` : 'manual'}${s.last_refreshed ? ` · ${new Date(s.last_refreshed).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}` : ''}`
                        : s.dsn}
                    </div>
                    {s.error && <div style={{ fontSize: 11.5, color: 'var(--critical)' }}>Not connected: {s.error}</div>}
                  </div>
                  {s.type !== 'file' && !s.error && s.available_schemas && <SchemaMenu source={s} onChanged={done} />}
                  {(s.type === 'url' || s.type === 'gsheet') && <button className="icon-btn" title="Reload now" onClick={async () => { try { await api.refreshSource(s.table!); done(`“${s.name}” reloaded`) } catch (e) { toast((e as Error).message, 'error') } }}><RefreshCw size={14} /></button>}
                  <button className="icon-btn" title="Remove" onClick={() => removeSource(s)}><Trash2 size={14} /></button>
                </div>
              ))}
            </div>
            <div className="card" style={{ padding: 10 }}>
              <p className="section-title" style={{ padding: '4px 6px 0' }}>Semantic models</p>
              {mdl?.models.map((m) => (
                <div key={m.name} className={'list-row' + (m.name === selected ? ' active' : '')} onClick={() => setSelected(m.name)}>
                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>{m.name}</span>
                  <span className="pill">{m.tableReference.catalog === 'workspace' ? 'file' : ({ pg: 'postgres', my: 'mysql', sq: 'sqlite' } as Record<string, string>)[m.tableReference.catalog.slice(0, 2)] ?? 'db'}</span>
                </div>
              ))}
              {mdl && mdl.relationships.length > 0 && (
                <>
                  <p className="section-title" style={{ padding: '12px 6px 0' }}>Relationships</p>
                  {mdl.relationships.map((r) => (
                    <div key={r.name} style={{ fontSize: 12, padding: '3px 6px' }} className="secondary">
                      {r.models[0]} → {r.models[1]} <span className="muted">({r.joinType})</span>
                    </div>
                  ))}
                </>
              )}
            </div>
          </div>
          <div>
            {model ? <ModelDetail model={model} onChanged={load} /> : (
              <div className="empty card">
                <h2>No data yet</h2>
                <p>Upload a file or connect a database to start.</p>
              </div>
            )}
          </div>
        </div>
        )}
      </div>
    </>
  )
}

import { AlertOctagon, AlertTriangle, CheckCircle2, Eye, Info, Loader2, Plus, ShieldCheck, Sparkles, Undo2, Wand2, Wrench } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type Mdl, type PrepStep, type QualityIssue, type QualityReport, type Recipe, type StepKind, type StepPreview } from '../api'
import { askAgent, notifyDataChanged, toast } from '../events'
import Modal from './Modal'

const SEV = {
  high: { label: 'High', icon: AlertOctagon, color: 'var(--critical)' },
  medium: { label: 'Medium', icon: AlertTriangle, color: '#d99a00' },
  low: { label: 'Low', icon: Info, color: 'var(--text-muted)' },
}
// Fixes that don't depend on business judgment: they can be applied in bulk.
const SAFE: StepKind[] = ['trim', 'map_values', 'parse_number', 'parse_date', 'cast', 'drop_duplicates']

function scoreColor(s: number) {
  return s >= 85 ? 'var(--good)' : s >= 60 ? '#d99a00' : 'var(--critical)'
}

function ScoreRing({ score }: { score: number }) {
  const r = 26, c = 2 * Math.PI * r
  return (
    <svg width="68" height="68" viewBox="0 0 68 68" aria-label={`Quality score ${score} of 100`}>
      <circle cx="34" cy="34" r={r} fill="none" stroke="var(--surface-3)" strokeWidth="7" />
      <circle cx="34" cy="34" r={r} fill="none" stroke={scoreColor(score)} strokeWidth="7" strokeLinecap="round"
        strokeDasharray={`${(score / 100) * c} ${c}`} transform="rotate(-90 34 34)" />
      <text x="34" y="39" textAnchor="middle" fontSize="17" fontWeight="700" fill="var(--text-primary)">{score}</text>
    </svg>
  )
}

function PreviewView({ p }: { p: StepPreview }) {
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row" style={{ gap: 18, flexWrap: 'wrap' }}>
        <span><strong>{p.rows_changed.toLocaleString('en-US')}</strong> rows change</span>
        {p.rows_before !== p.rows_after && <span>Rows: {p.rows_before.toLocaleString('en-US')} → <strong>{p.rows_after.toLocaleString('en-US')}</strong></span>}
        {!!p.new_nulls && <span style={{ color: 'var(--critical)' }}><AlertTriangle size={13} /> {p.new_nulls} values would end up blank (they couldn't be converted)</span>}
      </div>
      {p.examples && p.examples.length > 0 && (
        <div className="table-wrap" style={{ maxHeight: 260 }}>
          <table className="data"><thead><tr><th>Before</th><th>After</th><th>Rows</th></tr></thead>
            <tbody>{p.examples.map((e, i) => <tr key={i}><td><code>{e.before ?? '∅'}</code></td><td><code>{e.after ?? '∅'}</code></td><td className="num">{e.rows}</td></tr>)}</tbody>
          </table>
        </div>
      )}
    </div>
  )
}

const KIND_OPTIONS: [StepKind, string][] = [['trim', 'Trim spaces'], ['case', 'Upper/lower case'], ['cast', 'Change type'],
  ['parse_number', 'Text → number'], ['parse_date', 'Text → date'], ['fill_nulls', 'Fill blanks'], ['drop_duplicates', 'Remove duplicates'], ['filter_rows', 'Filter rows']]

function ManualStep({ model, columns, onPreview }: { model: string; columns: string[]; onPreview: (s: PrepStep) => void }) {
  const [s, setS] = useState<PrepStep>({ kind: 'filter_rows', column: columns[0] })
  const needsCol = !['drop_duplicates', 'filter_rows'].includes(s.kind)
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row" style={{ flexWrap: 'wrap' }}>
        <select className="select" style={{ width: 'auto' }} value={s.kind} onChange={(e) => setS({ kind: e.target.value as StepKind, column: s.column })}>
          {KIND_OPTIONS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        {needsCol && <select className="select" style={{ width: 'auto' }} value={s.column ?? ''} onChange={(e) => setS({ ...s, column: e.target.value })}>{columns.map((c) => <option key={c}>{c}</option>)}</select>}
        {s.kind === 'case' && <select className="select" style={{ width: 'auto' }} value={s.mode ?? 'title'} onChange={(e) => setS({ ...s, mode: e.target.value as PrepStep['mode'] })}><option value="title">Title Case</option><option value="upper">UPPERCASE</option><option value="lower">lowercase</option></select>}
        {s.kind === 'cast' && <select className="select" style={{ width: 'auto' }} value={s.type ?? 'double'} onChange={(e) => setS({ ...s, type: e.target.value })}>{['double', 'bigint', 'date', 'timestamp', 'varchar', 'boolean'].map((t) => <option key={t}>{t}</option>)}</select>}
        {s.kind === 'parse_number' && <select className="select" style={{ width: 'auto' }} value={s.decimal ?? ','} onChange={(e) => setS({ ...s, decimal: e.target.value as ',' | '.' })}><option value=",">Decimal comma (1.234,5)</option><option value=".">Decimal point (1,234.5)</option></select>}
        {s.kind === 'parse_date' && <input className="input" style={{ width: 130 }} value={s.format ?? '%d/%m/%Y'} onChange={(e) => setS({ ...s, format: e.target.value })} />}
        {s.kind === 'fill_nulls' && <input className="input" style={{ width: 160 }} placeholder="Value" value={s.value ?? ''} onChange={(e) => setS({ ...s, value: e.target.value })} />}
      </div>
      {s.kind === 'filter_rows' && <input className="input" style={{ fontFamily: 'var(--mono)' }} placeholder={`Rows to KEEP, e.g. status <> 'Cancelled'`} value={s.condition ?? ''} onChange={(e) => setS({ ...s, condition: e.target.value })} />}
      {s.kind === 'drop_duplicates' && <p className="hint" style={{ margin: 0 }}>Without columns it removes identical rows. For duplicates by key, use the suggested fix or ask the AI.</p>}
      <div><button className="btn small" onClick={() => onPreview({ ...s, column: needsCol ? s.column : null })} disabled={s.kind === 'filter_rows' && !s.condition}><Eye size={13} /> Preview</button></div>
      <span className="muted" style={{ fontSize: 11.5 }}>Model: {model}</span>
    </div>
  )
}

export default function QualityPanel({ mdl }: { mdl: Mdl }) {
  const [selected, setSelected] = useState(mdl.models[0]?.name ?? '')
  const [scores, setScores] = useState<Record<string, number>>({})
  const [report, setReport] = useState<QualityReport | null>(null)
  const [recipe, setRecipe] = useState<Recipe | null>(null)
  const [loading, setLoading] = useState(false)
  const [preview, setPreview] = useState<{ step: PrepStep; data?: StepPreview; error?: string } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [manual, setManual] = useState(false)

  const load = useCallback(async (model: string) => {
    setLoading(true)
    try {
      const [r, rc] = await Promise.all([api.quality(model), api.recipe(model)])
      setReport(r)
      setRecipe(rc)
      setScores((s) => ({ ...s, [model]: r.score }))
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    // Scores for every model (in the background) and detail for the selected one
    mdl.models.forEach((m) => api.quality(m.name).then((r) => setScores((s) => ({ ...s, [m.name]: r.score }))).catch(() => {}))
  }, [mdl])
  useEffect(() => { if (selected) load(selected) }, [selected, load])

  const openPreview = async (step: PrepStep) => {
    setPreview({ step })
    try {
      setPreview({ step, data: await api.previewStep(selected, step) })
    } catch (e) {
      setPreview({ step, error: (e as Error).message })
    }
  }
  const apply = async (step: PrepStep) => {
    setBusy('apply')
    try {
      await api.addStep(selected, step)
      toast('Fix applied', 'success')
      setPreview(null)
      notifyDataChanged()
      await load(selected)
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(null)
    }
  }
  const fixAllSafe = async () => {
    setBusy('all')
    let applied = 0
    try {
      // Re-check after each fix because some change what others detect (e.g. trim and categories).
      for (let i = 0; i < 12; i++) {
        const r = await api.quality(selected)
        const issue = r.issues.find((x) => x.fix && SAFE.includes(x.fix.kind) && !(x.fix.kind === 'drop_duplicates' && x.fix.columns?.length))
        if (!issue?.fix) break
        await api.addStep(selected, { ...issue.fix, note: issue.title })
        applied++
      }
      toast(applied ? `${applied} fixes applied` : 'No safe fixes left', applied ? 'success' : 'info')
      notifyDataChanged()
      await load(selected)
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setBusy(null)
    }
  }
  const undo = async (id: string) => {
    await api.removeStep(selected, id)
    notifyDataChanged()
    load(selected)
  }

  const columns = mdl.models.find((m) => m.name === selected)?.columns.filter((c) => !c.relationship && !c.isCalculated).map((c) => c.name) ?? []
  const safeCount = report?.issues.filter((i) => i.fix && SAFE.includes(i.fix.kind) && !(i.fix.kind === 'drop_duplicates' && i.fix.columns?.length)).length ?? 0

  return (
    <div className="two-col">
      <div className="card" style={{ padding: 8 }}>
        <p className="section-title" style={{ padding: '6px 8px 0' }}><ShieldCheck size={15} /> Quality by model</p>
        {mdl.models.map((m) => (
          <div key={m.name} className={'list-row' + (m.name === selected ? ' active' : '')} onClick={() => setSelected(m.name)}>
            <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>{m.name}</span>
            {scores[m.name] == null ? <Loader2 size={13} className="spin muted" /> : <span className="score-pill" style={{ color: scoreColor(scores[m.name]), borderColor: scoreColor(scores[m.name]) }}>{scores[m.name]}</span>}
          </div>
        ))}
      </div>
      <div className="stack">
        {loading && !report ? <div className="skeleton" style={{ height: 200 }} /> : report && (
          <>
            <div className="card quality-head">
              <ScoreRing score={report.score} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <h2 style={{ margin: 0, fontSize: 17 }}>{report.model}</h2>
                <p className="secondary" style={{ margin: '2px 0 0', fontSize: 13 }}>
                  {report.rows.toLocaleString('en-US')} rows{report.sampled ? ' (a sample was analyzed)' : ''} · {report.issues.length ? `${report.issues.length} issues found` : 'no issues found'}
                </p>
              </div>
              <div className="row" style={{ flexWrap: 'wrap' }}>
                <button className="btn" onClick={() => askAgent(`Check the quality of the model “${report.model}” with check_data_quality. Apply the safe fixes with add_cleaning_step, explaining each one, and for the ones that depend on business judgment (filtering rows, filling blanks) show me the preview and ask me.`)}>
                  <Sparkles size={14} /> Clean with AI
                </button>
                <button className="btn primary" disabled={!safeCount || busy !== null} onClick={fixAllSafe}>
                  {busy === 'all' ? <Loader2 size={14} className="spin" /> : <Wand2 size={14} />} Fix the safe ones ({safeCount})
                </button>
              </div>
            </div>
            {report.issues.length === 0 && <div className="card empty" style={{ padding: 28 }}><CheckCircle2 size={28} color="var(--good)" /><h2>All good</h2><p>No quality issues were found in this model.</p></div>}
            {report.issues.map((i: QualityIssue) => {
              const sev = SEV[i.severity]
              return (
                <div key={i.id} className="card issue-card" style={{ borderLeftColor: sev.color }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="row" style={{ gap: 6 }}><sev.icon size={14} color={sev.color} /><span className="finding-kind">{sev.label}</span>{i.column && <span className="pill">{i.column}</span>}</div>
                    <strong style={{ display: 'block', margin: '4px 0 2px' }}>{i.title}</strong>
                    <span className="secondary" style={{ fontSize: 13 }}>{i.detail}</span>
                    {i.examples.length > 0 && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>E.g. {i.examples.map(String).join(' · ')}</div>}
                  </div>
                  {i.fix && <button className="btn small" onClick={() => openPreview(i.fix!)}><Wrench size={13} /> Fix…</button>}
                </div>
              )
            })}
            <div className="card" style={{ padding: 16 }}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <p className="section-title" style={{ margin: 0 }}><Undo2 size={15} /> Cleanup recipe</p>
                <button className="btn small" onClick={() => setManual((v) => !v)}><Plus size={13} /> Manual step</button>
              </div>
              <p className="hint">The original data isn't modified: the recipe creates a clean version, which is what the AI, dashboards and metrics use. Remove a step to undo it.</p>
              {manual && <div className="notice" style={{ margin: '8px 0' }}><ManualStep model={selected} columns={columns} onPreview={openPreview} /></div>}
              {recipe?.steps.length ? (
                <ol className="recipe">
                  {recipe.steps.map((s) => (
                    <li key={s.id}><span style={{ flex: 1 }}>{s.description}</span>
                      <button className="icon-btn" title="Undo this step" onClick={() => undo(s.id!)}><Undo2 size={14} /></button></li>
                  ))}
                </ol>
              ) : <p className="muted" style={{ fontSize: 13, margin: 0 }}>No steps: the model reads the data as it comes in.</p>}
            </div>
          </>
        )}
      </div>
      {preview && (
        <Modal title="Fix preview" onClose={() => setPreview(null)}>
          <div className="stack" style={{ gap: 12, maxWidth: 640 }}>
            {preview.data && <p style={{ margin: 0 }}><strong>{preview.data.description}</strong></p>}
            {preview.error ? <div className="error-box">{preview.error}</div> : preview.data ? <PreviewView p={preview.data} /> : <div className="skeleton" style={{ height: 120 }} />}
            <div className="row" style={{ justifyContent: 'flex-end' }}>
              <button className="btn" onClick={() => setPreview(null)}>Cancel</button>
              <button className="btn primary" disabled={!preview.data || busy !== null} onClick={() => apply(preview.step)}>
                {busy === 'apply' ? <Loader2 size={14} className="spin" /> : <CheckCircle2 size={14} />} Apply
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}

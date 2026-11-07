import type * as echarts from 'echarts'
import {
  AlertTriangle, ChartArea, ChartBar, ChartColumn, ChartColumnStacked, ChartLine, ChartScatter, Code2, Download, Gauge,
  Check, Flag, FileSpreadsheet, Grid3x3, GripVertical, Image, MapPin, Pencil, Plus, SquareCode, Type, X, LayoutDashboard, Lightbulb, Maximize2, MoreHorizontal, Ruler, Shapes, Sparkles, Table2,
  Trash2, type LucideIcon,
} from 'lucide-react'
import { useRef, useState, type HTMLAttributes } from 'react'
import { api, type ChartSpec, type ChartType, type TableData } from '../api'
import { askAgent, openInEditor, toast } from '../events'
import AddToDashboard from './AddToDashboard'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Chart, { type PointClick } from './Chart'
import DataTable from './DataTable'
import Menu from './Menu'
import Modal from './Modal'

const TYPE_LABELS: Record<ChartType, string> = {
  bar: 'Bars', hbar: 'Horizontal bars', stacked_bar: 'Stacked bars', line: 'Lines', area: 'Area',
  scatter: 'Scatter', heatmap: 'Heatmap', kpi: 'KPI', table: 'Table', text: 'Text',
}

const TYPE_ICONS: Record<ChartType, LucideIcon> = {
  bar: ChartColumn, hbar: ChartBar, stacked_bar: ChartColumnStacked, line: ChartLine, area: ChartArea,
  scatter: ChartScatter, heatmap: Grid3x3, kpi: Gauge, table: Table2, text: Type,
}

function compatibleTypes(spec: ChartSpec): ChartType[] {
  if (['kpi', 'scatter', 'heatmap'].includes(spec.type)) return [spec.type, 'table']
  if (!spec.x) return ['table']
  return spec.series ? ['line', 'area', 'stacked_bar', 'bar', 'table'] : ['bar', 'hbar', 'line', 'area', 'table']
}

function download(name: string, href: string) {
  const a = document.createElement('a')
  a.href = href
  a.download = name
  a.click()
}

function toCsv(data: TableData): string {
  const esc = (v: unknown) => {
    const s = v == null ? '' : String(v)
    return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [data.columns.map((c) => esc(c.name)).join(','), ...data.rows.map((r) => r.map(esc).join(','))].join('\n')
}

const slug = (s: string) => s.toLowerCase().normalize('NFD').replace(/[^\w]+/g, '-').replace(/^-|-$/g, '') || 'chart'

function TargetsEditor({ spec, onSave }: { spec: ChartSpec; onSave: (targets: NonNullable<ChartSpec['targets']>) => void }) {
  const [items, setItems] = useState(spec.targets ?? [])
  const [value, setValue] = useState('')
  const [label, setLabel] = useState(spec.type === 'kpi' ? 'Target' : 'Budget')
  return (
    <div className="stack" style={{ gap: 12, maxWidth: 480 }}>
      <p className="hint" style={{ margin: 0 }}>{spec.type === 'kpi' ? 'The card will show the % reached against the goal.' : 'It\'s drawn as a horizontal reference line.'}
        {spec.value_format === 'percent' && ' For percentages use decimals: 0.35 = 35%.'}</p>
      {items.map((t, i) => (
        <div key={i} className="row"><Flag size={14} /> <span style={{ flex: 1 }}>{t.label}: <strong>{t.value.toLocaleString('en-US')}</strong></span>
          <button className="icon-btn" onClick={() => setItems(items.filter((_, j) => j !== i))}><X size={14} /></button></div>
      ))}
      <div className="row">
        <input className="input" style={{ flex: 1 }} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Label" />
        <input className="input" style={{ width: 140 }} type="number" value={value} onChange={(e) => setValue(e.target.value)} placeholder="Value" />
        <button className="btn" disabled={!value} onClick={() => { setItems([...items, { value: Number(value), label: label || 'Target' }]); setValue('') }}><Plus size={14} /></button>
      </div>
      <div className="row" style={{ justifyContent: 'flex-end' }}><button className="btn primary" onClick={() => onSave(items)}><Check size={14} /> Save</button></div>
    </div>
  )
}

function AnnotationsEditor({ spec, data, onSave }: { spec: ChartSpec; data: TableData; onSave: (a: NonNullable<ChartSpec['annotations']>) => void }) {
  const xi = data.columns.findIndex((c) => c.name === spec.x)
  const values = [...new Set(data.rows.map((r) => String(r[xi])))]
  const [items, setItems] = useState(spec.annotations ?? [])
  const [x, setX] = useState(values[0] ?? '')
  const [label, setLabel] = useState('')
  const show = (v: string) => (/^\d{4}-\d{2}-\d{2}/.test(v) ? v.slice(0, 10) : v)
  return (
    <div className="stack" style={{ gap: 12, maxWidth: 520 }}>
      <p className="hint" style={{ margin: 0 }}>Mark an event that explains a change (a campaign, a stockout, a price change…).</p>
      {items.map((a, i) => (
        <div key={i} className="row"><MapPin size={14} /> <span style={{ flex: 1 }}><strong>{show(a.x)}</strong> · {a.label}</span>
          <button className="icon-btn" onClick={() => setItems(items.filter((_, j) => j !== i))}><X size={14} /></button></div>
      ))}
      <div className="row">
        <select className="select" style={{ width: 170 }} value={x} onChange={(e) => setX(e.target.value)}>{values.map((v) => <option key={v} value={v}>{show(v)}</option>)}</select>
        <input className="input" style={{ flex: 1 }} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="What happened?" />
        <button className="btn" disabled={!label} onClick={() => { setItems([...items, { x, label }]); setLabel('') }}><Plus size={14} /></button>
      </div>
      <div className="row" style={{ justifyContent: 'flex-end' }}><button className="btn primary" onClick={() => onSave(items)}><Check size={14} /> Save</button></div>
    </div>
  )
}

function TextBlock({ spec, onPatch, onRemove, dragHandle, cardProps, className }: Pick<Props, 'spec' | 'onPatch' | 'onRemove' | 'dragHandle' | 'cardProps' | 'className'>) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(spec.text ?? '')
  return (
    <div {...cardProps} className={'card chart-card text-block ' + (className ?? '')}>
      {dragHandle && <span className="drag-handle" title="Drag to reorder" {...dragHandle}><GripVertical size={14} /></span>}
      {editing ? (
        <div className="stack" style={{ gap: 8 }}>
          <textarea className="input" rows={Math.max(3, text.split('\n').length + 1)} value={text} autoFocus onChange={(e) => setText(e.target.value)}
            placeholder="## Section title&#10;Markdown text…" style={{ fontFamily: 'var(--mono)', fontSize: 13 }} />
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button className="btn small ghost" onClick={() => { setText(spec.text ?? ''); setEditing(false) }}>Cancel</button>
            <button className="btn small primary" disabled={!text.trim()} onClick={() => { onPatch?.({ text }); setEditing(false) }}><Check size={13} /> Save</button>
          </div>
        </div>
      ) : (
        <>
          <div className="msg-text text-block-body" onDoubleClick={() => onPatch && setEditing(true)}><Markdown remarkPlugins={[remarkGfm]}>{spec.text ?? ''}</Markdown></div>
          {onPatch && (
            <div className="chart-actions text-block-actions">
              <button className="icon-btn" title="Edit text" onClick={() => setEditing(true)}><Pencil size={14} /></button>
              {onRemove && <button className="icon-btn" title="Remove" onClick={onRemove}><Trash2 size={14} /></button>}
            </div>
          )}
        </>
      )}
    </div>
  )
}

interface Props {
  spec: ChartSpec
  data: TableData | null
  error?: string | null
  onRemove?: () => void
  onPatch?: (patch: Partial<Pick<ChartSpec, 'type' | 'width' | 'title' | 'text' | 'targets' | 'annotations'>>) => void
  onPointClick?: (p: PointClick) => void
  allowPin?: boolean
  dragHandle?: HTMLAttributes<HTMLSpanElement>
  cardProps?: HTMLAttributes<HTMLDivElement>
  className?: string
}

export default function ChartCard(props: Props) {
  if (props.spec.type === 'text') return <TextBlock {...props} />
  return <PlotCard {...props} />
}

function PlotCard({ spec, data, error, onRemove, onPatch, onPointClick, allowPin, dragHandle, cardProps, className }: Props) {
  const [editor, setEditor] = useState<'targets' | 'annotations' | null>(null)
  const [view, setView] = useState<'chart' | 'table' | 'sql'>('chart')
  const [localType, setLocalType] = useState<ChartType | null>(null)
  const [pinOpen, setPinOpen] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const instance = useRef<echarts.ECharts | null>(null)
  const effective: ChartSpec = localType ? { ...spec, type: localType } : spec
  const isPlot = !['kpi', 'table'].includes(effective.type)

  const setType = (t: ChartType) => (onPatch ? onPatch({ type: t }) : setLocalType(t))

  const body = (height: number, withInstance: boolean) =>
    error ? (
      <div className="error-box"><AlertTriangle size={15} />{error}</div>
    ) : !data ? (
      <div className="skeleton" style={{ height }} />
    ) : view === 'sql' ? (
      <pre className="sql-view">{spec.sql}</pre>
    ) : view === 'table' ? (
      <DataTable data={data} maxHeight={height + 40} />
    ) : (
      <Chart spec={effective} data={data} height={height} onInstance={withInstance ? (c) => { instance.current = c } : undefined} onPointClick={withInstance ? onPointClick : undefined} />
    )

  return (
    <div {...cardProps} className={'card chart-card ' + (className ?? '')}>
      <div className="chart-head">
        {dragHandle && <span className="drag-handle" title="Drag to reorder" {...dragHandle}><GripVertical size={14} /></span>}
        <div style={{ minWidth: 0 }}>
          <h3 className="chart-title">{spec.title}</h3>
          {spec.subtitle && <p className="chart-subtitle">{spec.subtitle}</p>}
        </div>
        <div className="chart-actions">
          {isPlot && (
            <button className={'icon-btn' + (view === 'table' ? ' active' : '')} title={view === 'table' ? 'Show chart' : 'Show data'}
              onClick={() => setView(view === 'table' ? 'chart' : 'table')}>
              <Table2 size={15} />
            </button>
          )}
          <button className="icon-btn" title="Expand" onClick={() => setExpanded(true)}><Maximize2 size={15} /></button>
          <Menu trigger={(open, toggle) => (
            <button className={'icon-btn' + (open ? ' active' : '')} title="More actions" onClick={toggle}><MoreHorizontal size={16} /></button>
          )}>
            {(close) => (
              <>
                <button onClick={() => { setView(view === 'sql' ? 'chart' : 'sql'); close() }}><Code2 size={15} /> {view === 'sql' ? 'Hide SQL' : 'Show SQL'}</button>
                {data && <button onClick={() => { api.exportXlsx(spec.sql, spec.title, spec.id === 'preview' ? undefined : spec.id).catch((e) => toast(e.message, 'error')); close() }}><FileSpreadsheet size={15} /> Download Excel</button>}
                {data && <button onClick={() => { download(`${slug(spec.title)}.csv`, URL.createObjectURL(new Blob([toCsv(data)], { type: 'text/csv' }))); close() }}><Download size={15} /> Download CSV</button>}
                <button onClick={() => { openInEditor(spec.sql); close() }}><SquareCode size={15} /> Open in the SQL editor</button>
                {isPlot && view === 'chart' && (
                  <button onClick={() => { const url = instance.current?.getDataURL({ type: 'svg' }); if (url) download(`${slug(spec.title)}.svg`, url); close() }}>
                    <Image size={15} /> Download image
                  </button>
                )}
                {compatibleTypes(spec).length > 1 && (
                  <>
                    <hr />
                    <div className="menu-label"><Shapes size={11} /> Chart type</div>
                    {compatibleTypes(spec).map((t) => {
                      const Icon = TYPE_ICONS[t]
                      return (
                        <button key={t} className={effective.type === t ? 'on' : ''} onClick={() => { setType(t); setView('chart'); close() }}>
                          <Icon size={15} /> {TYPE_LABELS[t]}
                        </button>
                      )
                    })}
                  </>
                )}
                {onPatch && (spec.type === 'kpi' || ['bar', 'hbar', 'stacked_bar', 'line', 'area'].includes(spec.type)) && (
                  <>
                    <hr />
                    <button onClick={() => { setEditor('targets'); close() }}><Flag size={15} /> {spec.type === 'kpi' ? 'Target…' : 'Target or budget…'}{spec.targets?.length ? ` (${spec.targets.length})` : ''}</button>
                    {spec.type !== 'kpi' && data && <button onClick={() => { setEditor('annotations'); close() }}><MapPin size={15} /> Annotations…{spec.annotations?.length ? ` (${spec.annotations.length})` : ''}</button>}
                  </>
                )}
                {onPatch && spec.type !== 'kpi' && (
                  <>
                    <hr />
                    <div className="menu-label"><Ruler size={11} /> Width</div>
                    {[[4, 'One third'], [6, 'Half'], [12, 'Full']].map(([w, label]) => (
                      <button key={w} className={spec.width === w ? 'on' : ''} onClick={() => { onPatch({ width: w as number }); close() }}>{label}</button>
                    ))}
                  </>
                )}
                <hr />
                {allowPin && <button onClick={() => { setPinOpen(true); close() }}><LayoutDashboard size={15} /> Add to a dashboard</button>}
                <button onClick={() => { askAgent(`About the chart “${spec.title}” (SQL: ${spec.sql}): explain what it shows, what stands out and what I should look into next.`); close() }}>
                  <Sparkles size={15} /> Ask the AI
                </button>
                {onRemove && <button onClick={() => { onRemove(); close() }} style={{ color: 'var(--critical)' }}><Trash2 size={15} /> Remove from dashboard</button>}
              </>
            )}
          </Menu>
        </div>
      </div>
      {pinOpen && <AddToDashboard chartId={spec.id} onClose={() => setPinOpen(false)} />}
      {body(280, true)}
      {spec.insight && !error && <p className="chart-insight"><Lightbulb size={14} />{spec.insight}</p>}
      {editor && onPatch && (
        <Modal title={editor === 'targets' ? `Targets · ${spec.title}` : `Annotations · ${spec.title}`} onClose={() => setEditor(null)}>
          {editor === 'targets'
            ? <TargetsEditor spec={spec} onSave={(targets) => { onPatch({ targets }); setEditor(null) }} />
            : data && <AnnotationsEditor spec={spec} data={data} onSave={(annotations) => { onPatch({ annotations }); setEditor(null) }} />}
        </Modal>
      )}
      {expanded && (
        <Modal title={spec.title} onClose={() => setExpanded(false)}>
          {spec.subtitle && <p className="chart-subtitle" style={{ marginTop: -6, marginBottom: 12 }}>{spec.subtitle}</p>}
          {data && <Chart spec={effective} data={data} height={460} />}
          {data && <div style={{ marginTop: 16 }}><DataTable data={data} maxHeight={260} /></div>}
        </Modal>
      )}
    </div>
  )
}

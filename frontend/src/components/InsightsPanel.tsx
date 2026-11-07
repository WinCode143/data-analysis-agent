import { AlertTriangle, ChevronDown, ChevronUp, Copy, Filter, Info, Lightbulb, Loader2, RefreshCw, Sparkles, Target, TrendingDown, TrendingUp } from 'lucide-react'
import { useState } from 'react'
import Markdown from 'react-markdown'
import type { Finding, Insights } from '../api'
import { toast } from '../events'

export const KIND: Record<Finding['kind'], { label: string; icon: typeof Info; cls: string }> = {
  positive: { label: 'Positive', icon: TrendingUp, cls: 'good' },
  negative: { label: 'Negative', icon: TrendingDown, cls: 'bad' },
  warning: { label: 'Warning', icon: AlertTriangle, cls: 'warn' },
  neutral: { label: 'Key fact', icon: Info, cls: 'neutral' },
}

export function insightsMarkdown(title: string, ins: Insights): string {
  return [
    `# Conclusions: ${title}`, ins.scope ? `_${ins.scope}_` : '', '', ins.summary, '', '## Findings',
    ...ins.findings.map((f) => `- **${f.title}** (${(KIND[f.kind] ?? KIND.neutral).label}). ${f.detail}`), '',
    '## Recommendations', ...ins.recommendations.map((r) => `- ${r}`),
  ].join('\n')
}

export function FindingCard({ f, onJump }: { f: Finding; onJump?: (chartId: string) => void }) {
  const k = KIND[f.kind] ?? KIND.neutral
  const Icon = k.icon
  const clickable = !!(f.chart_id && onJump)
  return (
    <div className={`finding ${k.cls}` + (clickable ? ' clickable' : '')} onClick={clickable ? () => onJump!(f.chart_id!) : undefined}
      title={clickable ? 'Go to the chart that backs it' : undefined}>
      <div className="finding-head"><Icon size={14} /> <span className="finding-kind">{k.label}</span></div>
      <strong>{f.title}</strong>
      <p>{f.detail}</p>
    </div>
  )
}

interface Props {
  title: string
  dashboardId?: string
  filtersActive?: boolean
  insights?: Insights | null
  generating: boolean
  progress: string
  onGenerate: () => void
  onJump: (chartId: string) => void
}

const COLLAPSE_KEY = 'insight.insights.collapsed'
function readCollapsed(id?: string): boolean {
  try { return !!id && (JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '{}') as Record<string, boolean>)[id] === true } catch { return false }
}
function writeCollapsed(id: string | undefined, v: boolean) {
  if (!id) return
  try {
    const all = JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '{}') as Record<string, boolean>
    all[id] = v
    localStorage.setItem(COLLAPSE_KEY, JSON.stringify(all))
  } catch { /* storage unavailable */ }
}

export default function InsightsPanel({ title, dashboardId, filtersActive, insights, generating, progress, onGenerate, onJump }: Props) {
  const [collapsed, setCollapsed] = useState(() => readCollapsed(dashboardId))
  const toggle = () => { setCollapsed(!collapsed); writeCollapsed(dashboardId, !collapsed) }
  if (!insights) {
    return (
      <div className="card insights empty-insights no-print-empty">
        <div className="row" style={{ gap: 14 }}>
          <span className="empty-icon" style={{ width: 42, height: 42, borderRadius: 12 }}><Lightbulb size={20} /></span>
          <div style={{ flex: 1 }}>
            <strong>AI conclusions</strong>
            <p className="secondary" style={{ margin: '2px 0 0', fontSize: 13 }}>
              {generating ? progress || 'Analyzing the dashboard data…' : 'The AI investigates the dashboard data and writes an executive summary, findings and recommendations.'}
            </p>
          </div>
          <button className="btn primary" onClick={onGenerate} disabled={generating}>
            {generating ? <Loader2 size={14} className="spin" /> : <Sparkles size={14} />} {generating ? 'Analyzing…' : 'Generate conclusions'}
          </button>
        </div>
      </div>
    )
  }
  const when = new Date(insights.generated_at).toLocaleString('en-US', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
  return (
    <section className="card insights fade-up">
      <div className="insights-head">
        <div>
          <p className="section-title" style={{ margin: 0 }}><Lightbulb size={15} /> Conclusions</p>
          <span className="muted" style={{ fontSize: 12 }}>{insights.scope ? `${insights.scope} · ` : ''}generated {when}</span>
        </div>
        <div className="row no-print">
          <button className="icon-btn" title={collapsed ? 'Show conclusions' : 'Collapse conclusions'} onClick={toggle}>{collapsed ? <ChevronDown size={16} /> : <ChevronUp size={16} />}</button>
          <button className="icon-btn" title="Copy as text (Markdown)" onClick={() => { navigator.clipboard.writeText(insightsMarkdown(title, insights)); toast('Conclusions copied', 'success') }}><Copy size={15} /></button>
          <button className="btn small" onClick={onGenerate} disabled={generating}>
            {generating ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />} {generating ? 'Analyzing…' : 'Regenerate'}
          </button>
        </div>
      </div>
      {generating && progress && <p className="muted row" style={{ fontSize: 12.5, margin: '0 0 8px' }}><Loader2 size={12} className="spin" /> {progress}</p>}
      {filtersActive && !generating && (
        <div className="notice row no-print" style={{ marginBottom: 10, fontSize: 12.5 }}>
          <Filter size={14} /> These conclusions don't reflect the active filters.
          <button className="btn small" style={{ marginLeft: 'auto' }} onClick={onGenerate}><Sparkles size={13} /> Analyze this filter</button>
        </div>
      )}
      {collapsed ? (
        <p className="secondary" style={{ margin: 0, fontSize: 13.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{insights.summary}</p>
      ) : <>
      <div className="insights-summary msg-text"><Markdown>{insights.summary}</Markdown></div>
      <div className="findings">
        {insights.findings.map((f, i) => <FindingCard key={i} f={f} onJump={onJump} />)}
      </div>
      {insights.recommendations.length > 0 && (
        <div className="recommendations">
          <p className="section-title"><Target size={15} /> Recommendations</p>
          <ol>{insights.recommendations.map((r, i) => <li key={i}>{r}</li>)}</ol>
        </div>
      )}
      </>}
    </section>
  )
}

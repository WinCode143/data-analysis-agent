import * as echarts from 'echarts'
import { ArrowDownRight, ArrowUpRight } from 'lucide-react'
import { useEffect, useMemo, useRef } from 'react'
import type { ChartSpec, TableData } from '../api'
import { dateFormatter, valueFormatter } from '../format'
import { useColorScheme } from '../theme'
import DataTable from './DataTable'

const MAX_SERIES = 8

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

interface Series { name: string; values: (number | null)[] }

/** Turns rows into categories × series. With `series`, it pivots and groups anything beyond 8 into "Other". */
function pivot(spec: ChartSpec, data: TableData): { categories: unknown[]; series: Series[] } {
  const idx = (c: string) => data.columns.findIndex((col) => col.name === c)
  const xi = idx(spec.x!)
  const categories: unknown[] = []
  const catPos = new Map<string, number>()
  for (const r of data.rows) {
    const k = String(r[xi])
    if (!catPos.has(k)) {
      catPos.set(k, categories.length)
      categories.push(r[xi])
    }
  }
  if (!spec.series) {
    return {
      categories,
      series: spec.y.map((y) => {
        const yi = idx(y)
        const values = new Array(categories.length).fill(null)
        for (const r of data.rows) values[catPos.get(String(r[xi]))!] = r[yi] as number
        return { name: y, values }
      }),
    }
  }
  const si = idx(spec.series)
  const yi = idx(spec.y[0])
  const totals = new Map<string, number>()
  for (const r of data.rows) totals.set(String(r[si]), (totals.get(String(r[si])) ?? 0) + Math.abs(Number(r[yi]) || 0))
  const ranked = [...totals.keys()].sort((a, b) => totals.get(b)! - totals.get(a)!)
  const keep = ranked.length > MAX_SERIES ? ranked.slice(0, MAX_SERIES - 1) : ranked
  // Stable order by name: the color follows the entity, not its ranking.
  const names = [...keep].sort((a, b) => a.localeCompare(b, 'en'))
  if (ranked.length > keep.length) names.push('Other')
  const byName = new Map(names.map((n) => [n, new Array(categories.length).fill(null) as (number | null)[]]))
  for (const r of data.rows) {
    const name = keep.includes(String(r[si])) ? String(r[si]) : 'Other'
    const arr = byName.get(name)!
    const p = catPos.get(String(r[xi]))!
    arr[p] = (arr[p] ?? 0) + (Number(r[yi]) || 0)
  }
  return { categories, series: names.map((n) => ({ name: n, values: byName.get(n)! })) }
}

export interface PointClick { x: unknown; series?: string }
type ClickMap = (params: { dataIndex: number; seriesName?: string; value?: unknown }) => PointClick | null

function buildOption(spec: ChartSpec, data: TableData): { option: echarts.EChartsOption; click: ClickMap | null } {
  const palette = Array.from({ length: 8 }, (_, i) => cssVar(`--series-${i + 1}`))
  const surface = cssVar('--surface-1')
  const ink2 = cssVar('--text-secondary')
  const muted = cssVar('--text-muted')
  const grid = cssVar('--grid')
  const axis = cssVar('--axis')
  const fmt = valueFormatter(spec)
  const fmtAxis = valueFormatter(spec, true)

  const base: echarts.EChartsOption = {
    color: palette,
    backgroundColor: 'transparent',
    textStyle: { fontFamily: cssVar('--font'), color: ink2 },
    animationDuration: 300,
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      backgroundColor: cssVar('--surface-2'),
      borderColor: cssVar('--border-strong'),
      textStyle: { color: cssVar('--text-primary'), fontSize: 12 },
      valueFormatter: (v) => fmt(v),
    },
  }
  const valueAxis = {
    type: 'value' as const,
    axisLabel: { color: muted, formatter: (v: number) => fmtAxis(v) },
    splitLine: { lineStyle: { color: grid, width: 1 } },
    axisLine: { show: false },
  }

  if (spec.type === 'scatter') {
    const xi = data.columns.findIndex((c) => c.name === spec.x)
    const yi = data.columns.findIndex((c) => c.name === spec.y[0])
    const si = spec.series ? data.columns.findIndex((c) => c.name === spec.series) : -1
    const groups = new Map<string, number[][]>()
    for (const r of data.rows) {
      const k = si >= 0 ? String(r[si]) : spec.y[0]
      if (!groups.has(k)) groups.set(k, [])
      groups.get(k)!.push([r[xi] as number, r[yi] as number])
    }
    const names = [...groups.keys()].slice(0, 3) // scatter: at most 3 distinguishable groups
    return { click: null, option: {
      ...base,
      legend: names.length > 1 ? { top: 0, textStyle: { color: ink2 }, icon: 'circle' } : undefined,
      grid: { ...(base.grid as object), top: names.length > 1 ? 32 : 16 },
      tooltip: { ...(base.tooltip as object), trigger: 'item' },
      xAxis: { ...valueAxis, name: spec.x!, nameLocation: 'middle', nameGap: 28, axisLabel: { color: muted } },
      yAxis: valueAxis,
      series: names.map((n) => ({
        name: n, type: 'scatter', data: groups.get(n), symbolSize: 9,
        itemStyle: { borderColor: surface, borderWidth: 1.5, opacity: 0.85 },
      })),
    } }
  }

  if (spec.type === 'heatmap') {
    const xi = data.columns.findIndex((c) => c.name === spec.x)
    const si = data.columns.findIndex((c) => c.name === spec.series)
    const vi = data.columns.findIndex((c) => c.name === spec.y[0])
    const xs = [...new Set(data.rows.map((r) => String(r[xi])))]
    const ys = [...new Set(data.rows.map((r) => String(r[si])))]
    const vals = data.rows.map((r) => Number(r[vi]) || 0)
    const fx = dateFormatter(xs)
    const click: ClickMap = (p) => {
      const v = p.value as number[] | undefined
      return v ? { x: data.rows.find((r) => String(r[xi]) === xs[v[0]])?.[xi], series: ys[v[1]] } : null
    }
    return { click, option: {
      ...base,
      grid: { ...(base.grid as object), bottom: 48 },
      tooltip: { ...(base.tooltip as object), trigger: 'item' },
      xAxis: { type: 'category', data: xs.map(fx), axisLabel: { color: muted }, axisLine: { lineStyle: { color: axis } }, splitArea: { show: false } },
      yAxis: { type: 'category', data: ys, axisLabel: { color: muted }, axisLine: { show: false } },
      visualMap: {
        min: Math.min(...vals), max: Math.max(...vals), calculable: false, orient: 'horizontal', left: 'center', bottom: 0,
        itemHeight: 120, textStyle: { color: muted }, text: [fmtAxis(Math.max(...vals)), fmtAxis(Math.min(...vals))], inRange: { color: [cssVar('--seq-low'), cssVar('--seq-high')] },
        formatter: (v) => fmtAxis(v as number),
      },
      series: [{
        type: 'heatmap',
        data: data.rows.map((r) => [xs.indexOf(String(r[xi])), ys.indexOf(String(r[si])), Number(r[vi]) || 0]),
        itemStyle: { borderColor: surface, borderWidth: 2, borderRadius: 3 },
        emphasis: { itemStyle: { borderColor: cssVar('--text-primary'), borderWidth: 1 } },
      }],
    } }
  }

  // bar / hbar / stacked_bar / line / area
  const { categories, series } = pivot(spec, data)
  const fx = dateFormatter(categories)
  const labels = categories.map(fx)
  const horizontal = spec.type === 'hbar'
  const stacked = spec.type === 'stacked_bar'
  const isLine = spec.type === 'line' || spec.type === 'area'
  const multi = series.length > 1
  const catAxis = {
    type: 'category' as const,
    data: labels,
    axisLabel: { color: muted, hideOverlap: true, alignMinLabel: isLine ? 'left' as const : undefined, alignMaxLabel: isLine ? 'right' as const : undefined },
    axisLine: { lineStyle: { color: axis } },
    axisTick: { show: false },
    boundaryGap: !isLine,
  }
  const radius = horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]
  const targets = spec.targets ?? []
  const marks: object[] = [
    ...targets.map((t) => ({
      [horizontal ? 'xAxis' : 'yAxis']: t.value, name: t.label,
      lineStyle: { type: 'dashed', color: ink2, width: 1.5 },
      label: { formatter: `${t.label}: ${fmtAxis(t.value)}`, position: 'insideEndTop', color: ink2, fontSize: 11 },
    })),
    ...(spec.annotations ?? []).flatMap((a) => {
      const idx = categories.findIndex((c) => String(c) === a.x || String(c).slice(0, 10) === a.x.slice(0, 10))
      return idx < 0 ? [] : [{
        [horizontal ? 'yAxis' : 'xAxis']: labels[idx], name: a.label,
        lineStyle: { type: 'solid', color: cssVar('--series-8'), width: 1.5, opacity: 0.8 },
        label: { formatter: a.label, position: 'end', color: cssVar('--text-primary'), fontSize: 11, backgroundColor: cssVar('--surface-2'), padding: [2, 4], borderRadius: 4 },
      }]
    }),
  ]
  const targetMax = targets.length ? Math.max(...targets.map((t) => t.value)) : null
  const valueAxisWithTarget = targetMax == null ? valueAxis : { ...valueAxis, max: (v: { max: number }) => Math.max(v.max, targetMax * 1.05) }
  const click: ClickMap = (p) => ({ x: categories[p.dataIndex], series: spec.series && p.seriesName !== 'Other' ? p.seriesName : undefined })
  return { click, option: {
    ...base,
    legend: multi ? { top: 0, type: 'scroll', textStyle: { color: ink2 }, icon: isLine ? 'roundRect' : 'rect', itemWidth: 12, itemHeight: isLine ? 3 : 10 } : undefined,
    grid: { ...(base.grid as object), top: multi ? 36 : 16 },
    tooltip: {
      ...(base.tooltip as object),
      trigger: 'axis',
      axisPointer: isLine ? { type: 'line', lineStyle: { color: axis } } : { type: 'shadow', shadowStyle: { color: cssVar('--surface-3'), opacity: 0.6 } },
    },
    xAxis: horizontal ? valueAxisWithTarget : catAxis,
    yAxis: horizontal ? { ...catAxis, inverse: true } : valueAxisWithTarget,
    series: series.map((s, i) => ({
      name: s.name,
      type: isLine ? 'line' : 'bar',
      data: s.values,
      stack: stacked ? 'total' : undefined,
      barMaxWidth: 32,
      showSymbol: isLine && categories.length <= 24,
      symbolSize: 8,
      lineStyle: isLine ? { width: 2 } : undefined,
      areaStyle: spec.type === 'area' ? { opacity: multi ? 0.12 : 0.18 } : undefined,
      itemStyle: stacked
        ? { borderColor: surface, borderWidth: 1, borderRadius: i === series.length - 1 ? radius : 0 }
        : isLine ? { borderColor: surface, borderWidth: 2 } : { borderRadius: radius },
      emphasis: { focus: multi ? 'series' : 'none' },
      markLine: i === 0 && marks.length ? { symbol: ['none', 'none'], silent: true, animation: false, data: marks } : undefined,
    })),
  } }
}

function Kpi({ spec, data }: { spec: ChartSpec; data: TableData }) {
  const row = data.rows[0] ?? []
  const idx = (c: string) => data.columns.findIndex((col) => col.name === c)
  const value = row[idx(spec.y[0])] as number | null
  const prev = spec.y[1] ? (row[idx(spec.y[1])] as number | null) : null
  const fmt = valueFormatter(spec)
  // Large figures in compact form ($7.95M); the exact value stays in the native tooltip.
  const fmtBig = (v: number | null) => {
    if (typeof v !== 'number') return fmt(v)
    if (Math.abs(v) >= 1e6 && spec.value_format !== 'percent') return valueFormatter(spec, true)(v)
    if (Math.abs(v) >= 1000 && spec.value_format !== 'percent') {
      // Large figures without decimals: $36,599 instead of $36,599.00
      const opts: Intl.NumberFormatOptions = spec.value_format === 'currency'
        ? { style: 'currency', currency: spec.currency || 'USD', maximumFractionDigits: 0, minimumFractionDigits: 0 }
        : { maximumFractionDigits: 0 }
      try { return new Intl.NumberFormat('en-US', opts).format(v) } catch { return fmt(v) }
    }
    return fmt(v)
  }
  const delta = prev != null && prev !== 0 && value != null ? (value - prev) / Math.abs(prev) : null
  return (
    <div className="kpi">
      <div className="kpi-value" title={fmt(value)}>{fmtBig(value)}</div>
      {spec.targets?.[0] && typeof value === 'number' && (
        <div className="kpi-target" title={`${spec.targets[0].label}: ${fmt(spec.targets[0].value)}`}>
          <div className="kpi-target-bar"><span style={{ width: `${Math.min(100, (value / spec.targets[0].value) * 100)}%` }} /></div>
          <span className="muted">{new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 0 }).format(value / spec.targets[0].value)} de {spec.targets[0].label.toLowerCase()} ({fmtBig(spec.targets[0].value)})</span>
        </div>
      )}
      {delta != null && (
        <div className={'kpi-delta ' + (delta >= 0 ? 'up' : 'down')}>
          {delta >= 0 ? <ArrowUpRight size={16} aria-hidden /> : <ArrowDownRight size={16} aria-hidden />}
          {(delta >= 0 ? '+' : '') + new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 1 }).format(delta)}
          <span className="muted">vs. {fmtBig(prev)}</span>
        </div>
      )}
    </div>
  )
}

export default function Chart({ spec, data, height = 280, onInstance, onPointClick }: {
  spec: ChartSpec
  data: TableData
  height?: number
  onInstance?: (chart: echarts.ECharts | null) => void
  onPointClick?: (p: PointClick) => void
}) {
  const clickRef = useRef(onPointClick)
  clickRef.current = onPointClick
  const ref = useRef<HTMLDivElement>(null)
  const scheme = useColorScheme()
  const built = useMemo(
    () => (spec.type === 'kpi' || spec.type === 'table' || spec.type === 'text' ? null : buildOption(spec, data)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [spec, data, scheme],
  )

  useEffect(() => {
    if (!ref.current || !built) return
    const chart = echarts.init(ref.current, undefined, { renderer: 'svg' })
    chart.setOption(built.option)
    onInstance?.(chart)
    if (built.click) {
      chart.on('click', (params) => {
        const hit = built.click!(params as unknown as Parameters<ClickMap>[0])
        if (hit && clickRef.current) clickRef.current(hit)
      })
    }
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(ref.current)
    return () => {
      ro.disconnect()
      onInstance?.(null)
      chart.dispose()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [built])

  if (spec.type === 'kpi') return <Kpi spec={spec} data={data} />
  if (spec.type === 'table') return <DataTable data={data} maxHeight={height + 40} />
  return <div ref={ref} className={'chart-canvas' + (onPointClick && built?.click ? ' clickable' : '')} style={{ height }} role="img" aria-label={spec.title} />
}

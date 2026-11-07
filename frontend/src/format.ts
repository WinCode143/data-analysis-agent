import type { ChartSpec } from './api'

const LOCALE = 'en-US'

export function isDateLike(v: unknown): boolean {
  return typeof v === 'string' && /^\d{4}-\d{2}-\d{2}(T[\d:.]+)?$/.test(v)
}

/** Readable label for dates: if they all fall on day 1 they're shown as months. */
export function dateFormatter(values: unknown[]): (v: unknown) => string {
  const dates = values.filter(isDateLike) as string[]
  if (dates.length === 0 || dates.length < values.filter((v) => v != null).length) return (v) => (v == null ? '—' : String(v))
  const monthly = dates.every((d) => d.slice(8, 10) === '01')
  const yearly = monthly && dates.every((d) => d.slice(5, 7) === '01')
  const fmt = new Intl.DateTimeFormat(LOCALE, yearly ? { year: 'numeric' } : monthly ? { month: 'short', year: 'numeric' } : { day: 'numeric', month: 'short', year: 'numeric' })
  return (v) => (isDateLike(v) ? fmt.format(new Date((v as string).slice(0, 10) + 'T00:00:00')) : String(v ?? '—'))
}

export function valueFormatter(spec: Pick<ChartSpec, 'value_format' | 'currency'>, compact = false): (v: unknown) => string {
  const base: Intl.NumberFormatOptions = compact ? { notation: 'compact', maximumFractionDigits: 1 } : {}
  let opts: Intl.NumberFormatOptions
  switch (spec.value_format) {
    case 'currency':
      opts = { style: 'currency', currency: spec.currency || 'USD', maximumFractionDigits: 2, ...base }
      break
    case 'percent':
      opts = { style: 'percent', maximumFractionDigits: 1 }
      break
    case 'integer':
      opts = { maximumFractionDigits: 0, ...base }
      break
    default:
      opts = { maximumFractionDigits: 2, ...base }
  }
  let nf: Intl.NumberFormat
  try {
    nf = new Intl.NumberFormat(LOCALE, opts)
  } catch {
    nf = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 2 })
  }
  return (v) => (typeof v === 'number' ? nf.format(v) : v == null ? '—' : String(v))
}

export function cellText(v: unknown): string {
  if (v == null) return '—'
  if (typeof v === 'number') return new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 2 }).format(v)
  if (isDateLike(v)) return (v as string).replace('T00:00:00', '')
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

/** Relative change: 0.291 → "+29.1%". */
export function pctChange(v: number): string {
  return (v >= 0 ? '+' : '') + new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 1 }).format(v)
}

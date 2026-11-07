import type { TableData } from '../api'
import { cellText } from '../format'

export default function DataTable({ data, maxHeight = 360, limit = 500, sort }: { data: TableData; maxHeight?: number; limit?: number; sort?: { col: number; desc: boolean } | null }) {
  const rows = data.rows.slice(0, limit)
  return (
    <div className="table-wrap" style={{ maxHeight }}>
      <table className="data">
        <thead>
          <tr>{data.columns.map((c, i) => <th key={c.name} title={c.type} aria-sort={sort?.col === i ? (sort.desc ? 'descending' : 'ascending') : undefined}>{c.name}{sort?.col === i && (sort.desc ? ' ▼' : ' ▲')}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((v, j) => (
                <td key={j} className={typeof v === 'number' ? 'num' : undefined} title={cellText(v)}>{cellText(v)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {data.rows.length > limit && <div className="muted" style={{ padding: '6px 10px', fontSize: 12 }}>Showing {limit} of {data.rows.length} rows</div>}
    </div>
  )
}

import { Globe, Loader2, Sheet } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api'

const REFRESH: [number | null, string][] = [[null, 'No automatic reload'], [15, 'Every 15 minutes'], [60, 'Every hour'], [360, 'Every 6 hours'], [1440, 'Every day']]

export default function UrlSourceForm({ onDone }: { onDone: (msg: string) => void }) {
  const [url, setUrl] = useState('')
  const [name, setName] = useState('')
  const [refresh, setRefresh] = useState<number | null>(60)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const isSheet = url.includes('docs.google.com/spreadsheets')
  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const r = await api.addUrlSource(name || 'sheet', url, refresh)
      onDone(`Imported: ${r.tables.join(', ')}`)
      setUrl('')
      setName('')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="card" style={{ padding: 14 }}>
      <p className="section-title row">{isSheet ? <Sheet size={15} /> : <Globe size={15} />} Google Sheets or URL</p>
      <div className="stack" style={{ gap: 8 }}>
        <input className="input" placeholder="https://docs.google.com/spreadsheets/d/… or https://…/data.csv" value={url} onChange={(e) => setUrl(e.target.value)} />
        <div className="row">
          <input className="input" placeholder="Name (e.g. targets)" value={name} onChange={(e) => setName(e.target.value)} />
          <select className="select" style={{ width: 'auto' }} value={refresh ?? ''} onChange={(e) => setRefresh(e.target.value ? Number(e.target.value) : null)}>
            {REFRESH.map(([v, l]) => <option key={l} value={v ?? ''}>{l}</option>)}
          </select>
        </div>
        {isSheet && <p className="hint" style={{ margin: 0 }}>The sheet must be shared as “Anyone with the link (viewer)”. The tab in the link (gid) gets imported.</p>}
        <button className="btn" disabled={!url || busy} onClick={submit}>{busy ? <Loader2 size={14} className="spin" /> : <Globe size={14} />} Import</button>
        {error && <div className="error-box">{error}</div>}
      </div>
    </div>
  )
}

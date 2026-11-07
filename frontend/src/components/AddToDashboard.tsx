import { LayoutDashboard, Plus } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, type DashboardSummary } from '../api'
import { notifyDashboardsChanged, toast } from '../events'

export default function AddToDashboard({ chartId, onClose }: { chartId: string; onClose: () => void }) {
  const [dashboards, setDashboards] = useState<DashboardSummary[]>([])
  const [title, setTitle] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.dashboards().then(setDashboards).catch((e) => toast(e.message, 'error'))
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) onClose() }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [onClose])

  const finish = (name: string) => {
    toast(`Added to “${name}”`, 'success')
    notifyDashboardsChanged()
    onClose()
  }
  const add = async (d: DashboardSummary) => {
    try {
      await api.patchDashboard(d.id, { add_chart_ids: [chartId] })
      finish(d.title)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  const create = async () => {
    if (!title.trim()) return
    try {
      await api.createDashboard(title.trim(), [chartId])
      finish(title.trim())
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }

  return (
    <div className="menu" ref={ref} style={{ top: 44, right: 14, width: 270 }}>
      <div className="menu-label">Add to dashboard</div>
      {dashboards.map((d) => (
        <button key={d.id} onClick={() => add(d)}><LayoutDashboard size={15} /> {d.title}</button>
      ))}
      {dashboards.length > 0 && <hr />}
      <div className="row" style={{ padding: 4 }}>
        <input className="input" autoFocus placeholder="New dashboard…" value={title} onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && create()} />
        <button className="btn primary small" title="Create" onClick={create} disabled={!title.trim()}><Plus size={15} /></button>
      </div>
    </div>
  )
}

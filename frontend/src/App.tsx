import { Bell, CalendarClock, Code2, Database, Home, LayoutDashboard, Menu as MenuIcon, MessageSquare, Plus, Settings2, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type ConversationSummary, type DashboardSummary, type Status } from './api'
import ProviderLogo from './components/ProviderLogo'
import Toasts from './components/Toasts'
import { onSettingsChanged, subscribe, toast } from './events'
import { go, useRoute } from './router'
import ChatView from './views/ChatView'
import DashboardView from './views/DashboardView'
import DataView from './views/DataView'
import HomeView from './views/HomeView'
import SettingsView from './views/SettingsView'
import AutomationsView from './views/AutomationsView'
import InboxView from './views/InboxView'
import SqlView from './views/SqlView'

function groupByDate(convs: ConversationSummary[]): [string, ConversationSummary[]][] {
  const today = new Date().toDateString()
  const weekAgo = Date.now() - 7 * 86400_000
  const groups: Record<string, ConversationSummary[]> = { Today: [], 'Last 7 days': [], Earlier: [] }
  for (const c of convs) {
    const d = new Date(c.updated_at)
    const key = d.toDateString() === today ? 'Today' : d.getTime() > weekAgo ? 'Last 7 days' : 'Earlier'
    groups[key].push(c)
  }
  return Object.entries(groups).filter(([, v]) => v.length)
}

function Logo() {
  return (
    <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden>
      <defs><linearGradient id="lg" x1="0" x2="1" y1="0" y2="1"><stop offset="0" stopColor="#5598e7" /><stop offset="1" stopColor="#1c5cab" /></linearGradient></defs>
      <rect width="32" height="32" rx="9" fill="url(#lg)" />
      <rect x="8" y="17" width="4" height="8" rx="1.5" fill="#fff" opacity=".75" />
      <rect x="14" y="11" width="4" height="14" rx="1.5" fill="#fff" opacity=".9" />
      <rect x="20" y="7" width="4" height="18" rx="1.5" fill="#fff" />
    </svg>
  )
}

function Sidebar({ active, open, onNavigate }: { active: string; open: boolean; onNavigate: () => void }) {
  const [dashboards, setDashboards] = useState<DashboardSummary[]>([])
  const [convs, setConvs] = useState<ConversationSummary[]>([])
  const [status, setStatus] = useState<Status | null>(null)
  const [unread, setUnread] = useState(0)

  const loadDash = useCallback(() => { api.dashboards().then(setDashboards).catch(() => {}) }, [])
  const loadConvs = useCallback(() => { api.conversations().then(setConvs).catch(() => {}) }, [])
  const loadStatus = useCallback(() => { api.status().then(setStatus).catch(() => {}) }, [])

  useEffect(() => {
    loadDash()
    loadConvs()
    loadStatus()
    const loadUnread = () => { api.notifications().then((r) => setUnread(r.unread)).catch(() => {}) }
    loadUnread()
    const poll = setInterval(() => { loadUnread(); loadConvs() }, 30000)
    const subs = [subscribe('dashboards', loadDash), subscribe('conversations', loadConvs), subscribe('data', loadStatus),
      onSettingsChanged(loadStatus), subscribe('automations', loadUnread)]
    return () => { subs.forEach((u) => u()); clearInterval(poll) }
  }, [loadDash, loadConvs, loadStatus])

  const nav = (hash: string) => { go(hash); onNavigate() }
  const removeConv = async (id: string) => {
    await api.deleteConversation(id)
    loadConvs()
    toast('Conversation deleted')
    if (active === `chat:${id}`) go('#/chat')
  }

  return (
    <aside className={'sidebar' + (open ? ' open' : '')}>
      <button className="brand" onClick={() => nav('#/')}>
        <Logo />
        <div>Insight Suite<small>AI data analysis</small></div>
      </button>
      <nav className="nav">
        <button className="btn primary" style={{ marginBottom: 8 }} onClick={() => nav('#/chat')}><Plus size={15} /> New analysis</button>
        <button className={'nav-item' + (active === 'home' ? ' active' : '')} onClick={() => nav('#/')}><Home size={16} /> <span>Home</span></button>
        <button className={'nav-item' + (active === 'data' ? ' active' : '')} onClick={() => nav('#/data')}><Database size={16} /> <span>Data</span></button>
        <button className={'nav-item' + (active === 'sql' ? ' active' : '')} onClick={() => nav('#/sql')}><Code2 size={16} /> <span>SQL editor</span></button>
        <button className={'nav-item' + (active === 'automations' ? ' active' : '')} onClick={() => nav('#/automations')}><CalendarClock size={16} /> <span>Automations</span></button>
        <button className={'nav-item' + (active === 'inbox' ? ' active' : '')} onClick={() => nav('#/inbox')}>
          <Bell size={16} /> <span>Notifications</span>{unread > 0 && <span className="count-badge">{unread}</span>}
        </button>
        <button className={'nav-item' + (active === 'settings' ? ' active' : '')} onClick={() => nav('#/settings')}><Settings2 size={16} /> <span>Settings</span></button>
      </nav>
      <div className="nav-list">
        <div className="nav-section">Dashboards</div>
        {dashboards.length === 0 && <div className="muted" style={{ fontSize: 12.5, padding: '2px 10px' }}>None yet</div>}
        {dashboards.map((d) => (
          <button key={d.id} className={'nav-item' + (active === `dashboard:${d.id}` ? ' active' : '')} onClick={() => nav(`#/dashboard/${d.id}`)}>
            <LayoutDashboard size={15} /> <span>{d.title}</span>
          </button>
        ))}
        {groupByDate(convs).map(([label, list]) => (
          <div key={label}>
            <div className="nav-section">{label}</div>
            {list.map((c) => (
              <div key={c.id} className="nav-row">
                <button className={'nav-item' + (active === `chat:${c.id}` ? ' active' : '')} onClick={() => nav(`#/chat/${c.id}`)} title={`${c.title} · ${c.model}`}>
                  <MessageSquare size={15} /> <span>{c.title}</span>
                </button>
                <button className="icon-btn" title="Delete conversation" onClick={() => removeConv(c.id)}><Trash2 size={13} /></button>
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="sidebar-footer">
        <button className="model-chip" onClick={() => nav('#/settings')} title="Default provider and model">
          {status && <ProviderLogo id={status.provider} size={20} />}
          <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {status ? status.model || 'No model' : '…'}
          </span>
          <span className={'dot' + (status?.configured ? '' : ' off')} title={status?.configured ? 'Configured' : 'Not configured'} />
        </button>
      </div>
    </aside>
  )
}

export default function App() {
  const route = useRoute()
  const [drawer, setDrawer] = useState(false)
  const active = route.view === 'chat' || route.view === 'dashboard' ? `${route.view}:${route.id ?? ''}` : route.view
  return (
    <div className="app">
      <Sidebar active={active} open={drawer} onNavigate={() => setDrawer(false)} />
      {drawer && <div className="scrim" onClick={() => setDrawer(false)} />}
      <main className="main">
        <div className="topbar">
          <button className="icon-btn" onClick={() => setDrawer(true)} aria-label="Open menu"><MenuIcon size={20} /></button>
          Insight Suite
        </div>
        {route.view === 'home' && <HomeView />}
        {route.view === 'chat' && <ChatView key={route.id ?? `new-${route.nonce}`} conversationId={route.id} />}
        {route.view === 'dashboard' && <DashboardView key={route.id} id={route.id} />}
        {route.view === 'data' && <DataView />}
        {route.view === 'settings' && <SettingsView />}
        {route.view === 'automations' && <AutomationsView />}
        {route.view === 'inbox' && <InboxView />}
        {route.view === 'sql' && <SqlView />}
      </main>
      <Toasts />
    </div>
  )
}

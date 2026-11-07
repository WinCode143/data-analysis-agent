import { Loader2, Mail, MessageSquare, Save, Send, Webhook } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type Channel } from '../api'
import { toast } from '../events'

export default function ChannelsSettings() {
  const [cfg, setCfg] = useState<Record<string, string>>({})
  const [available, setAvailable] = useState<Channel[]>([])
  const [testTo, setTestTo] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  useEffect(() => { api.channels().then((r) => { setCfg(r.config); setAvailable(r.available) }).catch(() => {}) }, [])
  const set = (k: string, v: string) => setCfg({ ...cfg, [k]: v })

  const save = async () => {
    setBusy('save')
    try {
      const r = await api.saveChannels(cfg)
      setCfg(r.config)
      setAvailable(r.available)
      toast('Channels saved', 'success')
    } finally {
      setBusy(null)
    }
  }
  const test = async (ch: Channel) => {
    setBusy(ch)
    try {
      await api.saveChannels(cfg)
      const r = await api.testChannel(ch, ch === 'email' ? [testTo] : undefined)
      toast(r.message, r.ok ? 'success' : 'error')
    } finally {
      setBusy(null)
    }
  }
  const field = (k: string, label: string, props: Record<string, unknown> = {}) => (
    <div style={{ flex: 1, minWidth: 140 }}><label className="label">{label}</label><input className="input" value={cfg[k] ?? ''} onChange={(e) => set(k, e.target.value)} {...props} /></div>
  )
  const status = (ch: Channel) => available.includes(ch) ? <span className="badge good">Configured</span> : <span className="badge">Not configured</span>

  return (
    <section className="card" style={{ padding: 18 }}>
      <p className="section-title">Notifications</p>
      <p className="hint" style={{ marginTop: -6 }}>Channels for scheduled reports and alerts. The app's Notifications inbox always works. Passwords and URLs are stored encrypted.</p>
      <div className="stack" style={{ gap: 18, marginTop: 14 }}>
        <div>
          <div className="row" style={{ marginBottom: 8 }}><Mail size={15} /> <strong style={{ fontSize: 13.5 }}>Email (SMTP)</strong> {status('email')}</div>
          <div className="row" style={{ flexWrap: 'wrap' }}>
            {field('smtp_host', 'Server', { placeholder: 'smtp.gmail.com' })}
            {field('smtp_port', 'Port', { placeholder: '587', style: { width: 90 } })}
            <div><label className="label">Security</label>
              <select className="select" value={cfg.smtp_security ?? 'starttls'} onChange={(e) => set('smtp_security', e.target.value)}>
                <option value="starttls">STARTTLS (587)</option><option value="ssl">SSL (465)</option><option value="none">None</option>
              </select>
            </div>
          </div>
          <div className="row" style={{ flexWrap: 'wrap', marginTop: 8 }}>
            {field('smtp_user', 'User', { autoComplete: 'off' })}
            {field('smtp_password', 'Password', { type: 'password', autoComplete: 'new-password', placeholder: 'app password' })}
            {field('smtp_from', 'Sender', { placeholder: 'Insight <me@company.com>' })}
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <input className="input" style={{ maxWidth: 280 }} placeholder="Send a test to…" value={testTo} onChange={(e) => setTestTo(e.target.value)} />
            <button className="btn small" disabled={!testTo || busy !== null} onClick={() => test('email')}>{busy === 'email' ? <Loader2 size={13} className="spin" /> : <Send size={13} />} Test</button>
          </div>
          <p className="hint">With Gmail/Google Workspace, use an “app password” (not your normal password).</p>
        </div>
        <div>
          <div className="row" style={{ marginBottom: 8 }}><MessageSquare size={15} /> <strong style={{ fontSize: 13.5 }}>Slack</strong> {status('slack')}</div>
          <div className="row">
            {field('slack_webhook', 'Incoming webhook', { placeholder: 'https://hooks.slack.com/services/…' })}
            <button className="btn small" style={{ alignSelf: 'flex-end' }} disabled={!cfg.slack_webhook || busy !== null} onClick={() => test('slack')}>{busy === 'slack' ? <Loader2 size={13} className="spin" /> : <Send size={13} />} Test</button>
          </div>
        </div>
        <div>
          <div className="row" style={{ marginBottom: 8 }}><Webhook size={15} /> <strong style={{ fontSize: 13.5 }}>Generic webhook</strong> {status('webhook')}</div>
          <div className="row">
            {field('webhook_url', 'URL (receives JSON: title, text, level, link)', { placeholder: 'Teams, n8n, Zapier, Make…' })}
            <button className="btn small" style={{ alignSelf: 'flex-end' }} disabled={!cfg.webhook_url || busy !== null} onClick={() => test('webhook')}>{busy === 'webhook' ? <Loader2 size={13} className="spin" /> : <Send size={13} />} Test</button>
          </div>
        </div>
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button className="btn primary" onClick={save} disabled={busy !== null}>{busy === 'save' ? <Loader2 size={14} className="spin" /> : <Save size={14} />} Save channels</button>
        </div>
      </div>
    </section>
  )
}

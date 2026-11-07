import { CheckCircle2, ExternalLink, Loader2, Monitor, Moon, PlugZap, RefreshCw, Save, Star, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type AISettings, type ProviderInfo } from '../api'
import ChannelsSettings from '../components/ChannelsSettings'
import ProviderLogo from '../components/ProviderLogo'
import { notifySettingsChanged, toast } from '../events'
import { useThemePref, type ThemePref } from '../theme'

const EFFORT_LABELS: Record<string, string> = { low: 'Fast', medium: 'Balanced', high: 'Deep', xhigh: 'Very deep', max: 'Maximum' }

function ProviderCard({ p, isDefault, onSaved }: { p: ProviderInfo; isDefault: boolean; onSaved: (s: AISettings) => void }) {
  const [key, setKey] = useState('')
  const [model, setModel] = useState(p.model)
  const [baseUrl, setBaseUrl] = useState(p.base_url)
  const [models, setModels] = useState<string[]>(p.suggested_models)
  const [loadingModels, setLoadingModels] = useState(false)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const dirty = key !== '' || model !== p.model || baseUrl !== p.base_url

  const save = async (extra: Record<string, unknown> = {}) => {
    setSaving(true)
    try {
      const body: Record<string, string> = { model }
      if (key) body.api_key = key
      if (p.editable_base_url) body.base_url = baseUrl
      const s = await api.saveSettings({ providers: { [p.id]: body }, ...extra })
      setKey('')
      onSaved(s)
      notifySettingsChanged()
      return s
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setSaving(false)
    }
  }

  const loadModels = async () => {
    setLoadingModels(true)
    try {
      if (dirty) await save()
      setModels(await api.providerModels(p.id))
    } finally {
      setLoadingModels(false)
    }
  }

  const test = async () => {
    setTesting(true)
    try {
      if (dirty) await save()
      const r = await api.testProvider(p.id, model)
      toast(r.message, r.ok ? (r.tools ? 'success' : 'info') : 'error')
    } catch (e) {
      toast((e as Error).message, 'error')
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className={'card provider-card' + (isDefault ? ' default' : '')}>
      <div className="provider-head">
        <ProviderLogo id={p.id} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <strong>{p.name}</strong>
          <div className="row" style={{ gap: 6, marginTop: 2 }}>
            {p.configured ? <span className="badge good"><CheckCircle2 size={11} /> Ready</span> : <span className="badge">Not configured</span>}
            {isDefault && <span className="badge accent"><Star size={11} /> Default</span>}
          </div>
        </div>
        {p.docs_url && <a className="icon-btn" href={p.docs_url} target="_blank" rel="noreferrer" title="Get a key / see models"><ExternalLink size={15} /></a>}
      </div>
      {p.needs_key && (
        <div>
          <label className="label">API key</label>
          <input className="input" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)}
            placeholder={p.key_hint ? `Saved ${p.key_hint} (${p.key_source})` : 'Paste your key'} />
        </div>
      )}
      {p.editable_base_url && (
        <div>
          <label className="label">Base URL</label>
          <input className="input" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://…/v1" />
        </div>
      )}
      <div>
        <label className="label">Model</label>
        <div className="row">
          <input className="input" list={`models-${p.id}`} value={model} onChange={(e) => setModel(e.target.value)} placeholder="model id" />
          <button className="icon-btn" title="Load available models" onClick={loadModels} disabled={loadingModels}>
            <RefreshCw size={15} className={loadingModels ? 'spin' : undefined} />
          </button>
        </div>
        <datalist id={`models-${p.id}`}>{models.map((m) => <option key={m} value={m} />)}</datalist>
        {models.length > 0 && <p className="hint">{models.length} models available · type or pick from the list</p>}
      </div>
      <div className="row" style={{ marginTop: 'auto', flexWrap: 'wrap' }}>
        <button className="btn small" onClick={() => save()} disabled={!dirty || saving}><Save size={13} /> Save</button>
        <button className="btn small" onClick={test} disabled={testing}>{testing ? <Loader2 size={13} className="spin" /> : <PlugZap size={13} />} Test</button>
        {!isDefault && <button className="btn small ghost" onClick={() => save({ provider: p.id })}><Star size={13} /> Make default</button>}
      </div>
    </div>
  )
}

export default function SettingsView() {
  const [settings, setSettings] = useState<AISettings | null>(null)
  const [theme, setTheme] = useThemePref()
  useEffect(() => { api.settings().then(setSettings).catch((e) => toast(e.message, 'error')) }, [])

  const setEffort = async (effort: string) => {
    setSettings(await api.saveSettings({ effort }))
    notifySettingsChanged()
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Settings</h1>
          <p>AI providers, analysis depth and appearance.</p>
        </div>
      </div>
      <div className="page-body">
        <div className="container stack" style={{ gap: 28 }}>
          <section>
            <p className="section-title">AI providers</p>
            <p className="hint" style={{ marginTop: -6, marginBottom: 14 }}>
              Keys are only stored on this machine (workspace/settings.json, encrypted) or read from the .env file. Each conversation keeps the model it started with.
              Local models (Ollama, LM Studio) need tool calling support to work as analysts.
            </p>
            {!settings ? <div className="skeleton" style={{ height: 240 }} /> : (
              <div className="providers">
                {settings.providers.map((p) => (
                  <ProviderCard key={p.id + p.model + p.key_hint + p.base_url} p={p} isDefault={settings.provider === p.id} onSaved={setSettings} />
                ))}
              </div>
            )}
          </section>
          {settings && (
            <section className="card" style={{ padding: 18 }}>
              <p className="section-title">Reasoning depth</p>
              <div className="segmented">
                {settings.efforts.map((e) => (
                  <button key={e} className={settings.effort === e ? 'on' : ''} onClick={() => setEffort(e)}>{EFFORT_LABELS[e] ?? e}</button>
                ))}
              </div>
              <p className="hint">More depth = more careful analysis, but slower and more expensive. Providers without this control ignore it.</p>
            </section>
          )}
          <ChannelsSettings />
          <section className="card" style={{ padding: 18 }}>
            <p className="section-title">Appearance</p>
            <div className="segmented">
              {([['system', 'System', Monitor], ['light', 'Light', Sun], ['dark', 'Dark', Moon]] as [ThemePref, string, typeof Sun][]).map(([v, label, Icon]) => (
                <button key={v} className={theme === v ? 'on' : ''} onClick={() => setTheme(v)}><Icon size={13} /> {label}</button>
              ))}
            </div>
          </section>
        </div>
      </div>
    </>
  )
}

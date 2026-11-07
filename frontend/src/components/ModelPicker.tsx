import { Check, ChevronDown, Settings2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, type AISettings } from '../api'
import { notifySettingsChanged, onSettingsChanged, toast } from '../events'
import { go } from '../router'
import Menu from './Menu'
import ProviderLogo from './ProviderLogo'

type Choice = { provider: string; model: string }

/** Provider/model picker. Picking one makes it the default; in an existing conversation, the conversation
 * also continues with the chosen model (`onChange`). */
export default function ModelPicker({ value, onChange }: { value?: Choice | null; onChange?: (c: Choice) => void }) {
  const [settings, setSettings] = useState<AISettings | null>(null)
  useEffect(() => {
    const load = () => api.settings().then(setSettings).catch(() => {})
    load()
    return onSettingsChanged(load)
  }, [])
  if (!settings) return null

  const current = value ?? { provider: settings.provider, model: settings.providers.find((p) => p.id === settings.provider)?.model ?? '' }
  const currentName = settings.providers.find((p) => p.id === current.provider)?.name ?? current.provider

  const choose = async (provider: string, model: string) => {
    onChange?.({ provider, model })
    try {
      setSettings(await api.saveSettings({ provider, providers: { [provider]: { model } } }))
      notifySettingsChanged()
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  const configured = settings.providers.filter((p) => p.configured)

  return (
    <Menu align="left" trigger={(_, toggle) => (
      <button className="picker" onClick={toggle} title={`${currentName} · ${current.model}`}>
        <ProviderLogo id={current.provider} size={16} /> <span>{current.model || 'Choose a model'}</span> <ChevronDown size={13} />
      </button>
    )}>
      {(close) => (
        <div style={{ maxHeight: 380, overflow: 'auto', minWidth: 260 }}>
          {configured.length === 0 && <div className="menu-label">No providers configured</div>}
          {configured.map((p) => {
            const models = [...new Set([p.model, ...p.suggested_models].filter(Boolean))]
            return (
              <div key={p.id}>
                <div className="menu-label row" style={{ gap: 6 }}><ProviderLogo id={p.id} size={14} /> {p.name}</div>
                {models.map((m) => {
                  const on = current.provider === p.id && current.model === m
                  return (
                    <button key={m} className={on ? 'on' : ''} onClick={() => { choose(p.id, m); close() }}>
                      <span style={{ width: 15, display: 'inline-flex' }}>{on && <Check size={14} />}</span> {m}
                    </button>
                  )
                })}
              </div>
            )
          })}
          <hr />
          <button onClick={() => { go('#/settings'); close() }}><Settings2 size={15} /> Set up providers…</button>
        </div>
      )}
    </Menu>
  )
}

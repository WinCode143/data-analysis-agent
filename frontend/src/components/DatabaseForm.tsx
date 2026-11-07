import { CheckCircle2, ChevronRight, Database, HelpCircle, Link2, Loader2, PlugZap } from 'lucide-react'
import { useState } from 'react'
import { api, type DbEngine, type SchemaInfo } from '../api'

const ENGINES: { id: DbEngine; label: string; port: string }[] = [
  { id: 'postgres', label: 'PostgreSQL', port: '5432' },
  { id: 'mysql', label: 'MySQL / MariaDB', port: '3306' },
  { id: 'sqlite', label: 'SQLite', port: '' },
]

const PLACEHOLDER: Record<DbEngine, string> = {
  postgres: 'postgresql://user:password@host:5432/db?sslmode=require',
  mysql: 'mysql://user:password@host:3306/db',
  sqlite: '/path/to/file.sqlite',
}

const READONLY_SQL: Record<DbEngine, string> = {
  postgres: `CREATE USER insight WITH PASSWORD '…';
GRANT CONNECT ON DATABASE my_db TO insight;
GRANT USAGE ON SCHEMA public TO insight;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO insight;`,
  mysql: `CREATE USER 'insight'@'%' IDENTIFIED BY '…';
GRANT SELECT ON my_db.* TO 'insight'@'%';`,
  sqlite: '',
}

/** Schemas checked by default: the most likely "user" ones. */
function defaultSchemas(engine: DbEngine, schemas: SchemaInfo[], database: string): Set<string> {
  const names = schemas.map((s) => s.schema)
  if (engine === 'postgres' && names.includes('public')) return new Set(['public'])
  if (engine === 'mysql' && names.includes(database)) return new Set([database])
  return new Set(names)
}

export default function DatabaseForm({ onDone }: { onDone: (msg: string) => void }) {
  const [engine, setEngine] = useState<DbEngine>('postgres')
  const [mode, setMode] = useState<'fields' | 'dsn'>('fields')
  const [name, setName] = useState('')
  const [f, setF] = useState({ host: 'localhost', port: '', database: '', user: '', password: '', ssl: false, path: '' })
  const [dsn, setDsn] = useState('')
  const [schemas, setSchemas] = useState<SchemaInfo[] | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState<'test' | 'connect' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [help, setHelp] = useState(false)

  const conn = () => {
    const fields = engine === 'sqlite' ? { path: f.path } : { ...f, port: f.port ? Number(f.port) : undefined }
    return mode === 'dsn' ? { engine, dsn } : { engine, fields }
  }
  const reset = () => { setSchemas(null); setError(null) }
  const set = (k: keyof typeof f, v: string | boolean) => { setF({ ...f, [k]: v }); reset() }

  const test = async () => {
    setBusy('test')
    setError(null)
    try {
      const r = await api.inspectDb(conn())
      setSchemas(r.schemas)
      setSelected(defaultSchemas(engine, r.schemas, f.database))
      if (!name) setName(f.database || (engine === 'sqlite' ? f.path.split('/').pop()?.split('.')[0] ?? '' : '') || engine)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const connect = async () => {
    setBusy('connect')
    try {
      const all = schemas && selected.size === schemas.length
      const r = await api.addDb({ ...conn(), name, schemas: all ? null : [...selected] })
      onDone(`Connected “${name}”: ${r.models_added.length} ${r.models_added.length === 1 ? 'table' : 'tables'}`)
      setSchemas(null)
      setName('')
      setF({ ...f, password: '' })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const tables = schemas?.filter((s) => selected.has(s.schema)).reduce((a, s) => a + s.tables, 0) ?? 0

  return (
    <div className="card" style={{ padding: 14 }}>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <p className="section-title" style={{ margin: 0 }}><Database size={15} /> Connect a database</p>
        <button className="icon-btn" title="What do I need?" onClick={() => setHelp((v) => !v)}><HelpCircle size={15} /></button>
      </div>
      <div className="segmented" style={{ margin: '12px 0 10px', width: '100%' }}>
        {ENGINES.map((e) => (
          <button key={e.id} className={engine === e.id ? 'on' : ''} style={{ flex: 1, justifyContent: 'center' }}
            onClick={() => { setEngine(e.id); reset() }}>{e.label}</button>
        ))}
      </div>
      {help && (
        <div className="notice" style={{ marginBottom: 10 }}>
          {engine === 'sqlite' ? (
            <>Give the path of the file on this machine, or drop the <code>.sqlite</code>/<code>.db</code> file on the upload area above.</>
          ) : (
            <>
              This machine needs to reach the server (in the cloud, add your IP to the allow list and turn on SSL) and a user, ideally read-only:
              <pre className="sql-view" style={{ margin: '6px 0 0', fontSize: 11.5 }}>{READONLY_SQL[engine]}</pre>
              The suite always connects read-only and stores the password encrypted.
            </>
          )}
        </div>
      )}
      <div className="stack" style={{ gap: 8 }}>
        {engine !== 'sqlite' && (
          <div className="segmented" style={{ alignSelf: 'flex-start' }}>
            <button className={mode === 'fields' ? 'on' : ''} onClick={() => { setMode('fields'); reset() }}>Fields</button>
            <button className={mode === 'dsn' ? 'on' : ''} onClick={() => { setMode('dsn'); reset() }}>Connection string</button>
          </div>
        )}
        {engine === 'sqlite' ? (
          <div><label className="label">File path</label><input className="input" value={f.path} onChange={(e) => set('path', e.target.value)} placeholder={PLACEHOLDER.sqlite} /></div>
        ) : mode === 'dsn' ? (
          <div><label className="label">Connection string</label><input className="input" value={dsn} onChange={(e) => { setDsn(e.target.value); reset() }} placeholder={PLACEHOLDER[engine]} /></div>
        ) : (
          <>
            <div className="row">
              <div style={{ flex: 3 }}><label className="label">Host</label><input className="input" value={f.host} onChange={(e) => set('host', e.target.value)} /></div>
              <div style={{ flex: 1.3 }}><label className="label">Port</label><input className="input" value={f.port} onChange={(e) => set('port', e.target.value.replace(/\D/g, ''))} placeholder={ENGINES.find((x) => x.id === engine)?.port} /></div>
            </div>
            <div><label className="label">Database</label><input className="input" value={f.database} onChange={(e) => set('database', e.target.value)} /></div>
            <div className="row">
              <div style={{ flex: 1 }}><label className="label">User</label><input className="input" value={f.user} onChange={(e) => set('user', e.target.value)} autoComplete="off" /></div>
              <div style={{ flex: 1 }}><label className="label">Password</label><input className="input" type="password" value={f.password} onChange={(e) => set('password', e.target.value)} autoComplete="new-password" /></div>
            </div>
            <label className="row" style={{ fontSize: 13, cursor: 'pointer' }}>
              <input type="checkbox" checked={f.ssl} onChange={(e) => set('ssl', e.target.checked)} /> Require SSL (cloud databases)
            </label>
          </>
        )}

        {!schemas ? (
          <button className="btn" disabled={busy !== null} onClick={test}>
            {busy === 'test' ? <Loader2 size={14} className="spin" /> : <PlugZap size={14} />} Test connection
          </button>
        ) : (
          <div className="fade-up stack" style={{ gap: 8 }}>
            <div className="row" style={{ color: 'var(--good)', fontSize: 13, fontWeight: 600 }}><CheckCircle2 size={15} /> Connection OK</div>
            <div>
              <label className="label">Schemas to include</label>
              <div className="values-list card" style={{ padding: 4, boxShadow: 'none' }}>
                {schemas.length === 0 && <div className="muted" style={{ padding: 8, fontSize: 13 }}>This user can't see any tables.</div>}
                {schemas.map((s) => (
                  <label key={s.schema}>
                    <input type="checkbox" checked={selected.has(s.schema)} onChange={() => setSelected((cur) => {
                      const n = new Set(cur); if (n.has(s.schema)) n.delete(s.schema); else n.add(s.schema); return n
                    })} />
                    <span style={{ flex: 1 }}>{s.schema}</span><span className="pill">{s.tables} {s.tables === 1 ? 'table' : 'tables'}</span>
                  </label>
                ))}
              </div>
              {tables > 150 && <p className="hint" style={{ color: 'var(--critical)' }}>{tables} tables: the AI works better with fewer. Consider picking only the schemas you need.</p>}
            </div>
            <div><label className="label">Connection name</label><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="sales_prod" /></div>
            <button className="btn primary" disabled={!name || !selected.size || busy !== null} onClick={connect}>
              {busy === 'connect' ? <Loader2 size={14} className="spin" /> : <Link2 size={14} />} Connect {tables} {tables === 1 ? 'table' : 'tables'} <ChevronRight size={14} />
            </button>
          </div>
        )}
        {error && <div className="error-box">{error}</div>}
      </div>
    </div>
  )
}

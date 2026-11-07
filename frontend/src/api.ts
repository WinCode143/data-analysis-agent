export type ChartType = 'bar' | 'hbar' | 'stacked_bar' | 'line' | 'area' | 'scatter' | 'heatmap' | 'kpi' | 'table' | 'text'
export interface Target { value: number; label: string }
export interface Annotation { x: string; label: string }

export interface ChartSpec {
  id: string
  title: string
  subtitle?: string | null
  type: ChartType
  sql: string
  x?: string | null
  y: string[]
  series?: string | null
  value_format: 'number' | 'integer' | 'currency' | 'percent'
  currency?: string | null
  insight?: string | null
  width: number
  text?: string | null
  targets?: Target[]
  annotations?: Annotation[]
}

export interface Column { name: string; type: string }
export interface TableData { columns: Column[]; rows: unknown[][] }
export interface ChartWithData { spec: ChartSpec; data: TableData | null; error?: string | null }

export interface DashboardSummary { id: string; title: string; description?: string | null; charts: number; updated_at: string; types: ChartType[] }
export interface FilterOptions { models: string[]; has_time: boolean; dimensions: { column: string; models: string[] }[] }
export interface Finding { title: string; detail: string; kind: 'positive' | 'negative' | 'warning' | 'neutral'; chart_id?: string | null }
export interface Insights { summary: string; findings: Finding[]; recommendations: string[]; generated_at: string; scope?: string | null }
export interface Dashboard { id: string; title: string; description?: string | null; charts: ChartWithData[]; updated_at: string; filter_options: FilterOptions; insights?: Insights | null }
export interface DashboardFilters { date_from?: string | null; date_to?: string | null; dimensions: { column: string; values: string[] }[] }

export interface ProviderInfo {
  id: string
  name: string
  kind: 'anthropic' | 'openai'
  needs_key: boolean
  docs_url: string
  model: string
  base_url: string
  suggested_models: string[]
  configured: boolean
  key_hint: string
  key_source: string
  editable_base_url: boolean
}
export interface AISettings { provider: string; effort: string; efforts: string[]; providers: ProviderInfo[] }
export interface Status { provider: string; provider_name: string; model: string; configured: boolean; effort: string; python_enabled: boolean; models: number; dashboards: number }
export interface ConversationSummary { id: string; title: string; updated_at: string; provider: string; model: string }

export type DbEngine = 'postgres' | 'mysql' | 'sqlite'
export interface SchemaInfo { schema: string; tables: number }
export interface Source {
  type: 'file' | 'url' | 'gsheet' | DbEngine
  name: string
  table?: string
  alias?: string
  dsn?: string
  error?: string
  engine_name?: string
  schemas?: string[] | null
  available_schemas?: SchemaInfo[]
  url?: string
  refresh_minutes?: number | null
  last_refreshed?: string
}
export interface DbConnection { engine: DbEngine; name?: string; dsn?: string; fields?: Record<string, unknown>; schemas?: string[] | null }

export interface MdlColumn {
  name: string
  type: string
  isCalculated?: boolean
  expression?: string
  relationship?: string
  properties?: { description?: string }
}
export interface MdlModel {
  name: string
  tableReference: { catalog: string; schema: string; table: string }
  columns: MdlColumn[]
  properties?: { description?: string }
}
export interface Mdl { models: MdlModel[]; relationships: { name: string; models: string[]; joinType: string; condition: string }[] }

export interface Profile {
  table: string
  row_count: number
  profiled_on_sample: boolean
  columns: { column: string; type: string; min: unknown; max: unknown; distinct_approx: number; avg: unknown; null_pct: unknown }[]
  sample: { columns: string[]; rows: unknown[][] }
}

export type ValueFormat = 'number' | 'integer' | 'currency' | 'percent'
export interface Metric {
  name: string
  label: string
  model: string
  expression: string
  description: string
  format: ValueFormat
  currency?: string | null
  time_column?: string | null
  higher_is_better: boolean
  certified: boolean
  updated_at: string
}
export interface MetricQuery {
  metrics: string[]
  group_by?: string[]
  grain?: 'day' | 'week' | 'month' | 'quarter' | 'year' | null
  period?: string
  compare?: 'previous_period' | 'previous_year' | null
  filters?: { column: string; values: string[] }[]
}
export interface MetricResult extends TableData { sql: string; range: string[] | null; previous_range: string[] | null; anchor: string | null; period_label: string }

export interface Frequency { every: 'hour' | 'day' | 'week' | 'month'; at: string; weekday?: number; day?: number }
export type Channel = 'app' | 'email' | 'slack' | 'webhook'
export interface Automation {
  id: string
  kind: 'report' | 'alert'
  name: string
  enabled: boolean
  frequency: Frequency
  channels: Channel[]
  recipients: string[]
  dashboard_id?: string | null
  refresh_insights: boolean
  metric?: string | null
  period: string
  compare?: 'previous_period' | 'previous_year' | null
  filters: { column: string; values: string[] }[]
  condition?: { type: 'change_pct' | 'value'; op: '<' | '>'; threshold: number } | null
  explain: boolean
  next_run?: string | null
  last_run?: string | null
  last_status?: string | null
  last_message?: string | null
  description: string
  running: boolean
}
export interface AppNotification { id: string; automation_id?: string | null; kind: string; level: 'info' | 'warning' | 'error' | 'success'; title: string; body: string; link?: string | null; created_at: string; read: boolean; delivered: Record<string, string> }

export interface SavedQuery { id: string; title: string; sql: string; description: string; tags: string[]; created_at: string; updated_at: string }
export interface QueryRun extends TableData { row_count: number; truncated: boolean; planned_sql: string; elapsed_ms: number }

async function download(path: string, body: unknown, fallbackName: string) {
  const res = await fetch(path, json('POST', body))
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail } catch { /* no JSON */ }
    throw new Error(detail)
  }
  const cd = res.headers.get('content-disposition') ?? ''
  const m = cd.match(/filename\*=UTF-8''([^;]+)/)
  const name = m ? decodeURIComponent(m[1]) : fallbackName
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 5000)
}

export type StepKind = 'trim' | 'case' | 'map_values' | 'cast' | 'parse_number' | 'parse_date' | 'fill_nulls' | 'drop_duplicates' | 'filter_rows'
export interface PrepStep {
  id?: string
  kind: StepKind
  column?: string | null
  columns?: string[] | null
  mode?: 'upper' | 'lower' | 'title' | null
  mapping?: Record<string, string> | null
  type?: string | null
  decimal?: ',' | '.' | null
  format?: string | null
  value?: string | null
  condition?: string | null
  note?: string | null
  description?: string
}
export interface QualityIssue { id: string; severity: 'high' | 'medium' | 'low'; kind: string; column?: string | null; title: string; detail: string; count?: number | null; fix?: PrepStep | null; examples: unknown[] }
export interface QualityReport { model: string; rows: number; score: number; issues: QualityIssue[]; sampled: boolean }
export interface StepPreview { rows_before: number; rows_after: number; rows_changed: number; new_nulls?: number; description: string; examples?: { before: string | null; after: string | null; rows: number }[] }
export interface Recipe { model: string; source: { catalog: string; schema: string; table: string }; steps: PrepStep[] }

export type AgentEvent =
  | { type: 'conversation'; id: string; provider: string; model: string }
  | { type: 'user'; text: string }
  | { type: 'text'; delta: string }
  | { type: 'thinking'; delta: string }
  | { type: 'tool_start'; id: string; name: string; input: Record<string, unknown> }
  | { type: 'tool_end'; id: string; name: string; ok: boolean; error?: string | null }
  | { type: 'chart'; tool_id: string; spec: ChartSpec; data: TableData }
  | { type: 'query'; tool_id: string; sql: string; columns: Column[]; rows: unknown[][]; row_count: number; truncated: boolean }
  | { type: 'dashboard'; tool_id: string; id: string; title: string }
  | { type: 'python'; tool_id: string; code: string; stdout: string; stderr: string; exit_code: number }
  | { type: 'semantic_updated'; tool_id: string }
  | { type: 'insights'; tool_id: string; dashboard_id: string; title: string; insights: Insights }
  | { type: 'error'; message: string }
  | { type: 'notice'; message: string }
  | { type: 'quality'; tool_id: string; model: string; score: number; issues: number }
  | { type: 'cleaning'; tool_id: string; model: string; description: string; rows_changed?: number | null }
  | { type: 'saved_query'; tool_id: string; id: string; title: string }
  | { type: 'automation'; tool_id: string; id: string; name: string; description: string; next_run?: string | null }
  | { type: 'done'; usage: Record<string, number> }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* response without JSON */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  status: () => request<Status>('/api/status'),
  settings: () => request<AISettings>('/api/settings'),
  saveSettings: (patch: Record<string, unknown>) => request<AISettings>('/api/settings', json('PATCH', patch)),
  providerModels: (id: string) => request<string[]>(`/api/providers/${id}/models`),
  testProvider: (id: string, model?: string) => request<{ ok: boolean; tools?: boolean; message: string }>(`/api/providers/${id}/test`, json('POST', { model })),
  suggestions: () => request<string[]>('/api/suggestions'),
  columnValues: (model: string, column: string) => request<unknown[]>(`/api/semantic/values?model=${encodeURIComponent(model)}&column=${encodeURIComponent(column)}`),
  editSemantic: (body: Record<string, unknown>) => request<{ ok: boolean }>('/api/semantic/edit', json('POST', body)),
  sources: () => request<Source[]>('/api/sources'),
  upload: (files: File[]) => {
    const form = new FormData()
    files.forEach((f) => form.append('files', f))
    return request<{ tables: string[]; models_added: string[] }>('/api/sources/upload', { method: 'POST', body: form })
  },
  addPostgres: (name: string, dsn: string) => request<{ models_added: string[] }>('/api/sources/postgres', json('POST', { name, dsn })),
  inspectDb: (c: DbConnection) => request<{ schemas: SchemaInfo[] }>('/api/sources/inspect', json('POST', c)),
  addDb: (c: DbConnection) => request<{ models_added: string[] }>('/api/sources/database', json('POST', c)),
  setSchemas: (alias: string, schemas: string[] | null) => request<{ added: string[]; removed: string[] }>(`/api/sources/${alias}/schemas`, json('PUT', { schemas })),
  runSql: (sql: string, limit?: number) => request<QueryRun>('/api/query', json('POST', { sql, limit })),
  formatSql: (sql: string) => request<{ sql: string }>('/api/sql/format', json('POST', { sql })),
  assistSql: (body: { mode: 'generate' | 'fix' | 'explain'; prompt?: string; sql?: string; error?: string }) =>
    request<{ sql?: string | null; text: string }>('/api/sql/assist', json('POST', body)),
  queries: () => request<SavedQuery[]>('/api/queries'),
  saveQuery: (q: Partial<SavedQuery>) => request<SavedQuery>('/api/queries', json('POST', q)),
  deleteQuery: (id: string) => request(`/api/queries/${id}`, { method: 'DELETE' }),
  createChart: (spec: Partial<ChartSpec>) => request<{ chart_id: string; spec: ChartSpec; data: TableData }>('/api/charts', json('POST', spec)),
  exportXlsx: (sql: string, title: string, chartId?: string) => download('/api/export/xlsx', { sql, title, chart_id: chartId }, `${title}.xlsx`),
  exportDashboardXlsx: (id: string, filters: DashboardFilters, title: string) => download(`/api/dashboards/${id}/export.xlsx`, filters, `${title}.xlsx`),
  quality: (model: string) => request<QualityReport>(`/api/quality/${encodeURIComponent(model)}`),
  recipe: (model: string) => request<Recipe>(`/api/prep/${encodeURIComponent(model)}`),
  previewStep: (model: string, step: PrepStep) => request<StepPreview>(`/api/prep/${encodeURIComponent(model)}/preview`, json('POST', step)),
  addStep: (model: string, step: PrepStep) => request<Recipe>(`/api/prep/${encodeURIComponent(model)}/steps`, json('POST', step)),
  removeStep: (model: string, id: string) => request<Recipe>(`/api/prep/${encodeURIComponent(model)}/steps/${id}`, { method: 'DELETE' }),
  addUrlSource: (name: string, url: string, refresh_minutes: number | null) => request<{ tables: string[]; models_added: string[] }>('/api/sources/url', json('POST', { name, url, refresh_minutes })),
  refreshSource: (table: string) => request<{ tables: string[] }>(`/api/sources/${encodeURIComponent(table)}/refresh`, { method: 'POST' }),
  metrics: () => request<Metric[]>('/api/metrics'),
  saveMetric: (m: Partial<Metric> & { name: string }) => request<Metric>(`/api/metrics/${m.name}`, json('PUT', m)),
  deleteMetric: (name: string) => request(`/api/metrics/${name}`, { method: 'DELETE' }),
  queryMetrics: (q: MetricQuery) => request<MetricResult>('/api/metrics/query', json('POST', q)),
  automations: () => request<{ items: Automation[]; available_channels: Channel[] }>('/api/automations'),
  createAutomation: (a: Partial<Automation>) => request<Automation>('/api/automations', json('POST', a)),
  patchAutomation: (id: string, a: Partial<Automation>) => request<Automation>(`/api/automations/${id}`, json('PATCH', a)),
  deleteAutomation: (id: string) => request(`/api/automations/${id}`, { method: 'DELETE' }),
  runAutomation: (id: string) => request<{ started: boolean; message?: string }>(`/api/automations/${id}/run`, { method: 'POST' }),
  previewAlert: (a: Partial<Automation>) => request<{ value: number | null; previous: number | null; var: number | null; triggered: boolean; range: string[]; previous_range: string[] | null }>('/api/automations/preview-alert', json('POST', a)),
  notifications: () => request<{ items: AppNotification[]; unread: number }>('/api/notifications'),
  readNotifications: (ids?: string[]) => request('/api/notifications/read', json('POST', { ids })),
  channels: () => request<{ config: Record<string, string>; available: Channel[] }>('/api/channels'),
  saveChannels: (c: Record<string, unknown>) => request<{ config: Record<string, string>; available: Channel[] }>('/api/channels', json('PUT', c)),
  testChannel: (channel: Channel, recipients?: string[]) => request<{ ok: boolean; message: string }>('/api/channels/test', json('POST', { channel, recipients })),
  removeSource: (id: string) => request('/api/sources/' + encodeURIComponent(id), { method: 'DELETE' }),
  semantic: () => request<Mdl>('/api/semantic'),
  profile: (model: string) => request<Profile>(`/api/semantic/models/${encodeURIComponent(model)}/profile`),
  setDescription: (model: string, description: string, column?: string) =>
    request<Mdl>(`/api/semantic/models/${encodeURIComponent(model)}/description`, json('PUT', { description, column })),
  dashboards: () => request<DashboardSummary[]>('/api/dashboards'),
  dashboard: (id: string) => request<Dashboard>('/api/dashboards/' + id),
  createDashboard: (title: string, chart_ids: string[]) => request<{ id: string }>('/api/dashboards', json('POST', { title, chart_ids })),
  patchDashboard: (id: string, patch: Record<string, unknown>) => request<{ ok: boolean }>('/api/dashboards/' + id, json('PATCH', patch)),
  renderDashboard: (id: string, filters: DashboardFilters) => request<Dashboard>(`/api/dashboards/${id}/render`, json('POST', filters)),
  drill: (dashId: string, chartId: string, column: string, value: unknown) =>
    request<{ kind: 'date'; date_from: string; date_to: string } | { kind: 'dimension'; column: string; value: string }>(`/api/dashboards/${dashId}/drill`, json('POST', { chart_id: chartId, column, value })),
  addTextBlock: (dashId: string, text: string, position?: number) => request(`/api/dashboards/${dashId}/text`, json('POST', { text, position })),
  patchChart: (dashId: string, chartId: string, patch: Record<string, unknown>) => request<ChartWithData>(`/api/dashboards/${dashId}/charts/${chartId}`, json('PATCH', patch)),
  deleteDashboard: (id: string) => request('/api/dashboards/' + id, { method: 'DELETE' }),
  conversations: () => request<ConversationSummary[]>('/api/conversations'),
  conversation: (id: string) => request<{ id: string; title: string; ui: AgentEvent[]; provider: string; model: string; usage: Record<string, number> }>('/api/conversations/' + id),
  deleteConversation: (id: string) => request('/api/conversations/' + id, { method: 'DELETE' }),
}

/** Sends a message and yields the agent's events as they arrive (Server-Sent Events over POST). */
export async function streamChat(
  message: string,
  conversationId: string | null,
  onEvent: (e: AgentEvent) => void,
  signal?: AbortSignal,
  choice?: { provider: string; model: string },
): Promise<void> {
  const res = await fetch('/api/chat', { ...json('POST', { message, conversation_id: conversationId, ...choice }), signal })
  if (!res.ok || !res.body) throw new Error(`Error ${res.status}`)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      if (chunk.startsWith('data: ')) onEvent(JSON.parse(chunk.slice(6)) as AgentEvent)
    }
  }
}

/** A small bus so the sidebar hears about changes made in other views. */
const target = new EventTarget()

export const notifyDashboardsChanged = () => target.dispatchEvent(new Event('dashboards'))
export const notifyConversationsChanged = () => target.dispatchEvent(new Event('conversations'))
export const notifyDataChanged = () => target.dispatchEvent(new Event('data'))

export const notifyAutomationsChanged = () => target.dispatchEvent(new Event('automations'))

export function subscribe(name: 'dashboards' | 'conversations' | 'data' | 'automations', fn: () => void): () => void {
  target.addEventListener(name, fn)
  return () => target.removeEventListener(name, fn)
}

/** A prompt another view wants to send when the chat opens (e.g. "build a dashboard of X"). */
let pendingPrompt: string | null = null
export function askAgent(prompt: string) {
  pendingPrompt = prompt
  window.location.hash = '#/chat'
}
export function takePendingPrompt(): string | null {
  const p = pendingPrompt
  pendingPrompt = null
  return p
}

/** Short notices ("toasts") from any view. */
export type ToastKind = 'info' | 'success' | 'error'
export function toast(message: string, kind: ToastKind = 'info') {
  target.dispatchEvent(new CustomEvent('toast', { detail: { message, kind } }))
}
export function onToast(fn: (t: { message: string; kind: ToastKind }) => void): () => void {
  const h = (e: Event) => fn((e as CustomEvent).detail)
  target.addEventListener('toast', h)
  return () => target.removeEventListener('toast', h)
}
export const notifySettingsChanged = () => target.dispatchEvent(new Event('settings'))
export function onSettingsChanged(fn: () => void): () => void {
  target.addEventListener('settings', fn)
  return () => target.removeEventListener('settings', fn)
}

/** SQL another view wants to open in the editor. */
let pendingSql: string | null = null
export function openInEditor(sql: string) {
  pendingSql = sql
  window.location.hash = '#/sql'
  window.dispatchEvent(new Event('insight:open-sql'))
}
export function takePendingSql(): string | null {
  const s = pendingSql
  pendingSql = null
  return s
}

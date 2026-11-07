import { autocompletion } from '@codemirror/autocomplete'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { PostgreSQL, sql } from '@codemirror/lang-sql'
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { Compartment, EditorState } from '@codemirror/state'
import { EditorView, keymap, lineNumbers, placeholder } from '@codemirror/view'
import { tags as t } from '@lezer/highlight'
import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'

export interface SqlEditorHandle {
  getValue: () => string
  getSelection: () => string
  setValue: (v: string) => void
  insert: (text: string) => void
  focus: () => void
}

// Colors by role (the app's tokens), so it works in light and dark without separate themes.
const highlight = HighlightStyle.define([
  { tag: t.keyword, color: 'var(--series-1)', fontWeight: '600' },
  { tag: [t.string, t.special(t.string)], color: 'var(--series-3)' },
  { tag: [t.number, t.bool, t.null], color: 'var(--series-2)' },
  { tag: t.comment, color: 'var(--text-muted)', fontStyle: 'italic' },
  { tag: [t.function(t.variableName), t.standard(t.name)], color: 'var(--series-7)' },
  { tag: [t.operator, t.punctuation], color: 'var(--text-secondary)' },
  { tag: t.special(t.name), color: 'var(--series-5)' },
])

const theme = EditorView.theme({
  '&': { height: '100%', fontSize: '13.5px', backgroundColor: 'var(--surface-2)', color: 'var(--text-primary)' },
  '.cm-scroller': { fontFamily: 'var(--mono)', lineHeight: '1.6' },
  '.cm-content': { caretColor: 'var(--accent)', padding: '10px 0' },
  '.cm-gutters': { backgroundColor: 'var(--surface-1)', color: 'var(--text-muted)', border: 'none', borderRight: '1px solid var(--border)' },
  '.cm-activeLine': { backgroundColor: 'color-mix(in srgb, var(--accent) 5%, transparent)' },
  '.cm-activeLineGutter': { backgroundColor: 'transparent', color: 'var(--text-primary)' },
  '&.cm-focused': { outline: 'none' },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection': { backgroundColor: 'var(--accent-glow) !important' },
  '.cm-tooltip': { backgroundColor: 'var(--surface-2)', border: '1px solid var(--border-strong)', borderRadius: '8px', boxShadow: 'var(--shadow-lg)' },
  '.cm-tooltip-autocomplete ul li[aria-selected]': { backgroundColor: 'var(--accent-soft)', color: 'var(--text-primary)' },
  '.cm-placeholder': { color: 'var(--text-muted)' },
})

interface Props {
  initial: string
  schema: Record<string, string[]>
  onRun: () => void
  onChange?: (v: string) => void
}

const SqlEditor = forwardRef<SqlEditorHandle, Props>(function SqlEditor({ initial, schema, onRun, onChange }, ref) {
  const host = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
  const lang = useRef(new Compartment())
  const runRef = useRef(onRun)
  const changeRef = useRef(onChange)
  runRef.current = onRun
  changeRef.current = onChange

  useEffect(() => {
    const v = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: initial,
        extensions: [
          lineNumbers(), history(), theme, syntaxHighlighting(highlight),
          lang.current.of(sql({ dialect: PostgreSQL, schema, upperCaseKeywords: true })),
          autocompletion({ activateOnTyping: true }),
          placeholder('SELECT … FROM model   ·   Ctrl+Enter to run'),
          keymap.of([{ key: 'Mod-Enter', run: () => { runRef.current(); return true } }, indentWithTab, ...defaultKeymap, ...historyKeymap]),
          EditorView.updateListener.of((u) => { if (u.docChanged) changeRef.current?.(u.state.doc.toString()) }),
          EditorView.lineWrapping,
        ],
      }),
    })
    view.current = v
    return () => v.destroy()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    view.current?.dispatch({ effects: lang.current.reconfigure(sql({ dialect: PostgreSQL, schema, upperCaseKeywords: true })) })
  }, [schema])

  useImperativeHandle(ref, () => ({
    getValue: () => view.current?.state.doc.toString() ?? '',
    getSelection: () => {
      const s = view.current?.state
      if (!s) return ''
      return s.selection.ranges.map((r) => s.sliceDoc(r.from, r.to)).join('\n')
    },
    setValue: (text: string) => {
      const v = view.current
      v?.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text } })
    },
    insert: (text: string) => {
      const v = view.current
      if (!v) return
      v.dispatch(v.state.replaceSelection(text))
      v.focus()
    },
    focus: () => view.current?.focus(),
  }))

  return <div ref={host} className="sql-editor" />
})

export default SqlEditor

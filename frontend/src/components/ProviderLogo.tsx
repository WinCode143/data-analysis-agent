const COLORS: Record<string, string> = {
  anthropic: '#c96442', openai: '#10a37f', gemini: '#4285f4', openrouter: '#6467f2', deepseek: '#4d6bfe',
  groq: '#f55036', mistral: '#fa520f', ollama: '#52514e', lmstudio: '#6b46c1', custom: '#898781',
}
const LETTERS: Record<string, string> = { lmstudio: 'LM', openrouter: 'OR', custom: '{ }' }

export default function ProviderLogo({ id, size = 34 }: { id: string; size?: number }) {
  return (
    <span className="provider-logo" style={{ background: COLORS[id] ?? '#898781', width: size, height: size, fontSize: size * 0.4, borderRadius: size * 0.3 }} aria-hidden>
      {LETTERS[id] ?? id[0].toUpperCase()}
    </span>
  )
}

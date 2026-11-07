"""AI providers.

Two adapters cover everything:
- `AnthropicAdapter`: Claude's native API (adaptive thinking, fallbacks, prompt caching).
- `OpenAICompatAdapter`: any API compatible with OpenAI Chat Completions: OpenAI, Gemini,
  Ollama, OpenRouter, DeepSeek, Groq, Mistral, LM Studio or your own endpoint.

Each adapter speaks its provider's native format; a conversation's history stays tied
to the provider it started with.
"""

from __future__ import annotations

import copy
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import anthropic
import openai

from . import config, vault


# ---------------------------------------------------------------------- catalog
@dataclass(frozen=True)
class ProviderPreset:
    id: str
    name: str
    kind: str  # "anthropic" | "openai"
    env_key: str | None
    base_url: str | None
    default_model: str
    suggested_models: tuple[str, ...] = ()
    needs_key: bool = True
    supports_reasoning_effort: bool = False
    strict_schemas: bool = True  # False → drop additionalProperties (Gemini and others reject it)
    docs_url: str = ""


PRESETS: dict[str, ProviderPreset] = {p.id: p for p in [
    ProviderPreset("anthropic", "Anthropic API (Claude)", "anthropic", "ANTHROPIC_API_KEY", None, config.CLAUDE_MODEL,
                   ("claude-opus-5", "claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5", "claude-haiku-4-5"),
                   docs_url="https://console.anthropic.com/settings/keys"),
    ProviderPreset("openai", "OpenAI", "openai", "OPENAI_API_KEY", None, "gpt-6-astra",
                   ("gpt-6-astra", "gpt-6-sol", "gpt-6-luna"), supports_reasoning_effort=True,
                   docs_url="https://platform.openai.com/api-keys"),
    ProviderPreset("gemini", "Google Gemini", "openai", "GEMINI_API_KEY",
                   "https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.5-flash-lite",
                   ("gemini-2.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash"), supports_reasoning_effort=True, strict_schemas=False,
                   docs_url="https://aistudio.google.com/apikey"),
    ProviderPreset("openrouter", "OpenRouter", "openai", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1",
                   "anthropic/claude-opus-5", strict_schemas=False, docs_url="https://openrouter.ai/keys"),
    ProviderPreset("deepseek", "DeepSeek", "openai", "DEEPSEEK_API_KEY", "https://api.deepseek.com/v1",
                   "deepseek-chat", strict_schemas=False, docs_url="https://platform.deepseek.com/api_keys"),
    ProviderPreset("groq", "Groq", "openai", "GROQ_API_KEY", "https://api.groq.com/openai/v1", "",
                   strict_schemas=False, docs_url="https://console.groq.com/keys"),
    ProviderPreset("mistral", "Mistral", "openai", "MISTRAL_API_KEY", "https://api.mistral.ai/v1", "",
                   strict_schemas=False, docs_url="https://console.mistral.ai/api-keys"),
    ProviderPreset("ollama", "Ollama (local)", "openai", None, "http://localhost:11434/v1", "",
                   needs_key=False, strict_schemas=False, docs_url="https://ollama.com/library"),
    ProviderPreset("lmstudio", "LM Studio (local)", "openai", None, "http://localhost:1234/v1", "",
                   needs_key=False, strict_schemas=False),
    ProviderPreset("custom", "OpenAI-compatible (other)", "openai", None, "", "", needs_key=False,
                   strict_schemas=False),
]}

EFFORTS = ["low", "medium", "high", "xhigh", "max"]


# ---------------------------------------------------------------------- settings
class Settings:
    """Settings persisted in workspace/settings.json. Keys can come from the .env or from the interface."""

    def __init__(self) -> None:
        self.path = config.WORKSPACE_DIR / "settings.json"
        # Starting provider: INSIGHT_PROVIDER in the .env (e.g. "ollama"); otherwise the Anthropic API.
        default = os.getenv("INSIGHT_PROVIDER", "anthropic")
        if default not in PRESETS:
            default = "anthropic"
        self.data: dict[str, Any] = {"provider": default, "effort": config.CLAUDE_EFFORT, "providers": {}}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))
            plaintext = False
            for cfg in self.data["providers"].values():
                if cfg.get("api_key"):
                    plaintext = plaintext or not cfg["api_key"].startswith(vault.PREFIX)
                    cfg["api_key"] = vault.decrypt(cfg["api_key"])
            if plaintext:  # migration: old plaintext keys → encrypted
                self.save()

    def save(self) -> None:
        stored = copy.deepcopy(self.data)
        for cfg in stored["providers"].values():
            if cfg.get("api_key"):
                cfg["api_key"] = vault.encrypt(cfg["api_key"])
        self.path.write_text(json.dumps(stored, indent=2, ensure_ascii=False))
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def provider_cfg(self, pid: str) -> dict[str, Any]:
        preset = PRESETS[pid]
        cfg = dict(self.data["providers"].get(pid, {}))
        cfg.setdefault("model", preset.default_model)
        cfg.setdefault("base_url", preset.base_url)
        if not cfg.get("api_key") and preset.env_key:
            cfg["api_key"] = os.getenv(preset.env_key, "")
        return cfg

    def is_configured(self, pid: str) -> bool:
        preset = PRESETS[pid]
        cfg = self.provider_cfg(pid)
        if preset.kind == "openai" and not cfg.get("base_url") and pid != "openai":
            return False
        return bool(cfg.get("model")) and (bool(cfg.get("api_key")) or not preset.needs_key)

    def public(self) -> dict[str, Any]:
        providers = []
        for pid, p in PRESETS.items():
            cfg = self.provider_cfg(pid)
            key = cfg.get("api_key") or ""
            providers.append({
                "id": pid, "name": p.name, "kind": p.kind, "needs_key": p.needs_key, "docs_url": p.docs_url,
                "model": cfg.get("model") or "", "base_url": cfg.get("base_url") or "",
                "suggested_models": list(p.suggested_models), "configured": self.is_configured(pid),
                "key_hint": ("…" + key[-4:]) if key else "",
                "key_source": "settings" if self.data["providers"].get(pid, {}).get("api_key") else (".env" if key else ""),
                "editable_base_url": pid in {"ollama", "lmstudio", "custom"},
            })
        return {"provider": self.data["provider"], "effort": self.data["effort"], "efforts": EFFORTS,
                "providers": providers}

    def update(self, patch: dict[str, Any]) -> None:
        if "provider" in patch:
            if patch["provider"] not in PRESETS:
                raise ValueError(f"Unknown provider: {patch['provider']}")
            self.data["provider"] = patch["provider"]
        if "effort" in patch:
            if patch["effort"] not in EFFORTS:
                raise ValueError(f"effort must be one of {EFFORTS}")
            self.data["effort"] = patch["effort"]
        for pid, cfg in (patch.get("providers") or {}).items():
            if pid not in PRESETS:
                raise ValueError(f"Unknown provider: {pid}")
            cur = self.data["providers"].setdefault(pid, {})
            for k in ("model", "base_url", "api_key"):
                if k in cfg and cfg[k] is not None:
                    cur[k] = cfg[k].strip()
        self.save()


# ---------------------------------------------------------------------- common types
class ProviderError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    input: Any  # a dict if the JSON is valid; otherwise the raw text


@dataclass
class TurnResult:
    assistant_message: dict[str, Any] | None
    tool_calls: list[ToolCall]
    stop: str  # "end" | "tool_use" | "refusal" | "max_tokens" | "pause"
    usage: dict[str, int] = field(default_factory=dict)


def _strip_additional_properties(schema: Any) -> Any:
    if isinstance(schema, dict):
        out = {}
        for k, v in schema.items():
            if k == "additionalProperties":
                continue
            out[k] = _strip_additional_properties(v)
        # Map objects (e.g. widths) stay as a free-form object described in text.
        return out
    if isinstance(schema, list):
        return [_strip_additional_properties(x) for x in schema]
    return schema


# ---------------------------------------------------------------------- Anthropic
ANTHROPIC_FALLBACK_MODELS = {"claude-opus-5", "claude-opus-5-5", "claude-fable-5-1"}


class AnthropicAdapter:
    kind = "anthropic"

    def __init__(self, cfg: dict[str, Any], effort: str) -> None:
        self.model = cfg["model"]
        self.effort = effort
        self.client = anthropic.AsyncAnthropic(api_key=cfg.get("api_key") or None)

    def user_message(self, texts: list[str]) -> dict[str, Any]:
        return {"role": "user", "content": [{"type": "text", "text": t} for t in texts]}

    def tool_results(self, results: list[tuple[str, str, bool]]) -> list[dict[str, Any]]:
        return [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": content, **({"is_error": True} if err else {})}
            for tid, content, err in results
        ]}]

    def pending_tool_ids(self, messages: list[dict[str, Any]]) -> list[str]:
        if not messages or messages[-1]["role"] != "assistant":
            return []
        return [b["id"] for b in messages[-1]["content"] if b.get("type") == "tool_use"]

    def _params(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        params: dict[str, Any] = dict(
            model=self.model, max_tokens=64000, system=system, messages=messages,
            cache_control={"type": "ephemeral"},
        )
        if tools:
            params["tools"] = [{**t, "eager_input_streaming": True} for t in tools]
        if self.model.startswith("claude-haiku"):
            # Haiku 4.5 supports neither adaptive thinking nor effort.
            params["thinking"] = {"type": "enabled", "budget_tokens": 8000}
        else:
            params["thinking"] = {"type": "adaptive", "display": "summarized"}
            params["output_config"] = {"effort": self.effort}
        if self.model in ANTHROPIC_FALLBACK_MODELS:
            params["betas"] = ["server-side-fallback-2026-07-01"]
            params["fallbacks"] = "default"
        return params

    async def turn(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        params = self._params(system, messages, tools)
        json_retries = 0
        while True:
            try:
                async with self.client.beta.messages.stream(**params) as stream:
                    async for event in stream:
                        if event.type == "content_block_delta":
                            if event.delta.type == "text_delta":
                                yield {"type": "text", "delta": event.delta.text}
                            elif event.delta.type == "thinking_delta" and event.delta.thinking:
                                yield {"type": "thinking", "delta": event.delta.thinking}
                    response = await stream.get_final_message()
                break
            except ValueError:
                json_retries += 1  # tool JSON that can't be parsed (eager streaming)
                if json_retries > 2:
                    raise ProviderError("The model produced invalid tool arguments repeatedly.")
            except anthropic.AuthenticationError as e:
                raise ProviderError("Invalid Anthropic key. Check it in Settings.") from e
            except anthropic.RateLimitError as e:
                raise ProviderError("Anthropic rate limit reached. Wait a moment and try again.") from e
            except anthropic.APIStatusError as e:
                raise ProviderError(f"Error de Anthropic ({e.status_code}): {e.message}") from e
            except anthropic.APIConnectionError as e:
                raise ProviderError("Couldn't connect to the Anthropic API.") from e
            except TypeError as e:
                if "authentication" in str(e):
                    raise ProviderError("The Anthropic key is missing. Add it in Settings or in the .env.") from e
                raise

        stop = {"refusal": "refusal", "pause_turn": "pause", "max_tokens": "max_tokens", "tool_use": "tool_use"}.get(
            response.stop_reason or "", "end")
        calls = [ToolCall(b.id, b.name, b.input) for b in response.content if b.type == "tool_use"]
        if calls and stop == "end":
            stop = "tool_use"
        msg = {"role": "assistant", "content": [b.model_dump(mode="json", exclude_none=True) for b in response.content]} \
            if response.content else None
        u = response.usage
        yield {"type": "_turn", "result": TurnResult(msg, calls, stop, {
            "input_tokens": (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
            + (getattr(u, "cache_creation_input_tokens", 0) or 0),
            "output_tokens": u.output_tokens or 0,
        })}


# ---------------------------------------------------------------------- OpenAI-compatible
class OpenAICompatAdapter:
    kind = "openai"

    def __init__(self, preset: ProviderPreset, cfg: dict[str, Any], effort: str, http_client: Any = None) -> None:
        self.preset = preset
        self.model = cfg["model"]
        self.effort = effort
        self.client = openai.AsyncOpenAI(
            api_key=cfg.get("api_key") or "not-needed",
            base_url=cfg.get("base_url") or None,
            http_client=http_client,
        )

    def user_message(self, texts: list[str]) -> dict[str, Any]:
        return {"role": "user", "content": "\n\n".join(texts)}

    def tool_results(self, results: list[tuple[str, str, bool]]) -> list[dict[str, Any]]:
        return [{"role": "tool", "tool_call_id": tid, "content": ("ERROR: " + c) if err else c} for tid, c, err in results]

    def pending_tool_ids(self, messages: list[dict[str, Any]]) -> list[str]:
        if not messages or messages[-1]["role"] != "assistant":
            return []
        return [tc["id"] for tc in messages[-1].get("tool_calls") or []]

    def _tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for t in tools:
            schema = copy.deepcopy(t["input_schema"])
            if not self.preset.strict_schemas:
                schema = _strip_additional_properties(schema)
            out.append({"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": schema}})
        return out

    async def turn(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        params: dict[str, Any] = dict(
            model=self.model, messages=[{"role": "system", "content": system}, *messages], stream=True,
        )
        if tools:
            params["tools"] = self._tools(tools)
        if self.preset.id == "openai":
            params["stream_options"] = {"include_usage": True}
        if self.preset.supports_reasoning_effort:
            params["reasoning_effort"] = {"xhigh": "high", "max": "high"}.get(self.effort, self.effort)

        text: list[str] = []
        acc: dict[int, dict[str, str]] = {}
        finish = None
        usage = {"input_tokens": 0, "output_tokens": 0}
        try:
            stream = await self.client.chat.completions.create(**params)
            async for chunk in stream:
                if getattr(chunk, "usage", None):
                    usage = {"input_tokens": chunk.usage.prompt_tokens or 0, "output_tokens": chunk.usage.completion_tokens or 0}
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                extra = delta.model_extra or {}
                reasoning = extra.get("reasoning_content") or extra.get("reasoning")
                if isinstance(reasoning, str) and reasoning:
                    yield {"type": "thinking", "delta": reasoning}
                if delta.content:
                    text.append(delta.content)
                    yield {"type": "text", "delta": delta.content}
                for tc in delta.tool_calls or []:
                    slot = acc.setdefault(tc.index if tc.index is not None else len(acc), {"id": "", "name": "", "args": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function:
                        if tc.function.name and not slot["name"]:
                            slot["name"] = tc.function.name
                        if tc.function.arguments:
                            slot["args"] += tc.function.arguments
                if choice.finish_reason:
                    finish = choice.finish_reason
        except openai.AuthenticationError as e:
            raise ProviderError(f"Invalid {self.preset.name} key. Check it in Settings.") from e
        except openai.RateLimitError as e:
            raise ProviderError(f"{self.preset.name} rate limit reached.") from e
        except openai.NotFoundError as e:
            raise ProviderError(f"{self.preset.name}: the model “{self.model}” doesn't exist or isn't available.") from e
        except openai.APIStatusError as e:
            raise ProviderError(f"Error de {self.preset.name} ({e.status_code}): {e.message}") from e
        except openai.APIConnectionError as e:
            raise ProviderError(f"Couldn't connect to {self.preset.name} ({self.client.base_url}).") from e

        calls: list[ToolCall] = []
        native_calls = []
        for _, slot in sorted(acc.items()):
            tid = slot["id"] or f"call_{uuid.uuid4().hex[:12]}"
            try:
                args: Any = json.loads(slot["args"] or "{}")
            except json.JSONDecodeError:
                args = slot["args"]
            calls.append(ToolCall(tid, slot["name"], args))
            native_calls.append({"id": tid, "type": "function", "function": {"name": slot["name"], "arguments": slot["args"] or "{}"}})

        msg: dict[str, Any] = {"role": "assistant", "content": "".join(text) or None}
        if native_calls:
            msg["tool_calls"] = native_calls
        if calls:
            stop = "tool_use" if finish != "length" else "max_tokens"
        else:
            stop = {"length": "max_tokens", "content_filter": "refusal"}.get(finish or "", "end")
        yield {"type": "_turn", "result": TurnResult(msg if (text or native_calls) else None, calls, stop, usage)}


# ---------------------------------------------------------------------- factory
def make_adapter(settings: Settings, provider_id: str, model: str | None = None, http_client: Any = None):
    preset = PRESETS.get(provider_id)
    if preset is None:
        raise ProviderError(f"Unknown provider: {provider_id}")
    cfg = settings.provider_cfg(provider_id)
    if model:
        cfg["model"] = model
    if not cfg.get("model"):
        raise ProviderError(f"Pick a model for {preset.name} in Settings.")
    if preset.needs_key and not cfg.get("api_key"):
        raise ProviderError(f"The {preset.name} key is missing. Add it in Settings"
                            + (f" or in the .env as {preset.env_key}." if preset.env_key else "."))
    if preset.kind == "anthropic":
        return AnthropicAdapter(cfg, settings.data["effort"])
    if not cfg.get("base_url") and provider_id != "openai":
        raise ProviderError(f"The base URL for {preset.name} is missing.")
    return OpenAICompatAdapter(preset, cfg, settings.data["effort"], http_client=http_client)


async def list_models(settings: Settings, provider_id: str) -> list[str]:
    """Models available according to the provider itself; the suggested ones if that fails."""
    preset = PRESETS[provider_id]
    cfg = settings.provider_cfg(provider_id)
    try:
        if preset.kind == "anthropic":
            client = anthropic.AsyncAnthropic(api_key=cfg.get("api_key") or None, timeout=8)
            ids = [m.id async for m in client.models.list()]
        else:
            client = openai.AsyncOpenAI(api_key=cfg.get("api_key") or "not-needed", base_url=cfg.get("base_url") or None, timeout=8)
            ids = [m.id async for m in client.models.list()]
            ids = [i.removeprefix("models/") for i in ids]
            if provider_id == "openai":
                ids = [i for i in ids if i.startswith(("gpt", "o")) and not any(x in i for x in ("audio", "realtime", "tts", "transcribe", "image", "embedding", "search"))]
        ordered = [m for m in preset.suggested_models if m in ids] + sorted(i for i in ids if i not in preset.suggested_models)
        return ordered or list(preset.suggested_models)
    except Exception:
        return list(preset.suggested_models)

/**
 * Translation transport.
 *
 * The browser does NOT hold any LLM API key. All translation requests
 * go through the CORAL backend's POST /api/translate endpoint, which
 * reads the API key from a process env var injected via
 *   with-secrets llm -- coral ui ...
 *
 * This satisfies `~/.claude/rules/secrets.md`: the key never lives in
 * localStorage, never appears in the network response, never lands in
 * the chat transcript. The user manages secrets entirely via 1Password.
 */

import type { Provider, TranslateSettings } from "./settings";

export interface TranslationResult {
  text: string;
  tokensIn: number;
  tokensOut: number;
  provider: string;
  model: string;
}

export interface BackendHealth {
  available: Record<Provider, boolean>;
  default: Provider | null;
  models: Record<Provider, string[]>;
  default_models: Record<Provider, string>;
}

export async function fetchBackendHealth(): Promise<BackendHealth> {
  const r = await fetch("/api/translate/health");
  if (!r.ok) throw new Error(`health ${r.status}`);
  return (await r.json()) as BackendHealth;
}

// Match the backend's httpx timeout in coral/web/translate.py.
// Any single translation that hasn't returned in 60s is almost
// certainly stuck — we surface that as an error so the user can
// retry instead of staring at "翻译中…" forever.
const FETCH_TIMEOUT_MS = 60_000;

export async function translateRaw(
  text: string,
  s: TranslateSettings,
): Promise<TranslationResult> {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), FETCH_TIMEOUT_MS);
  try {
    const r = await fetch("/api/translate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        text,
        provider: s.provider,
        model: s.model,
      }),
      signal: ac.signal,
    });
    if (!r.ok) {
      let detail = "";
      try {
        const body = await r.json();
        detail = body.detail || body.error || "";
      } catch {
        /* ignore */
      }
      throw new Error(`backend ${r.status}: ${detail.slice(0, 200)}`);
    }
    return (await r.json()) as TranslationResult;
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new Error(`translation timed out after ${FETCH_TIMEOUT_MS / 1000}s`);
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

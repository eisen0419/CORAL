/**
 * Translation settings stored in localStorage.
 *
 * NOTE: API keys are NOT stored here. The backend reads them from
 * environment variables injected via `with-secrets llm -- coral ui ...`,
 * per `~/.claude/rules/secrets.md`. The frontend only persists user
 * preferences (enabled, provider, model) and a rolling token counter.
 */

const KEY = "coral-translate-settings-v2";

export type Provider = "deepseek" | "openai" | "anthropic" | "zhipu";

export interface TranslateSettings {
  enabled: boolean;
  provider: Provider;
  model: string;
  // Counters for the UI to display approximate usage
  tokensIn: number;
  tokensOut: number;
  requests: number;
}

const DEFAULTS: TranslateSettings = {
  enabled: false,
  provider: "deepseek",
  model: "deepseek-chat",
  tokensIn: 0,
  tokensOut: 0,
  requests: 0,
};

// Fallback model list used before /api/translate/health returns.
export const FALLBACK_MODELS: Record<Provider, string[]> = {
  deepseek: ["deepseek-chat", "deepseek-reasoner"],
  openai: ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
  anthropic: [
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
  ],
  zhipu: ["glm-4-flash", "glm-4-plus", "glm-4-air"],
};

export function getSettings(): TranslateSettings {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch {
    /* ignore */
  }
  return { ...DEFAULTS };
}

export function setSettings(patch: Partial<TranslateSettings>): TranslateSettings {
  const next = { ...getSettings(), ...patch };
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* ignore */
  }
  window.dispatchEvent(new CustomEvent("coral-translate-settings-changed"));
  return next;
}

export function bumpUsage(tokensIn: number, tokensOut: number): void {
  const cur = getSettings();
  setSettings({
    tokensIn: cur.tokensIn + tokensIn,
    tokensOut: cur.tokensOut + tokensOut,
    requests: cur.requests + 1,
  });
}

export function resetUsage(): void {
  setSettings({ tokensIn: 0, tokensOut: 0, requests: 0 });
}

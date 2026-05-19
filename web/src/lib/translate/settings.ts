/**
 * Translation settings stored in localStorage so the user only enters
 * their API key once. No secrets ever leave the browser — translation
 * requests go straight to the LLM provider.
 */

const KEY = "coral-translate-settings-v1";

export type Provider = "anthropic" | "openai" | "deepseek";

export interface TranslateSettings {
  enabled: boolean;
  provider: Provider;
  apiKey: string;
  model: string;
  // Counters for the UI to display approximate usage
  tokensIn: number;
  tokensOut: number;
  requests: number;
}

const DEFAULTS: TranslateSettings = {
  enabled: false,
  provider: "anthropic",
  apiKey: "",
  model: "claude-haiku-4-5-20251001",
  tokensIn: 0,
  tokensOut: 0,
  requests: 0,
};

export const MODEL_OPTIONS: Record<Provider, string[]> = {
  anthropic: ["claude-haiku-4-5-20251001", "claude-sonnet-4-6"],
  openai: ["gpt-4o-mini", "gpt-4o"],
  deepseek: ["deepseek-chat"],
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
  // Notify listeners (the SettingsModal and useTranslated hook)
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

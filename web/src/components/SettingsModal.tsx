/**
 * Settings modal for the translation feature.
 *
 * The API key lives on the backend (injected via `with-secrets llm
 * -- coral ui ...`). The frontend asks /api/translate/health to learn
 * which providers are usable, then lets the user pick one. No key
 * input field — that would violate ~/.claude/rules/secrets.md.
 */

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { createPortal } from "react-dom";
import {
  FALLBACK_MODELS,
  cacheClear,
  cacheSize,
  fetchBackendHealth,
  getSettings,
  resetUsage,
  setSettings,
  type BackendHealth,
  type Provider,
  type TranslateSettings,
} from "../lib/translate";

interface Props {
  open: boolean;
  onClose: () => void;
}

const PROVIDER_LABELS: Record<Provider, { en: string; zh: string }> = {
  deepseek: { en: "DeepSeek", zh: "DeepSeek（深度求索）" },
  openai: { en: "OpenAI (ChatGPT)", zh: "OpenAI" },
  anthropic: { en: "Anthropic (Claude)", zh: "Anthropic" },
  zhipu: { en: "Zhipu (GLM)", zh: "智谱 GLM" },
};

export default function SettingsModal({ open, onClose }: Props) {
  const { i18n } = useTranslation();
  const isZh = i18n.language.startsWith("zh");
  const [s, setS] = useState<TranslateSettings>(getSettings());
  const [health, setHealth] = useState<BackendHealth | null>(null);
  const [healthErr, setHealthErr] = useState<string | null>(null);
  const [cacheCount, setCacheCount] = useState<number>(0);

  useEffect(() => {
    if (!open) return;
    setS(getSettings());
    setCacheCount(cacheSize());
    setHealthErr(null);
    fetchBackendHealth()
      .then(setHealth)
      .catch((e) => setHealthErr(e instanceof Error ? e.message : String(e)));
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  const txt = (en: string, zh: string) => (isZh ? zh : en);

  const update = (patch: Partial<TranslateSettings>) => setS(setSettings(patch));

  const onProviderChange = (provider: Provider) => {
    const models =
      (health?.models?.[provider] && health.models[provider].length > 0
        ? health.models[provider]
        : FALLBACK_MODELS[provider]) ?? FALLBACK_MODELS[provider];
    update({ provider, model: models[0] });
  };

  const providerAvailable = (p: Provider) => health?.available?.[p] ?? false;
  const anyAvailable = !!health && Object.values(health.available).some(Boolean);
  const selectedAvailable = providerAvailable(s.provider);
  const models =
    (health?.models?.[s.provider] && health.models[s.provider].length > 0
      ? health.models[s.provider]
      : FALLBACK_MODELS[s.provider]);

  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center">
      <div className="absolute inset-0 bg-foreground/40" onClick={onClose} />

      <div className="relative z-10 bg-background border border-border rounded-xl m-4 w-[min(560px,calc(100vw-2rem))] max-h-[calc(100vh-4rem)] overflow-y-auto shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3 border-b border-border">
          <h2 className="font-display text-base font-semibold">
            {txt("Translation Settings", "翻译设置")}
          </h2>
          <button
            onClick={onClose}
            className="w-7 h-7 flex items-center justify-center rounded-md hover:bg-muted transition-colors text-muted-fg hover:text-foreground"
            title={txt("Close (Esc)", "关闭 (Esc)")}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="p-5 space-y-5">
          {/* Backend status */}
          <div className="border border-border rounded-lg p-3 bg-muted/30">
            <p className="font-mono text-[10px] uppercase tracking-widest text-muted-fg mb-2">
              {txt("Backend providers", "后端可用服务商")}
            </p>
            {healthErr ? (
              <p className="font-body text-[12px] text-orange-700 dark:text-orange-400">
                {txt("Could not reach backend:", "无法访问后端：")} {healthErr}
              </p>
            ) : !health ? (
              <p className="font-body text-[12px] text-muted-fg">
                {txt("Loading…", "加载中…")}
              </p>
            ) : (
              <div className="space-y-1">
                {(Object.keys(PROVIDER_LABELS) as Provider[]).map((p) => (
                  <div key={p} className="flex items-center gap-2 font-mono text-[12px]">
                    <span
                      className={`w-1.5 h-1.5 rounded-full ${
                        providerAvailable(p) ? "bg-green-500" : "bg-border-strong"
                      }`}
                    />
                    <span>{isZh ? PROVIDER_LABELS[p].zh : PROVIDER_LABELS[p].en}</span>
                    <span className="text-muted-fg ml-auto">
                      {providerAvailable(p)
                        ? txt("ready", "已就绪")
                        : txt("no key on backend", "后端无密钥")}
                    </span>
                  </div>
                ))}
                {!anyAvailable && (
                  <p className="font-body text-[11px] text-orange-700 dark:text-orange-400 mt-2 leading-relaxed">
                    {txt(
                      "No provider has an API key in the backend process. Restart the dashboard with:  with-secrets llm -- coral ui --host 0.0.0.0 --port 8421",
                      "后端进程没有任何服务商的密钥。请用以下命令重启：with-secrets llm -- coral ui --host 0.0.0.0 --port 8421",
                    )}
                  </p>
                )}
              </div>
            )}
          </div>

          {/* Enable switch */}
          <label className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={s.enabled}
              onChange={(e) => update({ enabled: e.target.checked })}
              disabled={!anyAvailable}
              className="mt-1 w-4 h-4"
            />
            <div className="flex-1">
              <p className="font-display text-[14px] font-medium">
                {txt("Enable automatic translation", "启用自动翻译")}
              </p>
              <p className="font-body text-[12px] text-muted-fg mt-0.5 leading-relaxed">
                {txt(
                  "When the UI is set to Chinese, agent-generated content (attempt titles, notes, feedback, logs) is sent to the backend for translation. Source data is never modified — translations live only in your browser's localStorage.",
                  "当界面语言为中文时，agent 产生的英文内容（尝试标题、笔记、评测反馈、日志）通过后端翻译。原数据不改动，翻译结果只存在你本地浏览器的 localStorage 里。",
                )}
              </p>
            </div>
          </label>

          {/* Provider */}
          <div>
            <label className="block font-mono text-[10px] uppercase tracking-widest text-muted-fg mb-1.5">
              {txt("Provider", "服务商")}
            </label>
            <select
              value={s.provider}
              onChange={(e) => onProviderChange(e.target.value as Provider)}
              className="w-full px-3 py-2 font-mono text-[13px] border border-border rounded-md bg-background"
            >
              {(Object.keys(PROVIDER_LABELS) as Provider[]).map((p) => (
                <option key={p} value={p} disabled={!providerAvailable(p)}>
                  {isZh ? PROVIDER_LABELS[p].zh : PROVIDER_LABELS[p].en}
                  {!providerAvailable(p)
                    ? txt(" (no key on backend)", "（后端无密钥）")
                    : ""}
                </option>
              ))}
            </select>
            {!selectedAvailable && s.enabled && (
              <p className="font-body text-[11px] text-orange-700 dark:text-orange-400 mt-1.5">
                {txt(
                  "Selected provider has no API key on the backend. Pick one marked ready.",
                  "所选服务商在后端无密钥。请改选已就绪的服务商。",
                )}
              </p>
            )}
          </div>

          {/* Model */}
          <div>
            <label className="block font-mono text-[10px] uppercase tracking-widest text-muted-fg mb-1.5">
              {txt("Model", "模型")}
            </label>
            <select
              value={s.model}
              onChange={(e) => update({ model: e.target.value })}
              className="w-full px-3 py-2 font-mono text-[13px] border border-border rounded-md bg-background"
            >
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <p className="font-body text-[11px] text-muted-fg mt-1.5">
              {txt(
                "Smaller models (deepseek-chat, gpt-4o-mini, glm-4-flash, claude-haiku) are faster and cheaper. Larger models translate long technical text better.",
                "小模型（deepseek-chat / gpt-4o-mini / glm-4-flash / claude-haiku）更快更便宜。大模型在长文技术内容上质量更好。",
              )}
            </p>
          </div>

          {/* Usage */}
          <div className="border border-border rounded-lg p-3 bg-muted/30">
            <p className="font-mono text-[10px] uppercase tracking-widest text-muted-fg mb-2">
              {txt("Usage so far", "累计用量")}
            </p>
            <div className="grid grid-cols-3 gap-3 font-mono text-[12px]">
              <div>
                <p className="text-muted-fg text-[10px]">{txt("Requests", "请求数")}</p>
                <p className="font-semibold">{s.requests.toLocaleString()}</p>
              </div>
              <div>
                <p className="text-muted-fg text-[10px]">{txt("Tokens in", "输入 token")}</p>
                <p className="font-semibold">{s.tokensIn.toLocaleString()}</p>
              </div>
              <div>
                <p className="text-muted-fg text-[10px]">{txt("Tokens out", "输出 token")}</p>
                <p className="font-semibold">{s.tokensOut.toLocaleString()}</p>
              </div>
            </div>
            <p className="font-mono text-[10px] text-muted-fg mt-2">
              {txt(`Cached translations: ${cacheCount}`, `已缓存翻译条数：${cacheCount}`)}
            </p>
            <div className="flex gap-2 mt-3">
              <button
                onClick={() => {
                  resetUsage();
                  setS(getSettings());
                }}
                className="px-2 py-1 font-mono text-[10px] uppercase tracking-wider border border-border rounded-md hover:bg-muted"
              >
                {txt("Reset counters", "重置计数")}
              </button>
              <button
                onClick={() => {
                  cacheClear();
                  setCacheCount(0);
                }}
                className="px-2 py-1 font-mono text-[10px] uppercase tracking-wider border border-border rounded-md hover:bg-muted"
              >
                {txt("Clear cache", "清空翻译缓存")}
              </button>
            </div>
          </div>

          {/* Secrets policy note */}
          <p className="font-body text-[11px] text-muted-fg leading-relaxed">
            {txt(
              "API keys are managed by 1Password and injected into the CORAL backend via `with-secrets llm`. They are never typed, copied, or stored in the browser.",
              "API 密钥由 1Password 管理，通过 `with-secrets llm` 注入 CORAL 后端进程，浏览器永远不接触密钥。",
            )}
          </p>
        </div>
      </div>
    </div>,
    document.body,
  );
}

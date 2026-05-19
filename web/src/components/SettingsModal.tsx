/**
 * Settings modal for the translation feature.
 *
 * The dashboard's Chinese-mode UX depends on the user supplying an LLM
 * API key. This modal walks the user through enabling translation,
 * picking a provider, and entering the key. It also surfaces the
 * cumulative token usage so the user can keep an eye on cost.
 */

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { createPortal } from "react-dom";
import {
  MODEL_OPTIONS,
  cacheClear,
  cacheSize,
  getSettings,
  resetUsage,
  setSettings,
  type Provider,
  type TranslateSettings,
} from "../lib/translate";

interface Props {
  open: boolean;
  onClose: () => void;
}

const PROVIDER_LABELS: Record<Provider, { en: string; zh: string }> = {
  anthropic: { en: "Anthropic (Claude)", zh: "Anthropic（Claude）" },
  openai: { en: "OpenAI (ChatGPT)", zh: "OpenAI（ChatGPT）" },
  deepseek: { en: "DeepSeek", zh: "DeepSeek" },
};

export default function SettingsModal({ open, onClose }: Props) {
  const { i18n } = useTranslation();
  const isZh = i18n.language.startsWith("zh");
  const [s, setS] = useState<TranslateSettings>(getSettings());
  const [cacheCount, setCacheCount] = useState<number>(0);

  useEffect(() => {
    if (open) {
      setS(getSettings());
      setCacheCount(cacheSize());
    }
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

  const update = (patch: Partial<TranslateSettings>) => {
    const next = setSettings(patch);
    setS(next);
  };

  const onProviderChange = (provider: Provider) => {
    const defaultModel = MODEL_OPTIONS[provider][0];
    update({ provider, model: defaultModel });
  };

  const txt = (en: string, zh: string) => (isZh ? zh : en);

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
          {/* Enable switch */}
          <label className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={s.enabled}
              onChange={(e) => update({ enabled: e.target.checked })}
              className="mt-1 w-4 h-4"
            />
            <div className="flex-1">
              <p className="font-display text-[14px] font-medium">
                {txt("Enable automatic translation", "启用自动翻译")}
              </p>
              <p className="font-body text-[12px] text-muted-fg mt-0.5 leading-relaxed">
                {txt(
                  "When the UI language is set to Chinese, agent-generated content (attempt titles, notes, feedback, logs) is sent to the selected LLM provider and translated on the fly. Source data is never modified — translations live only in your browser.",
                  "当界面语言切换为中文时，agent 产生的英文内容（尝试标题、笔记、评测反馈、日志）会通过你选的 LLM 服务实时翻译。原数据不会被修改，翻译结果只缓存在你本地浏览器。",
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
                <option key={p} value={p}>
                  {isZh ? PROVIDER_LABELS[p].zh : PROVIDER_LABELS[p].en}
                </option>
              ))}
            </select>
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
              {MODEL_OPTIONS[s.provider].map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <p className="font-body text-[11px] text-muted-fg mt-1.5">
              {txt(
                "Smaller models (Haiku, gpt-4o-mini) are faster and cheaper. Larger models give better translation for long technical text.",
                "小模型（Haiku、gpt-4o-mini）更快更便宜。大模型在长文技术内容上翻译质量更好。",
              )}
            </p>
          </div>

          {/* API Key */}
          <div>
            <label className="block font-mono text-[10px] uppercase tracking-widest text-muted-fg mb-1.5">
              {txt("API Key", "API 密钥")}
            </label>
            <input
              type="password"
              value={s.apiKey}
              onChange={(e) => update({ apiKey: e.target.value })}
              placeholder={txt("paste your key", "粘贴你的密钥")}
              className="w-full px-3 py-2 font-mono text-[13px] border border-border rounded-md bg-background"
              autoComplete="off"
            />
            <p className="font-body text-[11px] text-muted-fg mt-1.5">
              {txt(
                "The key is stored only in your browser's localStorage and sent directly to the provider — never to CORAL servers.",
                "密钥只存在你本地浏览器的 localStorage 里，直接发送给服务商，不会经过 CORAL 任何服务器。",
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
        </div>
      </div>
    </div>,
    document.body,
  );
}

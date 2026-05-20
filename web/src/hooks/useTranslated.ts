/**
 * useTranslated — return the Chinese translation of an English string
 * (or "translating" / "error" status) for use in JSX.
 *
 * Behavior:
 *   - When current language is English (or i18n not "zh"), return the
 *     original text as-is. No API calls.
 *   - When current language is "zh":
 *     - If translation is cached, return it immediately.
 *     - Else enqueue translation. Return ("translating", "翻译中...")
 *       until the result arrives, then re-render with the Chinese text.
 *     - On error, return ("error", <last value or placeholder>).
 *
 * IMPORTANT: This hook never returns the raw English when in zh mode
 * (per product requirement: Chinese UI must not leak English).
 *
 * For very short content like commit hashes, agent ids, model names,
 * skip translation by passing `kind: "identifier"` — the hook returns
 * the value unchanged regardless of language.
 */

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { cacheGet, enqueue, sha1, subscribe } from "../lib/translate";
import { getSettings } from "../lib/translate";

export type ContentKind =
  | "attempt_title"
  | "feedback"
  | "note_title"
  | "note_body"
  | "skill_name"
  | "skill_description"
  | "agent_log"
  | "identifier"; // never translate

interface UseTranslatedResult {
  text: string;
  status: "ready" | "translating" | "error" | "disabled";
  retry: () => void;
}

const TRANSLATING_PLACEHOLDER = "翻译中…";

export function useTranslated(
  source: string | null | undefined,
  kind: ContentKind = "agent_log",
): UseTranslatedResult {
  const { i18n } = useTranslation();
  const lang = i18n.language;
  const isZh = lang.startsWith("zh");

  const safe = source ?? "";
  const [translated, setTranslated] = useState<string | null>(null);
  const [status, setStatus] = useState<UseTranslatedResult["status"]>("ready");
  const [tick, setTick] = useState(0); // retry trigger

  // Identifiers always pass through
  const passThrough = kind === "identifier" || !isZh || !safe.trim();

  // Stable key for effect dependency: hash of source. We compute it lazily.
  const [hash, setHash] = useState<string | null>(null);
  useEffect(() => {
    if (passThrough) {
      setHash(null);
      return;
    }
    let cancelled = false;
    sha1(safe).then((h) => {
      if (!cancelled) setHash(h);
    });
    return () => {
      cancelled = true;
    };
  }, [safe, passThrough]);

  // Look up cache + enqueue if missing
  useEffect(() => {
    if (passThrough || !hash) return;
    let cancelled = false;

    (async () => {
      const cached = await cacheGet(safe);
      if (cancelled) return;
      if (cached) {
        setTranslated(cached);
        setStatus("ready");
        return;
      }
      // Not cached. Check settings; if not enabled, mark disabled.
      const s = getSettings();
      if (!s.enabled) {
        setStatus("disabled");
        return;
      }
      // Enqueue and listen for completion via subscription.
      setStatus("translating");
      enqueue(safe).catch(() => {
        /* error path handled in subscriber */
      });
    })();

    const unsub = subscribe(hash, (st, value, _err) => {
      if (cancelled) return;
      if (st === "translated" && value != null) {
        setTranslated(value);
        setStatus("ready");
      } else if (st === "translating") {
        setStatus("translating");
      } else if (st === "error") {
        setStatus("error");
      }
    });

    return () => {
      cancelled = true;
      unsub();
    };
  }, [hash, safe, passThrough, tick]);

  // Re-trigger translation when API settings change (e.g. user pasted key)
  useEffect(() => {
    if (passThrough) return;
    const handler = () => setTick((n) => n + 1);
    window.addEventListener("coral-translate-settings-changed", handler);
    return () =>
      window.removeEventListener("coral-translate-settings-changed", handler);
  }, [passThrough]);

  return useMemo<UseTranslatedResult>(() => {
    if (passThrough) return { text: safe, status: "ready", retry: () => setTick((n) => n + 1) };
    if (translated != null) return { text: translated, status, retry: () => setTick((n) => n + 1) };
    if (status === "disabled") {
      // Translation feature is off. The Chinese view shouldn't leak
      // English, so we surface a localized hint instead.
      return { text: "（翻译未启用，请打开设置）", status: "disabled", retry: () => setTick((n) => n + 1) };
    }
    if (status === "error") {
      return {
        text: "翻译失败 · 点击重试",
        status: "error",
        retry: () => setTick((n) => n + 1),
      };
    }
    return { text: TRANSLATING_PLACEHOLDER, status: "translating", retry: () => setTick((n) => n + 1) };
  }, [passThrough, safe, translated, status]);
}

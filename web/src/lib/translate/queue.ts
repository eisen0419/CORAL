/**
 * Translation queue with concurrency limit, in-flight dedup, retry,
 * and a pub/sub layer the React hook listens on.
 *
 * Why a queue? The dashboard can ask for hundreds of translations on
 * a single language switch. Firing 200 parallel API requests would
 * trigger provider rate limits. We cap at 3 concurrent and dedup by
 * source hash so the same text in flight is only requested once.
 */

import { cacheGet, cacheSet, sha1 } from "./cache";
import { translateRaw } from "./providers";
import { bumpUsage, getSettings } from "./settings";

const MAX_CONCURRENT = 3;
const MAX_RETRIES = 3;

type Status = "translating" | "translated" | "error" | "skipped";

interface Listener {
  hash: string;
  cb: (status: Status, value?: string, error?: string) => void;
}

const inFlight = new Map<string, Promise<string>>();
const listeners: Listener[] = [];

function emit(hash: string, status: Status, value?: string, error?: string) {
  for (const l of listeners) {
    if (l.hash === hash) l.cb(status, value, error);
  }
}

export function subscribe(
  hash: string,
  cb: (status: Status, value?: string, error?: string) => void,
): () => void {
  const entry = { hash, cb };
  listeners.push(entry);
  return () => {
    const i = listeners.indexOf(entry);
    if (i >= 0) listeners.splice(i, 1);
  };
}

let activeCount = 0;
const waiters: Array<() => void> = [];

async function acquireSlot(): Promise<void> {
  if (activeCount < MAX_CONCURRENT) {
    activeCount++;
    return;
  }
  await new Promise<void>((resolve) => waiters.push(resolve));
  activeCount++;
}

function releaseSlot(): void {
  activeCount--;
  const next = waiters.shift();
  if (next) next();
}

async function doTranslate(text: string): Promise<string> {
  const settings = getSettings();
  if (!settings.enabled) {
    throw new Error("translation_disabled");
  }
  let lastErr: unknown = null;
  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      await acquireSlot();
      try {
        const r = await translateRaw(text, settings);
        bumpUsage(r.tokensIn, r.tokensOut);
        return r.text;
      } finally {
        releaseSlot();
      }
    } catch (e) {
      lastErr = e;
      // Exponential backoff: 500ms, 1s, 2s
      await new Promise((r) => setTimeout(r, 500 * 2 ** (attempt - 1)));
    }
  }
  throw lastErr;
}

/**
 * Request a translation. Returns existing cached/in-flight result
 * if already known; otherwise enqueues and resolves to the result.
 *
 * Subscribers (the React hook) get status updates via subscribe().
 */
export async function enqueue(text: string): Promise<string> {
  if (!text || !text.trim()) return text;

  const hash = await sha1(text);

  // 1. Cache hit
  const cached = await cacheGet(text);
  if (cached) return cached;

  // 2. In-flight dedup
  const existing = inFlight.get(hash);
  if (existing) return existing;

  // 3. Fresh request
  emit(hash, "translating");
  const promise = doTranslate(text)
    .then(async (translated) => {
      await cacheSet(text, translated);
      emit(hash, "translated", translated);
      return translated;
    })
    .catch((err) => {
      const message = err instanceof Error ? err.message : String(err);
      emit(hash, "error", undefined, message);
      throw err;
    })
    .finally(() => {
      inFlight.delete(hash);
    });

  inFlight.set(hash, promise);
  return promise;
}

/** Forget any in-flight requests (called on settings change). */
export function reset(): void {
  inFlight.clear();
}

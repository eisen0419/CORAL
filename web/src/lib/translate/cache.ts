/**
 * Translation cache layer.
 *
 * Keyed by SHA-1 of the source text. Two layers:
 *   1. In-memory Map (fast, current session)
 *   2. localStorage (persistent across reloads)
 *
 * The cache is content-addressed: same English text always yields the
 * same cache key regardless of where it appears in the UI. This means
 * an attempt title shared across multiple views is only translated once.
 */

const STORAGE_KEY = "coral-translations-v1";
const MAX_ENTRIES = 5000;

interface CacheEntry {
  text: string;
  ts: number;
}

let mem: Map<string, CacheEntry> | null = null;

function loadFromStorage(): Map<string, CacheEntry> {
  if (mem) return mem;
  mem = new Map();
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const obj = JSON.parse(raw) as Record<string, CacheEntry>;
      for (const [k, v] of Object.entries(obj)) mem.set(k, v);
    }
  } catch {
    /* ignore */
  }
  return mem;
}

function persist(): void {
  if (!mem) return;
  try {
    // Sort by recency, drop oldest if over cap
    const entries = [...mem.entries()].sort((a, b) => b[1].ts - a[1].ts);
    if (entries.length > MAX_ENTRIES) entries.length = MAX_ENTRIES;
    const obj: Record<string, CacheEntry> = {};
    for (const [k, v] of entries) obj[k] = v;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(obj));
    mem = new Map(entries);
  } catch {
    /* localStorage quota errors are non-fatal */
  }
}

export async function sha1(text: string): Promise<string> {
  const buf = new TextEncoder().encode(text);
  const hash = await crypto.subtle.digest("SHA-1", buf);
  return [...new Uint8Array(hash)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export async function cacheGet(text: string): Promise<string | null> {
  const map = loadFromStorage();
  const key = await sha1(text);
  return map.get(key)?.text ?? null;
}

export async function cacheSet(text: string, translated: string): Promise<void> {
  const map = loadFromStorage();
  const key = await sha1(text);
  map.set(key, { text: translated, ts: Date.now() });
  persist();
}

export function cacheClear(): void {
  mem = new Map();
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export function cacheSize(): number {
  return loadFromStorage().size;
}

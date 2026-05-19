/**
 * LLM provider adapters for translation.
 *
 * Each provider returns { text, tokensIn, tokensOut }.
 * Errors bubble up — the caller handles retry/fallback.
 */

import type { Provider, TranslateSettings } from "./settings";

export interface TranslationResult {
  text: string;
  tokensIn: number;
  tokensOut: number;
}

const SYSTEM_PROMPT =
  "You translate technical English text about AI agents and multi-agent " +
  "systems into Simplified Chinese (zh-CN). The text comes from CORAL, a " +
  "framework where AI agents iteratively improve code via grading. " +
  "Rules: " +
  "(1) Keep code identifiers, file paths, commit hashes, agent IDs, " +
  "function names, command-line flags, and version numbers in English. " +
  "(2) Preserve markdown / code-block formatting exactly. " +
  "(3) Use natural fluent Chinese for prose. " +
  "(4) Translate concept words (e.g. 'reasoning' → '推理', 'attempt' → '尝试'). " +
  "(5) Output the translation ONLY — no preamble, no explanation, no quote marks.";

function buildUserPrompt(text: string): string {
  return `Translate the following text into Simplified Chinese. Output only the translation.\n\n${text}`;
}

async function callAnthropic(
  text: string,
  s: TranslateSettings,
): Promise<TranslationResult> {
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": s.apiKey,
      "anthropic-version": "2023-06-01",
      "anthropic-dangerous-direct-browser-access": "true",
    },
    body: JSON.stringify({
      model: s.model,
      max_tokens: 4096,
      system: SYSTEM_PROMPT,
      messages: [{ role: "user", content: buildUserPrompt(text) }],
    }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Anthropic ${res.status}: ${body.slice(0, 200)}`);
  }
  const data = await res.json();
  const out: string = data.content?.[0]?.text ?? "";
  return {
    text: out.trim(),
    tokensIn: data.usage?.input_tokens ?? 0,
    tokensOut: data.usage?.output_tokens ?? 0,
  };
}

async function callOpenAI(
  text: string,
  s: TranslateSettings,
): Promise<TranslationResult> {
  const res = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${s.apiKey}`,
    },
    body: JSON.stringify({
      model: s.model,
      messages: [
        { role: "system", content: SYSTEM_PROMPT },
        { role: "user", content: buildUserPrompt(text) },
      ],
      temperature: 0.2,
    }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`OpenAI ${res.status}: ${body.slice(0, 200)}`);
  }
  const data = await res.json();
  const out: string = data.choices?.[0]?.message?.content ?? "";
  return {
    text: out.trim(),
    tokensIn: data.usage?.prompt_tokens ?? 0,
    tokensOut: data.usage?.completion_tokens ?? 0,
  };
}

async function callDeepseek(
  text: string,
  s: TranslateSettings,
): Promise<TranslationResult> {
  const res = await fetch("https://api.deepseek.com/chat/completions", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${s.apiKey}`,
    },
    body: JSON.stringify({
      model: s.model,
      messages: [
        { role: "system", content: SYSTEM_PROMPT },
        { role: "user", content: buildUserPrompt(text) },
      ],
      temperature: 0.2,
    }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`DeepSeek ${res.status}: ${body.slice(0, 200)}`);
  }
  const data = await res.json();
  const out: string = data.choices?.[0]?.message?.content ?? "";
  return {
    text: out.trim(),
    tokensIn: data.usage?.prompt_tokens ?? 0,
    tokensOut: data.usage?.completion_tokens ?? 0,
  };
}

const DISPATCH: Record<Provider, (t: string, s: TranslateSettings) => Promise<TranslationResult>> = {
  anthropic: callAnthropic,
  openai: callOpenAI,
  deepseek: callDeepseek,
};

export async function translateRaw(
  text: string,
  s: TranslateSettings,
): Promise<TranslationResult> {
  return DISPATCH[s.provider](text, s);
}

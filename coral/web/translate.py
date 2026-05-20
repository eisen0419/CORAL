"""Translation endpoint — proxy LLM provider calls from the dashboard.

The browser never holds API keys. When the CORAL UI is started with
keys injected via `with-secrets llm -- coral ui ...`, this endpoint
reads them from the process environment and dispatches the request
to the chosen provider. The frontend simply POSTs the text it wants
translated; the backend does the rest.

This keeps secrets out of localStorage, out of the page, and out of
the network round-trip log: the key only lives in this process'
memory during a single LLM call.
"""

from __future__ import annotations

import os

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

# Map provider name -> env var that holds its API key.
# Matches `~/.config/op/profiles/llm.env` so a user running
# `with-secrets llm -- coral ui ...` gets all of these populated.
_PROVIDER_ENV: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "zhipu": "ZHIPU_API_KEY",
}

# Default model for each provider when the client doesn't specify one.
_DEFAULT_MODELS: dict[str, str] = {
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5-20251001",
    "zhipu": "glm-4-flash",
}

# Allowed models per provider — guards against the client injecting an
# arbitrary string that costs more than expected. Stored as lists so the
# /health endpoint can serialize them straight to JSON; membership checks
# inline a list -> set conversion (small enough that it's free).
_ALLOWED_MODELS: dict[str, list[str]] = {
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "openai": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
    "anthropic": [
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-6",
        "claude-opus-4-7",
    ],
    "zhipu": ["glm-4-flash", "glm-4-plus", "glm-4-air"],
}

_SYSTEM_PROMPT = (
    "You translate technical English text about AI agents and multi-agent "
    "systems into Simplified Chinese (zh-CN). The text comes from CORAL, a "
    "framework where AI agents iteratively improve code via grading. Rules: "
    "(1) Keep code identifiers, file paths, commit hashes, agent IDs, "
    "function names, command-line flags, and version numbers in English. "
    "(2) Preserve markdown / code-block formatting exactly. "
    "(3) Use natural fluent Chinese for prose. "
    "(4) Translate concept words (e.g. 'reasoning' -> '推理'). "
    "(5) Output the translation ONLY -- no preamble, no explanation, no quotes."
)


def _user_prompt(text: str) -> str:
    return (
        "Translate the following text into Simplified Chinese. "
        "Output only the translation.\n\n" + text
    )


async def translate_health(request: Request) -> JSONResponse:
    """GET /api/translate/health -- report which providers have keys configured."""
    available = {
        name: bool(os.environ.get(env_var))
        for name, env_var in _PROVIDER_ENV.items()
    }
    # Prefer deepseek -> openai -> zhipu -> anthropic for the default,
    # matching what `~/.config/op/profiles/llm.env` typically activates first.
    preferred_order = ["deepseek", "openai", "zhipu", "anthropic"]
    default = next((p for p in preferred_order if available.get(p)), None)
    return JSONResponse(
        {
            "available": available,
            "default": default,
            "models": _ALLOWED_MODELS,
            "default_models": _DEFAULT_MODELS,
        }
    )


def _build_request(provider: str, model: str, text: str, api_key: str) -> tuple[str, dict, dict]:
    """Return (url, headers, json_payload) for the given provider."""
    user = _user_prompt(text)

    if provider == "anthropic":
        return (
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            {
                "model": model,
                "max_tokens": 4096,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user}],
            },
        )

    if provider == "deepseek":
        url = "https://api.deepseek.com/chat/completions"
    elif provider == "openai":
        url = "https://api.openai.com/v1/chat/completions"
    elif provider == "zhipu":
        url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    else:
        raise ValueError(f"unknown provider: {provider}")

    return (
        url,
        {
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        },
        {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        },
    )


def _parse_response(provider: str, data: dict) -> tuple[str, int, int]:
    """Extract (translated_text, tokens_in, tokens_out) from a provider response."""
    if provider == "anthropic":
        text = (data.get("content") or [{}])[0].get("text", "").strip()
        usage = data.get("usage") or {}
        return text, usage.get("input_tokens", 0), usage.get("output_tokens", 0)

    # OpenAI-compatible (deepseek / openai / zhipu)
    choices = data.get("choices") or []
    text = (choices[0].get("message") or {}).get("content", "").strip() if choices else ""
    usage = data.get("usage") or {}
    return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


async def translate(request: Request) -> JSONResponse:
    """POST /api/translate -- translate `text` via the chosen provider's LLM."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    text = str(body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "empty_text"}, status_code=400)
    if len(text) > 20000:
        return JSONResponse({"error": "text_too_long"}, status_code=413)

    provider = str(body.get("provider") or "deepseek").lower()
    env_var = _PROVIDER_ENV.get(provider)
    if env_var is None:
        return JSONResponse({"error": f"unknown_provider:{provider}"}, status_code=400)

    api_key = os.environ.get(env_var)
    if not api_key:
        return JSONResponse(
            {
                "error": "missing_api_key",
                "detail": (
                    f"{env_var} is not set in the CORAL UI process. "
                    f"Start the UI via `with-secrets llm -- coral ui ...` "
                    f"so the key is injected from 1Password."
                ),
            },
            status_code=503,
        )

    model = str(body.get("model") or _DEFAULT_MODELS[provider])
    if model not in _ALLOWED_MODELS[provider]:
        return JSONResponse(
            {"error": f"unsupported_model:{model}"}, status_code=400
        )

    try:
        url, headers, payload = _build_request(provider, model, text, api_key)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            body_preview = r.text[:300] if r.text else ""
            return JSONResponse(
                {
                    "error": f"{provider}_{r.status_code}",
                    "detail": body_preview,
                },
                status_code=502,
            )
        data = r.json()
    except httpx.TimeoutException:
        return JSONResponse({"error": "upstream_timeout"}, status_code=504)
    except Exception as e:
        return JSONResponse(
            {"error": "upstream_request_failed", "detail": str(e)[:200]},
            status_code=502,
        )

    translated, tokens_in, tokens_out = _parse_response(provider, data)
    if not translated:
        return JSONResponse(
            {"error": "empty_translation", "detail": "provider returned no content"},
            status_code=502,
        )

    return JSONResponse(
        {
            "text": translated,
            "tokensIn": tokens_in,
            "tokensOut": tokens_out,
            "provider": provider,
            "model": model,
        }
    )

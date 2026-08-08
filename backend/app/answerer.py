"""Answerers under test.

Two engines answer probe questions using only catalog metadata:

`simulated` is a deterministic behavioural model. It is not a language model and
does not pretend to be one. It encodes one explicit, falsifiable hypothesis:

    A description that is present but missing the needed fact invites a
    confident guess, while a description that is absent, or that declares its
    own uncertainty, produces an abstention.

`llm` sends the same context to a real OpenAI-compatible model, so the
hypothesis above can be tested rather than assumed.
"""

import json
import os
import re
import time
from collections import Counter
from typing import Any

import httpx

# Phrases where the metadata openly signals that it is not trustworthy yet.
HEDGE_MARKERS = (
    "unverified", "tbd", "unknown", "see owner", "not confirmed",
    "deprecated", "do not use", "draft",
)


def _relevant_description(context: dict[str, Any], column_name: str | None) -> str:
    if column_name:
        for column in context["columns"]:
            if column["name"] == column_name:
                return (column["description"] or "").strip()
        return ""
    return (context["asset_description"] or "").strip()


def answerable(context_text_value: str, required_terms: list[str]) -> bool:
    """True when every fact the question needs is present in the given context."""
    return all(term.lower() in context_text_value for term in required_terms)


def simulated_answer(
    probe: dict[str, Any], context: dict[str, Any], context_text_value: str
) -> dict[str, Any]:
    description = _relevant_description(context, probe["column_name"])
    has_facts = answerable(context_text_value, probe["required_terms"])
    hedged = any(marker in context_text_value for marker in HEDGE_MARKERS)

    if has_facts:
        return {"answer": probe["expected_answer"], "abstained": False, "reason": "facts_present"}
    if not description:
        return {
            "answer": "The provided metadata does not contain this information.",
            "abstained": True,
            "reason": "no_description",
        }
    if hedged:
        return {
            "answer": "The metadata flags itself as unverified, so I cannot answer.",
            "abstained": True,
            "reason": "metadata_declares_uncertainty",
        }
    return {"answer": probe["wrong_answer"], "abstained": False, "reason": "vague_description"}


SYSTEM_PROMPT = (
    "You answer questions about a data asset using ONLY the catalog metadata provided. "
    "Never use outside knowledge or assumptions based on column names. "
    "If the metadata does not contain the answer, say so plainly instead of guessing. "
    'Reply with only a JSON object: {"answer": string, "abstained": boolean}.'
)

# Not every provider supports response_format. Negotiated once per process rather
# than per probe, so one 400 does not cost 18 retries.
_json_mode_supported: bool | None = None

# Call outcomes, so a study can report how much of its data actually came from the
# model. A silent fallback that gets counted as a model result is a contaminated
# measurement, which is worse than a missing one.
stats: Counter = Counter()

RETRY_STATUSES = {408, 409, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = (2.0, 5.0, 12.0)


def reset_stats() -> None:
    stats.clear()

JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def llm_available() -> bool:
    return bool(os.getenv("LLM_API_KEY") and os.getenv("LLM_MODEL"))


def _rejects_json_mode(response: httpx.Response) -> bool:
    if response.status_code not in {400, 404, 422}:
        return False
    body = response.text.lower()
    return any(
        marker in body
        for marker in ("structured-outputs", "structured outputs", "response_format", "json_object")
    )


def _parse(content: str) -> dict[str, Any]:
    """Accept JSON, fenced JSON, or plain prose.

    A prose answer is still gradeable: the grader detects abstention from phrasing
    and correctness from ground-truth markers, so JSON was only ever a convenience
    for the explicit abstained flag.
    """
    cleaned = FENCE.sub("", content.strip())
    for candidate in (cleaned, (JSON_OBJECT.search(cleaned) or type("", (), {"group": lambda _: ""})()).group(0)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("answer"), str):
            return {
                "answer": parsed["answer"].strip(),
                "abstained": bool(parsed.get("abstained")),
                "reason": "llm_json",
            }
    return {"answer": cleaned, "abstained": False, "reason": "llm_prose"}


def llm_answer(probe: dict[str, Any], context: dict[str, Any]) -> dict[str, Any] | None:
    """Ask a real model the same question. Returns None when unavailable or failing."""
    global _json_mode_supported
    if not llm_available():
        return None
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps({"metadata": context, "question": probe["question"]}),
        },
    ]

    delay = float(os.getenv("LLM_DELAY_SECONDS", "0.6"))
    attempt = 0
    while attempt < MAX_ATTEMPTS:
        attempt += 1
        use_json_mode = _json_mode_supported is not False
        payload: dict[str, Any] = {
            "model": os.getenv("LLM_MODEL"),
            "temperature": 0,
            "messages": messages,
        }
        if use_json_mode:
            payload["response_format"] = {"type": "json_object"}

        if delay:
            time.sleep(delay)
        stats["calls"] += 1
        try:
            response = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {os.getenv('LLM_API_KEY')}"},
                json=payload,
                timeout=90.0,
            )
        except httpx.HTTPError as error:
            stats[f"transport:{type(error).__name__}"] += 1
            return None

        if use_json_mode and _rejects_json_mode(response):
            _json_mode_supported = False
            stats["json_mode_unsupported"] += 1
            attempt -= 1  # negotiating the format down is not a failed attempt
            continue

        if response.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
            stats[f"retry:{response.status_code}"] += 1
            wait = BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)]
            header = response.headers.get("retry-after")
            if header and header.isdigit():
                wait = max(wait, min(float(header), 30.0))
            time.sleep(wait)
            continue

        if response.status_code != 200:
            stats[f"http:{response.status_code}"] += 1
            return None

        if use_json_mode:
            _json_mode_supported = True
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            stats["malformed_envelope"] += 1
            return None
        if not isinstance(content, str) or not content.strip():
            stats["empty_content"] += 1
            return None
        stats["ok"] += 1
        return _parse(content)

    stats["exhausted_retries"] += 1
    return None

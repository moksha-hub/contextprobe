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
    "Never use outside knowledge or assumptions about naming conventions. "
    "If the metadata does not contain the answer, set abstained to true. "
    'Reply with JSON: {"answer": string, "abstained": boolean}.'
)


def llm_available() -> bool:
    return bool(os.getenv("LLM_API_KEY") and os.getenv("LLM_MODEL"))


def llm_answer(probe: dict[str, Any], context: dict[str, Any]) -> dict[str, Any] | None:
    """Ask a real model the same question. Returns None when unavailable or failing."""
    if not llm_available():
        return None
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    payload = {
        "model": os.getenv("LLM_MODEL"),
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps({"metadata": context, "question": probe["question"]}),
            },
        ],
    }
    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('LLM_API_KEY')}"},
            json=payload,
            timeout=30.0,
        )
        response.raise_for_status()
        parsed = json.loads(response.json()["choices"][0]["message"]["content"])
    except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    answer = parsed.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return None
    return {
        "answer": answer.strip(),
        "abstained": bool(parsed.get("abstained")),
        "reason": "llm_response",
    }

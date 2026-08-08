"""One-shot connectivity and JSON-mode check for the configured provider.

Reads credentials from the environment only. Run with:
  py backend\\llmcheck.py
"""

import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import env  # noqa: E402

env.load()

key = os.getenv("LLM_API_KEY")
model = os.getenv("LLM_MODEL")
base = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")

if not key or not model:
    print("LLM_API_KEY and LLM_MODEL must be set")
    sys.exit(1)

print(f"model    {model}")
print(f"base_url {base}")
print(f"key      ...{key[-4:]} ({len(key)} chars)")

payload = {
    "model": model,
    "temperature": 0,
    "response_format": {"type": "json_object"},
    "messages": [
        {
            "role": "system",
            "content": 'Reply only with JSON: {"answer": string, "abstained": boolean}.',
        },
        {
            "role": "user",
            "content": json.dumps({
                "metadata": {
                    "asset": "fct_revenue",
                    "asset_description": "Revenue table.",
                    "columns": [
                        {"name": "net_revenue", "type": "decimal", "description": "Net revenue."}
                    ],
                },
                "question": "Does net_revenue include tax?",
            }),
        },
    ],
}

try:
    response = httpx.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=60.0,
    )
except httpx.HTTPError as error:
    print(f"TRANSPORT FAILURE: {type(error).__name__}: {error}")
    sys.exit(1)

print(f"status   {response.status_code}")
if response.status_code != 200:
    print(f"body     {response.text[:600]}")
    sys.exit(1)

body = response.json()
content = body["choices"][0]["message"]["content"]
print(f"raw      {content[:400]!r}")
try:
    parsed = json.loads(content)
    print(f"parsed   {parsed}")
    print("json_mode OK")
except json.JSONDecodeError as error:
    print(f"JSON PARSE FAILED: {error}")
    print("the harness will fall back to the simulated engine for every probe")

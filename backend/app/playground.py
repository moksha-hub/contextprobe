"""Stateless proof-carrying probe compiler and metadata mutation runner."""

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import answerer

COMPILER_VERSION = "proof-probe-v1"
LABELS = ("SUPPORTED", "CONTRADICTED", "NOT_STATED")
VARIANT_LABELS = {
    "original": "SUPPORTED",
    "flip": "CONTRADICTED",
    "remove": "NOT_STATED",
    "pad": "SUPPORTED",
}
PAD_SENTENCE = "This catalog entry is maintained for discovery and documentation workflows."
MAX_CLAIMS = 12

# Every replacement is reversible and local. Inflections are explicit so the
# compiler never asks a model to invent either the claim or its opposite.
_OPERATOR_FAMILIES: tuple[dict[str, Any], ...] = (
    {
        "name": "include_exclude",
        "pairs": {
            "include": "exclude", "includes": "excludes",
            "including": "excluding", "included": "excluded",
            "exclude": "include", "excludes": "includes",
            "excluding": "including", "excluded": "included",
        },
    },
    {"name": "before_after", "pairs": {"before": "after", "after": "before"}},
    {"name": "with_without", "pairs": {"with": "without", "without": "with"}},
)
SUPPORTED_OPERATORS = [
    {
        "name": "include_exclude",
        "pairs": [
            "include ↔ exclude", "includes ↔ excludes",
            "including ↔ excluding", "included ↔ excluded",
        ],
    },
    {"name": "before_after", "pairs": ["before ↔ after"]},
    {"name": "with_without", "pairs": ["with ↔ without"]},
]
_CLAUSE = re.compile(r"[^.;\n]+")
_OPERATOR_PATTERNS = tuple(
    (
        family["name"],
        family["pairs"],
        re.compile(
            r"(?<!\w)(" + "|".join(
                re.escape(token) for token in sorted(family["pairs"], key=len, reverse=True)
            ) + r")(?!\w)",
            re.IGNORECASE,
        ),
    )
    for family in _OPERATOR_FAMILIES
)


class PlaygroundUnavailable(RuntimeError):
    """Raised when explicit real-model execution cannot produce a label."""


class PlaygroundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column_name: str = Field(min_length=1, max_length=128)
    data_type: str | None = Field(default=None, max_length=128)
    description: str = Field(min_length=1, max_length=5000)
    mode: Literal["auto", "llm", "simulated"] = "auto"
    pad_repetitions: int = Field(default=2, ge=1, le=3)


def _preserve_case(source: str, replacement: str) -> str:
    if source.isupper():
        return replacement.upper()
    if source.istitle():
        return replacement.title()
    return replacement.lower()


def _replace(value: str, start: int, end: int, replacement: str) -> str:
    return value[:start] + replacement + value[end:]


def _source_hash(description: str) -> str:
    return hashlib.sha256(description.encode("utf-8")).hexdigest()


def _stable_id(
    source_sha256: str,
    evidence_start: int,
    evidence_end: int,
    operator_start: int,
    family: str,
    source_term: str,
) -> str:
    identity = "|".join(
        (
            COMPILER_VERSION, source_sha256, str(evidence_start), str(evidence_end),
            str(operator_start), family, source_term.lower(),
        )
    )
    return "pcp_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _variants(
    description: str,
    evidence_start: int,
    evidence_end: int,
    operator_start: int,
    operator_end: int,
    opposite: str,
    pad_repetitions: int,
) -> list[dict[str, Any]]:
    flipped = _replace(description, operator_start, operator_end, opposite)
    removed = _replace(description, evidence_start, evidence_end, "")
    padding = " " + " ".join(PAD_SENTENCE for _ in range(pad_repetitions))
    values = {
        "original": description,
        "flip": flipped,
        "remove": removed,
        "pad": description + padding,
    }
    return [
        {
            "kind": kind,
            "description": values[kind],
            "expected_label": VARIANT_LABELS[kind],
        }
        for kind in ("original", "flip", "remove", "pad")
    ]


def compile_proofs(
    description: str, pad_repetitions: int = 2
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compile only unambiguous claims already present in the source text."""
    if not 1 <= pad_repetitions <= 3:
        raise ValueError("Padding repetitions must be between 1 and 3")

    source_sha256 = _source_hash(description)
    proofs: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    for clause_match in _CLAUSE.finditer(description):
        raw = clause_match.group(0)
        left = len(raw) - len(raw.lstrip())
        right = len(raw.rstrip())
        if right <= left:
            continue
        evidence_start = clause_match.start() + left
        evidence_end = clause_match.start() + right
        evidence = description[evidence_start:evidence_end]

        matches: list[tuple[str, dict[str, str], re.Match[str]]] = []
        for family, pairs, pattern in _OPERATOR_PATTERNS:
            matches.extend((family, pairs, found) for found in pattern.finditer(evidence))
        matches.sort(key=lambda item: item[2].start())
        if not matches:
            continue
        if len(matches) != 1:
            diagnostics.append({
                "code": "ambiguous_clause",
                "message": "Skipped a clause containing more than one supported operator.",
                "span": {"start": evidence_start, "end": evidence_end, "text": evidence},
            })
            continue
        if description.count(evidence) != 1:
            diagnostics.append({
                "code": "duplicate_evidence",
                "message": "Skipped repeated evidence because its source span is not unique.",
                "span": {"start": evidence_start, "end": evidence_end, "text": evidence},
            })
            continue

        family, pairs, operator_match = matches[0]
        source_term = operator_match.group(0)
        opposite = _preserve_case(source_term, pairs[source_term.lower()])
        operator_start = evidence_start + operator_match.start()
        operator_end = evidence_start + operator_match.end()
        relative_start = operator_start - evidence_start
        relative_end = operator_end - evidence_start
        opposite_claim = _replace(evidence, relative_start, relative_end, opposite)
        if opposite_claim in description:
            diagnostics.append({
                "code": "opposite_already_present",
                "message": "Skipped a claim whose controlled opposite is already present.",
                "span": {"start": evidence_start, "end": evidence_end, "text": evidence},
            })
            continue

        proof_id = _stable_id(
            source_sha256, evidence_start, evidence_end, operator_start, family, source_term
        )
        proofs.append({
            "id": proof_id,
            "source_description_sha256": source_sha256,
            "claim": evidence,
            "opposite_claim": opposite_claim,
            "evidence_span": {
                "start": evidence_start, "end": evidence_end, "text": evidence,
            },
            "mutation_span": {
                "start": operator_start, "end": operator_end, "text": source_term,
            },
            "operator": {
                "family": family, "source": source_term, "opposite": opposite,
            },
            "variants": _variants(
                description, evidence_start, evidence_end, operator_start,
                operator_end, opposite, pad_repetitions,
            ),
        })
        if len(proofs) == MAX_CLAIMS:
            diagnostics.append({
                "code": "claim_limit_reached",
                "message": f"Compiled the first {MAX_CLAIMS} claims; remaining text was not evaluated.",
            })
            break

    if not proofs:
        diagnostics.append({
            "code": "no_supported_claim",
            "message": (
                "No unambiguous include/exclude, before/after, or with/without claim "
                "was found. No test was invented."
            ),
        })
    validate_proofs(description, proofs, pad_repetitions)
    return proofs, diagnostics


def validate_proofs(
    description: str, proofs: list[dict[str, Any]], pad_repetitions: int
) -> None:
    """Replay every transform and reject a malformed proof before model execution."""
    ids: set[str] = set()
    previous_end = -1
    source_sha256 = _source_hash(description)
    padding = " " + " ".join(PAD_SENTENCE for _ in range(pad_repetitions))

    for proof in proofs:
        if proof["id"] in ids:
            raise ValueError("Duplicate proof ID")
        ids.add(proof["id"])
        evidence = proof["evidence_span"]
        mutation = proof["mutation_span"]
        if evidence["start"] < previous_end:
            raise ValueError("Proof evidence spans overlap")
        previous_end = evidence["end"]
        if description[evidence["start"]:evidence["end"]] != evidence["text"]:
            raise ValueError("Evidence span does not replay against the source")
        if description[mutation["start"]:mutation["end"]] != mutation["text"]:
            raise ValueError("Mutation span does not replay against the source")
        if not (evidence["start"] <= mutation["start"] < mutation["end"] <= evidence["end"]):
            raise ValueError("Mutation span must be contained by its evidence span")
        if proof["source_description_sha256"] != source_sha256:
            raise ValueError("Proof source hash does not match the description")
        expected_id = _stable_id(
            source_sha256, evidence["start"], evidence["end"], mutation["start"],
            proof["operator"]["family"], proof["operator"]["source"],
        )
        if proof["id"] != expected_id:
            raise ValueError("Proof ID is not reproducible")

        variants = {item["kind"]: item for item in proof["variants"]}
        if set(variants) != set(VARIANT_LABELS):
            raise ValueError("Every proof must contain exactly four mutation variants")
        replayed_flip = _replace(
            description, mutation["start"], mutation["end"], proof["operator"]["opposite"]
        )
        replayed_remove = _replace(description, evidence["start"], evidence["end"], "")
        expected_values = {
            "original": description,
            "flip": replayed_flip,
            "remove": replayed_remove,
            "pad": description + padding,
        }
        for kind, label in VARIANT_LABELS.items():
            if variants[kind]["expected_label"] != label:
                raise ValueError(f"Unexpected label for {kind} mutation")
            if variants[kind]["description"] != expected_values[kind]:
                raise ValueError(f"{kind} transform does not replay")

        claim = proof["claim"]
        opposite_claim = proof["opposite_claim"]
        if claim not in variants["original"]["description"] or claim not in variants["pad"]["description"]:
            raise ValueError("Original and padded variants must retain the evidence witness")
        if opposite_claim not in variants["flip"]["description"] or claim in variants["flip"]["description"]:
            raise ValueError("Flipped variant must contain only the controlled opposite witness")
        if claim in variants["remove"]["description"] or opposite_claim in variants["remove"]["description"]:
            raise ValueError("Removed variant must contain neither witness")


def parse_label(answer: str) -> str | None:
    """Accept one exact fixed label; prose and partial matches fail closed."""
    candidate = answer.strip()
    return candidate if candidate in LABELS else None


def _simulated_label(description: str, claim: str, opposite_claim: str) -> str:
    """Classify lexical witnesses in the actual variant, never echo expectations."""
    has_claim = claim in description
    has_opposite = opposite_claim in description
    if has_claim and has_opposite:
        return "INVALID"
    if has_claim:
        return "SUPPORTED"
    if has_opposite:
        return "CONTRADICTED"
    return "NOT_STATED"


def _context(request: PlaygroundRequest, description: str) -> dict[str, Any]:
    return {
        "asset": "user_input",
        "asset_type": "table",
        "asset_description": None,
        "certified": False,
        "deprecated": False,
        "columns": [{
            "name": request.column_name.strip(),
            "type": (request.data_type or "unknown").strip() or "unknown",
            "description": description,
        }],
    }


def _question(claim: str) -> str:
    return (
        f'Claim: "{claim}"\nClassify its relationship to the metadata as exactly one of: '
        "SUPPORTED, CONTRADICTED, NOT_STATED. Return only that label as the answer."
    )


def _evaluate_variant(
    request: PlaygroundRequest, proof: dict[str, Any], variant: dict[str, Any]
) -> dict[str, Any]:
    attempted_llm = request.mode in {"auto", "llm"} and answerer.llm_available()
    response: dict[str, Any] | None = None
    engine = "simulated"
    fallback = False
    provider_status = "simulation_requested" if request.mode == "simulated" else "not_configured"

    if attempted_llm:
        response = answerer.llm_answer(
            {"question": _question(proof["claim"])},
            _context(request, variant["description"]),
        )
        if response is not None:
            engine = "llm"
            provider_status = "ok"
        elif request.mode == "llm":
            raise PlaygroundUnavailable("The model provider did not return a usable label")
        else:
            fallback = True
            provider_status = "fallback"

    if response is None:
        raw_output = _simulated_label(
            variant["description"], proof["claim"], proof["opposite_claim"]
        )
    else:
        raw_output = response["answer"]
    observed = parse_label(raw_output)
    return {
        **variant,
        "observed_label": observed or "INVALID",
        "passed": observed == variant["expected_label"],
        "engine": engine,
        "llm_fallback": fallback,
        "provider_status": provider_status,
        "raw_output": raw_output,
    }


def _score(evaluated: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(evaluated)
    passed = sum(item["passed"] for item in evaluated)
    model_cases = [item for item in evaluated if item["engine"] == "llm" and not item["llm_fallback"]]
    model_passed = sum(item["passed"] for item in model_cases)
    fallback_cases = sum(item["llm_fallback"] for item in evaluated)
    return {
        "total_cases": total,
        "passed_cases": passed,
        "total_score": round(passed / total, 3) if total else None,
        "model_cases": len(model_cases),
        "model_passed_cases": model_passed,
        "model_only_score": round(model_passed / len(model_cases), 3) if model_cases else None,
        "fallback_cases": fallback_cases,
    }


def run_playground(request: PlaygroundRequest) -> dict[str, Any]:
    """Compile and evaluate metadata mutations without touching SQLite."""
    if not request.column_name.strip():
        raise ValueError("Column name cannot be blank")
    if not request.description.strip():
        raise ValueError("Description cannot be blank")
    if request.mode == "llm" and not answerer.llm_available():
        raise PlaygroundUnavailable("Real-model mode is not configured on this deployment")

    proofs, diagnostics = compile_proofs(request.description, request.pad_repetitions)
    evaluated_cases: list[dict[str, Any]] = []
    for proof in proofs:
        proof["variants"] = [
            _evaluate_variant(request, proof, variant) for variant in proof["variants"]
        ]
        evaluated_cases.extend(proof["variants"])

    engines = {item["engine"] for item in evaluated_cases}
    engine = next(iter(engines)) if len(engines) == 1 else ("mixed" if engines else "none")
    fallback_cases = sum(item["llm_fallback"] for item in evaluated_cases)
    if not fallback_cases:
        fallback_status = "none"
    elif fallback_cases == len(evaluated_cases):
        fallback_status = "all"
    else:
        fallback_status = "partial"

    return {
        "compiler_version": COMPILER_VERSION,
        "requested_mode": request.mode,
        "engine": engine,
        "fallback_status": fallback_status,
        "fixed_labels": list(LABELS),
        "supported_operators": SUPPORTED_OPERATORS,
        "source_description_sha256": _source_hash(request.description),
        "proofs": proofs,
        "diagnostics": diagnostics,
        "summary": _score(evaluated_cases),
        "caution": (
            "This mutation test measures grounding, sensitivity, abstention discipline, "
            "and noise robustness. It does not establish that the source metadata is true."
        ),
    }

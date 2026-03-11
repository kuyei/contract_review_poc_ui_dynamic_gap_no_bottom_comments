from __future__ import annotations

import json
import re
from typing import Any

try:
    from json_repair import loads as json_repair_loads  # type: ignore
except Exception:  # pragma: no cover
    json_repair_loads = None


def strip_markdown_json(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _extract_first_json_candidate(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return text

    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return text[start:idx + 1]
    return text


def _load_json_with_repair(text: str) -> Any:
    last_error: Exception | None = None
    candidates = []
    cleaned = strip_markdown_json(text)
    candidates.append(cleaned)
    extracted = _extract_first_json_candidate(cleaned)
    if extracted != cleaned:
        candidates.append(extracted)

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception as e:
            last_error = e
            if json_repair_loads is not None:
                try:
                    return json_repair_loads(candidate)
                except Exception as repair_e:
                    last_error = repair_e

    raise ValueError(f"Failed to parse JSON payload. Last error: {last_error}")



def parse_clause_payload(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        if isinstance(raw.get("clauses"), list):
            return raw["clauses"]
        if isinstance(raw.get("text"), str):
            raw = raw["text"]
        else:
            raise ValueError("Unsupported clause payload dict")
    if not isinstance(raw, str):
        raise ValueError("Unsupported clause payload type")

    data = _load_json_with_repair(raw)
    if not isinstance(data, list):
        raise ValueError("Clause payload is not a JSON list")
    return data



def _map_contract_risk_report_to_risk_items(payload: dict[str, Any]) -> dict[str, Any]:
    report = payload.get("contract_risk_report")
    if not isinstance(report, dict):
        return payload

    details = report.get("risk_details")
    if not isinstance(details, list):
        return payload

    risk_items: list[dict[str, Any]] = []
    for idx, detail in enumerate(details, start=1):
        if not isinstance(detail, dict):
            continue

        issue = str(detail.get("risk_point", "") or "").strip()
        evidence = str(detail.get("evidence", "") or "").strip()
        suggestion = str(detail.get("suggestion", "") or "").strip()
        clause_reference = str(detail.get("clause_reference", "") or "").strip()
        category = str(detail.get("risk_category", "") or "").strip()
        level = str(detail.get("risk_level", "") or "").strip().lower()
        likelihood = str(detail.get("risk_likelihood", "") or "").strip()
        impact = str(detail.get("risk_impact", "") or "").strip()

        if level not in {"high", "medium", "low"}:
            if level in {"严重", "高"}:
                level = "high"
            elif level in {"中", "中等"}:
                level = "medium"
            elif level in {"低"}:
                level = "low"
            else:
                level = "medium"

        basis_parts = [p for p in [likelihood and f"发生可能性：{likelihood}", impact and f"影响：{impact}"] if p]
        basis = "；".join(basis_parts)

        risk_items.append({
            "risk_id": detail.get("risk_id", idx),
            "dimension": category,
            "risk_label": category or "合同风险",
            "risk_level": level,
            "issue": issue or suggestion or category or "存在待人工复核的风险点",
            "basis": basis,
            "evidence_text": evidence,
            "suggestion": suggestion,
            "clause_id": clause_reference,
            "anchor_text": evidence,
            "needs_human_review": True,
            "status": "pending",
        })

    return {"risk_items": risk_items, "contract_risk_report": report}



def parse_risk_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        if isinstance(raw.get("risk_items"), list):
            return raw
        if isinstance(raw.get("contract_risk_report"), dict):
            return _map_contract_risk_report_to_risk_items(raw)
        if isinstance(raw.get("text"), str):
            raw = raw["text"]
        else:
            raise ValueError("Unsupported risk payload dict")

    if not isinstance(raw, str):
        raise ValueError("Unsupported risk payload type")

    data = _load_json_with_repair(raw)
    if not isinstance(data, dict):
        raise ValueError("Risk payload is not a JSON object")
    if isinstance(data.get("contract_risk_report"), dict):
        return _map_contract_risk_report_to_risk_items(data)
    return data

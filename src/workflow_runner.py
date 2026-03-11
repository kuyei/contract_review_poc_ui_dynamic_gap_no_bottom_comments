from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import Settings

from .dify_client import DifyWorkflowClient, extract_blocking_outputs
from .file_utils import write_json
from .parse_outputs import parse_clause_payload, parse_risk_payload


class WorkflowRunner:
    def __init__(self, settings: Settings, run_dir: Path, user_id: str) -> None:
        self.settings = settings
        self.run_dir = run_dir
        self.user_id = user_id
        self.clause_client = DifyWorkflowClient(
            base_url=settings.dify_base_url,
            api_key=settings.dify_clause_workflow_api_key,
            timeout_seconds=settings.request_timeout_seconds,
        )
        self.risk_client = DifyWorkflowClient(
            base_url=settings.dify_base_url,
            api_key=settings.dify_risk_workflow_api_key,
            timeout_seconds=settings.request_timeout_seconds,
        )

    def run_clause_splitter(self, segment: dict[str, str]) -> list[dict[str, Any]]:
        response = self.clause_client.run_workflow(
            inputs={
                "segment_id": segment["segment_id"],
                "segment_title": segment["segment_title"],
                "segment_text": segment["segment_text"],
            },
            user=self.user_id,
            response_mode="blocking",
        )
        outputs = extract_blocking_outputs(response)
        raw = outputs.get("clauses") if "clauses" in outputs else outputs.get("text", outputs)
        clauses = parse_clause_payload(raw)
        for item in clauses:
            item.setdefault("segment_title", segment.get("segment_title", ""))
        write_json(self.run_dir / "clauses" / f"{segment['segment_id']}.json", clauses)
        return clauses

    def run_risk_reviewer(self, clauses: list[dict[str, Any]]) -> dict[str, Any]:
        minimal_clauses = [
            {
                "clause_uid": c.get("clause_uid"),
                "segment_id": c.get("segment_id"),
                "segment_title": c.get("segment_title"),
                "clause_id": c.get("clause_id"),
                "clause_title": c.get("clause_title"),
                "clause_text": c.get("clause_text"),
                "clause_kind": c.get("clause_kind"),
                "is_boilerplate_instruction": c.get("is_boilerplate_instruction"),
            }
            for c in clauses
        ]
        response = self.risk_client.run_workflow(
            inputs={
                "clauses_json": json.dumps(minimal_clauses, ensure_ascii=False),
                "review_side": self.settings.review_side,
                "contract_type_hint": self.settings.contract_type_hint,
            },
            user=self.user_id,
            response_mode="blocking",
        )
        outputs = extract_blocking_outputs(response)
        write_json(self.run_dir / "risk_result_outputs.json", outputs)
        raw = outputs.get("risk_items")
        if raw is not None and isinstance(raw, list):
            payload = {"risk_items": raw}
        elif "contract_risk_report" in outputs:
            payload = outputs
        else:
            payload = parse_risk_payload(outputs.get("text", outputs))
        write_json(self.run_dir / "risk_result_raw.json", payload)
        return payload

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from config import settings
from src.checkpoint import load_existing_clause_batch
from src.clean_text import clean_contract_text
from src.extract_docx import extract_docx_text
from src.file_utils import ensure_dir, write_json, write_text
from src.merge_clauses import merge_clause_batches
from src.normalize_clauses import normalize_clauses
from src.normalize_risks import normalize_and_dedupe_risks
from src.split_segments import split_into_segments
from src.validate_risks import validate_risk_result
from src.workflow_runner import WorkflowRunner


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Contract review POC controller")
    parser.add_argument("docx_path", help="Path to DOCX contract")
    parser.add_argument("--run-id", default="", help="Optional run id; defaults to timestamp")
    parser.add_argument("--user-id", default="contract-review-poc", help="Dify user identifier")
    parser.add_argument("--dry-run", action="store_true", help="Do not call Dify; only extract, clean and split")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from saved per-segment clause outputs when available",
    )
    return parser


def create_run_dir(run_id: str) -> Path:
    if not run_id:
        run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    run_dir = ensure_dir(settings.run_root / run_id)
    ensure_dir(run_dir / "clauses")
    return run_dir


def save_stage_outputs(run_dir: Path, extracted_text: str, cleaned_text: str, segment_bundle: dict[str, Any]) -> None:
    write_text(run_dir / "extracted_text.txt", extracted_text)
    write_text(run_dir / "cleaned_text.txt", cleaned_text)
    write_json(run_dir / "segments.json", segment_bundle)


def main() -> int:
    args = build_arg_parser().parse_args()
    docx_path = Path(args.docx_path)
    if not docx_path.exists():
        print(f"DOCX not found: {docx_path}", file=sys.stderr)
        return 2

    run_dir = create_run_dir(args.run_id)

    print("[1/6] Extracting DOCX text...")
    extracted_text = extract_docx_text(docx_path)

    print("[2/6] Cleaning text...")
    cleaned_text = clean_contract_text(extracted_text)

    print("[3/6] Splitting top-level segments...")
    segment_bundle = split_into_segments(cleaned_text)
    save_stage_outputs(run_dir, extracted_text, cleaned_text, segment_bundle)
    print(f"Segments: {segment_bundle['segment_count']} | heading_style={segment_bundle['heading_style']}")

    if args.dry_run:
        print(f"Dry run complete. Outputs saved under: {run_dir}")
        return 0

    settings.validate_for_live_call()
    runner = WorkflowRunner(settings=settings, run_dir=run_dir, user_id=args.user_id)

    print("[4/6] Running clause splitter workflow for each segment...")
    clause_batches: list[list[dict[str, Any]]] = []
    for segment in segment_bundle["segments"]:
        print(f"  - {segment['segment_id']} {segment['segment_title']}")
        existing_path = run_dir / "clauses" / f"{segment['segment_id']}.json"
        clauses = None
        if args.resume:
            clauses = load_existing_clause_batch(existing_path)
            if clauses is not None:
                print(f"    resumed from {existing_path.name} ({len(clauses)} clauses)")
        if clauses is None:
            clauses = runner.run_clause_splitter(segment)
        clause_batches.append(clauses)

    raw_merged_clauses = merge_clause_batches(clause_batches)
    write_json(run_dir / "merged_clauses_raw.json", raw_merged_clauses)
    merged_clauses = normalize_clauses(raw_merged_clauses)
    write_json(run_dir / "merged_clauses.json", merged_clauses)
    print(f"Merged clauses: {len(merged_clauses)}")

    print("[5/6] Running risk reviewer workflow...")
    risk_payload = runner.run_risk_reviewer(merged_clauses)
    normalized_risk_payload = normalize_and_dedupe_risks(risk_payload, merged_clauses)
    write_json(run_dir / "risk_result_normalized.json", normalized_risk_payload)

    print("[6/6] Validating risk result...")
    is_valid, error_message = validate_risk_result(normalized_risk_payload)
    validated = {
        "is_valid": is_valid,
        "error_message": error_message,
        "risk_result": normalized_risk_payload,
    }
    write_json(run_dir / "risk_result_validated.json", validated)

    if is_valid:
        print(f"Run complete. Outputs saved under: {run_dir}")
        return 0

    print(f"Risk result validation failed: {error_message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

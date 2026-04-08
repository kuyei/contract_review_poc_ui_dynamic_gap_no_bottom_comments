from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from config import settings
from src.dify_client import DifyWorkflowClient, DifyWorkflowError, extract_blocking_outputs
from src.parse_outputs import _load_json_with_repair, strip_markdown_json

BASE_DIR = Path(__file__).resolve().parent
RUN_ROOT = BASE_DIR / "data" / "runs"
UPLOAD_ROOT = BASE_DIR / "data" / "uploads"
WEB_META_ROOT = BASE_DIR / "data" / "web_meta"

for path in (RUN_ROOT, UPLOAD_ROOT, WEB_META_ROOT):
    path.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Contract Review Web API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_CLAUSE_UID_PATTERN = r"segment_[A-Za-z0-9_-]+::[A-Za-z0-9_.()（）\-]+"
_CLAUSE_UID_RE = re.compile(_CLAUSE_UID_PATTERN)
_CLAUSE_REF_SPLIT_RE = re.compile(r"\s*[、，,；;/]\s*")
_TARGET_PREFIX_RE = re.compile(rf"^\s*(?:{_CLAUSE_UID_PATTERN})\s*")
_TARGET_INTRO_RE = re.compile(
    r"^\s*(?:(?:第?\s*[0-9一二三四五六七八九十百千万零〇\.]+(?:条|款)?)\s*)?(?:条款)?(?:约定|规定|载明|提到|显示)?\s*[:：，,]?\s*"
)
_QUOTED_TEXT_RE_LIST = [
    re.compile(r"「([^」]{4,})」"),
    re.compile(r"“([^”]{4,})”"),
    re.compile(r'"([^"\n]{4,})"'),
]
_ACCEPTED_RISK_STATUSES = {"accepted", "ai_applied"}
_ACCEPT_OVERLAP_DETAIL = "该风险点与已接受修改存在重叠，请手动处理或先撤销前一条修改。"


def _meta_path(run_id: str) -> Path:
    return WEB_META_ROOT / f"{run_id}.json"


def _write_meta(run_id: str, payload: dict[str, Any]) -> None:
    current = {}
    path = _meta_path(run_id)
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    current.update(payload)
    current.setdefault("run_id", run_id)
    current["updated_at"] = datetime.utcnow().isoformat() + "Z"
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_iso_datetime(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def _latest_mtime_iso(target: Path) -> str:
    latest = target.stat().st_mtime
    if target.is_dir():
        for p in target.rglob("*"):
            if p.is_file():
                latest = max(latest, p.stat().st_mtime)
    return datetime.utcfromtimestamp(latest).isoformat() + "Z"


def _infer_meta_from_run(run_id: str) -> dict[str, Any]:
    run_dir = RUN_ROOT / run_id
    if not run_dir.exists() or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="run_id 不存在")

    merged_exists = (run_dir / "merged_clauses.json").exists()
    validated_path = run_dir / "risk_result_validated.json"
    status = "running"
    step = "历史运行记录"
    progress = 35
    error: str | None = None

    if merged_exists and validated_path.exists():
        validated = _safe_json(validated_path) or {}
        if bool(validated.get("is_valid")):
            status = "completed"
            step = "历史结果"
            progress = 100
        else:
            status = "failed"
            step = "历史结果校验失败"
            progress = 100
            error = validated.get("error_message") or "risk_result_validated.json 校验未通过"
    elif merged_exists:
        step = "历史运行记录（风险识别阶段）"
        progress = 65

    source_doc = run_dir / "source.docx"
    upload_doc = UPLOAD_ROOT / f"{run_id}.docx"
    reviewed_doc = run_dir / "reviewed_comments.docx"
    if source_doc.exists():
        file_name = source_doc.name
    elif upload_doc.exists():
        file_name = upload_doc.name
    elif reviewed_doc.exists():
        file_name = reviewed_doc.name
    else:
        file_name = f"{run_id}.docx"

    return {
        "run_id": run_id,
        "status": status,
        "file_name": file_name,
        "step": step,
        "progress": progress,
        "error": error,
        "updated_at": _latest_mtime_iso(run_dir),
    }


def _read_meta(run_id: str) -> dict[str, Any]:
    path = _meta_path(run_id)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("run_id", run_id)
        payload.setdefault("updated_at", datetime.utcnow().isoformat() + "Z")
        if payload.get("progress") is None:
            status = str(payload.get("status") or "")
            step = str(payload.get("step") or "")
            if status == "queued":
                payload["progress"] = 10
            elif status == "completed" or status == "failed":
                payload["progress"] = 100
            elif "风险" in step:
                payload["progress"] = 65
            elif "结果" in step or "导出" in step:
                payload["progress"] = 85
            else:
                payload["progress"] = 35
        return payload
    return _infer_meta_from_run(run_id)


def _safe_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _short_text(value: str | None, limit: int = 120) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _iso_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _extract_quoted_contract_text(text: str) -> str:
    candidates: list[str] = []
    for pattern in _QUOTED_TEXT_RE_LIST:
        for match in pattern.finditer(text):
            part = str(match.group(1) or "").strip()
            if not part:
                continue
            if _CLAUSE_UID_RE.fullmatch(part):
                continue
            candidates.append(part)
    if not candidates:
        return ""
    candidates.sort(key=len, reverse=True)
    return candidates[0]


def _strip_wrapping_quotes(text: str) -> str:
    s = str(text or "").strip()
    quote_pairs = (("“", "”"), ("「", "」"), ('"', '"'), ("'", "'"))
    for left, right in quote_pairs:
        if s.startswith(left) and s.endswith(right) and len(s) > len(left) + len(right):
            inner = s[len(left): len(s) - len(right)].strip()
            if inner:
                return inner
    return s


def _sanitize_ai_target_text(text: str | None) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"\s+", " ", raw)
    cleaned = _TARGET_PREFIX_RE.sub("", raw, count=1)
    cleaned = _TARGET_INTRO_RE.sub("", cleaned, count=1).strip()
    cleaned = _strip_wrapping_quotes(cleaned)
    if not cleaned:
        return ""
    if _CLAUSE_UID_RE.fullmatch(cleaned):
        return ""
    return cleaned


def _sanitize_target_for_match(text: str | None) -> str:
    raw = _sanitize_ai_target_text(text)
    if not raw:
        return ""
    quoted = _extract_quoted_contract_text(raw)
    if quoted and raw == _strip_wrapping_quotes(raw):
        return quoted
    return raw


def _find_clause_for_risk(risk: dict[str, Any], clauses: list[dict[str, Any]]) -> dict[str, Any] | None:
    by_uid: dict[str, dict[str, Any]] = {}
    for clause in clauses:
        if not isinstance(clause, dict):
            continue
        uid = str(clause.get("clause_uid") or "").strip()
        if uid:
            by_uid[uid] = clause
    for uid in risk.get("clause_uids") or []:
        clause = by_uid.get(str(uid or "").strip())
        if clause is not None:
            return clause
    uid = str(risk.get("clause_uid") or "").strip()
    if uid and uid in by_uid:
        return by_uid[uid]
    return None


def _clause_text_window(clause_text: str, target_text: str, limit: int = 1200) -> str:
    clause = str(clause_text or "").strip()
    if len(clause) <= limit:
        return clause
    target = str(target_text or "").strip()
    if target:
        idx = clause.find(target)
        if idx >= 0:
            half = limit // 2
            start = max(0, idx - half)
            end = min(len(clause), start + limit)
            if end - start < limit:
                start = max(0, end - limit)
            return clause[start:end]
    return clause[:limit]


def _parse_rewrite_outputs(outputs: dict[str, Any]) -> tuple[str, str, str]:
    structured = outputs.get("structured_output")
    structured_dict: dict[str, Any] | None = None
    if isinstance(structured, dict):
        structured_dict = structured
    elif isinstance(structured, str):
        cleaned = strip_markdown_json(structured)
        parsed = _load_json_with_repair(cleaned)
        if isinstance(parsed, dict):
            structured_dict = parsed

    if structured_dict is not None:
        revised_text = str(structured_dict.get("revised_text") or "").strip()
        rationale = str(structured_dict.get("rationale") or "").strip()
        edit_type = str(structured_dict.get("edit_type") or "").strip()
        if revised_text:
            return revised_text, rationale, edit_type

    revised_text = str(outputs.get("revised_text") or "").strip()
    rationale = str(outputs.get("rationale") or "").strip()
    edit_type = str(outputs.get("edit_type") or "").strip()
    if revised_text:
        return revised_text, rationale, edit_type

    text_payload = outputs.get("text")
    if not isinstance(text_payload, str):
        raise HTTPException(status_code=500, detail="rewrite workflow outputs 缺少 revised_text（structured_output/revised_text/text 均未提供）")
    cleaned = strip_markdown_json(text_payload)
    parsed = _load_json_with_repair(cleaned)
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=500, detail="rewrite workflow text 不是 JSON 对象")
    revised_text = str(parsed.get("revised_text") or "").strip()
    rationale = str(parsed.get("rationale") or "").strip()
    edit_type = str(parsed.get("edit_type") or "").strip()
    if not revised_text:
        raise HTTPException(status_code=500, detail="rewrite workflow 返回 revised_text 为空")
    return revised_text, rationale, edit_type


def _build_ai_comment_text(
    *,
    target_text: str,
    revised_text: str,
) -> str:
    before = str(target_text or "").strip()
    after = str(revised_text or "").strip()

    prefix = 0
    max_prefix = min(len(before), len(after))
    while prefix < max_prefix and before[prefix] == after[prefix]:
        prefix += 1

    suffix = 0
    max_suffix = min(len(before) - prefix, len(after) - prefix)
    while suffix < max_suffix and before[len(before) - 1 - suffix] == after[len(after) - 1 - suffix]:
        suffix += 1

    before_changed = before[prefix : len(before) - suffix if suffix > 0 else len(before)]
    after_changed = after[prefix : len(after) - suffix if suffix > 0 else len(after)]
    before_piece = _short_text(before_changed or before, 120) or "原文片段"
    after_piece = _short_text(after_changed or after, 120) or "修改后片段"
    return f"将“{before_piece}”修改为“{after_piece}”。"


def _ensure_risk_items_status(payload: dict[str, Any]) -> dict[str, Any]:
    risk_items = (((payload or {}).get("risk_result") or {}).get("risk_items") or [])
    if not isinstance(risk_items, list):
        return payload
    for item in risk_items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "") or "").strip()
        item["status"] = status or "pending"
    return payload


def _is_accepted_risk_status(value: Any) -> bool:
    return str(value or "").strip().lower() in _ACCEPTED_RISK_STATUSES


def _as_clause_ref_list(value: Any) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    raw_values = value if isinstance(value, (list, tuple, set)) else [value]
    for raw in raw_values:
        text = str(raw or "").strip()
        if not text:
            continue
        parts = [p.strip() for p in _CLAUSE_REF_SPLIT_RE.split(text) if p.strip()]
        if not parts:
            continue
        for part in parts:
            if part in seen:
                continue
            seen.add(part)
            refs.append(part)
    return refs


def _load_run_clauses(run_dir: Path) -> list[dict[str, Any]]:
    payload = _safe_json(run_dir / "merged_clauses.json")
    raw_items: list[Any] = []
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("clauses"), list):
        raw_items = payload.get("clauses") or []
    return [item for item in raw_items if isinstance(item, dict)]


def _build_clause_uid_alias_map(clauses: list[dict[str, Any]]) -> dict[str, str]:
    alias: dict[str, str] = {}
    for clause in clauses:
        uid = str(clause.get("clause_uid") or "").strip()
        if not uid:
            continue
        alias.setdefault(uid, uid)
        for field in ("clause_id", "display_clause_id", "local_clause_id", "source_clause_id"):
            for ref in _as_clause_ref_list(clause.get(field)):
                alias.setdefault(ref, uid)
    return alias


def _collect_risk_clause_keys(risk: dict[str, Any], clause_alias_map: dict[str, str] | None = None) -> set[str]:
    alias_map = clause_alias_map or {}
    keys: set[str] = set()

    for field in ("clause_uids", "related_clause_uids", "clause_uid"):
        for uid in _as_clause_ref_list(risk.get(field)):
            keys.add(alias_map.get(uid) or uid)

    for field in ("clause_ids", "related_clause_ids", "display_clause_ids", "clause_id", "display_clause_id"):
        for ref in _as_clause_ref_list(risk.get(field)):
            keys.add(alias_map.get(ref) or ref)

    return keys


def _find_accepted_clause_conflict(
    risk_items: list[dict[str, Any]],
    target_risk_id: str,
    clause_alias_map: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    target: dict[str, Any] | None = None
    for item in risk_items:
        if isinstance(item, dict) and str(item.get("risk_id", "")) == str(target_risk_id):
            target = item
            break
    if target is None:
        return None

    target_clause_keys = _collect_risk_clause_keys(target, clause_alias_map)
    if not target_clause_keys:
        return None

    for item in risk_items:
        if not isinstance(item, dict):
            continue
        if str(item.get("risk_id", "")) == str(target_risk_id):
            continue
        if not _is_accepted_risk_status(item.get("status")):
            continue
        clause_keys = _collect_risk_clause_keys(item, clause_alias_map)
        if clause_keys and target_clause_keys.intersection(clause_keys):
            return item
    return None


def _sanitize_reviewed_ai_payload(payload: dict[str, Any]) -> bool:
    risk_items = (((payload or {}).get("risk_result") or {}).get("risk_items") or [])
    if not isinstance(risk_items, list):
        return False

    changed = False
    for item in risk_items:
        if not isinstance(item, dict):
            continue
        fallback_target = _sanitize_ai_target_text(
            str(item.get("target_text") or item.get("evidence_text") or item.get("anchor_text") or "")
        )
        for field in ("ai_rewrite", "ai_apply"):
            ai_payload = item.get(field)
            if not isinstance(ai_payload, dict):
                continue
            old_target = str(ai_payload.get("target_text") or "").strip()
            cleaned_target = _sanitize_ai_target_text(old_target) or fallback_target
            if cleaned_target and cleaned_target != old_target:
                ai_payload["target_text"] = cleaned_target
                changed = True

            revised_text = str(ai_payload.get("revised_text") or "").strip()
            if not revised_text:
                continue
            target_for_comment = str(ai_payload.get("target_text") or "").strip() or fallback_target
            if target_for_comment and str(ai_payload.get("target_text") or "").strip() != target_for_comment:
                ai_payload["target_text"] = target_for_comment
                changed = True
            next_comment = _build_ai_comment_text(target_text=target_for_comment, revised_text=revised_text)
            if str(ai_payload.get("comment_text") or "").strip() != next_comment:
                ai_payload["comment_text"] = next_comment
                changed = True
    return changed


def _rewrite_client() -> DifyWorkflowClient:
    if not settings.dify_rewrite_workflow_api_key:
        raise HTTPException(status_code=500, detail="未配置 DIFY_REWRITE_WORKFLOW_API_KEY")
    return DifyWorkflowClient(
        base_url=settings.dify_base_url,
        api_key=settings.dify_rewrite_workflow_api_key,
        timeout_seconds=settings.request_timeout_seconds,
    )


def _extract_target_text(risk: dict[str, Any]) -> str:
    candidates = [
        str(risk.get("target_text") or "").strip(),
        str(risk.get("evidence_text") or "").strip(),
        str(risk.get("anchor_text") or "").strip(),
    ]
    fallback = ""
    for raw in candidates:
        if raw and not fallback:
            fallback = raw
        cleaned = _sanitize_ai_target_text(raw)
        if cleaned:
            return cleaned
    return _sanitize_ai_target_text(fallback) or fallback


def _build_rewrite_inputs(*, run_id: str, run_dir: Path, risk: dict[str, Any]) -> dict[str, Any]:
    target_text = _extract_target_text(risk)
    merged_path = run_dir / "merged_clauses.json"
    merged_clauses = _safe_json(merged_path)
    if not isinstance(merged_clauses, list):
        raise HTTPException(status_code=404, detail="merged_clauses.json 不存在或格式错误")
    clause = _find_clause_for_risk(risk, merged_clauses)
    clause_source = ""
    if clause is not None:
        clause_source = str(clause.get("source_excerpt") or clause.get("clause_text") or "").strip()
    clause_text = _clause_text_window(clause_source, target_text, limit=1200)

    meta = _read_meta(run_id)
    suggestion = str(risk.get("suggestion") or "").strip()
    return {
        "target_text": str(target_text or ""),
        "suggestion": suggestion,
        "clause_text": str(clause_text or ""),
        "issue": str(risk.get("issue") or ""),
        "risk_label": str(risk.get("risk_label") or ""),
        "review_side": str(meta.get("review_side") or ""),
        "contract_type_hint": str(meta.get("contract_type_hint") or ""),
    }


def _generate_ai_rewrite(*, run_id: str, run_dir: Path, risk: dict[str, Any], client: DifyWorkflowClient) -> dict[str, Any]:
    inputs = _build_rewrite_inputs(run_id=run_id, run_dir=run_dir, risk=risk)
    target_text = str(inputs.get("target_text") or "")
    try:
        resp = client.run_workflow(inputs=inputs, response_mode="blocking", user="web")
        outputs = extract_blocking_outputs(resp)
    except DifyWorkflowError as exc:
        raise HTTPException(status_code=500, detail=f"rewrite workflow 调用失败: {str(exc)}")

    revised_text, _rationale, _edit_type = _parse_rewrite_outputs(outputs)
    comment_text = _build_ai_comment_text(
        target_text=target_text,
        revised_text=revised_text,
    )
    return {
        "state": "succeeded",
        "target_text": target_text,
        "revised_text": revised_text,
        "comment_text": comment_text,
        "created_at": _iso_now(),
    }


def get_or_create_reviewed_risks(run_id: str) -> dict[str, Any]:
    run_dir = RUN_ROOT / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run_id 不存在")

    reviewed_path = run_dir / "risk_result_reviewed.json"
    validated_path = run_dir / "risk_result_validated.json"

    if reviewed_path.exists():
        reviewed = _safe_json(reviewed_path)
        if not isinstance(reviewed, dict):
            raise HTTPException(status_code=500, detail="risk_result_reviewed.json 格式错误")
        reviewed = _ensure_risk_items_status(reviewed)
        _sanitize_reviewed_ai_payload(reviewed)
        reviewed_path.write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding="utf-8")
        return reviewed

    validated = _safe_json(validated_path)
    if not isinstance(validated, dict):
        raise HTTPException(status_code=404, detail="risk_result_validated.json 不存在")
    reviewed = _ensure_risk_items_status(validated)
    _sanitize_reviewed_ai_payload(reviewed)
    reviewed_path.write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding="utf-8")
    return reviewed


def _build_result_payload(run_id: str) -> dict[str, Any]:
    run_dir = RUN_ROOT / run_id
    clauses = _safe_json(run_dir / "merged_clauses.json")
    if clauses is None:
        raise HTTPException(status_code=404, detail="结果尚未生成完成")
    validated = get_or_create_reviewed_risks(run_id)
    meta = _read_meta(run_id)
    reviewed_docx = run_dir / "reviewed_comments.docx"
    return {
        "run_id": run_id,
        "status": meta.get("status"),
        "file_name": meta.get("file_name"),
        "review_side": meta.get("review_side"),
        "contract_type_hint": meta.get("contract_type_hint"),
        "merged_clauses": clauses,
        "risk_result_validated": validated,
        "download_ready": reviewed_docx.exists(),
        "download_url": f"/api/reviews/{run_id}/download" if reviewed_docx.exists() else None,
    }


def _resolve_document_path(run_id: str) -> Path | None:
    run_dir = RUN_ROOT / run_id
    for candidate in (
        run_dir / "source.docx",
        UPLOAD_ROOT / f"{run_id}.docx",
        run_dir / "reviewed_comments.docx",
    ):
        if candidate.exists():
            return candidate
    return None


def _to_history_item(meta: dict[str, Any]) -> dict[str, Any]:
    run_id = str(meta.get("run_id") or "")
    run_dir = RUN_ROOT / run_id
    reviewed_docx = run_dir / "reviewed_comments.docx"
    document_path = _resolve_document_path(run_id)
    return {
        "run_id": run_id,
        "file_name": meta.get("file_name"),
        "status": meta.get("status") or "running",
        "review_side": meta.get("review_side"),
        "contract_type_hint": meta.get("contract_type_hint"),
        "updated_at": meta.get("updated_at") or (_latest_mtime_iso(run_dir) if run_dir.exists() else None),
        "step": meta.get("step"),
        "warning": meta.get("warning"),
        "error": meta.get("error"),
        "download_ready": reviewed_docx.exists(),
        "document_ready": document_path is not None,
    }


def _list_history_items(limit: int) -> list[dict[str, Any]]:
    run_ids: set[str] = set()
    for path in WEB_META_ROOT.glob("*.json"):
        run_ids.add(path.stem)
    for path in RUN_ROOT.iterdir():
        if path.is_dir():
            run_ids.add(path.name)

    items: list[dict[str, Any]] = []
    for run_id in run_ids:
        try:
            meta = _read_meta(run_id)
        except HTTPException:
            continue
        item = _to_history_item(meta)
        items.append(item)

    items.sort(key=lambda x: _parse_iso_datetime(x.get("updated_at")), reverse=True)
    return items[:limit]


def _run_pipeline(*, run_id: str, file_path: Path, file_name: str, review_side: str, contract_type_hint: str) -> None:
    run_dir = RUN_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    source_docx = run_dir / "source.docx"
    if not source_docx.exists():
        try:
            shutil.copy2(file_path, source_docx)
        except Exception:
            pass
    env = os.environ.copy()
    env["RUN_ROOT"] = str(RUN_ROOT)
    env["REVIEW_SIDE"] = review_side
    env["CONTRACT_TYPE_HINT"] = contract_type_hint

    _write_meta(
        run_id,
        {
            "status": "running",
            "file_name": file_name,
            "review_side": review_side,
            "contract_type_hint": contract_type_hint,
            "run_dir": str(run_dir),
            "step": "排队完成，准备开始审查",
            "progress": 15,
        },
    )

    cmd = ["python", "app.py", str(file_path), "--run-id", run_id]
    proc = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    last_phase = ""
    while True:
        merged_ready = (run_dir / "merged_clauses.json").exists()
        validated_ready = (run_dir / "risk_result_validated.json").exists()
        if validated_ready:
            phase = "assemble"
            phase_step = "风险识别完成，正在生成结果"
            phase_progress = 85
        elif merged_ready:
            phase = "scan"
            phase_step = "正在识别风险点"
            phase_progress = 65
        else:
            phase = "parse"
            phase_step = "正在解析与拆分合同"
            phase_progress = 35

        if phase != last_phase:
            _write_meta(
                run_id,
                {
                    "status": "running",
                    "step": phase_step,
                    "progress": phase_progress,
                },
            )
            last_phase = phase

        if proc.poll() is not None:
            break
        time.sleep(1.0)

    stdout, stderr = proc.communicate()
    (run_dir / "app.stdout.log").write_text(stdout or "", encoding="utf-8")
    (run_dir / "app.stderr.log").write_text(stderr or "", encoding="utf-8")

    if proc.returncode != 0:
        _write_meta(
            run_id,
            {
                "status": "failed",
                "step": "主流程执行失败",
                "progress": 100,
                "error": (stderr or stdout or "未知错误").strip(),
            },
        )
        return

    validated = _safe_json(run_dir / "risk_result_validated.json") or {}
    is_valid = bool(validated.get("is_valid"))
    if not is_valid:
        _write_meta(
            run_id,
            {
                "status": "failed",
                "step": "风险结果校验失败",
                "error": validated.get("error_message") or "risk_result_validated.json 校验未通过",
            },
        )
        return

    _write_meta(
        run_id,
        {
            "status": "running",
            "step": "风险识别完成，正在导出结果文档",
            "progress": 92,
        },
    )

    export_cmd = [
        "python",
        "-m",
        "src.docx_comments",
        str(file_path),
        str(run_dir / "merged_clauses.json"),
        str(run_dir / "risk_result_validated.json"),
        "--out",
        str(run_dir / "reviewed_comments.docx"),
        "--author",
        "合同审查系统",
    ]
    export_proc = subprocess.run(
        export_cmd,
        cwd=str(BASE_DIR),
        env=env,
        capture_output=True,
        text=True,
    )
    (run_dir / "export.stdout.log").write_text(export_proc.stdout or "", encoding="utf-8")
    (run_dir / "export.stderr.log").write_text(export_proc.stderr or "", encoding="utf-8")

    if export_proc.returncode != 0:
        _write_meta(
            run_id,
            {
                "status": "completed",
                "step": "审查完成，但 DOCX 导出失败",
                "progress": 100,
                "warning": (export_proc.stderr or export_proc.stdout or "DOCX 导出失败").strip(),
            },
        )
        return

    _write_meta(
        run_id,
        {
            "status": "completed",
            "step": "审查与 DOCX 批注导出已完成",
            "progress": 100,
        },
    )


@app.get("/api/config")
def get_config() -> dict[str, str]:
    return {
        "review_side": settings.review_side,
        "contract_type_hint": settings.contract_type_hint,
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/reviews")
async def create_review(
    file: UploadFile = File(...),
    review_side: str = Form(settings.review_side),
    contract_type_hint: str = Form("service_agreement"),
) -> dict[str, Any]:
    suffix = Path(file.filename or "contract.docx").suffix.lower()
    if suffix != ".docx":
        raise HTTPException(status_code=400, detail="目前仅支持 .docx 文件")

    run_id = datetime.now().strftime("web_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    upload_path = UPLOAD_ROOT / f"{run_id}{suffix}"
    with upload_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    run_dir = RUN_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(upload_path, run_dir / "source.docx")
    except Exception:
        pass

    _write_meta(
        run_id,
        {
            "status": "queued",
            "file_name": file.filename,
            "review_side": review_side,
            "contract_type_hint": contract_type_hint,
            "step": "任务已创建，等待执行",
            "progress": 8,
        },
    )
    threading.Thread(
        target=_run_pipeline,
        kwargs=dict(
            run_id=run_id,
            file_path=upload_path,
            file_name=file.filename or upload_path.name,
            review_side=review_side,
            contract_type_hint=contract_type_hint,
        ),
        daemon=True,
    ).start()
    return {"run_id": run_id, "status": "queued"}


@app.get("/api/reviews/history")
def get_review_history(limit: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    return {"items": _list_history_items(limit)}


@app.get("/api/reviews/{run_id}")
def get_review_status(run_id: str) -> dict[str, Any]:
    return _read_meta(run_id)


@app.get("/api/reviews/{run_id}/result")
def get_review_result(run_id: str) -> dict[str, Any]:
    meta = _read_meta(run_id)
    if meta.get("status") != "completed":
        raise HTTPException(status_code=409, detail="任务尚未完成")
    return _build_result_payload(run_id)


@app.get("/api/reviews/{run_id}/document")
def get_review_document(run_id: str) -> FileResponse:
    output = _resolve_document_path(run_id)
    if output is None:
        raise HTTPException(status_code=404, detail="未找到该 run 对应的 DOCX")
    return FileResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=output.name,
    )


class RiskPatchBody(BaseModel):
    status: str


class AiAcceptBody(BaseModel):
    revised_text: str | None = None
    target_text: str | None = None
    accepted_patch: dict[str, Any] | None = None


class AiEditBody(BaseModel):
    revised_text: str


def _export_docx_with_reviewed_risks(run_id: str) -> Path:
    run_dir = RUN_ROOT / run_id
    source_doc = run_dir / "source.docx"
    if not source_doc.exists():
        upload_doc = UPLOAD_ROOT / f"{run_id}.docx"
        if upload_doc.exists():
            source_doc = upload_doc
        else:
            raise HTTPException(status_code=404, detail="原始 DOCX 不存在")

    merged_path = run_dir / "merged_clauses.json"
    if not merged_path.exists():
        raise HTTPException(status_code=404, detail="merged_clauses.json 不存在")

    reviewed_payload = get_or_create_reviewed_risks(run_id)
    reviewed_path = run_dir / "risk_result_reviewed.json"
    reviewed_path.write_text(json.dumps(reviewed_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    patched_docx = run_dir / "ai_patched.docx"
    patch_cmd = [
        "python",
        "-m",
        "src.docx_apply_patches",
        str(source_doc),
        str(reviewed_path),
        "--out",
        str(patched_docx),
        "--author",
        "合同审查系统",
    ]
    patch_proc = subprocess.run(
        patch_cmd,
        cwd=str(BASE_DIR),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )

    out_path = run_dir / "reviewed_comments.docx"
    comment_cmd = [
        "python",
        "-m",
        "src.docx_comments",
        str(patched_docx),
        str(merged_path),
        str(reviewed_path),
        "--out",
        str(out_path),
        "--author",
        "合同审查系统",
        "--statuses",
        "accepted",
    ]
    comment_proc = subprocess.run(
        comment_cmd,
        cwd=str(BASE_DIR),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    stdout = "\n".join(
        [
            "[ai_patch]",
            patch_proc.stdout or "",
            "[risk_comments]",
            comment_proc.stdout or "",
        ]
    )
    stderr = "\n".join(
        [
            "[ai_patch]",
            patch_proc.stderr or "",
            "[risk_comments]",
            comment_proc.stderr or "",
        ]
    )
    (run_dir / "export.stdout.log").write_text(stdout, encoding="utf-8")
    (run_dir / "export.stderr.log").write_text(stderr, encoding="utf-8")
    if patch_proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=(patch_proc.stderr or patch_proc.stdout or "AI 改写应用失败").strip()[:1000],
        )
    if comment_proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=(comment_proc.stderr or comment_proc.stdout or "DOCX 导出失败").strip()[:1000],
        )
    return out_path


@app.get("/api/reviews/{run_id}/download")
def download_reviewed_docx(run_id: str) -> FileResponse:
    output = _export_docx_with_reviewed_risks(run_id)
    return FileResponse(output, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=f"{run_id}_reviewed_comments.docx")


@app.patch("/api/reviews/{run_id}/risks/{risk_id}")
def patch_risk_status(run_id: str, risk_id: str, body: RiskPatchBody) -> dict[str, Any]:
    status = str(body.status or "").strip().lower()
    if status not in {"pending", "accepted", "rejected"}:
        raise HTTPException(status_code=400, detail="status 仅支持 pending/accepted/rejected")

    run_dir = RUN_ROOT / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run_id 不存在")

    reviewed = get_or_create_reviewed_risks(run_id)
    risk_items = (((reviewed or {}).get("risk_result") or {}).get("risk_items") or [])
    if not isinstance(risk_items, list):
        raise HTTPException(status_code=500, detail="reviewed 风险数据格式错误")

    target: dict[str, Any] | None = None
    for item in risk_items:
        if isinstance(item, dict) and str(item.get("risk_id", "")) == str(risk_id):
            target = item
            break

    if target is None:
        raise HTTPException(status_code=404, detail="risk_id 不存在")

    current_status = str(target.get("status") or "pending").strip().lower()
    if status == "accepted" and not _is_accepted_risk_status(current_status):
        clause_alias_map = _build_clause_uid_alias_map(_load_run_clauses(run_dir))
        conflict = _find_accepted_clause_conflict(risk_items, str(risk_id), clause_alias_map)
        if conflict is not None:
            raise HTTPException(status_code=409, detail=_ACCEPT_OVERLAP_DETAIL)

    target["status"] = status
    if status == "accepted":
        ai_rewrite = target.get("ai_rewrite") if isinstance(target.get("ai_rewrite"), dict) else {}
        if str(ai_rewrite.get("state") or "").strip().lower() == "succeeded":
            target["ai_rewrite_decision"] = "accepted"
    elif status == "rejected":
        target["ai_rewrite_decision"] = "rejected"
        target.pop("accepted_patch", None)
    elif status == "pending":
        if isinstance(target.get("ai_rewrite"), dict):
            target["ai_rewrite_decision"] = "proposed"
        target.pop("accepted_patch", None)

    reviewed_path = run_dir / "risk_result_reviewed.json"
    reviewed_path.write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "item": target}


@app.post("/api/reviews/{run_id}/risks/accept_all")
def accept_all_risks(run_id: str) -> dict[str, Any]:
    run_dir = RUN_ROOT / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run_id 不存在")

    reviewed = get_or_create_reviewed_risks(run_id)
    risk_items = (((reviewed or {}).get("risk_result") or {}).get("risk_items") or [])
    if not isinstance(risk_items, list):
        raise HTTPException(status_code=500, detail="reviewed 风险数据格式错误")

    clause_alias_map = _build_clause_uid_alias_map(_load_run_clauses(run_dir))
    accepted_clause_keys: set[str] = set()
    for item in risk_items:
        if not isinstance(item, dict):
            continue
        if _is_accepted_risk_status(item.get("status")):
            accepted_clause_keys.update(_collect_risk_clause_keys(item, clause_alias_map))

    accepted = 0
    skipped = 0
    for item in risk_items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        status = str(item.get("status") or "pending").strip().lower()
        if status == "rejected":
            skipped += 1
            continue
        if _is_accepted_risk_status(status):
            skipped += 1
            continue
        clause_keys = _collect_risk_clause_keys(item, clause_alias_map)
        if clause_keys and accepted_clause_keys.intersection(clause_keys):
            skipped += 1
            continue
        item["status"] = "accepted"
        ai_rewrite = item.get("ai_rewrite") if isinstance(item.get("ai_rewrite"), dict) else {}
        ai_state = str(ai_rewrite.get("state") or "").strip().lower()
        if ai_state == "succeeded":
            item["ai_rewrite_decision"] = "accepted"
        accepted += 1
        accepted_clause_keys.update(clause_keys)

    reviewed_path = run_dir / "risk_result_reviewed.json"
    reviewed_path.write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "summary": {"accepted": accepted, "skipped": skipped}, "risk_items": risk_items}


@app.post("/api/reviews/{run_id}/risks/{risk_id}/ai_apply")
def ai_apply_risk(run_id: str, risk_id: str) -> dict[str, Any]:
    run_dir = RUN_ROOT / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run_id 不存在")

    reviewed = get_or_create_reviewed_risks(run_id)
    risk_items = (((reviewed or {}).get("risk_result") or {}).get("risk_items") or [])
    if not isinstance(risk_items, list):
        raise HTTPException(status_code=500, detail="reviewed 风险数据格式错误")

    target: dict[str, Any] | None = None
    for item in risk_items:
        if not isinstance(item, dict):
            continue
        if str(item.get("risk_id", "")) == str(risk_id):
            target = item
            break
    if target is None:
        raise HTTPException(status_code=404, detail="risk_id 不存在")
    if str(target.get("status") or "pending").strip().lower() == "rejected":
        raise HTTPException(status_code=409, detail="rejected 风险不允许 AI 自动修改")
    client = _rewrite_client()
    target["ai_rewrite"] = _generate_ai_rewrite(run_id=run_id, run_dir=run_dir, risk=target, client=client)
    target["ai_rewrite_decision"] = "proposed"

    reviewed_path = run_dir / "risk_result_reviewed.json"
    reviewed_path.write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "item": target}


@app.post("/api/reviews/{run_id}/ai_apply_all")
def ai_apply_all_risks(run_id: str) -> dict[str, Any]:
    run_dir = RUN_ROOT / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run_id 不存在")

    reviewed = get_or_create_reviewed_risks(run_id)
    risk_items = (((reviewed or {}).get("risk_result") or {}).get("risk_items") or [])
    if not isinstance(risk_items, list):
        raise HTTPException(status_code=500, detail="reviewed 风险数据格式错误")

    client = _rewrite_client()
    total = len(risk_items)
    created = 0
    skipped = 0
    failed = 0
    tasks: list[tuple[int, dict[str, Any]]] = []
    for idx, item in enumerate(risk_items):
        if not isinstance(item, dict):
            skipped += 1
            continue
        status = str(item.get("status") or "pending").strip().lower()
        if status == "rejected":
            skipped += 1
            continue
        if str(item.get("risk_source_type") or "").strip().lower() == "missing_clause":
            skipped += 1
            continue
        ai_rewrite = item.get("ai_rewrite") if isinstance(item.get("ai_rewrite"), dict) else {}
        if str(ai_rewrite.get("state") or "").strip().lower() == "succeeded":
            skipped += 1
            continue
        tasks.append((idx, item))

    max_workers = max(
        1,
        int(os.getenv("AI_REWRITE_MAX_CONCURRENCY", str(getattr(settings, "dify_max_concurrency", 6))) or 6),
    )
    reviewed_path = run_dir / "risk_result_reviewed.json"
    future_map: dict[Any, int] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for idx, risk in tasks:
            future = executor.submit(_generate_ai_rewrite, run_id=run_id, run_dir=run_dir, risk=dict(risk), client=client)
            future_map[future] = idx
        for future in as_completed(future_map):
            idx = future_map[future]
            risk = risk_items[idx]
            try:
                ai_rewrite = future.result()
                risk["ai_rewrite"] = ai_rewrite
                risk["ai_rewrite_decision"] = "proposed"
                created += 1
            except Exception as exc:
                failed += 1
                risk["ai_rewrite"] = {
                    "state": "failed",
                    "target_text": _extract_target_text(risk),
                    "revised_text": "",
                    "comment_text": str(exc),
                    "created_at": _iso_now(),
                }
            # Persist incremental progress so frontend can refresh and show newly generated items in real time.
            reviewed_path.write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding="utf-8")

    reviewed_path.write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "summary": {
            "total": total,
            "created": created,
            "skipped": skipped,
            "failed": failed,
        },
        "risk_items": risk_items,
    }


@app.post("/api/reviews/{run_id}/risks/{risk_id}/ai_accept")
def ai_accept_risk(run_id: str, risk_id: str, body: AiAcceptBody) -> dict[str, Any]:
    run_dir = RUN_ROOT / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run_id 不存在")
    reviewed = get_or_create_reviewed_risks(run_id)
    risk_items = (((reviewed or {}).get("risk_result") or {}).get("risk_items") or [])
    if not isinstance(risk_items, list):
        raise HTTPException(status_code=500, detail="reviewed 风险数据格式错误")

    target: dict[str, Any] | None = None
    for item in risk_items:
        if isinstance(item, dict) and str(item.get("risk_id", "")) == str(risk_id):
            target = item
            break
    if target is None:
        raise HTTPException(status_code=404, detail="risk_id 不存在")

    ai_rewrite = target.get("ai_rewrite") if isinstance(target.get("ai_rewrite"), dict) else None
    if not ai_rewrite or str(ai_rewrite.get("state") or "") != "succeeded":
        raise HTTPException(status_code=409, detail="当前风险不存在可接受的 AI 改写建议")

    current_status = str(target.get("status") or "pending").strip().lower()
    if not _is_accepted_risk_status(current_status):
        clause_alias_map = _build_clause_uid_alias_map(_load_run_clauses(run_dir))
        conflict = _find_accepted_clause_conflict(risk_items, str(risk_id), clause_alias_map)
        if conflict is not None:
            raise HTTPException(status_code=409, detail=_ACCEPT_OVERLAP_DETAIL)

    revised_text = str(body.revised_text or "").strip()
    submitted_target = _sanitize_ai_target_text(str(body.target_text or ""))
    current_target = _sanitize_ai_target_text(str(ai_rewrite.get("target_text") or ""))
    effective_target = submitted_target or current_target
    if current_target:
        ai_rewrite["target_text"] = current_target
    if revised_text:
        ai_rewrite["revised_text"] = revised_text
    effective_revised = str(ai_rewrite.get("revised_text") or "").strip()
    ai_rewrite["comment_text"] = _build_ai_comment_text(
        target_text=str(ai_rewrite.get("target_text") or effective_target or ""),
        revised_text=effective_revised,
    )

    accepted_patch = body.accepted_patch if isinstance(body.accepted_patch, dict) else {}
    accepted_before = _sanitize_ai_target_text(str(accepted_patch.get("before_text") or accepted_patch.get("target_text") or effective_target or ""))
    accepted_after = str(accepted_patch.get("after_text") or revised_text or effective_revised or "").strip()
    if accepted_before and accepted_after:
        target["accepted_patch"] = {
            "before_text": accepted_before,
            "after_text": accepted_after,
            "source": "ai_rewrite",
            "frozen_at": _iso_now(),
        }
    else:
        target.pop("accepted_patch", None)

    target["status"] = "accepted"
    target["ai_rewrite_decision"] = "accepted"

    reviewed_path = run_dir / "risk_result_reviewed.json"
    reviewed_path.write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "item": target}


@app.patch("/api/reviews/{run_id}/risks/{risk_id}/ai_edit")
def ai_edit_risk(run_id: str, risk_id: str, body: AiEditBody) -> dict[str, Any]:
    run_dir = RUN_ROOT / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run_id 不存在")
    reviewed = get_or_create_reviewed_risks(run_id)
    risk_items = (((reviewed or {}).get("risk_result") or {}).get("risk_items") or [])
    if not isinstance(risk_items, list):
        raise HTTPException(status_code=500, detail="reviewed 风险数据格式错误")

    target: dict[str, Any] | None = None
    for item in risk_items:
        if isinstance(item, dict) and str(item.get("risk_id", "")) == str(risk_id):
            target = item
            break
    if target is None:
        raise HTTPException(status_code=404, detail="risk_id 不存在")

    ai_rewrite = target.get("ai_rewrite") if isinstance(target.get("ai_rewrite"), dict) else None
    if not ai_rewrite or str(ai_rewrite.get("state") or "") != "succeeded":
        raise HTTPException(status_code=409, detail="当前风险不存在可编辑的 AI 改写建议")

    revised_text = str(body.revised_text or "").strip()
    if not revised_text:
        raise HTTPException(status_code=400, detail="revised_text 不能为空")

    current_target = _sanitize_ai_target_text(str(ai_rewrite.get("target_text") or ""))
    if current_target:
        ai_rewrite["target_text"] = current_target
    ai_rewrite["revised_text"] = revised_text
    ai_rewrite["comment_text"] = _build_ai_comment_text(
        target_text=str(ai_rewrite.get("target_text") or current_target or ""),
        revised_text=revised_text,
    )
    target["ai_rewrite_decision"] = "proposed"

    reviewed_path = run_dir / "risk_result_reviewed.json"
    reviewed_path.write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "item": target}


@app.post("/api/reviews/{run_id}/risks/{risk_id}/ai_reject")
def ai_reject_risk(run_id: str, risk_id: str) -> dict[str, Any]:
    run_dir = RUN_ROOT / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run_id 不存在")
    reviewed = get_or_create_reviewed_risks(run_id)
    risk_items = (((reviewed or {}).get("risk_result") or {}).get("risk_items") or [])
    if not isinstance(risk_items, list):
        raise HTTPException(status_code=500, detail="reviewed 风险数据格式错误")

    target: dict[str, Any] | None = None
    for item in risk_items:
        if isinstance(item, dict) and str(item.get("risk_id", "")) == str(risk_id):
            target = item
            break
    if target is None:
        raise HTTPException(status_code=404, detail="risk_id 不存在")

    target.pop("ai_rewrite", None)
    target["ai_rewrite_decision"] = "rejected"
    target["status"] = "rejected"

    reviewed_path = run_dir / "risk_result_reviewed.json"
    reviewed_path.write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "item": target}

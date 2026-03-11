from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

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
    error: str | None = None

    if merged_exists and validated_path.exists():
        validated = _safe_json(validated_path) or {}
        if bool(validated.get("is_valid")):
            status = "completed"
            step = "历史结果"
        else:
            status = "failed"
            step = "历史结果校验失败"
            error = validated.get("error_message") or "risk_result_validated.json 校验未通过"

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
        "error": error,
        "updated_at": _latest_mtime_iso(run_dir),
    }


def _read_meta(run_id: str) -> dict[str, Any]:
    path = _meta_path(run_id)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("run_id", run_id)
        payload.setdefault("updated_at", datetime.utcnow().isoformat() + "Z")
        return payload
    return _infer_meta_from_run(run_id)


def _safe_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _build_result_payload(run_id: str) -> dict[str, Any]:
    run_dir = RUN_ROOT / run_id
    clauses = _safe_json(run_dir / "merged_clauses.json")
    validated = _safe_json(run_dir / "risk_result_validated.json")
    if clauses is None or validated is None:
        raise HTTPException(status_code=404, detail="结果尚未生成完成")
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
            "step": "正在调用后端审查流程",
        },
    )

    cmd = ["python", "app.py", str(file_path), "--run-id", run_id]
    proc = subprocess.run(
        cmd,
        cwd=str(BASE_DIR),
        env=env,
        capture_output=True,
        text=True,
    )

    (run_dir / "app.stdout.log").write_text(proc.stdout or "", encoding="utf-8")
    (run_dir / "app.stderr.log").write_text(proc.stderr or "", encoding="utf-8")

    if proc.returncode != 0:
        _write_meta(
            run_id,
            {
                "status": "failed",
                "step": "主流程执行失败",
                "error": (proc.stderr or proc.stdout or "未知错误").strip(),
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
                "warning": (export_proc.stderr or export_proc.stdout or "DOCX 导出失败").strip(),
            },
        )
        return

    _write_meta(
        run_id,
        {
            "status": "completed",
            "step": "审查与 DOCX 批注导出已完成",
        },
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/reviews")
async def create_review(
    file: UploadFile = File(...),
    review_side: str = Form("supplier"),
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


@app.get("/api/reviews/{run_id}/download")
def download_reviewed_docx(run_id: str) -> FileResponse:
    run_dir = RUN_ROOT / run_id
    output = run_dir / "reviewed_comments.docx"
    if not output.exists():
        raise HTTPException(status_code=404, detail="批注版 DOCX 尚未生成")
    return FileResponse(output, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=f"{run_id}_reviewed_comments.docx")

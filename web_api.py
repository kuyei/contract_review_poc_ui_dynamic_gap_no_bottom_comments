from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
RUN_ROOT = BASE_DIR / "data" / "runs"
UPLOAD_ROOT = BASE_DIR / "data" / "uploads"
WEB_META_ROOT = BASE_DIR / "data" / "web_meta"
DEMO_FILE = BASE_DIR / "frontend" / "public" / "demo" / "review_payload.json"

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


def _read_meta(run_id: str) -> dict[str, Any]:
    path = _meta_path(run_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="run_id 不存在")
    return json.loads(path.read_text(encoding="utf-8"))


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


def _run_pipeline(*, run_id: str, file_path: Path, file_name: str, review_side: str, contract_type_hint: str) -> None:
    run_dir = RUN_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
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


class DemoResponse(BaseModel):
    message: str


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/demo/result")
def demo_result() -> Any:
    if not DEMO_FILE.exists():
        raise HTTPException(status_code=404, detail="未找到演示数据")
    return JSONResponse(json.loads(DEMO_FILE.read_text(encoding="utf-8")))


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


@app.get("/api/reviews/{run_id}")
def get_review_status(run_id: str) -> dict[str, Any]:
    return _read_meta(run_id)


@app.get("/api/reviews/{run_id}/result")
def get_review_result(run_id: str) -> dict[str, Any]:
    meta = _read_meta(run_id)
    if meta.get("status") != "completed":
        raise HTTPException(status_code=409, detail="任务尚未完成")
    return _build_result_payload(run_id)


@app.get("/api/reviews/{run_id}/download")
def download_reviewed_docx(run_id: str) -> FileResponse:
    run_dir = RUN_ROOT / run_id
    output = run_dir / "reviewed_comments.docx"
    if not output.exists():
        raise HTTPException(status_code=404, detail="批注版 DOCX 尚未生成")
    return FileResponse(output, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=f"{run_id}_reviewed_comments.docx")

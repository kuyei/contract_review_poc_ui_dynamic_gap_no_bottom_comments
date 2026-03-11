from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(slots=True)
class Settings:
    dify_base_url: str = os.getenv("DIFY_BASE_URL", "http://localhost/v1")
    dify_clause_workflow_api_key: str = os.getenv("DIFY_CLAUSE_WORKFLOW_API_KEY", "")
    dify_risk_workflow_api_key: str = os.getenv("DIFY_RISK_WORKFLOW_API_KEY", "")
    review_side: str = os.getenv("REVIEW_SIDE", "supplier")
    contract_type_hint: str = os.getenv("CONTRACT_TYPE_HINT", "service_agreement")
    request_timeout_seconds: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "180"))
    run_root: Path = Path(os.getenv("RUN_ROOT", "data/runs"))
    debug_save_intermediate: bool = os.getenv("DEBUG_SAVE_INTERMEDIATE", "1") == "1"

    def validate_for_live_call(self) -> None:
        missing = []
        if not self.dify_clause_workflow_api_key:
            missing.append("DIFY_CLAUSE_WORKFLOW_API_KEY")
        if not self.dify_risk_workflow_api_key:
            missing.append("DIFY_RISK_WORKFLOW_API_KEY")
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


settings = Settings()

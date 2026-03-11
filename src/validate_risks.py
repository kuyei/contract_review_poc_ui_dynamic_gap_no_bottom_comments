from __future__ import annotations

from typing import Any


REQUIRED_RISK_FIELDS = {
    "risk_id",
    "dimension",
    "risk_label",
    "risk_level",
    "issue",
    "basis",
    "evidence_text",
    "suggestion",
    "clause_id",
    "display_clause_id",
    "anchor_text",
    "needs_human_review",
    "status",
    "clause_uid",
    "clause_uids",
    "display_clause_ids",
    "clause_ids",
    "is_multi_clause_risk",
    "basis_rule_id",
    "basis_summary",
    "review_required_reason",
    "auto_apply_allowed",
    "is_boilerplate_related",
    "mapping_conflict",
    "risk_source_type",
}


def validate_risk_result(payload: dict[str, Any]) -> tuple[bool, str]:
    risk_items = payload.get("risk_items")
    if not isinstance(risk_items, list):
        return False, "risk_items 不是数组"

    for idx, item in enumerate(risk_items, start=1):
        if not isinstance(item, dict):
            return False, f"第 {idx} 条风险不是对象"
        if "risk_source_type" not in item:
            # Backward compatibility: old payloads without risk_source_type are treated as anchored.
            item["risk_source_type"] = "anchored"
        missing = sorted(REQUIRED_RISK_FIELDS - set(item.keys()))
        if missing:
            return False, f"第 {idx} 条风险缺少字段: {', '.join(missing)}"
        if item.get("needs_human_review") is not True:
            return False, f"第 {idx} 条风险 needs_human_review 必须为 true"
        if item.get("auto_apply_allowed") is not False:
            return False, f"第 {idx} 条风险 auto_apply_allowed 必须为 false"
        if item.get("status") != "pending":
            return False, f"第 {idx} 条风险 status 必须为 pending"
        if not isinstance(item.get("review_required_reason"), list) or not item.get("review_required_reason"):
            return False, f"第 {idx} 条风险 review_required_reason 必须为非空数组"
        risk_source_type = str(item.get("risk_source_type", "anchored") or "anchored").strip()
        if risk_source_type == "anchored":
            if not str(item.get("clause_uid", "")).strip():
                return False, f"第 {idx} 条风险 clause_uid 不能为空"
            if not isinstance(item.get("clause_uids"), list) or not item.get("clause_uids"):
                return False, f"第 {idx} 条风险 clause_uids 必须为非空数组"
            if item.get("clause_uid") != item.get("clause_uids")[0]:
                return False, f"第 {idx} 条风险 clause_uid 必须等于 clause_uids 的首项"
            if not isinstance(item.get("display_clause_ids"), list) or not item.get("display_clause_ids"):
                return False, f"第 {idx} 条风险 display_clause_ids 必须为非空数组"
            if not isinstance(item.get("clause_ids"), list) or not item.get("clause_ids"):
                return False, f"第 {idx} 条风险 clause_ids 必须为非空数组"
            if bool(item.get("is_multi_clause_risk")) != (len(item.get("clause_uids")) > 1):
                return False, f"第 {idx} 条风险 is_multi_clause_risk 与 clause_uids 数量不一致"
        elif risk_source_type == "missing_clause":
            evidence_text = str(item.get("evidence_text", "") or "").strip()
            issue = str(item.get("issue", "") or "").strip()
            risk_label = str(item.get("risk_label", "") or "").strip()
            if not evidence_text and not issue and not risk_label:
                return False, f"第 {idx} 条缺失型风险必须包含 evidence_text、issue 或 risk_label"
        else:
            return False, f"第 {idx} 条风险 risk_source_type 非法: {risk_source_type}"
    return True, ""

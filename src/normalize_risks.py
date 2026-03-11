from __future__ import annotations

import hashlib
import re
from typing import Any

from .normalize_clauses import extract_top_level_from_clause_ref

LAW_REVIEW_PATTERNS = [
    r"LPR",
    r"法定",
    r"上限",
    r"民间借贷",
    r"法律规定",
    r"民法典",
]

BOILERPLATE_RULE_ID = "RULE_TEMPLATE_001"
GENERIC_RULE_ID = "RULE_GENERAL_001"
DIMENSION_RULE_PREFIX = {
    "主体资格与签约权限": "RULE_SUBJECT",
    "服务范围与交付内容": "RULE_SCOPE",
    "服务期限、里程碑与验收标准": "RULE_ACCEPTANCE",
    "付款结算、发票与税费": "RULE_PAYMENT",
    "违约责任与赔偿机制": "RULE_LIABILITY",
    "解除、终止与续约机制": "RULE_TERMINATION",
    "保密、数据安全与合规": "RULE_CONFIDENTIALITY",
    "知识产权归属与使用权": "RULE_IP",
    "权责分配与责任限制": "RULE_ALLOCATION",
    "争议解决、适用法律与管辖": "RULE_DISPUTE",
}

MISSING_CLAUSE_MARKERS = [
    "未找到",
    "未约定",
    "缺失",
    "留白",
    "不明确",
    "没有明确",
    "未明确",
]


CLAUSE_REF_SPLIT_RE = re.compile(r"\s*[、，,；;/]\s*")


def normalize_text(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _basis_rule_id(dimension: str, boilerplate: bool, issue: str) -> str:
    if boilerplate:
        return BOILERPLATE_RULE_ID
    prefix = DIMENSION_RULE_PREFIX.get(dimension, GENERIC_RULE_ID)
    digest = hashlib.sha1(normalize_text(issue).encode("utf-8")).hexdigest()[:4].upper()
    return f"{prefix}_{digest}"


def _basis_summary(dimension: str, issue: str, evidence_text: str, boilerplate: bool) -> str:
    if boilerplate:
        return "模板中存在留白或填写说明，表明正式合同文本尚未定稿，相关权利义务缺失或不确定。"

    issue_norm = normalize_text(issue)
    evidence_norm = normalize_text(evidence_text)

    if any(key in issue_norm for key in ["未明确", "未约定", "缺失", "留白"]):
        return f"{dimension}相关条款存在缺失、留白或约定不明确，当前文本不足以支撑稳定履约与争议处理。"
    if any(key in issue_norm for key in ["过高", "过重", "失衡", "不对等"]):
        return f"{dimension}相关安排对供应商明显不利，责任或负担存在失衡，需要人工评估比例、范围与可谈判空间。"
    if evidence_norm:
        return f"根据原文“{evidence_norm[:60]}{'…' if len(evidence_norm) > 60 else ''}”，{dimension}存在需要进一步人工审核的风险点。"
    return f"{dimension}存在需要进一步人工审核的风险点。"


def _review_reason(item: dict[str, Any], clause_metas: list[dict[str, Any]]) -> list[str]:
    reasons = ["POC阶段默认全量人工复核，禁止系统自动采纳或自动修改合同内容。"]

    issue = normalize_text(str(item.get("issue", "")))
    evidence_text = normalize_text(str(item.get("evidence_text", "")))

    if any(re.search(pattern, issue) or re.search(pattern, evidence_text) for pattern in LAW_REVIEW_PATTERNS):
        reasons.append("风险说明涉及法律判断、比例上限或法规口径，必须由人工确认。")

    if any(bool(meta.get("is_boilerplate_instruction")) for meta in clause_metas):
        reasons.append("命中的条款属于模板说明或留白提示，需要人工判断是否为正式合同内容。")

    if len(clause_metas) > 1:
        reasons.append("该风险关联多个条款，需要人工综合判断其交叉影响与修改方式。")

    if str(item.get("risk_level", "")).lower() == "high":
        reasons.append("高风险项默认进入人工复核。")

    return reasons


def _signature(item: dict[str, Any], clause_uids: list[str]) -> str:
    parts = [
        str(item.get("risk_source_type", "anchored")),
        "|".join(sorted(clause_uids)),
        normalize_text(str(item.get("dimension", ""))).lower(),
        normalize_text(str(item.get("risk_label", ""))).lower(),
        normalize_text(str(item.get("anchor_text", item.get("evidence_text", "")))).lower(),
    ]
    return "||".join(parts)


def _is_missing_clause_risk(item: dict[str, Any]) -> bool:
    clause_id = str(item.get("clause_id", "") or "").strip()
    anchor_text = str(item.get("anchor_text", "") or "").strip()
    evidence_text = str(item.get("evidence_text", "") or "")
    if clause_id or anchor_text:
        return False
    evidence_norm = normalize_text(evidence_text)
    return any(marker in evidence_norm for marker in MISSING_CLAUSE_MARKERS)


def _build_clause_indexes(clauses: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    exact: dict[str, list[dict[str, Any]]] = {}
    by_top: dict[str, list[dict[str, Any]]] = {}

    for clause in clauses:
        keys = {
            str(clause.get("clause_uid", "") or "").strip(),
            str(clause.get("clause_id", "") or "").strip(),
            str(clause.get("display_clause_id", "") or "").strip(),
            str(clause.get("local_clause_id", "") or "").strip(),
            str(clause.get("source_clause_id", "") or "").strip(),
        }
        for key in keys:
            if key:
                exact.setdefault(key, []).append(clause)

        top = extract_top_level_from_clause_ref(clause.get("clause_id"))
        if top:
            by_top.setdefault(top, []).append(clause)

    return exact, by_top


def _select_candidate(candidates: list[dict[str, Any]], anchor: str, evidence: str) -> tuple[dict[str, Any] | None, bool]:
    if not candidates:
        return None, False
    if len(candidates) == 1:
        return candidates[0], False

    texts = [normalize_text(anchor), normalize_text(evidence)]
    texts = [t for t in texts if t]
    if texts:
        narrowed = []
        for candidate in candidates:
            clause_text = normalize_text(str(candidate.get("clause_text", "")))
            if any(t and (t in clause_text or clause_text in t) for t in texts):
                narrowed.append(candidate)
        if len(narrowed) == 1:
            return narrowed[0], False
        if narrowed:
            candidates = narrowed

    return candidates[0], len(candidates) > 1


def _resolve_single_clause_meta(
    clause_ref: str,
    anchor: str,
    evidence: str,
    exact_index: dict[str, list[dict[str, Any]]],
    by_top_index: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, bool]:
    clause_ref = str(clause_ref or "").strip()
    top = extract_top_level_from_clause_ref(clause_ref)

    if top and top in by_top_index:
        candidates = by_top_index[top]

        full_matches = [
            c for c in candidates
            if clause_ref in {
                str(c.get("clause_id", "") or "").strip(),
                str(c.get("display_clause_id", "") or "").strip(),
                str(c.get("source_clause_id", "") or "").strip(),
            }
        ]
        if full_matches:
            return _select_candidate(full_matches, anchor, evidence)

        local = clause_ref[len(top) + 1 :] if clause_ref.startswith(f"{top}.") else clause_ref
        local = local.strip()
        if local:
            local_matches = [
                c
                for c in candidates
                if local in {
                    str(c.get("local_clause_id", "") or "").strip(),
                    str(c.get("source_clause_id", "") or "").strip(),
                    str(c.get("display_clause_id", "") or "").strip().split(f"{top}.", 1)[-1],
                    str(c.get("clause_id", "") or "").strip().split(f"{top}.", 1)[-1],
                }
            ]
            if local_matches:
                chosen, conflict = _select_candidate(local_matches, anchor, evidence)
                if chosen is not None:
                    return chosen, conflict

        chosen, conflict = _select_candidate(candidates, anchor, evidence)
        if chosen is not None:
            return chosen, conflict

    if clause_ref and clause_ref in exact_index:
        return _select_candidate(exact_index[clause_ref], anchor, evidence)

    all_candidates = [c for arr in exact_index.values() for c in arr]
    dedup = {id(c): c for c in all_candidates}.values()
    text_candidates = []
    anchor_norm = normalize_text(anchor)
    evidence_norm = normalize_text(evidence)
    for candidate in dedup:
        clause_text = normalize_text(str(candidate.get("clause_text", "")))
        if anchor_norm and (anchor_norm in clause_text or clause_text in anchor_norm):
            text_candidates.append(candidate)
            continue
        if evidence_norm and (evidence_norm in clause_text or clause_text in evidence_norm):
            text_candidates.append(candidate)
    return _select_candidate(text_candidates, anchor, evidence)


def _split_clause_refs(clause_ref: str) -> list[str]:
    clause_ref = str(clause_ref or "").strip()
    if not clause_ref:
        return []
    refs = [part.strip() for part in CLAUSE_REF_SPLIT_RE.split(clause_ref) if part.strip()]
    return refs or [clause_ref]


def _resolve_clause_metas(
    clause_ref: str,
    anchor: str,
    evidence: str,
    exact_index: dict[str, list[dict[str, Any]]],
    by_top_index: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], bool]:
    refs = _split_clause_refs(clause_ref)
    metas: list[dict[str, Any]] = []
    any_conflict = False
    unresolved = False

    if refs:
        for ref in refs:
            chosen, conflict = _resolve_single_clause_meta(ref, anchor, evidence, exact_index, by_top_index)
            if chosen is None:
                unresolved = True
                continue
            any_conflict = any_conflict or conflict
            metas.append(chosen)
    else:
        chosen, conflict = _resolve_single_clause_meta("", anchor, evidence, exact_index, by_top_index)
        if chosen is not None:
            metas.append(chosen)
            any_conflict = conflict

    dedup: dict[str, dict[str, Any]] = {}
    for meta in metas:
        uid = str(meta.get("clause_uid", "") or "")
        if uid:
            dedup[uid] = meta
    resolved = list(dedup.values())

    if not resolved and (anchor or evidence):
        chosen, conflict = _resolve_single_clause_meta("", anchor, evidence, exact_index, by_top_index)
        if chosen is not None:
            resolved = [chosen]
            any_conflict = any_conflict or conflict

    return resolved, (any_conflict or unresolved)


def _extract_raw_risk_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("risk_items"), list):
        return payload.get("risk_items") or []

    report = payload.get("contract_risk_report")
    if isinstance(report, dict) and isinstance(report.get("risk_details"), list):
        return [item for item in report.get("risk_details") or [] if isinstance(item, dict)]

    return []


def _map_external_risk_item(raw_item: dict[str, Any]) -> dict[str, Any]:
    if "issue" in raw_item or "risk_label" in raw_item or "dimension" in raw_item:
        return dict(raw_item)

    category = str(raw_item.get("risk_category", "") or "").strip()
    issue = str(raw_item.get("risk_point", "") or "").strip()
    evidence = str(raw_item.get("evidence", "") or "").strip()
    suggestion = str(raw_item.get("suggestion", "") or "").strip()
    clause_reference = str(raw_item.get("clause_reference", "") or "").strip()
    level = str(raw_item.get("risk_level", "") or "").strip().lower()
    likelihood = str(raw_item.get("risk_likelihood", "") or "").strip()
    impact = str(raw_item.get("risk_impact", "") or "").strip()

    if level not in {"high", "medium", "low"}:
        if level in {"严重", "高"}:
            level = "high"
        elif level in {"中", "中等"}:
            level = "medium"
        elif level in {"低"}:
            level = "low"
        else:
            level = "medium"

    basis = "；".join([p for p in [likelihood and f"发生可能性：{likelihood}", impact and f"影响：{impact}"] if p])

    return {
        "risk_id": raw_item.get("risk_id", ""),
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
    }


def normalize_and_dedupe_risks(
    payload: dict[str, Any],
    clauses: list[dict[str, Any]],
) -> dict[str, Any]:
    exact_index, by_top_index = _build_clause_indexes(clauses)

    raw_items = _extract_raw_risk_items(payload)
    deduped: list[dict[str, Any]] = []
    seen_signatures: dict[str, dict[str, Any]] = {}

    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue

        item = _map_external_risk_item(raw_item)
        clause_ref = str(item.get("clause_id", "") or "").strip()
        anchor_text = str(item.get("anchor_text", "") or "")
        evidence_text = str(item.get("evidence_text", "") or "")
        risk_source_type = "missing_clause" if _is_missing_clause_risk(item) else "anchored"
        item["risk_source_type"] = risk_source_type

        if risk_source_type == "missing_clause":
            clause_metas = []
            mapping_conflict = False
            clause_uids = []
            display_clause_ids = []
            clause_ids = []
        else:
            clause_metas, mapping_conflict = _resolve_clause_metas(
                clause_ref,
                anchor_text,
                evidence_text,
                exact_index,
                by_top_index,
            )
            clause_uids = [str(meta.get("clause_uid", "") or "") for meta in clause_metas if str(meta.get("clause_uid", "") or "")]
            display_clause_ids = [str(meta.get("display_clause_id", "") or "") for meta in clause_metas if str(meta.get("display_clause_id", "") or "")]
            clause_ids = [str(meta.get("clause_id", "") or "") for meta in clause_metas if str(meta.get("clause_id", "") or "")]

        primary_clause_uid = clause_uids[0] if clause_uids else ""
        primary_display_clause_id = display_clause_ids[0] if display_clause_ids else ""
        joined_clause_ref = "、".join(display_clause_ids) if display_clause_ids else clause_ref

        item["clause_uid"] = primary_clause_uid
        item["display_clause_id"] = primary_display_clause_id
        item["clause_id"] = joined_clause_ref
        item["clause_uids"] = clause_uids
        item["display_clause_ids"] = display_clause_ids
        item["clause_ids"] = clause_ids
        item["is_multi_clause_risk"] = len(clause_uids) > 1
        item["needs_human_review"] = True
        item["status"] = "pending"
        item["auto_apply_allowed"] = False
        item["mapping_conflict"] = mapping_conflict

        is_boilerplate = any(bool(meta.get("is_boilerplate_instruction")) for meta in clause_metas)
        issue = str(item.get("issue", "") or "")
        dimension = str(item.get("dimension", "") or "")

        rule_id = _basis_rule_id(dimension, is_boilerplate, issue)
        basis_summary = _basis_summary(dimension, issue, evidence_text, is_boilerplate)
        item["basis_rule_id"] = rule_id
        item["basis_summary"] = basis_summary
        item["basis"] = f"[{rule_id}] {basis_summary}"
        item["review_required_reason"] = _review_reason(item, clause_metas)
        item["is_boilerplate_related"] = is_boilerplate

        signature = _signature(item, clause_uids or [clause_ref])
        existing = seen_signatures.get(signature)
        if existing is not None:
            existing.setdefault("merged_from_risk_ids", []).append(item.get("risk_id"))
            for field in ["clause_uids", "display_clause_ids", "clause_ids"]:
                merged = list(dict.fromkeys((existing.get(field) or []) + (item.get(field) or [])))
                existing[field] = merged
            existing["is_multi_clause_risk"] = len(existing.get("clause_uids") or []) > 1
            existing["clause_uid"] = (existing.get("clause_uids") or [existing.get("clause_uid", "")])[0]
            existing["display_clause_id"] = (existing.get("display_clause_ids") or [existing.get("display_clause_id", "")])[0]
            existing["clause_id"] = "、".join(existing.get("display_clause_ids") or []) or existing.get("clause_id", "")
            continue

        item["merged_from_risk_ids"] = [item.get("risk_id")]
        seen_signatures[signature] = item
        deduped.append(item)

    for idx, item in enumerate(deduped, start=1):
        item["risk_id"] = idx

    return {"risk_items": deduped}

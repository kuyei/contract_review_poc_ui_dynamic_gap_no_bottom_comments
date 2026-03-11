import unittest

from src.normalize_risks import normalize_and_dedupe_risks
from src.validate_risks import validate_risk_result


CLAUSES = [
    {
        "clause_uid": "segment_10::10.1",
        "segment_id": "segment_10",
        "segment_title": "十、违约责任",
        "clause_id": "10.1",
        "display_clause_id": "10.1",
        "local_clause_id": "1",
        "source_clause_id": "10.1",
        "clause_title": "赔偿责任上限",
        "clause_text": "乙方赔偿责任上限为合同总额的20%。",
        "clause_kind": "contract_clause",
        "is_boilerplate_instruction": False,
    }
]


def _normalize(payload):
    return normalize_and_dedupe_risks(payload, CLAUSES)


class RiskSourceTypeTests(unittest.TestCase):
    def test_anchored_risk_should_pass(self):
        payload = {
            "risk_items": [
                {
                    "risk_id": "R1",
                    "dimension": "违约责任与赔偿机制",
                    "risk_label": "赔偿责任上限过低",
                    "risk_level": "medium",
                    "issue": "赔偿责任上限约定可能不足",
                    "basis": "",
                    "evidence_text": "乙方赔偿责任上限为合同总额的20%。",
                    "suggestion": "提高上限",
                    "clause_id": "10.1",
                    "anchor_text": "乙方赔偿责任上限",
                    "status": "pending",
                }
            ]
        }
        normalized = _normalize(payload)
        item = normalized["risk_items"][0]
        self.assertEqual(item["risk_source_type"], "anchored")
        self.assertTrue(str(item["clause_uid"]).strip())
        self.assertTrue(item["clause_uids"])
        ok, msg = validate_risk_result(normalized)
        self.assertTrue(ok, msg)

    def test_missing_clause_risk_should_pass(self):
        payload = {
            "risk_items": [
                {
                    "risk_id": "R2",
                    "dimension": "权责分配与责任限制",
                    "risk_label": "无乙方赔偿责任上限",
                    "risk_level": "high",
                    "issue": "合同未约定乙方赔偿上限",
                    "basis": "",
                    "evidence_text": "合同中未找到明确的责任限制条款",
                    "suggestion": "补充责任上限条款",
                    "clause_id": "",
                    "anchor_text": "",
                    "status": "pending",
                }
            ]
        }
        normalized = _normalize(payload)
        item = normalized["risk_items"][0]
        self.assertEqual(item["risk_source_type"], "missing_clause")
        self.assertEqual(item["clause_uid"], "")
        self.assertEqual(item["clause_uids"], [])
        ok, msg = validate_risk_result(normalized)
        self.assertTrue(ok, msg)

    def test_missing_clause_without_content_should_fail(self):
        payload = {
            "risk_items": [
                {
                    "risk_id": 1,
                    "dimension": "权责分配与责任限制",
                    "risk_label": "",
                    "risk_level": "medium",
                    "issue": "",
                    "basis": "",
                    "evidence_text": "",
                    "suggestion": "",
                    "clause_id": "",
                    "display_clause_id": "",
                    "anchor_text": "",
                    "needs_human_review": True,
                    "status": "pending",
                    "clause_uid": "",
                    "clause_uids": [],
                    "display_clause_ids": [],
                    "clause_ids": [],
                    "is_multi_clause_risk": False,
                    "basis_rule_id": "RULE_GENERAL_001",
                    "basis_summary": "",
                    "review_required_reason": ["POC阶段默认全量人工复核"],
                    "auto_apply_allowed": False,
                    "is_boilerplate_related": False,
                    "mapping_conflict": False,
                    "risk_source_type": "missing_clause",
                }
            ]
        }
        ok, msg = validate_risk_result(payload)
        self.assertFalse(ok)
        self.assertIn("缺失型风险必须包含", msg)

    def test_unmapped_non_missing_should_stay_anchored_and_fail(self):
        payload = {
            "risk_items": [
                {
                    "risk_id": "R4",
                    "dimension": "付款结算、发票与税费",
                    "risk_label": "付款条款表述不清",
                    "risk_level": "medium",
                    "issue": "付款表述存在歧义",
                    "basis": "",
                    "evidence_text": "付款流程描述需要进一步明确",
                    "suggestion": "补充细节",
                    "clause_id": "",
                    "anchor_text": "",
                    "status": "pending",
                }
            ]
        }
        normalized = _normalize(payload)
        item = normalized["risk_items"][0]
        self.assertEqual(item["risk_source_type"], "anchored")
        self.assertEqual(item["clause_uid"], "")
        self.assertEqual(item["clause_uids"], [])
        ok, msg = validate_risk_result(normalized)
        self.assertFalse(ok)
        self.assertIn("clause_uid 不能为空", msg)


if __name__ == "__main__":
    unittest.main()

"""字段血缘核对证据包测试：导出、更正、异地签核、复核清单与幂等重演。"""
from __future__ import annotations

import sys
sys.path.insert(0, '.')

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.lineage_evidence import LineageEvidenceService

client = TestClient(app)


@pytest.fixture()
def svc() -> LineageEvidenceService:
    return LineageEvidenceService()


class TestExportPackage:
    def test_export_keeps_both_paths_of_same_column(self, svc):
        package = svc.export_package("net_sales")
        assert package["path_count"] == 2
        assert {entry["path_id"] for entry in package["entries"]} == {"e-81", "e-82"}
        # 列名相同也不能丢路径：两条路径的方向不同，分别登记。
        directions = {entry["path_id"]: entry["direction"] for entry in package["entries"]}
        assert directions["e-81"] == "upstream_to_downstream"
        assert directions["e-82"] == "downstream_to_upstream"

    def test_export_carries_version_and_evidence_id(self, svc):
        package = svc.export_package("net_sales")
        for entry in package["entries"]:
            assert entry["source_version"]
            assert entry["evidence_id"]
            assert entry["evidence_revision"] >= 1
            assert entry["evidence_digest"]

    def test_export_blocked_when_path_missing_evidence(self, svc):
        with pytest.raises(Exception) as exc:
            svc.export_package("refund_rate")
        assert "e-83" in str(exc.value)

    def test_package_versions_increment_and_supersede(self, svc):
        first = svc.export_package("net_sales")
        second = svc.export_package("net_sales")
        assert (first["version"], second["version"]) == (1, 2)
        assert second["supersedes"] == first["package_id"]
        summaries = svc.list_packages("net_sales")
        assert [item["version"] for item in summaries] == [1, 2]


class TestCorrection:
    def test_correction_creates_new_revision_without_mutating_history(self, svc):
        package = svc.export_package("net_sales")
        result = svc.correct_evidence("e-81", "v4", "EV-81-B", "发布方更正")
        assert result["changed"] is True
        assert result["previous_evidence"]["evidence_id"] == "EV-81-A"
        assert result["current_evidence"]["evidence_id"] == "EV-81-B"
        assert result["current_evidence"]["revision"] == 2
        # 原包仍然冻结在 v1 的 EV-81-A。
        frozen = svc.get_package(package["package_id"])
        e81 = next(e for e in frozen["entries"] if e["path_id"] == "e-81")
        assert e81["evidence_id"] == "EV-81-A"

    def test_correction_reports_impacted_packages(self, svc):
        package = svc.export_package("net_sales")
        result = svc.correct_evidence("e-81", "v4", "EV-81-B")
        assert [item["package_id"] for item in result["impacted_packages"]] == [package["package_id"]]

    def test_identical_correction_is_noop(self, svc):
        svc.export_package("net_sales")
        result = svc.correct_evidence("e-81", "v3", "EV-81-A")
        assert result["changed"] is False
        assert result["current_evidence"]["revision"] == 1

    def test_supplement_only_path_becomes_exportable(self, svc):
        svc.supplement_evidence("e-83", "v2", "EV-83-A", "补齐退款金额证据")
        package = svc.export_package("refund_rate")
        assert package["entries"][0]["evidence_id"] == "EV-83-A"


class TestAuditSignoff:
    def _export_and_correct(self, svc):
        package = svc.export_package("net_sales")
        svc.correct_evidence("e-81", "v4", "EV-81-B", "发布方更正")
        return package

    def test_audit_uses_original_evidence_and_flags_correction(self, svc):
        package = self._export_and_correct(svc)
        detail = svc.audit_package(package)
        assert detail["conclusion"] == "conditional"
        assert detail["conclusion_label"] == "有条件通过"
        assert detail["anomaly_count"] == 1
        assert detail["anomalies"][0]["path_id"] == "e-81"
        assert detail["anomalies"][0]["code"] == "evidence_corrected"
        # e-82 未被更正，仍然有效。
        assert not any(item["path_id"] == "e-82" for item in detail["anomalies"])

    def test_audit_detail_shows_adopted_vs_current(self, svc):
        package = self._export_and_correct(svc)
        detail = svc.audit_package(package)
        e81 = next(e for e in detail["entries"] if e["path_id"] == "e-81")
        assert e81["adopted_evidence_id"] == "EV-81-A"
        assert e81["adopted_source_version"] == "v3"
        assert e81["current_evidence_id"] == "EV-81-B"
        assert e81["current_source_version"] == "v4"
        assert e81["stale"] is True
        e82 = next(e for e in detail["entries"] if e["path_id"] == "e-82")
        assert e82["stale"] is False
        assert e82["adopted_evidence_id"] == "EV-82-A"

    def test_audit_detail_lists_correction_todo(self, svc):
        detail = svc.audit_package(self._export_and_correct(svc))
        assert [t["path_id"] for t in detail["correction_todos"]] == ["e-81"]
        assert detail["correction_todos"][0]["status"] == "待复核"

    def test_checklist_reflects_signoff(self, svc):
        package = self._export_and_correct(svc)
        svc.audit_package(package)
        rows = {row["path_id"]: row for row in svc.checklist()}
        assert rows["e-81"]["checklist_status"] == "已签核·待复核"
        assert rows["e-81"]["correction_pending"] is True
        assert rows["e-81"]["adopted_evidence_id"] == "EV-81-A"
        assert rows["e-82"]["checklist_status"] == "已签核"
        assert rows["e-82"]["correction_pending"] is False
        assert rows["e-83"]["checklist_status"] == "待补证"

    def test_clean_package_passes(self, svc):
        package = svc.export_package("net_sales")
        detail = svc.audit_package(package)
        assert detail["conclusion"] == "passed"
        assert detail["anomaly_count"] == 0

    def test_path_missing_evidence_blocks_signoff(self, svc):
        package = svc.export_package("net_sales")
        svc._paths["e-82"]["evidence_revisions"].clear()
        detail = svc.audit_package(package)
        assert detail["conclusion"] == "rejected"
        assert any(a["code"] == "evidence_missing" for a in detail["anomalies"])


class TestIdempotency:
    def test_repeat_submission_does_not_double_count(self, svc):
        package = svc.export_package("net_sales")
        svc.correct_evidence("e-81", "v4", "EV-81-B")
        first = svc.audit_package(package)
        second = svc.audit_package(package)
        assert first["audit_id"] == second["audit_id"]
        assert second["duplicate"] is True
        assert second["new_anomaly_count"] == 0
        assert second["anomaly_count"] == 1
        assert second["submission_count"] == 2
        assert second["duplicate_submissions"] == 1

    def test_replay_uses_original_package_not_corrected_evidence(self, svc):
        package = svc.export_package("net_sales")
        audit = svc.audit_package(package)
        svc.correct_evidence("e-81", "v4", "EV-81-B")
        # 再签一版包含更正证据的新包，不能影响原包的签核记录。
        svc.audit_package(svc.export_package("net_sales"))
        replay = svc.replay_audit(audit["audit_id"])
        assert replay["audit_id"] == audit["audit_id"]
        e81 = next(e for e in replay["entries"] if e["path_id"] == "e-81")
        assert e81["adopted_evidence_id"] == "EV-81-A"
        assert e81["current_evidence_id"] == "EV-81-B"
        assert replay["anomaly_count"] == 1
        assert replay["duplicate"] is True

    def test_replay_does_not_roll_back_newer_signoff(self, svc):
        original = svc.export_package("net_sales")
        old_audit = svc.audit_package(original)
        svc.correct_evidence("e-81", "v4", "EV-81-B")
        newer = svc.export_package("net_sales")
        svc.audit_package(newer)
        assert svc.checklist()[0]["checklist_status"] == "已签核"
        # 原包重演只复算异常，不把清单里的新签核退回旧证据。
        svc.replay_audit(old_audit["audit_id"])
        rows = {row["path_id"]: row for row in svc.checklist()}
        assert rows["e-81"]["checklist_status"] == "已签核"
        assert rows["e-81"]["adopted_evidence_id"] == "EV-81-B"
        assert rows["e-81"]["package_id"] == newer["package_id"]

    def test_tampered_package_is_rejected(self, svc):
        package = svc.export_package("net_sales")
        package["entries"][0]["evidence_id"] = "EV-FAKE"
        with pytest.raises(Exception) as exc:
            svc.audit_package(package)
        assert "篡改" in str(exc.value)

    def test_dropping_one_path_is_an_anomaly_not_a_silent_pass(self, svc):
        package = svc.export_package("net_sales")
        package["entries"] = [e for e in package["entries"] if e["path_id"] != "e-82"]
        detail = svc.audit_package(package)
        assert detail["missing_entries"] == ["e-82"]
        assert detail["conclusion"] == "rejected"
        assert [(a["path_id"], a["code"]) for a in detail["anomalies"]] == [
            ("e-82", "path_uncovered"),
        ]
        # e-81 仍然按原包签核，不能因为漏传 e-82 就把整列当成单路径。
        assert [e["path_id"] for e in detail["entries"]] == ["e-81", "e-82"]
        assert detail["adopted_evidence"][0]["path_id"] == "e-81"

    def test_unknown_package_is_rejected(self, svc):
        with pytest.raises(Exception):
            svc.audit_package({"package_id": "PKG-unknown-0001"})


class TestApi:
    def test_full_flow_via_api(self):
        package = client.post(
            "/api/lineage-evidence/packages/export",
            json={"report_column": "net_sales", "issued_by": "发布方"},
        ).json()["package"]
        assert package["path_count"] == 2

        corrected = client.post(
            "/api/lineage-evidence/evidence/e-81/correct",
            json={"source_version": "v4", "evidence_id": "EV-81-B", "note": "更正"},
        )
        assert corrected.status_code == 200
        assert corrected.json()["changed"] is True

        audit = client.post(
            "/api/lineage-evidence/audits",
            json={"package": package, "receiver": "异地审计组"},
        ).json()["audit"]
        assert audit["conclusion_label"] == "有条件通过"

        detail = client.get(f"/api/lineage-evidence/audits/{audit['audit_id']}").json()
        assert detail["evidence_version"]["adopted_revisions"] == {"e-81": 1, "e-82": 1}

        checklist = client.get("/api/lineage-evidence/checklist").json()["items"]
        statuses = {row["path_id"]: row["checklist_status"] for row in checklist}
        assert statuses["e-81"] == "已签核·待复核"
        assert statuses["e-83"] == "待补证"

        replay = client.post(
            f"/api/lineage-evidence/audits/{audit['audit_id']}/replay", json={},
        ).json()["audit"]
        assert replay["anomaly_count"] == 1
        assert replay["duplicate"] is True

        overview = client.get("/api/lineage-evidence/overview").json()
        assert overview["stats"]["signoff_count"] >= 1
        assert any(item["path_id"] == "e-83" for item in overview["pending_evidence"])

    def test_export_unknown_column_returns_404(self):
        resp = client.post(
            "/api/lineage-evidence/packages/export", json={"report_column": "nope"},
        )
        assert resp.status_code == 404

    def test_download_returns_frozen_original(self):
        package = client.post(
            "/api/lineage-evidence/packages/export", json={"report_column": "net_sales"},
        ).json()["package"]
        resp = client.get(f"/api/lineage-evidence/packages/{package['package_id']}/download")
        assert resp.status_code == 200
        assert "attachment" in resp.headers["content-disposition"]
        import json as jsonlib
        downloaded = jsonlib.loads(resp.text)
        assert downloaded["digest"] == package["digest"]
        assert [e["path_id"] for e in downloaded["entries"]] == ["e-81", "e-82"]

    def test_export_incomplete_column_returns_409(self):
        resp = client.post(
            "/api/lineage-evidence/packages/export", json={"report_column": "refund_rate"},
        )
        assert resp.status_code == 409

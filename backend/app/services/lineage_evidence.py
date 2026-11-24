"""字段血缘核对证据包领域服务。

同一报表列可能由多条血缘路径共同支撑（例如 net_sales 同时来自订单金额与
销售明细）。本服务把报表列的**每一条路径**分别封装进证据包，携带方向、
来源版本和证据标识，供异地审计下载后上传核对。证据包一经签发即冻结，
发布方后续更正只新增证据修订，不会改写已签发的原包；签核结论回写复核清单。
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any


class EvidenceError(Exception):
    """证据包领域异常，路由层据此返回可读的业务错误。"""

    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class EvidenceNotFound(EvidenceError):
    status_code = 404


class EvidenceConflict(EvidenceError):
    status_code = 409


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canon(value).encode("utf-8")).hexdigest()


def _evidence(
    path_id: str,
    revision: int,
    source_version: str,
    evidence_id: str,
    issued_at: str,
    note: str,
) -> dict[str, Any]:
    base = {
        "path_id": path_id,
        "revision": revision,
        "source_version": source_version,
        "evidence_id": evidence_id,
        "issued_at": issued_at,
    }
    return {
        **base,
        "evidence_digest": _digest(base),
        "issued_by": "数据平台发布方",
        "note": note,
        "corrected": False,
    }


def _seed_paths() -> dict[str, dict[str, Any]]:
    """三条路径覆盖两个报表列：net_sales 两条路径 + refund_rate 待补证一条。"""
    rows: list[dict[str, Any]] = [
        {
            "path_id": "e-81",
            "report_column": "net_sales",
            "report_name": "净销售额",
            "report_table": "ads_sales_report",
            "direction": "upstream_to_downstream",
            "direction_label": "上游→下游（正向）",
            "upstream_table": "ods_order",
            "upstream_column": "order_amount",
            "downstream_table": "dws_sales_daily",
            "downstream_column": "net_sales",
            "transform": "SUM(order_amount) - SUM(refund_amount)",
            "link_version": "L-5",
            "collected_at": "2026-11-18",
            "evidence_revisions": [
                _evidence(
                    "e-81", 1, "v3", "EV-81-A",
                    "2026-11-18T09:00:00+00:00", "订单金额汇总原始采集证据",
                ),
            ],
        },
        {
            "path_id": "e-82",
            "report_column": "net_sales",
            "report_name": "净销售额",
            "report_table": "ads_sales_report",
            "direction": "downstream_to_upstream",
            "direction_label": "下游→上游（反向回溯）",
            "upstream_table": "dwd_sales_detail",
            "upstream_column": "net_sales",
            "downstream_table": "dws_sales_daily",
            "downstream_column": "net_sales",
            "transform": "COALESCE(detail.net_sales, 0)",
            "link_version": "L-5",
            "collected_at": "2026-11-18",
            "evidence_revisions": [
                _evidence(
                    "e-82", 1, "v7", "EV-82-A",
                    "2026-11-18T09:05:00+00:00", "销售明细回溯采集证据",
                ),
            ],
        },
        {
            "path_id": "e-83",
            "report_column": "refund_rate",
            "report_name": "退款率",
            "report_table": "ads_sales_report",
            "direction": "upstream_to_downstream",
            "direction_label": "上游→下游（正向）",
            "upstream_table": "ods_refund",
            "upstream_column": "refund_amount",
            "downstream_table": "dws_sales_daily",
            "downstream_column": "refund_rate",
            "transform": "SUM(refund_amount) / SUM(order_amount)",
            "link_version": "L-5",
            "collected_at": "2026-11-19",
            "evidence_revisions": [],
        },
    ]
    return {row["path_id"]: row for row in rows}


class LineageEvidenceService:
    """证据包、签核记录和复核清单的内存实现；每次核对都留痕可重演。"""

    def __init__(self) -> None:
        self.reset()

    # ------------------------------------------------------------------
    # 生命周期与读取
    # ------------------------------------------------------------------

    def reset(self) -> None:
        self._paths: dict[str, dict[str, Any]] = _seed_paths()
        self._packages: list[dict[str, Any]] = []
        self._audits: list[dict[str, Any]] = []
        self._audit_seq = 0

    def _path(self, path_id: str) -> dict[str, Any]:
        path = self._paths.get(path_id)
        if path is None:
            raise EvidenceNotFound(f"血缘路径 {path_id} 不存在")
        return path

    @staticmethod
    def current_evidence(path: dict[str, Any]) -> dict[str, Any] | None:
        revisions = path.get("evidence_revisions") or []
        return revisions[-1] if revisions else None

    def _find_package(self, package_id: str) -> dict[str, Any] | None:
        for package in self._packages:
            if package["package_id"] == package_id:
                return package
        return None

    def _column_paths(self, report_column: str) -> list[dict[str, Any]]:
        return sorted(
            (p for p in self._paths.values() if p["report_column"] == report_column),
            key=lambda p: p["path_id"],
        )

    # ------------------------------------------------------------------
    # 报表列
    # ------------------------------------------------------------------

    def columns(self) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for path in self._paths.values():
            grouped.setdefault(path["report_column"], []).append(path)
        result: list[dict[str, Any]] = []
        for column, paths in sorted(grouped.items()):
            paths = sorted(paths, key=lambda p: p["path_id"])
            missing = [p["path_id"] for p in paths if self.current_evidence(p) is None]
            latest = self._latest_package(column)
            result.append({
                "report_column": column,
                "report_name": paths[0]["report_name"],
                "report_table": paths[0]["report_table"],
                "path_count": len(paths),
                "path_ids": [p["path_id"] for p in paths],
                "directions": [p["direction_label"] for p in paths],
                "evidence_ready": not missing,
                "missing_evidence_paths": missing,
                "latest_package": (
                    {
                        "package_id": latest["package_id"],
                        "version": latest["version"],
                        "issued_at": latest["issued_at"],
                        "path_count": latest["path_count"],
                    }
                    if latest else None
                ),
            })
        return result

    def _latest_package(self, report_column: str) -> dict[str, Any] | None:
        candidates = [p for p in self._packages if p["report_column"] == report_column]
        return max(candidates, key=lambda p: p["version"]) if candidates else None

    # ------------------------------------------------------------------
    # 证据包
    # ------------------------------------------------------------------

    def export_package(
        self, report_column: str, issued_by: str = "数据平台发布方",
    ) -> dict[str, Any]:
        column = (report_column or "").strip()
        if not column:
            raise EvidenceError("请指定需要导出证据包的报表列")
        paths = self._column_paths(column)
        if not paths:
            raise EvidenceNotFound(f"报表列 {column} 没有登记血缘路径")
        missing = [p["path_id"] for p in paths if self.current_evidence(p) is None]
        if missing:
            raise EvidenceConflict(
                f"报表列 {column} 存在待补证路径：{'、'.join(missing)}，补齐证据后才能生成证据包"
            )
        version = max(
            (p["version"] for p in self._packages if p["report_column"] == column),
            default=0,
        ) + 1
        entries = [self._package_entry(path) for path in paths]
        previous = self._latest_package(column)
        body = {
            "report_column": column,
            "report_name": paths[0]["report_name"],
            "report_table": paths[0]["report_table"],
            "version": version,
            "entries": entries,
        }
        package = {
            **body,
            "package_id": f"PKG-{column}-{version:04d}",
            "issued_at": _now(),
            "issued_by": issued_by or "数据平台发布方",
            "path_count": len(entries),
            "digest": _digest(body),
            "supersedes": previous["package_id"] if previous else None,
        }
        self._packages.append(package)
        return copy.deepcopy(package)

    @staticmethod
    def _package_entry(path: dict[str, Any]) -> dict[str, Any]:
        evidence = LineageEvidenceService.current_evidence(path)
        if evidence is None:  # pragma: no cover - 调用前已拦截
            raise EvidenceConflict(f"路径 {path['path_id']} 缺少证据")
        return {
            "path_id": path["path_id"],
            "report_column": path["report_column"],
            "direction": path["direction"],
            "direction_label": path["direction_label"],
            "upstream_table": path["upstream_table"],
            "upstream_column": path["upstream_column"],
            "downstream_table": path["downstream_table"],
            "downstream_column": path["downstream_column"],
            "transform": path["transform"],
            "link_version": path["link_version"],
            "source_version": evidence["source_version"],
            "evidence_id": evidence["evidence_id"],
            "evidence_revision": evidence["revision"],
            "evidence_digest": evidence["evidence_digest"],
            "evidence_issued_at": evidence["issued_at"],
        }

    def list_packages(self, report_column: str | None = None) -> list[dict[str, Any]]:
        packages = self._packages
        if report_column:
            packages = [p for p in packages if p["report_column"] == report_column]
        return [self._package_summary(p) for p in sorted(packages, key=lambda p: p["issued_at"])]

    @staticmethod
    def _package_summary(package: dict[str, Any]) -> dict[str, Any]:
        return {
            "package_id": package["package_id"],
            "report_column": package["report_column"],
            "report_name": package["report_name"],
            "version": package["version"],
            "path_count": package["path_count"],
            "path_ids": [entry["path_id"] for entry in package["entries"]],
            "issued_at": package["issued_at"],
            "issued_by": package["issued_by"],
            "digest": package["digest"],
            "supersedes": package.get("supersedes"),
        }

    def get_package(self, package_id: str) -> dict[str, Any]:
        package = self._find_package(package_id)
        if package is None:
            raise EvidenceNotFound(f"证据包 {package_id} 不存在")
        return copy.deepcopy(package)

    # ------------------------------------------------------------------
    # 证据更正 / 补录
    # ------------------------------------------------------------------

    def correct_evidence(
        self,
        path_id: str,
        source_version: str,
        evidence_id: str,
        note: str = "",
        operator: str = "数据平台发布方",
    ) -> dict[str, Any]:
        path = self._path(path_id)
        version = (source_version or "").strip()
        identifier = (evidence_id or "").strip()
        if not version or not identifier:
            raise EvidenceError("更正证据必须填写来源版本和证据标识")
        current = self.current_evidence(path)
        if current and current["source_version"] == version and current["evidence_id"] == identifier:
            return {
                "changed": False,
                "path_id": path_id,
                "message": "来源版本与证据标识均未变化，未新增证据修订",
                "current_evidence": copy.deepcopy(current),
                "impacted_packages": [],
            }
        revision = current["revision"] + 1 if current else 1
        if current:
            current["corrected"] = True
            current["corrected_at"] = _now()
            current["corrected_by"] = operator
        revision_row = _evidence(
            path_id, revision, version, identifier, _now(),
            (note or "").strip() or "发布方更正证据",
        )
        path.setdefault("evidence_revisions", []).append(revision_row)
        review = path.get("review")
        if review is not None:
            # 已签核后又发生更正：签核采用的仍是原证据，更正另行待办。
            review["status"] = "已签核·待复核"
            review["correction_pending"] = True
            review["pending_correction_revision"] = revision
        impacted = [
            {
                "package_id": package["package_id"],
                "version": package["version"],
                "issued_at": package["issued_at"],
                "path_id": entry["path_id"],
                "adopted_evidence_id": entry["evidence_id"],
                "adopted_source_version": entry["source_version"],
            }
            for package in self._packages
            for entry in package["entries"]
            if entry["path_id"] == path_id and entry["evidence_revision"] < revision
        ]
        return {
            "changed": True,
            "path_id": path_id,
            "report_column": path["report_column"],
            "previous_evidence": copy.deepcopy(current),
            "current_evidence": copy.deepcopy(revision_row),
            "message": f"路径 {path_id} 已更正到证据 {identifier}（{version}，第{revision}版）",
            "impacted_packages": impacted,
        }

    def supplement_evidence(
        self,
        path_id: str,
        source_version: str,
        evidence_id: str,
        note: str = "",
        operator: str = "数据平台发布方",
    ) -> dict[str, Any]:
        return self.correct_evidence(path_id, source_version, evidence_id, note, operator)

    # ------------------------------------------------------------------
    # 异地签核
    # ------------------------------------------------------------------

    def audit_package(
        self,
        package: dict[str, Any],
        receiver: str = "异地审计组",
        *,
        update_review: bool = True,
    ) -> dict[str, Any]:
        stored, missing_entries = self._verify_package(package)
        audit = next(
            (
                item for item in self._audits
                if item["package_id"] == stored["package_id"]
                and item["package_digest"] == stored["digest"]
            ),
            None,
        )
        duplicate = audit is not None
        if audit is None:
            self._audit_seq += 1
            audit = {
                "audit_id": f"AUD-{self._audit_seq:04d}",
                "package_id": stored["package_id"],
                "package_version": stored["version"],
                "package_digest": stored["digest"],
                "report_column": stored["report_column"],
                "created_at": _now(),
                "submissions": [],
                "anomalies": [],
            }
            self._audits.append(audit)
        audit["receiver"] = (receiver or "").strip() or "异地审计组"
        new_anomaly_count = self._evaluate_package(
            stored, audit, missing_entries, update_review=update_review,
        )
        attempt = len(audit["submissions"]) + 1
        audit["submissions"].append({
            "attempt": attempt,
            "submitted_at": _now(),
            "duplicate": duplicate,
            "incomplete_entries": missing_entries,
            "new_anomaly_count": new_anomaly_count,
            "anomaly_count": len(audit["anomalies"]),
        })
        audit["submission_count"] = attempt
        audit["duplicate_submissions"] = sum(
            1 for item in audit["submissions"] if item["duplicate"]
        )
        audit["last_submitted_at"] = audit["submissions"][-1]["submitted_at"]
        audit["conclusion"], audit["conclusion_label"] = self._conclusion(audit)
        detail = self._audit_detail(audit)
        detail["duplicate"] = duplicate
        detail["new_anomaly_count"] = new_anomaly_count
        detail["missing_entries"] = missing_entries
        return detail

    def _verify_package(
        self, package: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """校验上传包与签发记录一致，返回签发原包和缺失的路径列表。

        每条上传路径必须与签发时逐字段一致；只允许「整条路径缺失」这一种
        残缺形态，它会被记为 path_uncovered 异常而不是直接拒绝，便于留下
        接收方删掉一路的审计痕迹。
        """
        if not isinstance(package, dict):
            raise EvidenceError("证据包格式不正确，应为 JSON 对象")
        package_id = str(package.get("package_id") or "").strip()
        if not package_id:
            raise EvidenceError("证据包缺少 package_id，无法核对")
        stored = self._find_package(package_id)
        if stored is None:
            raise EvidenceNotFound(f"证据包 {package_id} 不是本平台签发，无法核对")
        required = ("report_column", "report_name", "report_table", "version", "entries", "digest")
        missing = [key for key in required if key not in package]
        if missing:
            raise EvidenceError(f"证据包缺少字段：{'、'.join(missing)}")
        if (
            package.get("report_column") != stored["report_column"]
            or package.get("report_name") != stored["report_name"]
            or package.get("report_table") != stored["report_table"]
            or package.get("version") != stored["version"]
            or package.get("digest") != stored["digest"]
        ):
            raise EvidenceConflict("证据包摘要与签发记录不一致，疑似被篡改，已拒绝核对")
        signed_entries = {entry["path_id"]: entry for entry in stored["entries"]}
        uploaded = package.get("entries")
        if not isinstance(uploaded, list) or not uploaded:
            raise EvidenceError("证据包没有路径明细，无法逐条核对")
        uploaded_paths: list[str] = []
        for entry in uploaded:
            if not isinstance(entry, dict):
                raise EvidenceError("证据包路径明细格式不正确")
            path_id = str(entry.get("path_id") or "")
            if path_id not in signed_entries:
                raise EvidenceConflict(
                    f"证据包出现未签发的路径 {path_id or '（空）'}，疑似被篡改，已拒绝核对")
            if _canon(entry) != _canon(signed_entries[path_id]):
                raise EvidenceConflict(
                    f"路径 {path_id} 的证据字段与签发记录不一致，疑似被篡改，已拒绝核对")
            uploaded_paths.append(path_id)
        if len(uploaded_paths) != len(set(uploaded_paths)):
            raise EvidenceConflict("证据包同一路径重复出现，疑似被篡改，已拒绝核对")
        missing_entries = [
            path_id for path_id in signed_entries if path_id not in set(uploaded_paths)
        ]
        if not missing_entries:
            body = {
                key: stored[key]
                for key in ("report_column", "report_name", "report_table", "version", "entries")
            }
            if stored["digest"] != _digest(body):
                raise EvidenceConflict("证据包摘要与签发记录不一致，疑似被篡改，已拒绝核对")
        return stored, missing_entries

    def _evaluate_package(
        self,
        package: dict[str, Any],
        audit: dict[str, Any],
        missing_entries: list[str] | None = None,
        *,
        update_review: bool = True,
    ) -> int:
        """逐路径比对原包证据与当前证据，重建本次核对的异常集合。

        每次核对都按当前状态重新计算，重复提交同一个包得到同样的集合，
        因此异常条数不会因为重复提交而累加；返回本次新增的异常条数。
        ``update_review=False`` 用于重演：只复算异常，不回退复核清单里
        更新的签核结论。
        """
        findings: dict[str, dict[str, Any]] = {}
        package_paths = {entry["path_id"] for entry in package["entries"]}

        def record(path_id: str, code: str, detail: str) -> None:
            key = f"{path_id}|{code}"
            findings.setdefault(key, {
                "key": key, "path_id": path_id, "code": code, "detail": detail,
            })

        for entry in package["entries"]:
            path = self._paths.get(entry["path_id"])
            if path is None:
                record(
                    entry["path_id"], "path_missing",
                    f"证据包中的路径 {entry['path_id']} 已不存在于血缘目录",
                )
                continue
            current = self.current_evidence(path)
            stale = current is not None and current["revision"] > entry["evidence_revision"]
            if current is None:
                record(
                    path["path_id"], "evidence_missing",
                    f"路径 {path['path_id']} 当前缺少证据标识，本次采用的原包证据无法复核",
                )
            elif stale:
                record(
                    path["path_id"], "evidence_corrected",
                    f"本次采用 {entry['evidence_id']}（{entry['source_version']}，"
                    f"第{entry['evidence_revision']}版），当前已更正为 "
                    f"{current['evidence_id']}（{current['source_version']}，"
                    f"第{current['revision']}版），更正需另行复核",
                )
            if not update_review:
                continue
            path["review"] = {
                "status": "已签核·待复核" if stale else "已签核",
                "audit_id": audit["audit_id"],
                "package_id": package["package_id"],
                "package_version": package["version"],
                "adopted_evidence_id": entry["evidence_id"],
                "adopted_source_version": entry["source_version"],
                "adopted_revision": entry["evidence_revision"],
                "current_evidence_id": current["evidence_id"] if current else None,
                "current_source_version": current["source_version"] if current else None,
                "current_revision": current["revision"] if current else None,
                "signed_at": _now(),
                "correction_pending": stale,
            }        # 同一报表列的所有路径都要核对，原包漏掉任何一条都要报异常。
        uncovered = set(missing_entries or [])
        for path in self._column_paths(package["report_column"]):
            if path["path_id"] not in package_paths:
                uncovered.add(path["path_id"])
        for path_id in sorted(uncovered):
            record(
                path_id, "path_uncovered",
                f"原包未包含报表列 {package['report_column']} 的路径 "
                f"{path_id}，同一报表列的多条路径必须分别核对",
            )

        audit["anomalies"] = list(findings.values())
        seen: list[str] = audit.setdefault("seen_anomaly_keys", [])
        new_keys = [key for key in findings if key not in seen]
        for key in new_keys:
            seen.append(key)
        return len(new_keys)

    @staticmethod
    def _conclusion(audit: dict[str, Any]) -> tuple[str, str]:
        codes = {item["code"] for item in audit["anomalies"]}
        if codes & {"path_missing", "path_uncovered", "evidence_missing"}:
            return "rejected", "不通过"
        if "evidence_corrected" in codes:
            return "conditional", "有条件通过"
        return "passed", "通过"

    def get_audit(self, audit_id: str) -> dict[str, Any]:
        audit = next((a for a in self._audits if a["audit_id"] == audit_id), None)
        if audit is None:
            raise EvidenceNotFound(f"签核记录 {audit_id} 不存在")
        return self._audit_detail(audit)

    def replay_audit(self, audit_id: str, receiver: str | None = None) -> dict[str, Any]:
        audit = next((a for a in self._audits if a["audit_id"] == audit_id), None)
        if audit is None:
            raise EvidenceNotFound(f"签核记录 {audit_id} 不存在")
        stored = self._find_package(audit["package_id"])
        if stored is None:  # pragma: no cover - 正常流程不会发生
            raise EvidenceNotFound(f"证据包 {audit['package_id']} 不存在")
        # 重演始终使用当初签核的原包，而不是后来更正的证据；只复算异常，
        # 不把复核清单回退到旧包的签核结论。
        return self.audit_package(
            stored, receiver or audit.get("receiver") or "异地审计组",
            update_review=False,
        )

    def list_audits(self) -> list[dict[str, Any]]:
        return [self._audit_summary(a) for a in sorted(self._audits, key=lambda x: x["created_at"])]

    def _audit_summary(self, audit: dict[str, Any]) -> dict[str, Any]:
        conclusion, label = self._conclusion(audit)
        return {
            "audit_id": audit["audit_id"],
            "package_id": audit["package_id"],
            "package_version": audit["package_version"],
            "package_digest": audit["package_digest"],
            "report_column": audit["report_column"],
            "receiver": audit.get("receiver", "异地审计组"),
            "created_at": audit["created_at"],
            "last_submitted_at": audit.get("last_submitted_at"),
            "conclusion": conclusion,
            "conclusion_label": label,
            "anomaly_count": len(audit["anomalies"]),
            "submission_count": audit.get("submission_count", 0),
            "duplicate_submissions": audit.get("duplicate_submissions", 0),
            "correction_todo_count": sum(
                1 for item in audit["anomalies"] if item["code"] == "evidence_corrected"
            ),
        }

    def _audit_detail(self, audit: dict[str, Any]) -> dict[str, Any]:
        package = self._find_package(audit["package_id"])
        entries: list[dict[str, Any]] = []
        todos: list[dict[str, Any]] = []
        if package is not None:
            for entry in package["entries"]:
                path = self._paths.get(entry["path_id"])
                current = self.current_evidence(path) if path else None
                stale = current is not None and current["revision"] > entry["evidence_revision"]
                conclusion = "证据已更正，待复核" if stale else "与当前证据一致"
                entries.append({
                    **entry,
                    "adopted_evidence_id": entry["evidence_id"],
                    "adopted_source_version": entry["source_version"],
                    "adopted_revision": entry["evidence_revision"],
                    "current_evidence_id": current["evidence_id"] if current else None,
                    "current_source_version": current["source_version"] if current else None,
                    "current_revision": current["revision"] if current else None,
                    "stale": stale,
                    "conclusion": conclusion,
                })
                if stale:
                    todos.append({
                        "todo_id": f"{audit['audit_id']}-{entry['path_id']}",
                        "path_id": entry["path_id"],
                        "report_column": package["report_column"],
                        "code": "evidence_corrected",
                        "status": "待复核",
                        "adopted_evidence_id": entry["evidence_id"],
                        "adopted_source_version": entry["source_version"],
                        "current_evidence_id": current["evidence_id"],
                        "current_source_version": current["source_version"],
                        "detail": f"{entry['path_id']} 的更正证据 "
                                  f"{current['evidence_id']}（{current['source_version']}）待补充复核",
                    })
        conclusion, label = self._conclusion(audit)
        return {
            "audit_id": audit["audit_id"],
            "package_id": audit["package_id"],
            "package_version": audit["package_version"],
            "package_digest": audit["package_digest"],
            "report_column": audit["report_column"],
            "receiver": audit.get("receiver", "异地审计组"),
            "created_at": audit["created_at"],
            "last_submitted_at": audit.get("last_submitted_at"),
            "conclusion": conclusion,
            "conclusion_label": label,
            "submission_count": audit.get("submission_count", 0),
            "duplicate_submissions": audit.get("duplicate_submissions", 0),
            "anomaly_count": len(audit["anomalies"]),
            "anomalies": copy.deepcopy(audit["anomalies"]),
            "correction_todos": todos,
            "entries": entries,
            "adopted_evidence": [
                {
                    "path_id": item["path_id"],
                    "direction": item["direction"],
                    "direction_label": item["direction_label"],
                    "evidence_id": item["adopted_evidence_id"],
                    "source_version": item["adopted_source_version"],
                    "revision": item["adopted_revision"],
                }
                for item in entries
            ],
            "evidence_version": {
                "package_version": audit["package_version"],
                "package_digest": audit["package_digest"],
                "adopted_revisions": {
                    item["path_id"]: item["adopted_revision"] for item in entries
                },
            },
            "submissions": copy.deepcopy(audit["submissions"]),
            "package": (
                {
                    "package_id": package["package_id"],
                    "version": package["version"],
                    "issued_at": package["issued_at"],
                    "digest": package["digest"],
                }
                if package else None
            ),
        }

    # ------------------------------------------------------------------
    # 复核清单与总览
    # ------------------------------------------------------------------

    def checklist(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self._paths.values(), key=lambda p: p["path_id"]):
            current = self.current_evidence(path)
            review = path.get("review")
            if current is None:
                checklist_status = "待补证"
            elif review is None:
                checklist_status = "待签核"
            elif int(review.get("adopted_revision", 0)) < int(current["revision"]):
                checklist_status = "已签核·待复核"
            else:
                checklist_status = "已签核"
            rows.append({
                "path_id": path["path_id"],
                "report_column": path["report_column"],
                "report_name": path["report_name"],
                "direction": path["direction"],
                "direction_label": path["direction_label"],
                "upstream_table": path["upstream_table"],
                "upstream_column": path["upstream_column"],
                "downstream_table": path["downstream_table"],
                "downstream_column": path["downstream_column"],
                "current_evidence_id": current["evidence_id"] if current else None,
                "current_source_version": current["source_version"] if current else None,
                "current_revision": current["revision"] if current else None,
                "checklist_status": checklist_status,
                "needs_evidence": current is None,
                "correction_pending": checklist_status == "已签核·待复核",
                "adopted_evidence_id": review.get("adopted_evidence_id") if review else None,
                "adopted_source_version": review.get("adopted_source_version") if review else None,
                "adopted_revision": review.get("adopted_revision") if review else None,
                "signed_audit_id": review.get("audit_id") if review else None,
                "package_id": review.get("package_id") if review else None,
                "package_version": review.get("package_version") if review else None,
                "signed_at": review.get("signed_at") if review else None,
            })
        return rows

    def overview(self) -> dict[str, Any]:
        columns = self.columns()
        packages = self.list_packages()
        signoffs = self.list_audits()
        checklist = self.checklist()
        # 「仍需补证」只反映当前确实缺证据的关系；历史上漏传某一路属于
        # 那次签核的异常，记在签核详情里，不在这里长期挂着。
        pending_evidence = [
            {
                "path_id": row["path_id"],
                "report_column": row["report_column"],
                "direction_label": row["direction_label"],
                "reason": "当前没有证据标识",
            }
            for row in checklist if row["needs_evidence"]
        ]
        pending_review = [
            {
                "path_id": row["path_id"],
                "report_column": row["report_column"],
                "adopted_evidence_id": row["adopted_evidence_id"],
                "adopted_source_version": row["adopted_source_version"],
                "current_evidence_id": row["current_evidence_id"],
                "current_source_version": row["current_source_version"],
                "signed_audit_id": row["signed_audit_id"],
                "todo": "更正证据待补充复核",
            }
            for row in checklist if row["correction_pending"]
        ]
        return {
            "stats": {
                "column_count": len(columns),
                "package_count": len(packages),
                "signoff_count": len(signoffs),
                "pending_evidence_count": len(pending_evidence),
                "pending_review_count": len(pending_review),
                "anomaly_count": sum(len(a["anomalies"]) for a in self._audits),
            },
            "columns": columns,
            "packages": packages,
            "signoffs": signoffs,
            "checklist": checklist,
            "pending_evidence": pending_evidence,
            "pending_review": pending_review,
        }


lineage_evidence_service = LineageEvidenceService()

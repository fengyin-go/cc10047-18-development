"""字段血缘核对证据包接口：导出、更正、异地签核、复核清单与重演。"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.services.lineage_evidence import (
    EvidenceError,
    lineage_evidence_service as service,
)

router = APIRouter(prefix="/api/lineage-evidence", tags=["字段血缘核对"])


class ExportPayload(BaseModel):
    report_column: str
    issued_by: str | None = None


class EvidencePayload(BaseModel):
    source_version: str
    evidence_id: str
    note: str | None = None
    operator: str | None = None


class AuditPayload(BaseModel):
    package: dict[str, Any]
    receiver: str | None = None


class ReplayPayload(BaseModel):
    receiver: str | None = None


def _fail(error: EvidenceError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


@router.get("/overview")
def overview() -> dict[str, Any]:
    """证据包、签核结论和仍需补证关系的总览。"""
    return service.overview()


@router.get("/columns")
def columns() -> dict[str, Any]:
    return {"items": service.columns(), "total": len(service.columns())}


@router.get("/checklist")
def checklist() -> dict[str, Any]:
    """血缘复核清单：每条路径的当前证据、采用证据与待办状态。"""
    rows = service.checklist()
    return {"items": rows, "total": len(rows)}


@router.get("/packages")
def list_packages(report_column: str | None = None) -> dict[str, Any]:
    rows = service.list_packages(report_column)
    return {"items": rows, "total": len(rows)}


@router.post("/packages/export")
def export_package(payload: ExportPayload) -> dict[str, Any]:
    """按报表列导出证据包，每条路径带方向、来源版本和证据标识。"""
    try:
        package = service.export_package(payload.report_column, payload.issued_by or "")
    except EvidenceError as error:
        raise _fail(error) from error
    return {"ok": True, "message": f"证据包 {package['package_id']} 已生成", "package": package}


@router.get("/packages/{package_id}")
def get_package(package_id: str) -> dict[str, Any]:
    try:
        return service.get_package(package_id)
    except EvidenceError as error:
        raise _fail(error) from error


@router.get("/packages/{package_id}/download")
def download_package(package_id: str) -> Response:
    """下载证据包 JSON 原件，供接收方断网后带到异地核对。"""
    try:
        package = service.get_package(package_id)
    except EvidenceError as error:
        raise _fail(error) from error
    body = json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True)
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{package_id}.json"',
        },
    )


@router.post("/evidence/{path_id}/correct")
def correct_evidence(path_id: str, payload: EvidencePayload) -> dict[str, Any]:
    try:
        result = service.correct_evidence(
            path_id, payload.source_version, payload.evidence_id,
            payload.note or "", payload.operator or "数据平台发布方",
        )
    except EvidenceError as error:
        raise _fail(error) from error
    return {
        "ok": True,
        "message": result["message"],
        "changed": result["changed"],
        "correction": result,
    }


@router.post("/evidence/{path_id}/supplement")
def supplement_evidence(path_id: str, payload: EvidencePayload) -> dict[str, Any]:
    try:
        result = service.supplement_evidence(
            path_id, payload.source_version, payload.evidence_id,
            payload.note or "", payload.operator or "数据平台发布方",
        )
    except EvidenceError as error:
        raise _fail(error) from error
    return {"ok": True, "message": result["message"], "correction": result}


@router.post("/audits")
def submit_package(payload: AuditPayload = Body(...)) -> dict[str, Any]:
    """接收方上传证据包完成异地核对，结论回写复核清单。"""
    try:
        detail = service.audit_package(payload.package, payload.receiver or "异地审计组")
    except EvidenceError as error:
        raise _fail(error) from error
    return {"ok": True, "message": f"签核完成：{detail['conclusion_label']}", "audit": detail}


@router.get("/audits")
def list_audits() -> dict[str, Any]:
    rows = service.list_audits()
    return {"items": rows, "total": len(rows)}


@router.get("/audits/{audit_id}")
def get_audit(audit_id: str) -> dict[str, Any]:
    try:
        return service.get_audit(audit_id)
    except EvidenceError as error:
        raise _fail(error) from error


@router.post("/audits/{audit_id}/replay")
def replay_audit(audit_id: str, payload: ReplayPayload | None = None) -> dict[str, Any]:
    """用当初签核的原包重演，复制粘贴同一包不会重复累计异常。"""
    try:
        detail = service.replay_audit(audit_id, payload.receiver if payload else None)
    except EvidenceError as error:
        raise _fail(error) from error
    return {"ok": True, "message": f"原包重演完成：{detail['conclusion_label']}", "audit": detail}

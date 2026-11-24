import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ------------------------------------------------------------------
// 字段血缘核对证据包
// ------------------------------------------------------------------

export type EvidenceEntry = {
  path_id: string
  report_column: string
  direction: string
  direction_label: string
  upstream_table: string
  upstream_column: string
  downstream_table: string
  downstream_column: string
  transform: string
  link_version: string
  source_version: string
  evidence_id: string
  evidence_revision: number
  evidence_digest: string
  evidence_issued_at: string
}

export type EvidencePackage = {
  package_id: string
  report_column: string
  report_name: string
  report_table: string
  version: number
  entries: EvidenceEntry[]
  issued_at: string
  issued_by: string
  path_count: number
  digest: string
  supersedes?: string | null
}

export type EvidenceColumn = {
  report_column: string
  report_name: string
  report_table: string
  path_count: number
  path_ids: string[]
  directions: string[]
  evidence_ready: boolean
  missing_evidence_paths: string[]
  latest_package: { package_id: string; version: number; issued_at: string; path_count: number } | null
}

export type ChecklistRow = {
  path_id: string
  report_column: string
  report_name: string
  direction: string
  direction_label: string
  upstream_table: string
  upstream_column: string
  downstream_table: string
  downstream_column: string
  current_evidence_id: string | null
  current_source_version: string | null
  current_revision: number | null
  checklist_status: string
  needs_evidence: boolean
  correction_pending: boolean
  adopted_evidence_id: string | null
  adopted_source_version: string | null
  adopted_revision: number | null
  signed_audit_id: string | null
  package_id: string | null
  package_version: number | null
  signed_at: string | null
}

export type AuditEntry = EvidenceEntry & {
  adopted_evidence_id: string
  adopted_source_version: string
  adopted_revision: number
  current_evidence_id: string | null
  current_source_version: string | null
  current_revision: number | null
  stale: boolean
  conclusion: string
}

export type CorrectionTodo = {
  todo_id: string
  path_id: string
  report_column: string
  code: string
  status: string
  adopted_evidence_id: string
  adopted_source_version: string
  current_evidence_id: string
  current_source_version: string
  detail: string
}

export type AuditDetail = {
  audit_id: string
  package_id: string
  package_version: number
  package_digest: string
  report_column: string
  receiver: string
  created_at: string
  last_submitted_at: string | null
  conclusion: string
  conclusion_label: string
  submission_count: number
  duplicate_submissions: number
  anomaly_count: number
  new_anomaly_count?: number
  duplicate?: boolean
  anomalies: { key: string; path_id: string; code: string; detail: string }[]
  correction_todos: CorrectionTodo[]
  entries: AuditEntry[]
  adopted_evidence: {
    path_id: string
    direction: string
    direction_label: string
    evidence_id: string
    source_version: string
    revision: number
  }[]
  evidence_version: {
    package_version: number
    package_digest: string
    adopted_revisions: Record<string, number>
  }
  submissions: {
    attempt: number
    submitted_at: string
    duplicate: boolean
    new_anomaly_count: number
    anomaly_count: number
  }[]
  package: { package_id: string; version: number; issued_at: string; digest: string } | null
}

export type AuditSummary = {
  audit_id: string
  package_id: string
  package_version: number
  package_digest: string
  report_column: string
  receiver: string
  created_at: string
  last_submitted_at: string | null
  conclusion: string
  conclusion_label: string
  anomaly_count: number
  submission_count: number
  duplicate_submissions: number
  correction_todo_count: number
}

export type EvidenceOverview = {
  stats: {
    column_count: number
    package_count: number
    signoff_count: number
    pending_evidence_count: number
    pending_review_count: number
    anomaly_count: number
  }
  columns: EvidenceColumn[]
  packages: (Omit<EvidencePackage, 'entries'> & { path_ids: string[] })[]
  signoffs: AuditSummary[]
  checklist: ChecklistRow[]
  pending_evidence: {
    path_id: string
    report_column: string | null
    direction_label: string | null
    reason: string
  }[]
  pending_review: {
    path_id: string
    report_column: string
    adopted_evidence_id: string | null
    adopted_source_version: string | null
    current_evidence_id: string | null
    current_source_version: string | null
    signed_audit_id: string | null
    todo: string
  }[]
}

async function readError(response: Response, fallback: string): Promise<never> {
  const payload = await response.json().catch(() => null)
  if (payload && typeof payload.detail === 'string') throw new Error(payload.detail)
  throw new Error(fallback)
}

export async function loadPackage(packageId: string): Promise<EvidencePackage> {
  const response = await request(`/api/lineage-evidence/packages/${packageId}`)
  if (!response.ok) return readError(response, '证据包读取失败')
  return await response.json()
}

export async function loadEvidenceOverview(): Promise<EvidenceOverview> {
  const response = await request('/api/lineage-evidence/overview')
  if (!response.ok) return readError(response, '证据总览读取失败')
  return await response.json()
}

export async function exportEvidencePackage(
  reportColumn: string,
): Promise<EvidencePackage> {
  const response = await request('/api/lineage-evidence/packages/export', {
    method: 'POST',
    body: JSON.stringify({ report_column: reportColumn }),
  })
  if (!response.ok) return readError(response, '证据包生成失败')
  return (await response.json()).package
}

export async function correctEvidence(
  pathId: string,
  values: { source_version: string; evidence_id: string; note?: string },
): Promise<{ ok: boolean; message: string; changed: boolean }> {
  const response = await request(`/api/lineage-evidence/evidence/${pathId}/correct`, {
    method: 'POST',
    body: JSON.stringify(values),
  })
  if (!response.ok) return readError(response, '证据更正失败')
  return await response.json()
}

export async function supplementEvidence(
  pathId: string,
  values: { source_version: string; evidence_id: string; note?: string },
): Promise<{ ok: boolean; message: string }> {
  const response = await request(`/api/lineage-evidence/evidence/${pathId}/supplement`, {
    method: 'POST',
    body: JSON.stringify(values),
  })
  if (!response.ok) return readError(response, '证据补录失败')
  return await response.json()
}

export async function submitEvidencePackage(
  packagePayload: EvidencePackage,
  receiver: string,
): Promise<AuditDetail> {
  const response = await request('/api/lineage-evidence/audits', {
    method: 'POST',
    body: JSON.stringify({ package: packagePayload, receiver }),
  })
  if (!response.ok) return readError(response, '证据包核对失败')
  return (await response.json()).audit
}

export async function replayAudit(auditId: string): Promise<AuditDetail> {
  const response = await request(`/api/lineage-evidence/audits/${auditId}/replay`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
  if (!response.ok) return readError(response, '原包重演失败')
  return (await response.json()).audit
}

export async function loadAudit(auditId: string): Promise<AuditDetail> {
  const response = await request(`/api/lineage-evidence/audits/${auditId}`)
  if (!response.ok) return readError(response, '签核详情读取失败')
  return await response.json()
}

export function downloadPackageFile(packagePayload: EvidencePackage): void {
  const blob = new Blob([JSON.stringify(packagePayload, null, 2)], {
    type: 'application/json',
  })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${packagePayload.package_id}.json`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function downloadPackageFromServer(packageId: string): Promise<void> {
  const response = await request(`/api/lineage-evidence/packages/${packageId}/download`)
  if (!response.ok) return readError(response, '证据包下载失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${packageId}.json`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

<template>
  <section class="page evidence-page" data-testid="lineage-evidence">
    <header class="page-head">
      <div>
        <h2>字段血缘核对证据包</h2>
        <p class="page-desc">
          按报表列导出证据包，每条路径分别带方向、来源版本和证据标识；接收方下载后上传完成异地核对。
        </p>
      </div>
      <div class="page-actions">
        <select v-model="selectedColumn" data-testid="column-select" class="btn">
          <option v-for="item in columns" :key="item.report_column" :value="item.report_column">
            {{ item.report_column }}（{{ item.path_count }} 条路径）
          </option>
        </select>
        <button class="btn primary" data-testid="export-package" type="button" @click="exportPackage">
          生成证据包
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="card in statCards" :key="card.label" class="stat-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value" :data-testid="`stat-${card.key}`">{{ card.value }}</strong>
      </article>
    </div>

    <p v-if="message" class="notice" data-testid="notice">{{ message }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="error">{{ errorMessage }}</p>

    <section class="panel" data-testid="pending-panel">
      <div class="panel-head">
        <h3>仍待处理的关系</h3>
        <span class="hint">证据包版本、签核结论与补证待办的统一入口</span>
      </div>
      <div class="pending-grid">
        <div>
          <h4>仍需补证（{{ pendingEvidence.length }}）</h4>
          <ul v-if="pendingEvidence.length" data-testid="pending-evidence-list">
            <li v-for="item in pendingEvidence" :key="item.path_id">
              {{ item.path_id }} · {{ item.report_column }} · {{ item.direction_label }} — {{ item.reason }}
            </li>
          </ul>
          <p v-else class="hint">没有缺证据的路径</p>
        </div>
        <div>
          <h4>更正待复核（{{ pendingReview.length }}）</h4>
          <ul v-if="pendingReview.length" data-testid="pending-review-list">
            <li v-for="item in pendingReview" :key="item.path_id">
              {{ item.path_id }}：已采用 {{ item.adopted_evidence_id }}（{{ item.adopted_source_version }}），
              更正为 {{ item.current_evidence_id }}（{{ item.current_source_version }}）
            </li>
          </ul>
          <p v-else class="hint">没有待复核的更正</p>
        </div>
      </div>
    </section>

    <section class="panel" data-testid="package-panel">
      <h3>本列路径与当前证据</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>路径</th><th>方向</th><th>上游</th><th>下游</th>
            <th>当前来源版本</th><th>当前证据标识</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in columnRows" :key="row.path_id" :data-testid="`path-${row.path_id}`">
            <td>{{ row.path_id }}</td>
            <td>{{ row.direction_label }}</td>
            <td>{{ row.upstream_table }}.{{ row.upstream_column }}</td>
            <td>{{ row.downstream_table }}.{{ row.downstream_column }}</td>
            <td>{{ row.current_source_version ?? '—' }}</td>
            <td>{{ row.current_evidence_id ?? '待补证' }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`correct-${row.path_id}`" @click="openCorrect(row)">
                更正/补录证据
              </button>
            </td>
          </tr>
          <tr v-if="!columnRows.length">
            <td colspan="7" class="empty-state">该报表列暂无血缘路径</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h3>证据包版本</h3>
        <span class="hint">同一报表列重复导出会形成新版本，旧包保持冻结</span>
      </div>
      <table class="data-table">
        <thead>
          <tr><th>证据包</th><th>版本</th><th>路径</th><th>签发时间</th><th>摘要</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in columnPackages" :key="item.package_id" :data-testid="`package-${item.package_id}`">
            <td>{{ item.package_id }}</td>
            <td>v{{ item.version }}</td>
            <td>{{ item.path_ids.join('、') }}</td>
            <td>{{ item.issued_at }}</td>
            <td class="mono">{{ item.digest.slice(0, 12) }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`download-${item.package_id}`" @click="downloadPackage(item)">
                下载
              </button>
              <button class="link" type="button" :data-testid="`audit-${item.package_id}`" @click="auditStoredPackage(item)">
                直接核对
              </button>
            </td>
          </tr>
          <tr v-if="!columnPackages.length">
            <td colspan="6" class="empty-state">尚未生成证据包</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel" data-testid="upload-panel">
      <div class="panel-head">
        <h3>接收方上传核对</h3>
        <span class="hint">上传此前下载的证据包 JSON，签核结果回写复核清单</span>
      </div>
      <div class="upload-row">
        <label class="create-field">
          <span>核对人</span>
          <input v-model="receiver" data-testid="receiver" placeholder="异地审计组" />
        </label>
        <input
          ref="fileInput"
          type="file"
          accept="application/json,.json"
          data-testid="upload-input"
          class="file-input"
          @change="onFileSelected"
        />
        <button class="btn" type="button" data-testid="upload-submit" @click="submitUpload">
          上传并核对
        </button>
      </div>
      <p v-if="uploadHint" class="hint" data-testid="upload-hint">{{ uploadHint }}</p>
      <p class="hint">漏传某一条路径会被记为「路径未覆盖」异常，不会因为列名相同就只核对一路。</p>
    </section>

    <section class="panel" data-testid="signoff-panel">
      <div class="panel-head">
        <h3>签核详情</h3>
        <span class="hint">显示本次签核采用的证据版本，以及更正是否另有待办</span>
      </div>
      <div v-if="audit" class="audit-detail">
        <dl class="detail-grid">
          <div><dt>签核单</dt><dd data-testid="audit-id">{{ audit.audit_id }}</dd></div>
          <div><dt>证据包</dt><dd>{{ audit.package_id }}（v{{ audit.package_version }}）</dd></div>
          <div><dt>结论</dt><dd :data-testid="'audit-conclusion'" :data-conclusion="audit.conclusion">{{ audit.conclusion_label }}</dd></div>
          <div><dt>本次证据版本</dt><dd data-testid="audit-evidence-version">{{ adoptedRevisionsText }}</dd></div>
          <div><dt>异常数</dt><dd data-testid="audit-anomaly-count">{{ audit.anomaly_count }}</dd></div>
          <div><dt>提交次数 / 重复提交</dt><dd data-testid="audit-submissions">{{ audit.submission_count }} / {{ audit.duplicate_submissions }}</dd></div>
        </dl>

        <table class="data-table">
          <thead>
            <tr><th>路径</th><th>方向</th><th>本次采用</th><th>当前证据</th><th>核对结论</th></tr>
          </thead>
          <tbody>
            <tr v-for="entry in audit.entries" :key="entry.path_id" :data-testid="`audit-entry-${entry.path_id}`">
              <td>{{ entry.path_id }}</td>
              <td>{{ entry.direction_label }}</td>
              <td>{{ entry.adopted_evidence_id }}（{{ entry.adopted_source_version }}，第{{ entry.adopted_revision }}版）</td>
              <td>{{ entry.current_evidence_id }}（{{ entry.current_source_version }}，第{{ entry.current_revision }}版）</td>
              <td :data-stale="entry.stale">{{ entry.conclusion }}</td>
            </tr>
          </tbody>
        </table>

        <div v-if="audit.correction_todos.length" class="todo-block" data-testid="correction-todos">
          <h4>更正待办（{{ audit.correction_todos.length }}）</h4>
          <ul>
            <li v-for="todo in audit.correction_todos" :key="todo.todo_id">
              {{ todo.path_id }}：{{ todo.detail }}
            </li>
          </ul>
        </div>
        <p v-else class="hint" data-testid="no-correction-todo">本次签核没有额外更正待办</p>

        <div class="panel-actions">
          <button class="btn" type="button" data-testid="refresh-audit" @click="refreshAudit">刷新签核详情</button>
          <button class="btn" type="button" data-testid="replay-audit" @click="replay">用原包重演</button>
        </div>
      </div>
      <p v-else class="empty-state">尚未执行核对，上传证据包或选择证据包直接核对</p>
    </section>

    <section class="panel" data-testid="checklist-panel">
      <div class="panel-head">
        <h3>血缘复核清单</h3>
        <button class="btn ghost" type="button" data-testid="refresh-all" @click="reload">刷新</button>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>路径</th><th>报表列</th><th>方向</th><th>当前证据</th>
            <th>采用证据</th><th>清单状态</th><th>签核单</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in checklist" :key="row.path_id" :data-testid="`checklist-${row.path_id}`" :data-status="row.checklist_status">
            <td>{{ row.path_id }}</td>
            <td>{{ row.report_column }}</td>
            <td>{{ row.direction_label }}</td>
            <td>{{ row.current_evidence_id ?? '待补证' }}</td>
            <td>{{ row.adopted_evidence_id ?? '—' }}{{ row.adopted_source_version ? `（${row.adopted_source_version}）` : '' }}</td>
            <td>{{ row.checklist_status }}</td>
            <td>{{ row.signed_audit_id ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel" data-testid="signoff-history">
      <h3>签核记录</h3>
      <table class="data-table">
        <thead>
          <tr><th>签核单</th><th>证据包</th><th>结论</th><th>异常</th><th>提交/重复</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in auditHistory" :key="item.audit_id" :data-testid="`history-${item.audit_id}`">
            <td>{{ item.audit_id }}</td>
            <td>{{ item.package_id }}（v{{ item.package_version }}）</td>
            <td>{{ item.conclusion_label }}</td>
            <td>{{ item.anomaly_count }}</td>
            <td>{{ item.submission_count }} / {{ item.duplicate_submissions }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`open-${item.audit_id}`" @click="openAudit(item.audit_id)">
                查看
              </button>
            </td>
          </tr>
          <tr v-if="!auditHistory.length">
            <td colspan="6" class="empty-state">暂无签核记录</td>
          </tr>
        </tbody>
      </table>
    </section>

    <div v-if="correcting" class="modal-backdrop" data-testid="correct-modal">
      <form class="modal" @submit.prevent="submitCorrection">
        <h3>{{ correcting.needs_evidence ? '补录' : '更正' }}证据：{{ correcting.path_id }}</h3>
        <p class="hint">{{ correcting.direction_label }} · {{ correcting.upstream_table }} → {{ correcting.downstream_table }}</p>
        <label class="create-field">
          <span>来源版本</span>
          <input v-model="correctValues.source_version" data-testid="correct-version" required />
        </label>
        <label class="create-field">
          <span>证据标识</span>
          <input v-model="correctValues.evidence_id" data-testid="correct-evidence" required />
        </label>
        <label class="create-field">
          <span>说明</span>
          <input v-model="correctValues.note" data-testid="correct-note" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="correcting = null">取消</button>
          <button class="btn primary" type="submit" data-testid="correct-submit">确认提交</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  correctEvidence,
  downloadPackageFile,
  downloadPackageFromServer,
  exportEvidencePackage,
  loadAudit,
  loadEvidenceOverview,
  loadPackage,
  replayAudit as replayAuditRequest,
  submitEvidencePackage,
  supplementEvidence,
} from '@/api/backend-service'
import type {
  AuditDetail,
  AuditSummary,
  ChecklistRow,
  EvidenceColumn,
  EvidenceOverview,
  EvidencePackage,
} from '@/api/backend-service'

const overview = ref<EvidenceOverview | null>(null)
const selectedColumn = ref('net_sales')
const audit = ref<AuditDetail | null>(null)
const receiver = ref('异地审计组')
const message = ref('')
const errorMessage = ref('')
const uploadHint = ref('')
const uploadPackage = ref<EvidencePackage | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const correcting = ref<ChecklistRow | null>(null)
const correctValues = ref({ source_version: '', evidence_id: '', note: '' })

const columns = computed<EvidenceColumn[]>(() => overview.value?.columns ?? [])
const checklist = computed<ChecklistRow[]>(() => overview.value?.checklist ?? [])
const auditHistory = computed<AuditSummary[]>(() => overview.value?.signoffs ?? [])
const pendingEvidence = computed(() => overview.value?.pending_evidence ?? [])
const pendingReview = computed(() => overview.value?.pending_review ?? [])
const columnRows = computed<ChecklistRow[]>(() =>
  checklist.value.filter((row) => row.report_column === selectedColumn.value),
)
const columnPackages = computed(() =>
  (overview.value?.packages ?? []).filter((item) => item.report_column === selectedColumn.value),
)
const adoptedRevisionsText = computed(() => {
  if (!audit.value) return '—'
  const revisions = audit.value.evidence_version.adopted_revisions
  return Object.entries(revisions)
    .map(([pathId, revision]) => `${pathId}=第${revision}版`)
    .join('，')
})
const statCards = computed(() => {
  const stats = overview.value?.stats
  return [
    { key: 'columns', label: '报表列', value: stats?.column_count ?? 0 },
    { key: 'packages', label: '证据包', value: stats?.package_count ?? 0 },
    { key: 'signoffs', label: '签核记录', value: stats?.signoff_count ?? 0 },
    { key: 'pending-evidence', label: '仍需补证', value: stats?.pending_evidence_count ?? 0 },
    { key: 'pending-review', label: '待复核更正', value: stats?.pending_review_count ?? 0 },
  ]
})

function describeError(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

async function reload() {
  errorMessage.value = ''
  try {
    overview.value = await loadEvidenceOverview()
    if (columns.value.length && !columns.value.some((item) => item.report_column === selectedColumn.value)) {
      selectedColumn.value = columns.value[0].report_column
    }
  } catch (error) {
    errorMessage.value = describeError(error, '证据总览读取失败')
  }
}

async function exportPackage() {
  errorMessage.value = ''
  try {
    const packagePayload = await exportEvidencePackage(selectedColumn.value)
    downloadPackageFile(packagePayload)
    message.value = `已生成并下载 ${packagePayload.package_id}，含 ${packagePayload.path_count} 条路径`
    await reload()
    return packagePayload
  } catch (error) {
    errorMessage.value = describeError(error, '证据包生成失败')
    return null
  }
}

async function downloadPackage(item: { package_id: string }) {
  // 列表只有摘要，走服务端下载原件，保证拿到的是签发时的冻结内容。
  errorMessage.value = ''
  try {
    await downloadPackageFromServer(item.package_id)
    message.value = `已下载 ${item.package_id}`
  } catch (error) {
    errorMessage.value = describeError(error, '证据包下载失败')
  }
}

async function auditStoredPackage(item: { package_id: string }) {
  errorMessage.value = ''
  try {
    const full = await loadPackage(item.package_id)
    audit.value = await submitEvidencePackage(full, receiver.value)
    message.value = `核对完成：${audit.value.conclusion_label}`
    await reload()
  } catch (error) {
    errorMessage.value = describeError(error, '证据包核对失败')
  }
}

function onFileSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  uploadPackage.value = null
  uploadHint.value = ''
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    try {
      uploadPackage.value = JSON.parse(String(reader.result)) as EvidencePackage
      uploadHint.value = `已选择 ${uploadPackage.value.package_id ?? '未知证据包'}`
    } catch {
      uploadHint.value = '文件不是合法的证据包 JSON'
    }
  }
  reader.readAsText(file)
}

async function submitUpload() {
  errorMessage.value = ''
  if (!uploadPackage.value) {
    errorMessage.value = '请先选择已下载的证据包文件'
    return
  }
  try {
    audit.value = await submitEvidencePackage(uploadPackage.value, receiver.value)
    message.value = `上传核对完成：${audit.value.conclusion_label}`
    await reload()
  } catch (error) {
    errorMessage.value = describeError(error, '上传核对失败')
  }
}

async function refreshAudit() {
  if (!audit.value) return
  errorMessage.value = ''
  try {
    audit.value = await loadAudit(audit.value.audit_id)
    message.value = '签核详情已刷新'
  } catch (error) {
    errorMessage.value = describeError(error, '签核详情读取失败')
  }
}

async function replay() {
  if (!audit.value) return
  errorMessage.value = ''
  try {
    audit.value = await replayAuditRequest(audit.value.audit_id)
    message.value = `原包重演完成：异常 ${audit.value.anomaly_count} 条，重复提交 ${audit.value.duplicate_submissions} 次`
    await reload()
  } catch (error) {
    errorMessage.value = describeError(error, '原包重演失败')
  }
}

async function openAudit(auditId: string) {
  errorMessage.value = ''
  try {
    audit.value = await loadAudit(auditId)
  } catch (error) {
    errorMessage.value = describeError(error, '签核详情读取失败')
  }
}

function openCorrect(row: ChecklistRow) {
  correcting.value = row
  correctValues.value = {
    source_version: row.current_source_version ?? '',
    evidence_id: row.current_evidence_id ?? '',
    note: '',
  }
}

async function submitCorrection() {
  if (!correcting.value) return
  errorMessage.value = ''
  try {
    const result = correcting.value.needs_evidence
      ? await supplementEvidence(correcting.value.path_id, correctValues.value)
      : await correctEvidence(correcting.value.path_id, correctValues.value)
    message.value = result.message
    correcting.value = null
    await reload()
  } catch (error) {
    errorMessage.value = describeError(error, '证据更正失败')
  }
}

onMounted(reload)
</script>

<style scoped>
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 12px; }
.panel h3 { margin: 0 0 8px; font-size: 14px; }
.panel-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.panel-head h3 { margin: 0; }
.panel-actions { display: flex; gap: 8px; margin-top: 10px; }
.hint { color: var(--muted); font-size: 12px; }
.notice { background: #eef6ff; border: 1px solid #bfdbfe; color: #1e40af; border-radius: 6px; padding: 6px 10px; font-size: 13px; }
.mono { font-family: monospace; font-size: 12px; }
.upload-row { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }
.upload-row .create-field { margin-bottom: 0; min-width: 160px; }
.file-input { font-size: 12px; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; margin: 0 0 10px; }.detail-grid dt { font-size: 12px; color: var(--muted); }
.detail-grid dd { margin: 2px 0 0; font-size: 13px; }
.todo-block { margin-top: 10px; background: #fff7ed; border: 1px solid #fed7aa; border-radius: 6px; padding: 8px 12px; }
.todo-block h4 { margin: 0 0 6px; font-size: 13px; color: #9a3412; }
.todo-block ul { margin: 0; padding-left: 18px; font-size: 13px; }
.pending-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
.pending-grid h4 { margin: 0 0 6px; font-size: 13px; }
.pending-grid ul { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.6; }
</style>

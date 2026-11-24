# 实时流数据湖构建与治理平台

面向实时流数据接入、Delta湖表构建、流批一体转换、分区合并和数据治理的流式数据湖平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8011/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5175/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8011`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 流数据源 | `stream_source` | 流数据源 | 来源标识、接入协议、主题名称 |
| 湖表定义 | `lake_table` | 湖表定义 | 表标识、表名称、主键列 |
| 流式摄入 | `stream_ingest` | 摄入管道 | 管道标识、来源流、目标湖表 |
| 提交版本 | `delta_commit` | 版本提交 | 提交标识、目标湖表、提交类型 |
| 分区合并 | `partition_merge` | 合并任务 | 任务标识、目标湖表、源分区 |
| 流Schema演进 | `stream_schema` | Schema变更 | 变更标识、目标湖表、变更类型 |
| 快照管理 | `snapshot_mgr` | 表快照 | 快照标识、目标湖表、快照时间 |
| 流质量校验 | `stream_quality` | 质量检查 | 检查标识、目标管道、校验字段 |
| 历史清理 | `vacuum_job` | 清理作业 | 作业标识、目标湖表、保留版本 |
| 批流合并 | `batch_merge` | 合并管道 | 管道标识、流来源、批来源 |
| 湖表血缘 | `lake_lineage` | 血缘链路 | 链路标识、上游表、下游表 |
| 保留策略 | `retention_policy` | 保留规则 | 规则标识、目标湖表、保留天数 |
| 压缩任务 | `compaction_task` | 压缩任务 | 任务标识、目标湖表、压缩算法 |
| 数据回填 | `backfill_pipeline` | 回填任务 | 任务标识、目标湖表、回填范围 |
| 流处理指标 | `stream_metric` | 处理指标 | 指标标识、关联管道、指标类型 |
| 格式迁移 | `iceberg_migrate` | 迁移计划 | 计划标识、源格式、目标格式 |
| 表目录同步 | `table_catalog` | 同步记录 | 记录标识、来源Catalog、目标Catalog |
| 湖仓审计 | `lake_audit` | 审计报告 | 报告标识、审计范围、审计时间 |

## 字段血缘核对证据包

湖表血缘的字段级路径有两种核对形态，两条入口互补：

- `lake_lineage` 通用工作台（`/lake_lineage`）：维护链路本身的上游表、下游表和转换算子。
- **血缘核对证据包**专用页（`/lineage-evidence`）：把报表列的每一条血缘路径封装成证据包，
  供异地审计下载、上传核对。后端服务在 `backend/app/services/lineage_evidence.py`，
  接口前缀 `/api/lineage-evidence`。

核对要点：

- 同一报表列的多条路径**分别核对**，`net_sales` 的 `e-81`（上游→下游）和 `e-82`（下游→上游）
  各自带方向、来源版本和证据标识；证据包漏掉任一路会被记为「路径未覆盖」异常，不会因为列名相同就合并。
- 证据包一经签发即冻结。发布方更正证据后只新增证据修订，原包内容和摘要保持不变；
  用原包核对时清单会显示「本次采用哪份证据、当前是哪份证据」，更正的证据单独列为待办。
- 接收方重复提交同一个包不会重复累计异常（异常按「路径 + 类型」去重），
  `submission_count` / `duplicate_submissions` 单独记录提交次数。
- 断线恢复可用原包重演（`POST /api/lineage-evidence/audits/{id}/replay`），
  重演只复算当时的异常，不会把复核清单回退到旧证据。
- 证据包版本可在 `GET /api/lineage-evidence/packages` 查看，后发包通过 `supersedes` 指向上一版。

常用接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/lineage-evidence/overview` | 证据包版本、签核结论、仍需补证关系总览 |
| GET | `/api/lineage-evidence/checklist` | 血缘复核清单（每条路径的签核状态） |
| POST | `/api/lineage-evidence/packages/export` | 按报表列生成证据包 |
| GET | `/api/lineage-evidence/packages/{id}` | 查看证据包全文（供下载/异地核对） |
| POST | `/api/lineage-evidence/evidence/{path_id}/correct` | 发布方更正证据 |
| POST | `/api/lineage-evidence/evidence/{path_id}/supplement` | 为缺证据的路径补录 |
| POST | `/api/lineage-evidence/audits` | 上传证据包完成异地核对、回写清单 |
| GET | `/api/lineage-evidence/audits/{id}` | 签核详情：采用证据、当前证据、更正待办 |
| POST | `/api/lineage-evidence/audits/{id}/replay` | 原包重演，验证异常计数不重复 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数仓与数据湖

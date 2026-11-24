"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import resource, chains
from app.routers import lineage_evidence

# 固定前缀的专用路由必须先于通用 /api/{module}/{entry_id} 注册，
# 否则 overview、checklist 这类固定路径会被动态路径抢先匹配。
ROUTERS = [lineage_evidence, resource, chains]

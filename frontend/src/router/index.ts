import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'
import LineageEvidenceView from '@/views/LineageEvidenceView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 固定路径必须放在动态模块路由之前，否则会被 /:moduleKey 抢先匹配。
    { path: '/lineage-evidence', name: 'lineage-evidence', component: LineageEvidenceView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router

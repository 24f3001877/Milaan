import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'runs-list', component: () => import('../views/RunsList.vue') },
  { path: '/runs/new', name: 'new-run', component: () => import('../views/MappingReview.vue') },
  { path: '/runs/:runId', name: 'run-dashboard', component: () => import('../views/RunDashboard.vue'), props: true },
  {
    path: '/runs/:runId/exceptions',
    name: 'exception-queue',
    component: () => import('../views/ExceptionQueue.vue'),
    props: true,
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

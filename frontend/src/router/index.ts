import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import EmployeesView from '../views/EmployeesView.vue'
import EmployeeDetailView from '../views/EmployeeDetailView.vue'
import EmployeeNewView from '../views/EmployeeNewView.vue'
import SystemConfigView from '../views/SystemConfigView.vue'

// Showcase 视图(B2) — 路由 lazy import 不影响 Dashboard 首屏体积
const ShowcaseLayout = () => import('../views/showcase/ShowcaseLayout.vue')
const ShowcaseHomeView = () => import('../views/showcase/ShowcaseHomeView.vue')
const ShowcaseWorkflowView = () => import('../views/showcase/ShowcaseWorkflowView.vue')
const ShowcaseResourcesView = () => import('../views/showcase/ShowcaseResourcesView.vue')
const ShowcaseInstructionsView = () => import('../views/showcase/ShowcaseInstructionsView.vue')

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/',                  name: 'dashboard',         component: DashboardView },
    { path: '/employees',         name: 'employees',         component: EmployeesView },
    { path: '/employees/new',     name: 'employee-new',      component: EmployeeNewView },
    { path: '/employees/:key',    name: 'employee-detail',   component: EmployeeDetailView },
    { path: '/system-config',     name: 'system-config',     component: SystemConfigView },
    {
      path: '/showcase/:project',
      component: ShowcaseLayout,
      redirect: { name: 'showcase-home' },
      children: [
        { path: '',             name: 'showcase-home',         component: ShowcaseHomeView },
        { path: 'workflow',     name: 'showcase-workflow',     component: ShowcaseWorkflowView },
        { path: 'resources',    name: 'showcase-resources',    component: ShowcaseResourcesView },
        { path: 'instructions', name: 'showcase-instructions', component: ShowcaseInstructionsView },
      ],
    },
  ],
})

export default router

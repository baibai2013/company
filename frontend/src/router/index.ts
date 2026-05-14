import { createRouter, createWebHistory } from 'vue-router'
import DashboardView from '../views/DashboardView.vue'
import EmployeesView from '../views/EmployeesView.vue'
import EmployeeDetailView from '../views/EmployeeDetailView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/',                  name: 'dashboard',         component: DashboardView },
    { path: '/employees',         name: 'employees',         component: EmployeesView },
    { path: '/employees/:key',    name: 'employee-detail',   component: EmployeeDetailView },
  ],
})

export default router

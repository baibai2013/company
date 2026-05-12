import { defineStore } from 'pinia'
import { ref } from 'vue'
import { employeesApi, type Employee } from '@/api/client'

export const useEmployeeStore = defineStore('employees', () => {
  const employees = ref<Employee[]>([])

  async function fetchEmployees() {
    employees.value = await employeesApi.list()
  }

  return { employees, fetchEmployees }
})

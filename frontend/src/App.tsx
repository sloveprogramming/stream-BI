import { Navigate, Route, Routes } from 'react-router-dom'
import MainLayout from './layouts/MainLayout'
import Login from './pages/Login'
import Datasets from './pages/Datasets'
import DatasetQuery from './pages/DatasetQuery'
import MyKeys from './pages/MyKeys'
import MyReports from './pages/MyReports'
import DataAssistant from './pages/DataAssistant'
import FlowList from './pages/FlowList'
import FlowDesigner from './pages/FlowDesigner'
import AdminUsers from './pages/admin/Users'
import AdminDatasets from './pages/admin/DatasetsAdmin'
import AdminSync from './pages/admin/SyncTasks'
import AdminAudit from './pages/admin/AuditLogs'
import AdminProviders from './pages/admin/ModelProviders'
import AdminGrantRequests from './pages/admin/GrantRequests'
import AdminQuality from './pages/admin/QualityMonitor'
import AdminMetricLineage from './pages/admin/MetricLineage'
import AdminTenants from './pages/admin/Tenants'
import OpsDashboard from './pages/dashboards/OpsDashboard'
import SalesDashboard from './pages/dashboards/SalesDashboard'

function RequireAuth({ children, admin = false }: { children: React.ReactNode; admin?: boolean }) {
  const token = localStorage.getItem('token')
  const user = JSON.parse(localStorage.getItem('user') || 'null')
  if (!token || !user) return <Navigate to="/login" replace />
  if (admin && !user.is_admin) return <Navigate to="/" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <MainLayout />
          </RequireAuth>
        }
      >
        <Route index element={<Datasets />} />
        <Route path="dashboards/ops" element={<OpsDashboard />} />
        <Route path="dashboards/sales" element={<SalesDashboard />} />
        <Route path="datasets/:name" element={<DatasetQuery />} />
        <Route path="my-keys" element={<MyKeys />} />
        <Route path="my-reports" element={<MyReports />} />
        <Route path="data-assistant" element={<DataAssistant />} />
        <Route path="flows" element={<FlowList />} />
        <Route path="flows/:id" element={<FlowDesigner />} />
        <Route path="admin/users" element={<RequireAuth admin><AdminUsers /></RequireAuth>} />
        <Route path="admin/datasets" element={<RequireAuth admin><AdminDatasets /></RequireAuth>} />
        <Route path="admin/sync" element={<RequireAuth admin><AdminSync /></RequireAuth>} />
        <Route path="admin/audit" element={<RequireAuth admin><AdminAudit /></RequireAuth>} />
        <Route path="admin/providers" element={<RequireAuth admin><AdminProviders /></RequireAuth>} />
        <Route path="admin/grant-requests" element={<RequireAuth admin><AdminGrantRequests /></RequireAuth>} />
        <Route path="admin/quality" element={<RequireAuth admin><AdminQuality /></RequireAuth>} />
        <Route path="admin/metric-lineage" element={<RequireAuth admin><AdminMetricLineage /></RequireAuth>} />
        <Route path="admin/tenants" element={<RequireAuth admin><AdminTenants /></RequireAuth>} />
      </Route>
    </Routes>
  )
}

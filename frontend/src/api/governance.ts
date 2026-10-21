import http from './http'

// ---------------------------------------------------------------- 治理补全
// 租户 / 权限申请与审批 / 数据质量巡检 / 指标血缘

export interface GrantRequest {
  id: number
  status: 'pending' | 'approved' | 'rejected' | 'cancelled'
  reason: string
  decision_note?: string | null
  dataset_id: number
  applicant?: string | null
  applicant_id?: number
  decided_by?: string | null
  requested_row_rules: { field: string; op: string; value: unknown }[]
  requested_field_whitelist?: string[] | null
  requested_days?: number | null
  granted_id?: number | null
  created_at?: string | null
  decided_at?: string | null
}

export interface QualityRule {
  id: number
  name: string
  dataset_id: number
  dataset_name?: string | null
  enabled: boolean
  rule_type: 'freshness' | 'row_count' | 'null_rate'
  threshold: number
  target_field?: string | null
  timestamp_field?: string | null
  direction: string
  cron: string
  owner_name?: string | null
  last_run_at?: string | null
  last_status: string
}

export interface QualityResult {
  id: number
  rule_id: number
  rule_name?: string | null
  rule_type?: string | null
  dataset_name?: string | null
  status: string
  value?: number | null
  baseline?: number | null
  change_pct?: number | null
  message: string
  error?: string | null
  elapsed_ms: number
  checked_at?: string | null
}

export interface MetricLineage {
  metric: {
    name: string
    label: string
    owner: string
    dataset: string
    expr: string
  }
  used_by: {
    flow_id: number
    flow_name: string
    flow_title?: string | null
    target_table: string
    output_dataset?: string | null
    enabled: boolean
    downstream: { name: string; via?: string | null }[][]
  }[]
  used_by_count: number
}

export interface Tenant {
  id: number
  name: string
  slug: string
  enabled: boolean
  created_at?: string | null
}

// ---- 权限申请 ----

export const listGrantRequests = (mine = false, status = '') =>
  http
    .get<GrantRequest[]>('/grant-requests', { params: { mine, status } })
    .then((r) => r.data)

export const createGrantRequest = (body: {
  dataset_id: number
  reason: string
  row_rules?: unknown[]
  field_whitelist?: string[] | null
  days?: number | null
}) => http.post('/grant-requests', body).then((r) => r.data)

export const approveGrantRequest = (
  id: number,
  body: { note?: string; row_rules?: unknown[]; field_whitelist?: string[] | null; days?: number | null },
) => http.post(`/grant-requests/${id}/approve`, body).then((r) => r.data)

export const rejectGrantRequest = (id: number, note: string) =>
  http.post(`/grant-requests/${id}/reject`, { note }).then((r) => r.data)

export const cancelGrantRequest = (id: number) =>
  http.post(`/grant-requests/${id}/cancel`).then((r) => r.data)

// ---- 数据质量 ----

export const listQualityRules = () =>
  http.get<QualityRule[]>('/quality/rules').then((r) => r.data)

export const createQualityRule = (body: Record<string, unknown>) =>
  http.post('/quality/rules', body).then((r) => r.data)

export const updateQualityRule = (id: number, body: Record<string, unknown>) =>
  http.patch(`/quality/rules/${id}`, body).then((r) => r.data)

export const deleteQualityRule = (id: number) =>
  http.delete(`/quality/rules/${id}`).then((r) => r.data)

export const listQualityResults = (params: {
  rule_id?: number
  status?: string
  limit?: number
}) => http.get<QualityResult[]>('/quality/results', { params }).then((r) => r.data)

export const runQualityRule = (id: number) =>
  http.post<QualityResult>(`/quality/rules/${id}/run`).then((r) => r.data)

export const qualitySummary = () =>
  http
    .get<{
      total: number
      anomaly: number
      failed: number
      rules: {
        rule_id: number
        name: string
        rule_type: string
        enabled: boolean
        last_status: string
        last_run_at?: string | null
        latest_message: string
      }[]
    }>('/quality/summary')
    .then((r) => r.data)

// ---- 指标血缘 ----

export const metricLineage = (name: string) =>
  http.get<MetricLineage>(`/metrics/${encodeURIComponent(name)}/lineage`).then((r) => r.data)

// ---- 租户 ----

export const listTenants = () => http.get<Tenant[]>('/tenants').then((r) => r.data)

export const createTenant = (body: { name: string; slug: string }) =>
  http.post('/tenants', body).then((r) => r.data)

export const updateTenant = (id: number, body: { name?: string; enabled?: boolean }) =>
  http.patch(`/tenants/${id}`, body).then((r) => r.data)

import http from './http'

export type JsonSchema = {
  type?: string | string[]
  title?: string
  description?: string
  enum?: unknown[]
  default?: unknown
  minimum?: number
  maximum?: number
  minItems?: number
  items?: JsonSchema
  additionalProperties?: JsonSchema | boolean
  anyOf?: JsonSchema[]
  properties?: Record<string, JsonSchema>
  required?: string[]
  prefixItems?: JsonSchema[]
}

export type OperatorSpec = {
  type: string
  category: string
  title: string
  description: string
  engine: 'sql' | 'python'
  isSource: boolean
  isSink: boolean
  paramsSchema: JsonSchema
}

export type OperatorCatalog = { categories: string[]; operators: OperatorSpec[] }

export type FlowNodeSpec = {
  id: string
  type: string
  params: Record<string, unknown>
  position: { x: number; y: number }
}

export type FlowEdgeSpec = { source: string; target: string }

export type FlowOutput = {
  materialization: 'table' | 'view'
  target_schema: 'dwd' | 'dws' | 'ads'
  target_name: string
  pk_columns: string[]
  register_dataset: boolean
  share_with: number[]
}

export type FlowDSL = {
  name: string
  title: string
  description: string
  nodes: FlowNodeSpec[]
  edges: FlowEdgeSpec[]
  output: FlowOutput
}

export type FlowSummary = {
  id: number
  name: string
  title: string | null
  description: string | null
  current_version: number
  target: string
  output_dataset_id: number | null
  enabled: boolean
  updated_at: string | null
  version_count: number | null
}

export type FlowDetail = FlowSummary & {
  current: { version: number; dsl: FlowDSL; compiled_sql: string | null }
}

export type FlowRunRecord = {
  run_id: string
  version: number
  status: 'running' | 'ok' | 'failed' | 'aborted'
  triggered_by: string
  rows_out: number
  elapsed_ms: number
  error: string | null
  grant_snapshot: Record<string, unknown[]> | null
  meta: Record<string, unknown> | null
  started_at: string | null
  finished_at: string | null
}

export type PreviewResult = {
  columns: string[]
  rows: unknown[][]
  limit: number
  sql: string
}

export type DatasetOption = { id: number; name: string; title: string | null; columns: string[] }

export const flowApi = {
  operators: () => http.get<OperatorCatalog>('/flows/operators').then((r) => r.data),
  list: () => http.get<FlowSummary[]>('/flows').then((r) => r.data),
  create: (dsl: FlowDSL) => http.post<FlowSummary>('/flows', { dsl }).then((r) => r.data),
  get: (id: number) => http.get<FlowDetail>(`/flows/${id}`).then((r) => r.data),
  save: (id: number, dsl: FlowDSL) => http.put(`/flows/${id}`, { dsl }).then((r) => r.data),
  validate: (id: number, dsl: FlowDSL) =>
    http.post(`/flows/${id}/validate`, { dsl }).then((r) => r.data),
  compile: (id: number, dsl: FlowDSL) =>
    http
      .post<{ model_sql: string; schema_yml: string; target: string }>(`/flows/${id}/compile`, {
        dsl,
      })
      .then((r) => r.data),
  preview: (id: number, dsl: FlowDSL, nodeId: string, limit = 20) =>
    http
      .post<PreviewResult>(`/flows/${id}/preview`, { dsl, node_id: nodeId, limit })
      .then((r) => r.data),
  run: (id: number) => http.post<FlowRunRecord>(`/flows/${id}/run`).then((r) => r.data),
  runs: (id: number, limit = 20) =>
    http.get<FlowRunRecord[]>(`/flows/${id}/runs`, { params: { limit } }).then((r) => r.data),
  versions: (id: number) =>
    http
      .get<{ version: number; version_id: string; created_at: string | null }[]>(
        `/flows/${id}/versions`,
      )
      .then((r) => r.data),
  rollback: (id: number, version: number) =>
    http.post(`/flows/${id}/rollback/${version}`).then((r) => r.data),
  setEnabled: (id: number, enabled: boolean) =>
    http.post(`/flows/${id}/enable`, null, { params: { enabled } }).then((r) => r.data),
  datasets: () => http.get<DatasetOption[]>('/datasets').then((r) => r.data),
}

/** 把后端返回的错误变成人能读的一句话。 */
export function errorText(err: unknown): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((d) => JSON.stringify(d)).join('; ')
  if (detail) return JSON.stringify(detail)
  return (err as Error)?.message ?? '未知错误'
}

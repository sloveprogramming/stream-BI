import { create } from 'zustand'
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from '@xyflow/react'
import type { FlowDSL, FlowOutput, FlowNodeSpec, JsonSchema, OperatorSpec } from '../api/flows'

export type OpNodeData = {
  opType: string
  title: string
  engine: 'sql' | 'python'
  isSource: boolean
  isSink: boolean
  params: Record<string, unknown>
  schema: JsonSchema
}

export type OpNode = Node<OpNodeData, 'operator'>

export type ConnectCheck = { ok: boolean; reason?: string }

/** 连线的合法性规则 —— 在前端就挡住，别等运行时才炸。 */
export function checkConnection(nodes: OpNode[], edges: Edge[], conn: Connection): ConnectCheck {
  if (!conn.source || !conn.target) return { ok: false, reason: '连接不完整' }
  if (conn.source === conn.target) return { ok: false, reason: '节点不能连接自己' }

  const src = nodes.find((n) => n.id === conn.source)
  const tgt = nodes.find((n) => n.id === conn.target)
  if (!src || !tgt) return { ok: false, reason: '节点不存在' }
  if (src.data.isSink) return { ok: false, reason: '输出节点不能有下游' }
  if (tgt.data.isSource) return { ok: false, reason: '输入节点不能有上游' }
  if (edges.some((e) => e.source === conn.source && e.target === conn.target)) {
    return { ok: false, reason: '这条连线已经存在' }
  }
  if (reaches(edges, conn.target, conn.source)) return { ok: false, reason: '会形成环' }
  return { ok: true }
}

/** from 顺着边能不能走到 to（用于成环检测）。 */
function reaches(edges: Edge[], from: string, to: string): boolean {
  const stack = [from]
  const seen = new Set<string>()
  while (stack.length) {
    const cur = stack.pop() as string
    if (cur === to) return true
    if (seen.has(cur)) continue
    seen.add(cur)
    edges.filter((e) => e.source === cur).forEach((e) => stack.push(e.target))
  }
  return false
}

/** 节点上那行小字：让人一眼看出这个节点在干什么。 */
export function summarize(
  opType: string,
  params: Record<string, unknown>,
  datasetNames: Record<number, string>,
): string {
  const p = params ?? {}
  if (opType === 'source.dataset') {
    const id = Number(p.dataset_id)
    return datasetNames[id] ?? (Number.isFinite(id) && id ? `数据集 #${id}` : '未选择数据集')
  }
  const parts: string[] = []
  for (const [key, value] of Object.entries(p)) {
    if (value === undefined || value === null || value === '') continue
    if (typeof value === 'object') {
      const size = Array.isArray(value) ? value.length : Object.keys(value).length
      if (size) parts.push(`${key}[${size}]`)
    } else {
      parts.push(`${key}=${String(value)}`)
    }
    if (parts.length === 2) break
  }
  return parts.join(' · ') || '未配置'
}

type FlowState = {
  flowId: number | null
  meta: { name: string; title: string; description: string }
  output: FlowOutput
  nodes: OpNode[]
  edges: Edge[]
  selectedId: string | null
  dirty: boolean
  operators: Record<string, OperatorSpec>
  datasetNames: Record<number, string>

  setOperators: (list: OperatorSpec[]) => void
  setDatasetNames: (names: Record<number, string>) => void
  setMeta: (patch: Partial<FlowState['meta']>) => void
  setOutput: (patch: Partial<FlowOutput>) => void
  load: (flowId: number, dsl: FlowDSL) => void

  onNodesChange: (changes: NodeChange<OpNode>[]) => void
  onEdgesChange: (changes: EdgeChange[]) => void
  onConnect: (conn: Connection) => void
  addOperator: (op: OperatorSpec, position: { x: number; y: number }) => ConnectCheck
  updateParams: (id: string, params: Record<string, unknown>) => void
  removeNode: (id: string) => void
  select: (id: string | null) => void
  toDsl: () => FlowDSL
}

const DEFAULT_OUTPUT: FlowOutput = {
  materialization: 'table',
  target_schema: 'dwd',
  target_name: '',
  pk_columns: [],
  register_dataset: true,
  share_with: [],
}

let seq = 0
function nextNodeId(existing: OpNode[]): string {
  const used = new Set(existing.map((n) => n.id))
  do {
    seq += 1
  } while (used.has(`n${seq}`))
  return `n${seq}`
}

export const useFlowStore = create<FlowState>((set, get) => ({
  flowId: null,
  meta: { name: '', title: '', description: '' },
  output: DEFAULT_OUTPUT,
  nodes: [],
  edges: [],
  selectedId: null,
  dirty: false,
  operators: {},
  datasetNames: {},

  setOperators: (list) =>
    set({ operators: Object.fromEntries(list.map((op) => [op.type, op])) }),
  setDatasetNames: (names) => set({ datasetNames: names }),
  setMeta: (patch) => set((s) => ({ meta: { ...s.meta, ...patch }, dirty: true })),
  setOutput: (patch) => set((s) => ({ output: { ...s.output, ...patch }, dirty: true })),

  load: (flowId, dsl) =>
    set((s) => ({
      flowId,
      meta: { name: dsl.name, title: dsl.title, description: dsl.description },
      output: dsl.output,
      nodes: dsl.nodes.map((n) => toNode(n, s.operators)),
      edges: dsl.edges.map((e, i) => ({
        id: `e${i}-${e.source}-${e.target}`,
        source: e.source,
        target: e.target,
      })),
      selectedId: null,
      dirty: false,
    })),

  onNodesChange: (changes) =>
    set((s) => ({ nodes: applyNodeChanges(changes, s.nodes), dirty: true })),
  onEdgesChange: (changes) =>
    set((s) => ({ edges: applyEdgeChanges(changes, s.edges), dirty: true })),

  onConnect: (conn) =>
    set((s) => {
      const check = checkConnection(s.nodes, s.edges, conn)
      if (!check.ok) return s
      return {
        edges: addEdge({ ...conn, id: `e-${conn.source}-${conn.target}` }, s.edges),
        dirty: true,
      }
    }),

  addOperator: (op, position) => {
    const s = get()
    if (op.isSink && s.nodes.some((n) => n.data.isSink)) {
      return { ok: false, reason: '一条流程只能有一个输出节点' }
    }
    const id = nextNodeId(s.nodes)
    const params = defaultParams(op.paramsSchema)
    const node: OpNode = {
      id,
      type: 'operator',
      position,
      data: {
        opType: op.type,
        title: op.title,
        engine: op.engine,
        isSource: op.isSource,
        isSink: op.isSink,
        params,
        schema: op.paramsSchema,
      },
    }
    set({ nodes: [...s.nodes, node], selectedId: id, dirty: true })
    return { ok: true }
  },

  updateParams: (id, params) =>
    set((s) => ({
      nodes: s.nodes.map((n) => (n.id === id ? { ...n, data: { ...n.data, params } } : n)),
      dirty: true,
    })),

  removeNode: (id) =>
    set((s) => ({
      nodes: s.nodes.filter((n) => n.id !== id),
      edges: s.edges.filter((e) => e.source !== id && e.target !== id),
      selectedId: s.selectedId === id ? null : s.selectedId,
      dirty: true,
    })),

  select: (id) => set({ selectedId: id }),

  toDsl: () => {
    const s = get()
    return {
      name: s.meta.name,
      title: s.meta.title,
      description: s.meta.description,
      nodes: s.nodes.map((n) => ({
        id: n.id,
        type: n.data.opType,
        params: n.data.params,
        position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
      })),
      edges: s.edges.map((e) => ({ source: e.source, target: e.target })),
      output: s.output,
    }
  },
}))

/** 用 schema 里的 default 预填参数，省得用户从零填。 */
export function defaultParams(schema: JsonSchema): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [key, prop] of Object.entries(schema.properties ?? {})) {
    if (prop.default !== undefined && prop.default !== null) out[key] = prop.default
  }
  return out
}

function toNode(spec: FlowNodeSpec, operators: Record<string, OperatorSpec>): OpNode {
  const op = operators[spec.type]
  return {
    id: spec.id,
    type: 'operator',
    position: spec.position ?? { x: 0, y: 0 },
    data: {
      opType: spec.type,
      title: op?.title ?? spec.type,
      engine: op?.engine ?? 'sql',
      isSource: spec.type.startsWith('source.'),
      isSink: spec.type.startsWith('sink.'),
      params: spec.params ?? {},
      schema: op?.paramsSchema ?? { type: 'object', properties: {} },
    },
  }
}

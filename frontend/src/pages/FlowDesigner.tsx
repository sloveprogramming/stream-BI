import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert,
  Button,
  Empty,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  CodeOutlined,
  HistoryOutlined,
  PlayCircleOutlined,
  SaveOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Connection,
  type NodeTypes,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import '../components/flow/flow.css'

import {
  errorText,
  flowApi,
  type DatasetOption,
  type FlowRunRecord,
  type OperatorSpec,
  type PreviewResult,
} from '../api/flows'
import { checkConnection, useFlowStore } from '../store/flowStore'
import OperatorPanel from '../components/flow/OperatorPanel'
import OperatorNode from '../components/flow/OperatorNode'
import ConfigDrawer from '../components/flow/ConfigDrawer'

const nodeTypes: NodeTypes = { operator: OperatorNode }

const STATUS_COLOR: Record<string, string> = {
  ok: 'green',
  failed: 'red',
  aborted: 'orange',
  running: 'blue',
}

function DesignerInner() {
  const { id } = useParams()
  const flowId = Number(id)
  const nav = useNavigate()
  const { screenToFlowPosition } = useReactFlow()

  const [operators, setOperators] = useState<OperatorSpec[]>([])
  const [datasets, setDatasets] = useState<DatasetOption[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [sql, setSql] = useState<{ sql: string; yml: string; target: string } | null>(null)
  const [runs, setRuns] = useState<FlowRunRecord[] | null>(null)
  const [preview, setPreview] = useState<PreviewResult | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [outputOpen, setOutputOpen] = useState(false)
  const loaded = useRef(false)

  const store = useFlowStore()
  const { nodes, edges, meta, output, dirty } = store

  // ---- 初始加载 ----
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [catalog, detail, dsList] = await Promise.all([
          flowApi.operators(),
          flowApi.get(flowId),
          flowApi.datasets(),
        ])
        if (cancelled) return
        const names: Record<number, string> = {}
        dsList.forEach((d) => {
          names[d.id] = d.name
        })
        useFlowStore.getState().setOperators(catalog.operators)
        useFlowStore.getState().setDatasetNames(names)
        useFlowStore.getState().load(flowId, detail.current.dsl)
        setOperators(catalog.operators)
        setDatasets(dsList)
        loaded.current = true
      } catch (err) {
        message.error(errorText(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [flowId])

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault()
      const opType = event.dataTransfer.getData('application/bisql-operator')
      const op = operators.find((o) => o.type === opType)
      if (!op) return
      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY })
      const result = useFlowStore.getState().addOperator(op, position)
      if (!result.ok) message.warning(result.reason)
    },
    [operators, screenToFlowPosition],
  )

  const onConnect = useCallback((conn: Connection) => {
    // 不在 isValidConnection 里拒绝：那样用户看不到原因。这里统一拦并给出提示。
    const state = useFlowStore.getState()
    const check = checkConnection(state.nodes, state.edges, conn)
    if (!check.ok) {
      message.warning(check.reason)
      return
    }
    state.onConnect(conn)
  }, [])

  // ---- 工具栏动作 ----
  const requireDirtyDsl = () => {
    if (!meta.name) {
      message.error('流程标识（name）不能为空')
      return null
    }
    if (!output.target_name) {
      message.error('请先在「输出设置」里填目标表名')
      return null
    }
    return store.toDsl()
  }

  const doValidate = async () => {
    const dsl = requireDirtyDsl()
    if (!dsl) return
    setBusy('validate')
    try {
      const res = await flowApi.validate(flowId, dsl)
      message.success(`校验通过：用到 ${res.operators.length} 个算子`)
    } catch (err) {
      Modal.error({ title: '校验未通过', content: errorText(err) })
    } finally {
      setBusy('')
    }
  }

  const doCompile = async () => {
    const dsl = requireDirtyDsl()
    if (!dsl) return
    setBusy('compile')
    try {
      const res = await flowApi.compile(flowId, dsl)
      setSql({ sql: res.model_sql, yml: res.schema_yml, target: res.target })
    } catch (err) {
      Modal.error({ title: '编译失败', content: errorText(err) })
    } finally {
      setBusy('')
    }
  }

  const doSave = async () => {
    const dsl = requireDirtyDsl()
    if (!dsl) return
    setBusy('save')
    try {
      const res = await flowApi.save(flowId, dsl)
      useFlowStore.setState({ dirty: false })
      message.success(`已保存为 v${res.version.version}`)
    } catch (err) {
      Modal.error({ title: '保存失败', content: errorText(err) })
    } finally {
      setBusy('')
    }
  }

  const doRun = async () => {
    if (dirty) {
      message.warning('有未保存的改动，请先保存（运行的是已保存的版本）')
      return
    }
    setBusy('run')
    try {
      const res = await flowApi.run(flowId)
      if (res.status === 'ok') {
        message.success(`运行成功：${res.rows_out} 行，耗时 ${res.elapsed_ms} ms`)
      } else {
        Modal.warning({
          title: res.status === 'aborted' ? '运行被拒绝' : '运行失败',
          content: res.error || '未知原因',
        })
      }
      setRuns(await flowApi.runs(flowId))
    } catch (err) {
      Modal.error({ title: '运行失败', content: errorText(err) })
    } finally {
      setBusy('')
    }
  }

  const doPreview = async (nodeId: string) => {
    const dsl = requireDirtyDsl()
    if (!dsl) return
    setPreviewing(true)
    try {
      setPreview(await flowApi.preview(flowId, dsl, nodeId))
    } catch (err) {
      Modal.error({ title: '预览失败', content: errorText(err) })
    } finally {
      setPreviewing(false)
    }
  }

  const openRuns = async () => {
    try {
      setRuns(await flowApi.runs(flowId))
    } catch (err) {
      message.error(errorText(err))
    }
  }

  const summary = useMemo(
    () => ({
      nodes: nodes.length,
      aiNodes: nodes.filter((n) => n.data.engine === 'python').length,
      missing: nodes.filter((n) => {
        const required = n.data.schema.required ?? []
        return required.some((k) => n.data.params[k] === undefined || n.data.params[k] === '')
      }).length,
    }),
    [nodes],
  )

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin />
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 112px)' }}>
      <div className="flow-toolbar">
        <Button icon={<ArrowLeftOutlined />} onClick={() => nav('/flows')}>
          返回
        </Button>
        <Input
          style={{ width: 220 }}
          addonBefore="名称"
          value={meta.name}
          onChange={(e) => store.setMeta({ name: e.target.value })}
        />
        <Input
          style={{ width: 240 }}
          addonBefore="标题"
          value={meta.title}
          onChange={(e) => store.setMeta({ title: e.target.value })}
        />
        <Tag>{output.target_schema}.{output.target_name || '未设置'}</Tag>
        {dirty ? <Tag color="orange">有未保存改动</Tag> : <Tag color="green">已同步</Tag>}
        <Space style={{ marginLeft: 'auto' }}>
          <Button icon={<SettingOutlined />} onClick={() => setOutputOpen(true)}>
            输出设置
          </Button>
          <Button icon={<CheckCircleOutlined />} loading={busy === 'validate'} onClick={doValidate}>
            校验
          </Button>
          <Button icon={<CodeOutlined />} loading={busy === 'compile'} onClick={doCompile}>
            查看 SQL
          </Button>
          <Button icon={<HistoryOutlined />} onClick={openRuns}>
            运行历史
          </Button>
          <Button icon={<SaveOutlined />} loading={busy === 'save'} onClick={doSave}>
            保存
          </Button>
          <Button type="primary" icon={<PlayCircleOutlined />} loading={busy === 'run'} onClick={doRun}>
            运行
          </Button>
        </Space>
      </div>

      {summary.missing > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 8 }}
          message={`有 ${summary.missing} 个节点还没填完必填参数（红色边框），点节点补齐后再运行`}
        />
      )}

      <div style={{ display: 'flex', flex: 1, minHeight: 0, gap: 12 }}>
        <OperatorPanel operators={operators} />

        <div className="flow-canvas">
          {nodes.length === 0 ? (
            <div style={{ padding: 60, textAlign: 'center' }}>
              <Empty description="把左侧算子拖进来开始编排（建议先放一个「读取数据集」）" />
            </div>
          ) : (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={store.onNodesChange}
              onEdgesChange={store.onEdgesChange}
              onConnect={onConnect}
              onNodeClick={(_, node) => store.select(node.id)}
              onPaneClick={() => store.select(null)}
              onDrop={onDrop}
              onDragOver={onDragOver}
              fitView
              proOptions={{ hideAttribution: true }}
            >
              <Background />
              <Controls />
              <MiniMap pannable zoomable />
            </ReactFlow>
          )}
        </div>
      </div>

      <ConfigDrawer datasets={datasets} onPreview={doPreview} previewing={previewing} />

      {/* 输出设置 */}
      <Modal
        title="输出设置"
        open={outputOpen}
        onCancel={() => setOutputOpen(false)}
        onOk={() => setOutputOpen(false)}
        okText="关闭"
        cancelButtonProps={{ style: { display: 'none' } }}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <div>落哪一层</div>
            <Select
              style={{ width: '100%' }}
              value={output.target_schema}
              options={['dwd', 'dws', 'ads'].map((v) => ({ value: v, label: v }))}
              onChange={(v) => store.setOutput({ target_schema: v })}
            />
          </div>
          <div>
            <div>目标表名（只能字母/数字/下划线）</div>
            <Input
              value={output.target_name}
              placeholder="sales_order_clean"
              onChange={(e) => store.setOutput({ target_name: e.target.value })}
            />
          </div>
          <div>
            <div>主键 / 粒度（建议填，否则数据集分页没有稳定排序）</div>
            <Select
              mode="tags"
              style={{ width: '100%' }}
              value={output.pk_columns}
              placeholder="如 order_id"
              onChange={(v) => store.setOutput({ pk_columns: v })}
            />
          </div>
          <div>
            <Space>
              <Switch
                checked={output.register_dataset}
                onChange={(v) => store.setOutput({ register_dataset: v })}
              />
              <span>跑完自动注册为数据集（自动获得权限与四类出口）</span>
            </Space>
          </div>
          <div>
            <div>额外授权给哪些用户 id（留空 = 仅自己可见）</div>
            <Select
              mode="tags"
              style={{ width: '100%' }}
              value={output.share_with.map(String)}
              placeholder="回车添加用户 id"
              onChange={(v) => store.setOutput({ share_with: v.map(Number).filter((n) => !Number.isNaN(n)) })}
            />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              产物会继承来源的行级规则；若规则字段在产物里不存在，系统会强制只对自己可见。
            </Typography.Text>
          </div>
        </Space>
      </Modal>

      {/* 编译结果 */}
      <Modal
        title={`生成的 dbt model（${sql?.target ?? ''}）`}
        open={!!sql}
        onCancel={() => setSql(null)}
        footer={null}
        width={900}
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          这份 SQL 会写到 backend/dbt/models/generated/ 下，由 dbt 执行（含 tests）。
          源数据集的行级权限已内联在第一个 CTE 里。
        </Typography.Paragraph>
        <Input.TextArea readOnly value={sql?.sql ?? ''} autoSize={{ minRows: 12, maxRows: 22 }} />
        <div style={{ marginTop: 12, fontWeight: 600 }}>schema.yml</div>
        <Input.TextArea readOnly value={sql?.yml ?? ''} autoSize={{ minRows: 4, maxRows: 10 }} />
      </Modal>

      {/* 运行历史 */}
      <Modal
        title="运行历史"
        open={runs !== null}
        onCancel={() => setRuns(null)}
        footer={null}
        width={880}
      >
        <Table<FlowRunRecord>
          size="small"
          rowKey="run_id"
          dataSource={runs ?? []}
          pagination={false}
          locale={{ emptyText: '还没有运行记录' }}
          columns={[
            {
              title: '状态',
              dataIndex: 'status',
              width: 90,
              render: (v: string) => <Tag color={STATUS_COLOR[v] ?? 'default'}>{v}</Tag>,
            },
            { title: '版本', dataIndex: 'version', width: 70 },
            { title: '行数', dataIndex: 'rows_out', width: 90 },
            {
              title: '耗时',
              dataIndex: 'elapsed_ms',
              width: 90,
              render: (v: number) => `${v} ms`,
            },
            { title: '触发', dataIndex: 'triggered_by', width: 80 },
            {
              title: '开始时间',
              dataIndex: 'started_at',
              width: 180,
              render: (v: string | null) => (v ? new Date(v).toLocaleString() : '-'),
            },
            {
              title: '信息',
              dataIndex: 'error',
              render: (v: string | null) => (
                <Tooltip title={v}>
                  <span
                    style={{
                      display: 'inline-block',
                      maxWidth: 240,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      color: v ? '#cf1322' : '#8c8c8c',
                    }}
                  >
                    {v || '—'}
                  </span>
                </Tooltip>
              ),
            },
          ]}
        />
      </Modal>

      {/* 单步预览 */}
      <Modal
        title={<Space>预览结果（前 {preview?.limit ?? 20} 行）</Space>}
        open={!!preview}
        onCancel={() => setPreview(null)}
        footer={null}
        width={900}
      >
        {preview && (
          <>
            <Table
              size="small"
              rowKey={(_, i) => String(i)}
              dataSource={preview.rows.map((r, i) => {
                const row: Record<string, unknown> = { __k: i }
                preview.columns.forEach((c, ci) => {
                  row[c] = r[ci]
                })
                return row
              })}
              columns={preview.columns.map((c) => ({ title: c, dataIndex: c, ellipsis: true }))}
              scroll={{ x: 'max-content' }}
              pagination={false}
              locale={{ emptyText: '这一步没有数据（可能是上游过滤太严，或确实为空）' }}
            />
            <details style={{ marginTop: 12 }}>
              <summary style={{ cursor: 'pointer', color: '#8c8c8c' }}>查看这一步的 SQL</summary>
              <Input.TextArea readOnly value={preview.sql} autoSize={{ minRows: 6, maxRows: 16 }} />
            </details>
          </>
        )}
      </Modal>
    </div>
  )
}

export default function FlowDesigner() {
  return (
    <ReactFlowProvider>
      <DesignerInner />
    </ReactFlowProvider>
  )
}

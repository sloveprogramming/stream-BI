import { useEffect, useState } from 'react'
import {
  Alert, Button, Card, Col, Form, Input, InputNumber, Modal, Row, Select, Space,
  Statistic, Switch, Table, Tag, Tooltip, message,
} from 'antd'
import {
  createQualityRule, deleteQualityRule, listQualityResults, listQualityRules,
  qualitySummary, runQualityRule, updateQualityRule,
  type QualityResult, type QualityRule,
} from '../../api/governance'
import { flowApi } from '../../api/flows'

const RULE_TYPES = [
  { value: 'freshness', label: '新鲜度（数据多久没更新）' },
  { value: 'row_count', label: '行数波动（环比涨跌）' },
  { value: 'null_rate', label: '空值率（字段空值占比）' },
]

const STATUS_META: Record<string, { color: string; text: string }> = {
  ok: { color: 'success', text: '正常' },
  anomaly: { color: 'error', text: '异常' },
  failed: { color: 'warning', text: '执行失败' },
  skipped: { color: 'default', text: '跳过' },
  '': { color: 'default', text: '未运行' },
}

const HINTS: Record<string, string> = {
  freshness: '阈值 = 小时数。最新数据距现在超过这个小时数就算异常。',
  row_count: '阈值 = 百分比。行数相对上一期的涨跌幅超过这个百分比就算异常。',
  null_rate: '阈值 = 百分比。指定字段的空值率超过这个百分比就算异常。必须填「检查字段」。',
}

export default function QualityMonitor() {
  const [rules, setRules] = useState<QualityRule[]>([])
  const [results, setResults] = useState<QualityResult[]>([])
  const [summary, setSummary] = useState<{ total: number; anomaly: number; failed: number }>({
    total: 0, anomaly: 0, failed: 0,
  })
  const [datasets, setDatasets] = useState<{ id: number; name: string }[]>([])
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState<QualityRule | null>(null)
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()
  const ruleType = Form.useWatch('rule_type', form)

  const load = async () => {
    setLoading(true)
    try {
      const [r, s, res, ds] = await Promise.all([
        listQualityRules().catch(() => [] as QualityRule[]),
        qualitySummary().catch(() => ({ total: 0, anomaly: 0, failed: 0, rules: [] })),
        listQualityResults({ limit: 50 }).catch(() => [] as QualityResult[]),
        flowApi.datasets().catch(() => [] as { id: number; name: string }[]),
      ])
      setRules(r)
      setSummary({ total: s.total, anomaly: s.anomaly, failed: s.failed })
      setResults(res)
      setDatasets(ds)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const submit = async () => {
    const v = await form.validateFields()
    try {
      if (editing) await updateQualityRule(editing.id, v)
      else await createQualityRule(v)
      message.success(editing ? '已更新' : '已创建')
      setOpen(false)
      setEditing(null)
      form.resetFields()
      void load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '保存失败')
    }
  }

  const remove = async (row: QualityRule) => {
    try {
      await deleteQualityRule(row.id)
      message.success('已删除')
      void load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '删除失败')
    }
  }

  const runNow = async (row: QualityRule) => {
    try {
      const r = await runQualityRule(row.id)
      message.info(r.message || '已执行')
      void load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '执行失败')
    }
  }

  const toggle = async (row: QualityRule, enabled: boolean) => {
    try {
      await updateQualityRule(row.id, { enabled })
      void load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '更新失败')
    }
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Row gutter={16}>
        <Col span={6}><Card><Statistic title="巡检规则" value={summary.total} /></Card></Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="当前异常"
              value={summary.anomaly}
              valueStyle={{ color: summary.anomaly ? 'var(--semantic-error)' : undefined }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="执行失败" value={summary.failed} /></Card>
        </Col>
        <Col span={6}>
          <Card>
            <Button type="primary" onClick={() => { setEditing(null); form.resetFields(); setOpen(true) }}>
              新建规则
            </Button>
          </Card>
        </Col>
      </Row>

      {summary.anomaly > 0 && (
        <Alert
          type="error"
          showIcon
          message={`有 ${summary.anomaly} 条巡检规则当前处于异常状态`}
          description="看下「最近结果」里的说明，确认是数据没到、还是真的出了问题。"
        />
      )}

      <Card title="巡检规则" loading={loading} extra={<Button onClick={() => void load()}>刷新</Button>}>
        <Table
          rowKey="id"
          dataSource={rules}
          pagination={false}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: '数据集', dataIndex: 'dataset_name', width: 160 },
            {
              title: '类型', dataIndex: 'rule_type', width: 130,
              render: (v: string) => RULE_TYPES.find((t) => t.value === v)?.label ?? v,
            },
            {
              title: '阈值', dataIndex: 'threshold', width: 100,
              render: (v: number, row: QualityRule) =>
                row.rule_type === 'freshness' ? `${v} 小时` : `${v}%`,
            },
            {
              title: '检查字段', dataIndex: 'target_field', width: 120,
              render: (v: string | null, row: QualityRule) =>
                v || (row.rule_type === 'freshness' ? row.timestamp_field || '自动识别' : '-'),
            },
            { title: '调度', dataIndex: 'cron', width: 110 },
            {
              title: '最近状态', dataIndex: 'last_status', width: 110,
              render: (s: string) => {
                const m = STATUS_META[s] ?? STATUS_META['']
                return <Tag color={m.color}>{m.text}</Tag>
              },
            },
            {
              title: '最近运行', dataIndex: 'last_run_at', width: 170,
              render: (v: string | null) => (v ? new Date(v).toLocaleString() : '从未'),
            },
            {
              title: '启用', dataIndex: 'enabled', width: 80,
              render: (v: boolean, row: QualityRule) => (
                <Switch checked={v} size="small" onChange={(c) => void toggle(row, c)} />
              ),
            },
            {
              title: '操作', width: 190,
              render: (_: unknown, row: QualityRule) => (
                <Space>
                  <Button size="small" onClick={() => void runNow(row)}>立即巡检</Button>
                  <Button
                    size="small"
                    onClick={() => { setEditing(row); form.setFieldsValue(row); setOpen(true) }}
                  >
                    编辑
                  </Button>
                  <Button size="small" danger onClick={() => void remove(row)}>删除</Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Card title="最近结果">
        <Table
          rowKey="id"
          dataSource={results}
          pagination={{ pageSize: 10 }}
          columns={[
            {
              title: '时间', dataIndex: 'checked_at', width: 170,
              render: (v: string | null) => (v ? new Date(v).toLocaleString() : '-'),
            },
            { title: '规则', dataIndex: 'rule_name', width: 160 },
            { title: '数据集', dataIndex: 'dataset_name', width: 150 },
            {
              title: '结果', dataIndex: 'status', width: 100,
              render: (s: string) => {
                const m = STATUS_META[s] ?? STATUS_META['']
                return <Tag color={m.color}>{m.text}</Tag>
              },
            },
            {
              title: '说明', dataIndex: 'message',
              render: (v: string, row: QualityResult) =>
                row.error ? (
                  <Tooltip title={row.error}>
                    <span style={{ color: 'var(--semantic-warning)' }}>{row.error.slice(0, 80)}</span>
                  </Tooltip>
                ) : (
                  v || '-'
                ),
            },
            {
              title: '耗时', dataIndex: 'elapsed_ms', width: 90,
              render: (v: number) => `${v} ms`,
            },
          ]}
        />
      </Card>

      <Modal
        title={editing ? `编辑规则：${editing.name}` : '新建巡检规则'}
        open={open}
        onCancel={() => { setOpen(false); setEditing(null); form.resetFields() }}
        onOk={() => void submit()}
        width={560}
      >
        <Form form={form} layout="vertical" initialValues={{ rule_type: 'freshness', threshold: 24, direction: 'both', cron: '0 7 * * *' }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '给规则起个名字' }]}>
            <Input placeholder="例如：销售订单-每日新鲜度" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="dataset_id" label="数据集" rules={[{ required: true, message: '选择要巡检的数据集' }]}>
            <Select
              showSearch
              optionFilterProp="label"
              disabled={!!editing}
              options={datasets.map((d) => ({ value: d.id, label: d.name }))}
            />
          </Form.Item>
          <Form.Item name="rule_type" label="规则类型" rules={[{ required: true }]}>
            <Select options={RULE_TYPES} disabled={!!editing} />
          </Form.Item>
          {ruleType && <Alert type="info" showIcon message={HINTS[ruleType]} style={{ marginBottom: 16 }} />}
          <Form.Item name="threshold" label="阈值" rules={[{ required: true, message: '填一个阈值' }]}>
            <InputNumber min={0.01} step={1} style={{ width: '100%' }} />
          </Form.Item>
          {ruleType === 'null_rate' && (
            <Form.Item
              name="target_field"
              label="检查字段"
              rules={[{ required: true, message: '空值率必须指定字段' }]}
            >
              <Input placeholder="列名，例如 amount" />
            </Form.Item>
          )}
          {ruleType === 'freshness' && (
            <Form.Item
              name="timestamp_field"
              label="时间字段"
              extra="留空则自动识别 modified / update_time / gmt_modified / updated_at"
            >
              <Input placeholder="列名，例如 update_time" />
            </Form.Item>
          )}
          {ruleType === 'row_count' && (
            <Form.Item name="direction" label="关注方向">
              <Select
                options={[
                  { value: 'both', label: '涨跌都报' },
                  { value: 'up', label: '只报上涨' },
                  { value: 'down', label: '只报下跌' },
                ]}
              />
            </Form.Item>
          )}
          <Form.Item name="cron" label="调度（cron）" rules={[{ required: true }]}>
            <Input placeholder="0 7 * * *（每天 7 点）" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}

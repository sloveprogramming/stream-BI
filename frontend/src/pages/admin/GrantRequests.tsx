import { useEffect, useState } from 'react'
import {
  Button, Card, Form, Input, Modal, Space, Table, Tabs, Tag, message,
} from 'antd'
import {
  approveGrantRequest, cancelGrantRequest, listGrantRequests, rejectGrantRequest,
  type GrantRequest,
} from '../../api/governance'

const STATUS_META: Record<string, { color: string; text: string }> = {
  pending: { color: 'processing', text: '待审批' },
  approved: { color: 'success', text: '已通过' },
  rejected: { color: 'error', text: '已拒绝' },
  cancelled: { color: 'default', text: '已撤销' },
}

function fmtRules(rules: GrantRequest['requested_row_rules']) {
  if (!rules?.length) return '（不限行）'
  return rules.map((r) => `${r.field} ${r.op} ${String(r.value)}`).join(' 且 ')
}

export default function GrantRequests() {
  const [rows, setRows] = useState<GrantRequest[]>([])
  const [mine, setMine] = useState<GrantRequest[]>([])
  const [loading, setLoading] = useState(false)
  const [rejecting, setRejecting] = useState<GrantRequest | null>(null)
  const [form] = Form.useForm()

  const load = async () => {
    setLoading(true)
    try {
      const [todo, own] = await Promise.all([
        listGrantRequests(false).catch(() => [] as GrantRequest[]),
        listGrantRequests(true),
      ])
      setRows(todo)
      setMine(own)
    } catch {
      message.error('加载申请单失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const approve = async (row: GrantRequest, values: { note?: string; days?: number }) => {
    try {
      await approveGrantRequest(row.id, values)
      message.success('已通过并生成授权')
      void load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '审批失败')
    }
  }

  const doReject = async () => {
    const note = (form.getFieldValue('note') as string) || ''
    if (!note.trim()) {
      message.warning('拒绝必须填写理由')
      return
    }
    try {
      await rejectGrantRequest(rejecting!.id, note.trim())
      message.success('已拒绝')
      setRejecting(null)
      form.resetFields()
      void load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '操作失败')
    }
  }

  const cancel = async (row: GrantRequest) => {
    try {
      await cancelGrantRequest(row.id)
      message.success('已撤销')
      void load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '撤销失败')
    }
  }

  const columns = [
    { title: '申请人', dataIndex: 'applicant', width: 120 },
    { title: '数据集', dataIndex: 'dataset_id', width: 90 },
    { title: '申请理由', dataIndex: 'reason', ellipsis: true },
    {
      title: '期望行级规则',
      dataIndex: 'requested_row_rules',
      width: 220,
      render: (v: GrantRequest['requested_row_rules']) => fmtRules(v),
    },
    {
      title: '有效期',
      dataIndex: 'requested_days',
      width: 90,
      render: (v: number | null) => (v ? `${v} 天` : '永久'),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (s: string) => {
        const m = STATUS_META[s] ?? { color: 'default', text: s }
        return <Tag color={m.color}>{m.text}</Tag>
      },
    },
    {
      title: '提交时间',
      dataIndex: 'created_at',
      width: 170,
      render: (v: string | null) => (v ? new Date(v).toLocaleString() : '-'),
    },
  ]

  return (
    <Card title="权限申请与审批" extra={<Button onClick={() => void load()}>刷新</Button>}>
      <Tabs
        items={[
          {
            key: 'todo',
            label: `待我审批 (${rows.length})`,
            children: (
              <Table
                rowKey="id"
                loading={loading}
                dataSource={rows}
                columns={[
                  ...columns,
                  {
                    title: '操作',
                    width: 220,
                    render: (_: unknown, row: GrantRequest) => (
                      <Space>
                        <Button
                          type="primary"
                          size="small"
                          onClick={() => void approve(row, { note: '同意' })}
                        >
                          通过
                        </Button>
                        <Button danger size="small" onClick={() => setRejecting(row)}>
                          拒绝
                        </Button>
                      </Space>
                    ),
                  },
                ]}
                pagination={{ pageSize: 10 }}
              />
            ),
          },
          {
            key: 'mine',
            label: `我提交的 (${mine.length})`,
            children: (
              <Table
                rowKey="id"
                loading={loading}
                dataSource={mine}
                columns={[
                  ...columns,
                  {
                    title: '审批意见',
                    dataIndex: 'decision_note',
                    ellipsis: true,
                    render: (v: string | null) => v || '-',
                  },
                  {
                    title: '操作',
                    width: 100,
                    render: (_: unknown, row: GrantRequest) =>
                      row.status === 'pending' ? (
                        <Button size="small" onClick={() => void cancel(row)}>
                          撤销
                        </Button>
                      ) : (
                        '-'
                      ),
                  },
                ]}
                pagination={{ pageSize: 10 }}
              />
            ),
          },
        ]}
      />

      <Modal
        title={`拒绝申请 #${rejecting?.id ?? ''}`}
        open={!!rejecting}
        onCancel={() => { setRejecting(null); form.resetFields() }}
        onOk={() => void doReject()}
        okText="确认拒绝"
        okButtonProps={{ danger: true }}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="note"
            label="拒绝理由"
            rules={[{ required: true, message: '申请人需要知道为什么被拒' }]}
          >
            <Input.TextArea rows={3} placeholder="例如：该数据属财务口径，请走财务审批" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Empty, Segmented, Space, Table, Tag, Tooltip, Typography, message } from 'antd'
import { AuditOutlined, ReloadOutlined } from '@ant-design/icons'
import http from '../../api/http'

type AuditLog = {
  id: number
  user_id: number | null
  api_key_id: number | null
  channel: string
  dataset: string | null
  action: string
  row_count: number
  duration_ms: number
  detail?: Record<string, unknown> | null
  created_at?: string | null
}

/** 渠道 → 展示名与配色。审计的价值在"一眼看出是谁从哪来的"。 */
const CHANNEL: Record<string, { text: string; color: string }> = {
  web: { text: '网页', color: 'blue' },
  api: { text: 'API', color: 'geekblue' },
  mcp: { text: 'MCP', color: 'purple' },
  export: { text: '导出', color: 'orange' },
  assistant: { text: '问数', color: 'cyan' },
}

/** 动作 → 展示名。管理类动作（授权/登录）用醒目色，取数类用中性色。 */
const ACTION: Record<string, { text: string; color?: string }> = {
  query: { text: '查询' },
  describe: { text: '查看结构' },
  export: { text: '导出' },
  'export.download': { text: '下载报表', color: 'orange' },
  chat: { text: '问数' },
  'grant.create': { text: '授权创建', color: 'green' },
  'grant.revoke': { text: '授权撤销', color: 'red' },
  'grant.approve': { text: '授权审批', color: 'green' },
  'login.failed': { text: '登录失败', color: 'red' },
  'quality.anomaly': { text: '质量异常', color: 'gold' },
}

const FILTERS = [
  { label: '全部', value: 'all' },
  { label: '取数', value: 'query' },
  { label: '权限变更', value: 'grant' },
  { label: '登录', value: 'login' },
  { label: '质量', value: 'quality' },
]

function fmtTime(v?: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString()
}

export default function AuditLogs() {
  const [items, setItems] = useState<AuditLog[]>([])
  const [limit, setLimit] = useState(100)
  const [filter, setFilter] = useState('all')
  const [loading, setLoading] = useState(false)

  const load = useCallback(async (n: number) => {
    setLoading(true)
    try {
      const { data } = await http.get('/admin/audit-logs', { params: { limit: n } })
      setItems(data)
    } catch {
      message.error('加载审计日志失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load(limit)
  }, [load, limit])

  const visible = items.filter((a) => {
    if (filter === 'all') return true
    if (filter === 'query') {
      return ['query', 'describe', 'export', 'export.download', 'chat'].includes(a.action)
    }
    if (filter === 'grant') return a.action.startsWith('grant.')
    if (filter === 'login') return a.action.startsWith('login.')
    if (filter === 'quality') return a.action.startsWith('quality.')
    return true
  })

  return (
    <Card
      title={
        <Space size={8}>
          <AuditOutlined style={{ color: 'var(--brand-primary)' }} />
          <span>审计日志</span>
          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
            共 {items.length} 条
          </Typography.Text>
        </Space>
      }
      extra={
        <Space>
          <Segmented
            size="small"
            value={filter}
            onChange={(v) => setFilter(v as string)}
            options={FILTERS}
          />
          <Button
            size="small"
            icon={<ReloadOutlined />}
            loading={loading}
            onClick={() => void load(limit)}
          >
            刷新
          </Button>
          <Segmented
            size="small"
            value={limit}
            onChange={(v) => setLimit(v as number)}
            options={[
              { label: '最近 100', value: 100 },
              { label: '最近 200', value: 200 },
            ]}
          />
        </Space>
      }
    >
      <Table
        rowKey="id"
        className="bisql-table"
        size="small"
        loading={loading}
        dataSource={visible}
        locale={{
          emptyText: <Empty description="暂无审计记录" image={Empty.PRESENTED_IMAGE_SIMPLE} />,
        }}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        columns={[
          { title: 'ID', dataIndex: 'id', width: 70 },
          {
            title: '用户',
            dataIndex: 'user_id',
            width: 80,
            render: (v: number | null) =>
              v == null ? (
                <Typography.Text type="secondary">—</Typography.Text>
              ) : (
                v
              ),
          },
          {
            title: '渠道',
            dataIndex: 'channel',
            width: 90,
            render: (v: string) => {
              const c = CHANNEL[v]
              return c ? <Tag color={c.color}>{c.text}</Tag> : <Tag>{v}</Tag>
            },
          },
          {
            title: '动作',
            dataIndex: 'action',
            width: 130,
            render: (v: string) => {
              const a = ACTION[v]
              return a?.color ? <Tag color={a.color}>{a.text}</Tag> : (a?.text ?? v)
            },
          },
          {
            title: '数据集',
            dataIndex: 'dataset',
            ellipsis: true,
            render: (v: string | null) =>
              v ? <span className="bisql-mono" style={{ fontSize: 12 }}>{v}</span> : '—',
          },
          {
            title: '影响行数',
            dataIndex: 'row_count',
            width: 100,
            align: 'right' as const,
            render: (v: number) => v?.toLocaleString?.() ?? v,
          },
          {
            title: '耗时',
            dataIndex: 'duration_ms',
            width: 100,
            align: 'right' as const,
            render: (v: number) => (v ? `${v} ms` : '—'),
          },
          {
            title: '时间',
            dataIndex: 'created_at',
            width: 180,
            render: fmtTime,
          },
          {
            title: '详情',
            width: 80,
            render: (_: unknown, r: AuditLog) =>
              r.detail && Object.keys(r.detail).length ? (
                <Tooltip
                  title={
                    <pre style={{ margin: 0, fontSize: 11, maxHeight: 260, overflow: 'auto' }}>
                      {JSON.stringify(r.detail, null, 2)}
                    </pre>
                  }
                >
                  <Typography.Text style={{ fontSize: 12, cursor: 'help' }} type="secondary">
                    查看
                  </Typography.Text>
                </Tooltip>
              ) : (
                '—'
              ),
          },
        ]}
      />
    </Card>
  )
}

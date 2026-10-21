import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Empty, Popconfirm, Space, Table, Tag, Typography, message } from 'antd'
import { DownloadOutlined, FileTextOutlined, ReloadOutlined } from '@ant-design/icons'
import http from '../api/http'

type ReportRun = {
  id: number
  task_id: number
  file_name: string
  row_count: number
  status: string
  error?: string | null
  created_at?: string | null
}

function fmtTime(v?: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString()
}

export default function MyReports() {
  const [items, setItems] = useState<ReportRun[]>([])
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await http.get('/my/reports')
      setItems(data)
    } catch {
      message.error('加载报表失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const download = async (id: number, fileName: string) => {
    try {
      const resp = await http.get(`/my/reports/${id}/download`, { responseType: 'blob' })
      const url = URL.createObjectURL(resp.data)
      const a = document.createElement('a')
      a.href = url
      a.download = fileName
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      message.error('下载失败，文件可能已过期')
    }
  }

  return (
    <Card
      title={
        <Space size={8}>
          <FileTextOutlined style={{ color: 'var(--brand-primary)' }} />
          <span>我的报表</span>
        </Space>
      }
      extra={
        <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
          刷新
        </Button>
      }
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 12.5, marginBottom: 16 }}>
        由定时任务生成，按你的权限裁剪后落盘。文件有留存期，过期后需重新生成。
      </Typography.Paragraph>

      <Table
        rowKey="id"
        className="bisql-table"
        loading={loading}
        dataSource={items}
        locale={{
          emptyText: <Empty description="还没有生成过报表" image={Empty.PRESENTED_IMAGE_SIMPLE} />,
        }}
        pagination={{ pageSize: 10, hideOnSinglePage: true }}
        columns={[
          {
            title: '文件名',
            dataIndex: 'file_name',
            render: (v: string) => <span className="bisql-mono" style={{ fontSize: 12.5 }}>{v}</span>,
          },
          {
            title: '行数',
            dataIndex: 'row_count',
            width: 100,
            align: 'right' as const,
            render: (v: number) => v?.toLocaleString?.() ?? v,
          },
          {
            title: '状态',
            dataIndex: 'status',
            width: 90,
            render: (v: string) =>
              v === 'success' ? (
                <Tag color="success">成功</Tag>
              ) : (
                <Tag color="error">失败</Tag>
              ),
          },
          {
            title: '生成时间',
            dataIndex: 'created_at',
            width: 180,
            render: fmtTime,
          },
          {
            title: '操作',
            width: 150,
            render: (_: unknown, r: ReportRun) =>
              r.status === 'success' ? (
                <Button
                  size="small"
                  icon={<DownloadOutlined />}
                  onClick={() => void download(r.id, r.file_name)}
                >
                  下载
                </Button>
              ) : (
                <Popconfirm title={r.error || '生成失败'} showCancel={false} okText="知道了">
                  <Typography.Text type="danger" style={{ fontSize: 12, cursor: 'help' }}>
                    查看原因
                  </Typography.Text>
                </Popconfirm>
              ),
          },
        ]}
      />
    </Card>
  )
}

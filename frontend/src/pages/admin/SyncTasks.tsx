import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Space, Table, Tag, Tooltip, Typography, message } from 'antd'
import { ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons'
import http from '../../api/http'

const REFRESH_MS = 5000

export default function SyncTasks() {
  const [items, setItems] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [lastAt, setLastAt] = useState<Date | null>(null)

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const { data } = await http.get('/sync/tasks')
      setItems(data)
      setLastAt(new Date())
    } catch {
      /* 轮询失败静默重试 */
    } finally {
      if (!silent) setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const timer = setInterval(() => load(true), REFRESH_MS)
    return () => clearInterval(timer)
  }, [load])

  const run = async (id: number) => {
    try {
      const { data } = await http.post(`/sync/tasks/${id}/run`)
      if (data.status === 'failed') message.error(`同步失败：${data.error}`)
      else message.success(`同步成功，写入 ${data.rows_written} 行`)
      load()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '触发失败')
    }
  }

  const scanErp = async () => {
    try {
      const { data } = await http.post('/admin/sync/discover')
      const n = data.created?.length || 0
      message.success(
        n
          ? `扫描完成，新建 ${n} 个同步任务：${data.created.map((c: any) => c.source_table).join('、')}`
          : `扫描完成，${data.erp_tables} 张表均已配置`,
      )
      load()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '扫描失败（ERP 可能不可达）')
    }
  }

  return (
    <Card
      title="同步任务"
      extra={
        <Space size={12} wrap>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            每 {REFRESH_MS / 1000} 秒自动刷新
            {lastAt ? ` · 上次 ${lastAt.toLocaleTimeString()}` : ''}
          </Typography.Text>
          <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => load()}>
            刷新
          </Button>
          <Tooltip title="扫描 ERP 库里还没有同步任务的新表，自动建任务并立即拉一次">
            <Button size="small" type="primary" icon={<ThunderboltOutlined />} onClick={scanErp}>
              扫描 ERP 新表
            </Button>
          </Tooltip>
        </Space>
      }
    >
      <Table
        rowKey="id"
        dataSource={items}
        columns={[
          { title: 'ID', dataIndex: 'id', width: 60 },
          {
            title: '名称',
            dataIndex: 'name',
            render: (n: string) =>
              n.endsWith('__auto') ? (
                <Space size={4}>
                  <span>{n.replace('__auto', '')}</span>
                  <Tag color="blue">自动</Tag>
                </Space>
              ) : (
                n
              ),
          },
          { title: '源表', dataIndex: 'source_table' },
          { title: '目标表', dataIndex: 'target_table' },
          { title: '周期', dataIndex: 'cron' },
          {
            title: '状态',
            dataIndex: 'enabled',
            render: (v) => (v ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>),
          },
          { title: '水位线', dataIndex: 'watermark' },
          {
            title: '操作',
            render: (_, r) => (
              <Button size="small" type="primary" onClick={() => run(r.id)}>
                立即同步
              </Button>
            ),
          },
        ]}
      />
    </Card>
  )
}

import { useEffect, useState } from 'react'
import { Button, Card, Table, Tag, message } from 'antd'
import http from '../../api/http'

export default function SyncTasks() {
  const [items, setItems] = useState<any[]>([])
  const load = () => http.get('/sync/tasks').then((r) => setItems(r.data))
  useEffect(() => {
    load()
  }, [])

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

  return (
    <Card title="同步任务">
      <Table
        rowKey="id"
        dataSource={items}
        columns={[
          { title: 'ID', dataIndex: 'id' },
          { title: '名称', dataIndex: 'name' },
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

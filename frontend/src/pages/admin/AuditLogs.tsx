import { useEffect, useState } from 'react'
import { Card, Select, Table } from 'antd'
import http from '../../api/http'

export default function AuditLogs() {
  const [items, setItems] = useState<any[]>([])
  const [limit, setLimit] = useState(100)

  const load = (n = limit) => http.get('/admin/audit-logs', { params: { limit: n } }).then((r) => setItems(r.data))
  useEffect(() => {
    load()
  }, [])

  return (
    <Card
      title="审计日志（最近调用）"
      extra={
        <Select
          style={{ width: 140 }}
          value={limit}
          onChange={(v) => {
            setLimit(v)
            load(v)
          }}
          options={[100, 200].map((n) => ({ value: n, label: `最近 ${n} 条` }))}
        />
      }
    >
      <Table
        rowKey="id"
        dataSource={items}
        size="small"
        columns={[
          { title: 'ID', dataIndex: 'id', width: 60 },
          { title: '用户', dataIndex: 'user_id', width: 60 },
          { title: '渠道', dataIndex: 'channel', width: 80 },
          { title: '数据集', dataIndex: 'dataset' },
          { title: '动作', dataIndex: 'action', width: 80 },
          { title: '行数', dataIndex: 'row_count', width: 70 },
          { title: '耗时(ms)', dataIndex: 'duration_ms', width: 90 },
          { title: '时间', dataIndex: 'created_at', width: 180 },
        ]}
      />
    </Card>
  )
}

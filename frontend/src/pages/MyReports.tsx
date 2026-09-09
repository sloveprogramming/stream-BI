import { useEffect, useState } from 'react'
import { Button, Card, Table, Tag } from 'antd'
import http from '../api/http'

export default function MyReports() {
  const [items, setItems] = useState<any[]>([])
  const load = () => http.get('/my/reports').then((r) => setItems(r.data))
  useEffect(() => {
    load()
  }, [])

  const download = (id: number, fileName: string) => {
    http.get(`/my/reports/${id}/download`, { responseType: 'blob' }).then((resp) => {
      const url = URL.createObjectURL(resp.data)
      const a = document.createElement('a')
      a.href = url
      a.download = fileName
      a.click()
      URL.revokeObjectURL(url)
    })
  }

  return (
    <Card title="我的报表（定时生成，可下载）">
      <Table
        rowKey="id"
        dataSource={items}
        columns={[
          { title: '文件名', dataIndex: 'file_name' },
          { title: '行数', dataIndex: 'row_count' },
          {
            title: '状态',
            dataIndex: 'status',
            render: (v) => (v === 'success' ? <Tag color="green">成功</Tag> : <Tag color="red">失败</Tag>),
          },
          { title: '生成时间', dataIndex: 'created_at' },
          {
            title: '操作',
            render: (_, r) =>
              r.status === 'success' ? (
                <Button size="small" onClick={() => download(r.id, r.file_name)}>
                  下载
                </Button>
              ) : (
                r.error
              ),
          },
        ]}
      />
    </Card>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Form, Input, Table, message } from 'antd'
import { useParams } from 'react-router-dom'
import http from '../api/http'

export default function DatasetQuery() {
  const { name } = useParams()
  const [columns, setColumns] = useState<string[]>([])
  const [rows, setRows] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [filters, setFilters] = useState<Record<string, string>>({})
  const pageSize = 20

  const load = useCallback(async () => {
    const params: any = { limit: pageSize, offset: (page - 1) * pageSize, ...filters }
    const { data } = await http.get(`/datasets/${name}/rows`, { params })
    setColumns(data.columns)
    setRows(data.rows)
    setTotal(data.total)
  }, [name, page, filters])

  useEffect(() => {
    load().catch((e) => message.error(e.response?.data?.detail || '查询失败'))
  }, [load])

  const tableColumns = [
    ...columns.map((c) => ({
      title: c,
      dataIndex: c,
      key: c,
      width: c === 'buyer_message' || c === 'receiver_name' ? 200 : 150,
      ellipsis: true,
    })),
  ]

  const onFilter = (v: any) => {
    const clean: Record<string, string> = {}
    Object.entries(v).forEach(([k, val]) => {
      if (val !== undefined && val !== null && val !== '') clean[k] = String(val)
    })
    setPage(1)
    setFilters(clean)
  }

  const doExport = async (format: string) => {
    try {
      const resp = await http.get(`/datasets/${name}/export`, {
        params: { format, ...filters },
        responseType: 'blob',
      })
      const url = URL.createObjectURL(resp.data)
      const a = document.createElement('a')
      a.href = url
      a.download = `${name}.${format}`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e: any) {
      message.error(e.response?.data?.detail || '导出失败')
    }
  }

  return (
    <Card
      title={`数据集：${name}`}
      extra={
        <Button.Group>
          <Button onClick={() => doExport('xlsx')}>导出 Excel</Button>
          <Button onClick={() => doExport('csv')}>导出 CSV</Button>
        </Button.Group>
      }
    >
      <Form layout="inline" onFinish={onFilter} style={{ marginBottom: 16 }}>
        {columns.map((c) => (
          <Form.Item key={c} name={c} label={c}>
            <Input allowClear style={{ width: 140 }} />
          </Form.Item>
        ))}
        <Button type="primary" htmlType="submit">
          筛选
        </Button>
      </Form>
      <Table
        rowKey={(r) => JSON.stringify(r)}
        scroll={{ x: 'max-content' }}
        columns={tableColumns}
        dataSource={rows}
        pagination={{
          current: page,
          pageSize,
          total,
          onChange: (p) => setPage(p),
          showTotal: (t) => `共 ${t} 行`,
        }}
      />
    </Card>
  )
}

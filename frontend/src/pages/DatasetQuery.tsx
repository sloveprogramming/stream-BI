import { useEffect, useState } from 'react'
import { Alert, Button, Card, Empty, Input, Select, Space, Table, Typography, message } from 'antd'
import { DeleteOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons'
import { useParams } from 'react-router-dom'
import http from '../api/http'
import { getFieldLabels, labelOf } from '../api/fieldLabels'

type Cond = { field?: string; value?: string }

export default function DatasetQuery() {
  const { name } = useParams()
  const [title, setTitle] = useState('')
  const [allColumns, setAllColumns] = useState<string[]>([])
  const [labels, setLabels] = useState<Record<string, string>>({})
  const [conds, setConds] = useState<Cond[]>([{ field: undefined, value: '' }])
  const [applied, setApplied] = useState<Record<string, string> | null>(null)
  const [columns, setColumns] = useState<string[]>([])
  const [rows, setRows] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const pageSize = 20

  useEffect(() => {
    http.get('/datasets').then((r) => {
      const ds = r.data.find((d: any) => d.name === name)
      setTitle(ds?.title || name || '')
      setAllColumns(ds?.columns || [])
    })
    getFieldLabels().then(setLabels)
  }, [name])

  const runQuery = async (filters: Record<string, string>, targetPage = 1) => {
    try {
      const { data } = await http.get(`/datasets/${name}/rows`, {
        params: { limit: pageSize, offset: (targetPage - 1) * pageSize, ...filters },
      })
      setColumns(data.columns)
      setRows(data.rows)
      setTotal(data.total)
      setPage(targetPage)
      setApplied(filters)
    } catch (e: any) {
      message.error(e.response?.data?.detail || '查询失败')
    }
  }

  const onSearch = () => {
    const clean: Record<string, string> = {}
    conds.forEach((c) => {
      if (c.field && c.value !== undefined && c.value !== '') clean[c.field] = c.value
    })
    if (Object.keys(clean).length === 0) {
      message.warning('请先设置至少一个筛选条件，再点击查询')
      return
    }
    runQuery(clean, 1)
  }

  const doExport = async (format: string) => {
    if (!applied) {
      message.warning('请先筛选查询，再导出')
      return
    }
    try {
      const resp = await http.get(`/datasets/${name}/export`, {
        params: { format, ...applied },
        responseType: 'blob',
      })
      const url = URL.createObjectURL(resp.data)
      const a = document.createElement('a')
      a.href = url
      a.download = `${title || name}.${format}`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e: any) {
      message.error(e.response?.data?.detail || '导出失败')
    }
  }

  const tableColumns = columns.map((c) => ({
    title: labelOf(labels, c),
    dataIndex: c,
    key: c,
    width: 160,
    ellipsis: true,
  }))

  const fieldOptions = allColumns.map((c) => ({ value: c, label: labelOf(labels, c) }))

  return (
    <Card
      title={`数据集：${title || name}`}
      extra={
        <Button.Group>
          <Button disabled={!applied} onClick={() => doExport('xlsx')}>
            导出 Excel（当前筛选）
          </Button>
          <Button disabled={!applied} onClick={() => doExport('csv')}>
            导出 CSV（当前筛选）
          </Button>
        </Button.Group>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="为保证性能与数据安全，请先设置筛选条件再查询；结果与导出都会按筛选条件过滤。"
      />
      <Space direction="vertical" style={{ width: '100%', marginBottom: 12 }}>
        {conds.map((cond, i) => (
          <Space key={i} wrap>
            <Select
              style={{ width: 220 }}
              placeholder="选择字段"
              showSearch
              optionFilterProp="label"
              value={cond.field}
              onChange={(v) =>
                setConds(conds.map((c, j) => (j === i ? { ...c, field: v } : c)))
              }
              options={fieldOptions}
            />
            <Input
              style={{ width: 240 }}
              placeholder="筛选值（精确匹配）"
              value={cond.value}
              onChange={(e) =>
                setConds(conds.map((c, j) => (j === i ? { ...c, value: e.target.value } : c)))
              }
              onPressEnter={onSearch}
            />
            {conds.length > 1 && (
              <Button
                type="text"
                danger
                icon={<DeleteOutlined />}
                onClick={() => setConds(conds.filter((_, j) => j !== i))}
              />
            )}
          </Space>
        ))}
        <Space>
          <Button icon={<PlusOutlined />} onClick={() => setConds([...conds, { value: '' }])}>
            添加条件
          </Button>
          <Button type="primary" icon={<SearchOutlined />} onClick={onSearch}>
            查询
          </Button>
        </Space>
      </Space>
      {applied ? (
        <Table
          rowKey={(r) => JSON.stringify(r)}
          scroll={{ x: 'max-content' }}
          columns={tableColumns}
          dataSource={rows}
          pagination={{
            current: page,
            pageSize,
            total,
            onChange: (p) => runQuery(applied, p),
            showTotal: (t) => `共 ${t} 行`,
          }}
        />
      ) : (
        <Empty
          description={
            <Typography.Text type="secondary">
              设置筛选条件后点击「查询」，这里会显示筛选结果
            </Typography.Text>
          }
        />
      )}
    </Card>
  )
}

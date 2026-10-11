import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert, Button, Card, Collapse, Empty, Input, Select, Space, Spin, Switch, Table, Tag, Typography, message,
} from 'antd'
import { DeleteOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { useParams } from 'react-router-dom'
import http from '../api/http'
import { getFieldLabels, labelOf } from '../api/fieldLabels'

type Cond = { field?: string; value?: string }

const REFRESH_MS = 5000
const PAGE_SIZE = 20

/** 一层血缘：节点 Tag + 经由的流程 + 物理源标注 */
function LineageLayer({ data, emptyText }: { data: any; emptyText: string }) {
  const layers: any[][] = data?.layers || []
  if (!layers.length)
    return (
      <div style={{ marginTop: 4 }}>
        <Typography.Text type="secondary">{emptyText}</Typography.Text>
      </div>
    )
  return (
    <Space direction="vertical" size={6} style={{ width: '100%', marginTop: 4 }}>
      {layers.map((layer, i) => (
        <div key={i}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            第 {i + 1} 层
          </Typography.Text>
          <div>
            {layer.map((n: any) => (
              <Tag key={n.name} color={n.leaf ? 'orange' : 'blue'} style={{ marginBottom: 4 }}>
                {n.name}
                {n.via ? `（经由 ${n.via}）` : ''}
                {n.leaf ? ' · 物理源表' : ''}
              </Tag>
            ))}
          </div>
        </div>
      ))}
    </Space>
  )
}

export default function DatasetQuery() {
  const { name } = useParams()
  const [title, setTitle] = useState('')
  const [allColumns, setAllColumns] = useState<string[]>([])
  const [labels, setLabels] = useState<Record<string, string>>({})
  const [conds, setConds] = useState<Cond[]>([{ field: undefined, value: '' }])
  const [applied, setApplied] = useState<Record<string, string>>({})
  const [columns, setColumns] = useState<string[]>([])
  const [rows, setRows] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [lastAt, setLastAt] = useState<Date | null>(null)
  const [loading, setLoading] = useState(false)
  const [lineage, setLineage] = useState<{ upstream: any; downstream: any } | null>(null)
  const [lineageOpen, setLineageOpen] = useState(false)

  const appliedRef = useRef(applied)
  const pageRef = useRef(page)
  appliedRef.current = applied
  pageRef.current = page

  const runQuery = useCallback(
    async (filters: Record<string, string>, targetPage = 1, silent = false) => {
      if (!silent) setLoading(true)
      try {
        const { data } = await http.get(`/datasets/${name}/rows`, {
          params: { limit: PAGE_SIZE, offset: (targetPage - 1) * PAGE_SIZE, ...filters },
        })
        setColumns(data.columns)
        setRows(data.rows)
        setTotal(data.total)
        setPage(targetPage)
        setApplied(filters)
        setLastAt(new Date())
      } catch (e: any) {
        if (!silent) message.error(e.response?.data?.detail || '查询失败')
      } finally {
        if (!silent) setLoading(false)
      }
    },
    [name],
  )

  // 进入数据集：读取元信息并直接展示第一页
  useEffect(() => {
    http.get('/datasets').then((r) => {
      const ds = r.data.find((d: any) => d.name === name)
      setTitle(ds?.title || name || '')
      setAllColumns(ds?.columns || [])
    })
    getFieldLabels().then(setLabels)
    setConds([{ field: undefined, value: '' }])
    runQuery({}, 1)
  }, [name, runQuery])

  // 默认每 5 秒刷新当前页
  useEffect(() => {
    if (!autoRefresh) return
    const timer = setInterval(() => runQuery(appliedRef.current, pageRef.current, true), REFRESH_MS)
    return () => clearInterval(timer)
  }, [autoRefresh, runQuery])

  const onSearch = () => {
    const clean: Record<string, string> = {}
    conds.forEach((c) => {
      if (c.field && c.value !== undefined && c.value !== '') clean[c.field] = c.value
    })
    runQuery(clean, 1)
  }

  const onReset = () => {
    setConds([{ field: undefined, value: '' }])
    runQuery({}, 1)
  }

  // 血缘懒加载：展开面板时才查（数据页的主任务是查数据）
  const loadLineage = () => {
    if (!name) return
    Promise.all([
      http.get(`/lineage/datasets/${name}/upstream`, { params: { depth: 3 } }),
      http.get(`/lineage/datasets/${name}/downstream`, { params: { depth: 3 } }),
    ])
      .then(([u, d]) => setLineage({ upstream: u.data, downstream: d.data }))
      .catch((e: any) => message.error(e.response?.data?.detail || '血缘查询失败'))
  }

  const doExport = async (format: string) => {
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
  const filtered = Object.keys(applied).length > 0

  return (
    <Card
      title={`数据集：${title || name}`}
      extra={
        <Button.Group>
          <Button onClick={() => doExport('xlsx')}>导出 Excel（{filtered ? '当前筛选' : '全部'}）</Button>
          <Button onClick={() => doExport('csv')}>导出 CSV（{filtered ? '当前筛选' : '全部'}）</Button>
        </Button.Group>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="默认展示第一页数据；可添加筛选条件缩小范围，结果与导出都会按筛选条件过滤。"
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
        <Space wrap>
          <Button icon={<PlusOutlined />} onClick={() => setConds([...conds, { value: '' }])}>
            添加条件
          </Button>
          <Button type="primary" icon={<SearchOutlined />} onClick={onSearch}>
            查询
          </Button>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => runQuery(applied, page)}>
            刷新
          </Button>
          <Button onClick={onReset} disabled={!filtered && conds.length === 1}>
            重置
          </Button>
          <Space size={6}>
            <Switch checked={autoRefresh} onChange={setAutoRefresh} size="small" />
            <Typography.Text style={{ fontSize: 12 }}>每 5 秒自动刷新</Typography.Text>
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            上次刷新：{lastAt ? lastAt.toLocaleTimeString() : '—'}
          </Typography.Text>
        </Space>
      </Space>
      {columns.length > 0 ? (
        <Table
          rowKey={(r) => JSON.stringify(r)}
          scroll={{ x: 'max-content' }}
          columns={tableColumns}
          dataSource={rows}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            onChange: (p) => runQuery(appliedRef.current, p),
            showTotal: (t) => `共 ${t} 行`,
          }}
        />
      ) : (
        <Empty
          description={
            <Typography.Text type="secondary">该数据集暂无数据</Typography.Text>
          }
        />
      )}
      <Collapse
        style={{ marginTop: 16 }}
        activeKey={lineageOpen ? ['lineage'] : []}
        onChange={(keys) => {
          const open = (keys as string[]).includes('lineage')
          setLineageOpen(open)
          if (open && !lineage) loadLineage()
        }}
        items={[
          {
            key: 'lineage',
            label: '血缘（上游来源 / 下游影响）',
            children: lineage ? (
              <Space direction="vertical" size={14} style={{ width: '100%' }}>
                <div>
                  <Typography.Text strong>上游（这批数从哪来）</Typography.Text>
                  <LineageLayer
                    data={lineage.upstream}
                    emptyText="没有上游 —— 它是源头数据集（或还没有流程加工出它）"
                  />
                </div>
                <div>
                  <Typography.Text strong>下游（改它会砸到谁）</Typography.Text>
                  <LineageLayer
                    data={lineage.downstream}
                    emptyText="没有下游 —— 还没有流程把它加工成别的数据集"
                  />
                </div>
              </Space>
            ) : (
              <Spin size="small" />
            ),
          },
        ]}
      />
    </Card>
  )
}

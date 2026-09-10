import { useCallback, useEffect, useRef, useState } from 'react'
import { Badge, Button, Card, Col, Row, Space, Switch, Tag, Typography, message } from 'antd'
import { ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import http from '../api/http'
import { getFieldLabels, labelOf } from '../api/fieldLabels'

const REFRESH_MS = 5000
const PREVIEW_FIELDS = 4

export default function Datasets() {
  const [items, setItems] = useState<any[]>([])
  const [labels, setLabels] = useState<Record<string, string>>({})
  const [lastAt, setLastAt] = useState<Date | null>(null)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [newIds, setNewIds] = useState<number[]>([])
  const [loading, setLoading] = useState(false)
  const knownIds = useRef<Set<number> | null>(null)
  const isAdmin = JSON.parse(localStorage.getItem('user') || 'null')?.is_admin

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const { data } = await http.get('/datasets')
      const ids = data.map((d: any) => d.id)
      if (knownIds.current) {
        const fresh = ids.filter((id: number) => !knownIds.current!.has(id))
        if (fresh.length) {
          setNewIds((prev) => Array.from(new Set([...prev, ...fresh])))
          message.success(`发现 ${fresh.length} 个新数据集`)
        }
      }
      knownIds.current = new Set(ids)
      setItems(data)
      setLastAt(new Date())
    } catch {
      /* 轮询失败静默重试，不打扰用户 */
    } finally {
      if (!silent) setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    getFieldLabels().then(setLabels)
  }, [load])

  useEffect(() => {
    if (!autoRefresh) return
    const timer = setInterval(() => load(true), REFRESH_MS)
    return () => clearInterval(timer)
  }, [autoRefresh, load])

  const discoverNow = async () => {
    try {
      const { data } = await http.post('/admin/datasets/discover')
      message.success(
        data.count ? `扫描完成，新增 ${data.count} 个数据集` : '扫描完成，暂无新表',
      )
      load()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '扫描失败')
    }
  }

  return (
    <>
      <Card
        size="small"
        style={{ marginBottom: 16 }}
        styles={{ body: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 } }}
      >
        <Space size={16} wrap>
          <Typography.Text strong>共 {items.length} 个数据集</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            上次刷新：{lastAt ? lastAt.toLocaleTimeString() : '—'}
            {autoRefresh ? `（每 ${REFRESH_MS / 1000} 秒自动刷新）` : ''}
          </Typography.Text>
        </Space>
        <Space>
          <Space size={6}>
            <Switch checked={autoRefresh} onChange={setAutoRefresh} size="small" />
            <Typography.Text style={{ fontSize: 12 }}>自动刷新</Typography.Text>
          </Space>
          <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => load()}>
            刷新
          </Button>
          {isAdmin && (
            <Button size="small" icon={<ThunderboltOutlined />} onClick={discoverNow}>
              扫描新表
            </Button>
          )}
        </Space>
      </Card>

      <Row gutter={[16, 16]}>
        {items.length === 0 && (
          <Col span={24}>
            <Card>
              <Typography.Text type="secondary">
                暂无可用数据集，请联系管理员为你所在角色授权。
              </Typography.Text>
            </Card>
          </Col>
        )}
        {items.map((d) => {
          const cols: string[] = d.columns || []
          const preview = cols.slice(0, PREVIEW_FIELDS).map((c) => labelOf(labels, c))
          const isNew = newIds.includes(d.id)
          const card = (
            <Card
              title={
                <Space size={8}>
                  <span>{d.title || d.name}</span>
                  <Tag color="blue">字段 {d.column_count ?? cols.length}</Tag>
                </Space>
              }
              hoverable
            >
              {d.title && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  标识：{d.name}
                </Typography.Text>
              )}
              <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
                {d.description || '（无描述）'}
              </Typography.Paragraph>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {preview.join('、')}
                {cols.length > PREVIEW_FIELDS ? ` 等 ${cols.length} 个字段` : ''}
              </Typography.Text>
              <div style={{ marginTop: 12 }}>
                <Link to={`/datasets/${d.name}`}>进入查询 →</Link>
              </div>
            </Card>
          )
          return (
            <Col span={8} key={d.id}>
              {isNew ? (
                <Badge.Ribbon text="新" color="green">
                  {card}
                </Badge.Ribbon>
              ) : (
                card
              )}
            </Col>
          )
        })}
      </Row>
    </>
  )
}

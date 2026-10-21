import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Badge,
  Button,
  Card,
  Col,
  Empty,
  Input,
  Row,
  Segmented,
  Space,
  Switch,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  ClockCircleOutlined,
  DatabaseOutlined,
  ReloadOutlined,
  RobotOutlined,
  SearchOutlined,
  TableOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { Link } from 'react-router-dom'
import http from '../api/http'
import { getFieldLabels } from '../api/fieldLabels'

const REFRESH_MS = 5000
const PREVIEW_FIELDS = 5

/** KPI 图标底色：蓝/绿/紫/琥珀，与设计系统配色一致 */
const KPI_TINTS = {
  blue: 'linear-gradient(135deg,#1E40AF,#3B82F6)',
  green: 'linear-gradient(135deg,#16A34A,#4ADE80)',
  purple: 'linear-gradient(135deg,#7C3AED,#A78BFA)',
  amber: 'linear-gradient(135deg,#D97706,#FBBF24)',
}

/** 顶部概览卡：数字大、标签小，给页面一个视觉重心。 */
function KpiCard({
  icon,
  label,
  value,
  suffix,
  tint,
  hint,
}: {
  icon: React.ReactNode
  label: string
  value: number | string
  suffix?: string
  tint: string
  hint?: string
}) {
  return (
    <Card size="small" style={{ height: '100%' }}>
      <div className="bisql-kpi">
        <div className="bisql-kpi__icon" style={{ background: tint }}>
          {icon}
        </div>
        <div style={{ minWidth: 0 }}>
          <div className="bisql-kpi__value">
            {value}
            {suffix && <span style={{ fontSize: 12, fontWeight: 500, color: '#94A3B8' }}>{suffix}</span>}
          </div>
          <Tooltip title={hint}>
            <div className="bisql-kpi__label">{label}</div>
          </Tooltip>
        </div>
      </div>
    </Card>
  )
}

/** 数据集名 → 稳定配色（名称哈希，排序变化时颜色不跳） */
const PALETTE = ['#1E40AF', '#16A34A', '#D97706', '#7C3AED', '#0891B2', '#DC2626']

function paletteOf(name: string) {
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0
  return PALETTE[h % PALETTE.length]
}

/** 字段显示名：优先数据集自带的中文列注释，其次全局映射，最后原列名。 */
function columnLabel(comments: Record<string, string>, col: string, global: Record<string, string>) {
  return comments?.[col] || global[col] || col
}

/** 数据集卡片 */
function DatasetCard({ d, labels, isNew }: { d: any; labels: Record<string, string>; isNew: boolean }) {
  const [rowCount, setRowCount] = useState<number | null>(null)
  const [broken, setBroken] = useState(false)
  const bar = paletteOf(d.name)
  const cols: string[] = d.columns || []
  const comments: Record<string, string> = d.column_comments || {}
  const preview = cols.slice(0, PREVIEW_FIELDS)

  useEffect(() => {
    let alive = true
    http
      .get(`/datasets/${d.name}/rows`, { params: { limit: 1 } })
      .then((r) => alive && setRowCount(r.data?.total ?? null))
      .catch(() => {
        if (alive) {
          setRowCount(null)
          setBroken(true)
        }
      })
    return () => {
      alive = false
    }
  }, [d.name])

  const body = (
    <Card
      className="bisql-card-hover"
      hoverable
      style={{ height: '100%', overflow: 'hidden' }}
      styles={{ body: { padding: 0, height: '100%', display: 'flex', flexDirection: 'column' } }}
    >
      <div className="bisql-card-bar" style={{ background: bar }} />

      <div style={{ padding: '12px 14px 0' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 9 }}>
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 'var(--radius-md)',
              background: 'var(--brand-primary-bg)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
            }}
          >
            <TableOutlined style={{ color: bar, fontSize: 15 }} />
          </div>
          <div style={{ minWidth: 0, flex: 1 }}>
            <Typography.Text strong style={{ fontSize: 13.5, display: 'block', lineHeight: 1.35 }} ellipsis>
              {d.title || d.name}
            </Typography.Text>
            <span className="bisql-mono" style={{ fontSize: 10.5, color: '#94A3B8' }}>
              {d.name}
            </span>
          </div>
        </div>

        <Typography.Paragraph
          type="secondary"
          style={{ fontSize: 12, margin: '9px 0 0', minHeight: 34, lineHeight: 1.55 }}
          ellipsis={{ rows: 2, tooltip: d.description }}
        >
          {d.description || '（无描述）'}
        </Typography.Paragraph>
      </div>

      <div style={{ padding: '10px 14px 0', flex: 1 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {preview.map((c) => {
            const zh = columnLabel(comments, c, labels)
            return (
              <span key={c} className="bisql-chip" title={zh === c ? c : `${zh}（${c}）`}>
                {zh}
              </span>
            )
          })}
          {cols.length > PREVIEW_FIELDS && (
            <span style={{ fontSize: 11, padding: '1px 2px', color: '#94A3B8' }}>
              +{cols.length - PREVIEW_FIELDS}
            </span>
          )}
        </div>
      </div>

      <div
        style={{
          marginTop: 11,
          padding: '8px 14px',
          borderTop: '1px solid var(--neutral-border-strong)',
          background: 'var(--neutral-surface-alt)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <Space size={12} style={{ fontSize: 11.5, color: '#475569' }}>
          <span>{cols.length} 字段</span>
          <span>
            {broken ? (
              <Tooltip title="源表已不存在，可能是历史残留。可让管理员清理。">
                <span style={{ color: 'var(--semantic-warning)', cursor: 'help' }}>源表缺失</span>
              </Tooltip>
            ) : (
              <>{rowCount === null ? '统计中…' : `${rowCount.toLocaleString()} 行`}</>
            )}
          </span>
        </Space>
        <Link to={`/datasets/${d.name}`} style={{ fontSize: 12, fontWeight: 500 }}>
          查询 →
        </Link>
      </div>
    </Card>
  )

  return isNew ? (
    <Badge.Ribbon text="新" color="green">
      {body}
    </Badge.Ribbon>
  ) : (
    body
  )
}

export default function Datasets() {
  const [items, setItems] = useState<any[]>([])
  const [labels, setLabels] = useState<Record<string, string>>({})
  const [lastAt, setLastAt] = useState<Date | null>(null)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [newIds, setNewIds] = useState<number[]>([])
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [sortBy, setSortBy] = useState<'name' | 'fields'>('name')
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
      /* 轮询失败静默重试 */
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
      message.success(data.count ? `扫描完成，新增 ${data.count} 个数据集` : '扫描完成，暂无新表')
      load()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '扫描失败')
    }
  }

  const stats = useMemo(
    () => ({
      datasets: items.length,
      fields: items.reduce((s, d) => s + (d.column_count || d.columns?.length || 0), 0),
    }),
    [items],
  )

  const visible = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    const filtered = kw
      ? items.filter(
          (d) =>
            String(d.name).toLowerCase().includes(kw) ||
            String(d.title || '').toLowerCase().includes(kw) ||
            String(d.description || '').toLowerCase().includes(kw),
        )
      : items
    return [...filtered].sort((a, b) => {
      if (sortBy === 'fields') {
        return (b.column_count ?? 0) - (a.column_count ?? 0)
      }
      return String(a.title || a.name).localeCompare(String(b.title || b.name), 'zh')
    })
  }, [items, keyword, sortBy])

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {/* ---------- KPI 概览 ---------- */}
      <Row gutter={[12, 12]}>
        <Col xs={12} md={6}>
          <KpiCard
            icon={<DatabaseOutlined />}
            label="可用数据集"
            value={stats.datasets}
            tint={KPI_TINTS.blue}
            hint="你有权限访问的数据集数量"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            icon={<TableOutlined />}
            label="字段总数"
            value={stats.fields}
            tint={KPI_TINTS.green}
            hint="所有数据集去重前的字段合计"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            icon={<RobotOutlined />}
            label="自然语言问数"
            value="可用"
            tint={KPI_TINTS.purple}
            hint="数据助手支持多轮追问与口径对齐"
          />
        </Col>
        <Col xs={12} md={6}>
          <KpiCard
            icon={<ClockCircleOutlined />}
            label={autoRefresh ? '自动刷新中' : '已暂停刷新'}
            value={lastAt ? lastAt.toLocaleTimeString().slice(0, 5) : '—'}
            tint={KPI_TINTS.amber}
            hint={`每 ${REFRESH_MS / 1000} 秒检查一次新数据集`}
          />
        </Col>
      </Row>

      {/* ---------- 工具条 ---------- */}
      <Card size="small">
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: 10,
          }}
        >
          <Typography.Text type="secondary" style={{ fontSize: 12.5 }}>
            {keyword ? `「${keyword}」匹配 ${visible.length} 个` : `共 ${items.length} 个数据集`}
          </Typography.Text>
          <Space wrap>
            <Input
              allowClear
              size="small"
              prefix={<SearchOutlined style={{ color: 'var(--neutral-text-tertiary)' }} />}
              placeholder="搜索名称 / 描述"
              style={{ width: 180 }}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
            <Segmented
              size="small"
              value={sortBy}
              onChange={(v) => setSortBy(v as 'name' | 'fields')}
              options={[
                { label: '按名称', value: 'name' },
                { label: '按字段数', value: 'fields' },
              ]}
            />
            <Space size={5}>
              <Switch checked={autoRefresh} onChange={setAutoRefresh} size="small" />
              <Typography.Text style={{ fontSize: 11.5 }}>自动刷新</Typography.Text>
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
        </div>
      </Card>

      {/* ---------- 数据集 ---------- */}
      {visible.length === 0 ? (
        <Card>
          <Empty
            description={
              keyword ? `没有匹配「${keyword}」的数据集` : '暂无可用数据集，请联系管理员为你所在角色授权'
            }
          />
        </Card>
      ) : (
        <Row gutter={[12, 12]}>
          {visible.map((d) => (
            <Col xs={24} sm={12} lg={8} xxl={6} key={d.id}>
              <DatasetCard d={d} labels={labels} isNew={newIds.includes(d.id)} />
            </Col>
          ))}
        </Row>
      )}
    </Space>
  )
}

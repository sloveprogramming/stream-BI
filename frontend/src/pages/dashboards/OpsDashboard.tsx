/**
 * 运营看板 —— GMV 趋势、渠道分布、区域分布、发货时效。
 *
 * 设计依据（ui-ux-pro-max 设计系统，Data-Dense Dashboard 风格）：
 * - 趋势用面积折线（时间轴看涨跌，填充 20% 透明度）
 * - 渠道/区域用横向条形（名称长，横向比纵向可读）
 * - KPI 用大数字卡，数字用等宽字体保证对齐
 */
import { useEffect, useMemo, useState } from 'react'
import { Card, Col, Row, Segmented, Space, Spin, Typography } from 'antd'
import { DatabaseOutlined, ShoppingCartOutlined, ThunderboltOutlined } from '@ant-design/icons'
import ReactECharts from 'echarts-for-react'
import { fmtMoney, opsOverview, type OpsOverview } from '../../api/dashboards'

const BLUE = '#1E40AF'
const AMBER = '#D97706'
const GREEN = '#16A34A'

/** KPI 小卡：数字用等宽字体，业务扫读时列对齐。 */
function Kpi({
  icon,
  label,
  value,
  sub,
  tint,
}: {
  icon: React.ReactNode
  label: string
  value: string
  sub?: string
  tint: string
}) {
  return (
    <Card size="small" style={{ height: '100%' }}>
      <div className="bisql-kpi">
        <div className="bisql-kpi__icon" style={{ background: tint }}>{icon}</div>
        <div style={{ minWidth: 0 }}>
          <div className="bisql-kpi__value">{value}</div>
          <div className="bisql-kpi__label">
            {label}
            {sub && (
              <span style={{ color: 'var(--neutral-text-tertiary)' }}> · {sub}</span>
            )}
          </div>
        </div>
      </div>
    </Card>
  )
}

export default function OpsDashboard() {
  // 旺店通历史数据跨度约 4 个月，默认看近一年才能兜住
  const [days, setDays] = useState(365)
  const [data, setData] = useState<OpsOverview | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError('')
    opsOverview(days)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e?.response?.data?.detail || '加载失败'))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [days])

  /** 趋势图：面积折线。数据点多时关掉 symbol，视觉更干净。 */
  const trendOption = useMemo(() => {
    if (!data) return {}
    return {
      grid: { left: 8, right: 12, top: 30, bottom: 0, containLabel: true },
      tooltip: { trigger: 'axis' },
      legend: { data: ['GMV', '订单数'], top: 0, textStyle: { fontSize: 11 } },
      xAxis: {
        type: 'category',
        data: data.trend.map((t) => t.date.slice(5)),
        axisLabel: { fontSize: 10 },
        boundaryGap: false,
      },
      yAxis: [
        { type: 'value', name: 'GMV', axisLabel: { fontSize: 10, formatter: fmtMoney } },
        { type: 'value', name: '订单', axisLabel: { fontSize: 10 } },
      ],
      series: [
        {
          name: 'GMV',
          type: 'line',
          smooth: true,
          symbol: data.trend.length > 40 ? 'none' : 'circle',
          symbolSize: 5,
          data: data.trend.map((t) => t.gmv),
          itemStyle: { color: BLUE },
          lineStyle: { width: 2 },
          areaStyle: { opacity: 0.15, color: BLUE },
          animation: false,
        },
        {
          name: '订单数',
          type: 'line',
          yAxisIndex: 1,
          smooth: true,
          symbol: data.trend.length > 40 ? 'none' : 'circle',
          symbolSize: 5,
          data: data.trend.map((t) => t.orders),
          itemStyle: { color: AMBER },
          lineStyle: { width: 1.5, type: 'dashed' },
          animation: false,
        },
      ],
    }
  }, [data])

  /** 横向条形：店铺名长，横向才放得下。 */
  const channelOption = useMemo(() => {
    if (!data) return {}
    const rows = [...data.by_channel].sort((a, b) => a.gmv - b.gmv)
    return {
      grid: { left: 8, right: 30, top: 8, bottom: 0, containLabel: true },
      tooltip: { trigger: 'axis', valueFormatter: (v: number) => fmtMoney(v) },
      xAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: fmtMoney } },
      yAxis: {
        type: 'category',
        data: rows.map((r) => r.name),
        axisLabel: { fontSize: 10.5, width: 110, overflow: 'truncate' },
      },
      series: [
        {
          type: 'bar',
          data: rows.map((r) => r.gmv),
          barMaxWidth: 16,
          itemStyle: { color: BLUE, borderRadius: [0, 3, 3, 0] },
          label: { show: true, position: 'right', fontSize: 10, formatter: (p: any) => fmtMoney(p.value) },
          animation: false,
        },
      ],
    }
  }, [data])

  const regionOption = useMemo(() => {
    if (!data) return {}
    const rows = [...data.by_region].sort((a, b) => a.gmv - b.gmv)
    return {
      grid: { left: 8, right: 30, top: 8, bottom: 0, containLabel: true },
      tooltip: { trigger: 'axis', valueFormatter: (v: number) => fmtMoney(v) },
      xAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: fmtMoney } },
      yAxis: {
        type: 'category',
        data: rows.map((r) => r.name),
        axisLabel: { fontSize: 10.5 },
      },
      series: [
        {
          type: 'bar',
          data: rows.map((r) => r.gmv),
          barMaxWidth: 16,
          itemStyle: { color: GREEN, borderRadius: [0, 3, 3, 0] },
          label: { show: true, position: 'right', fontSize: 10, formatter: (p: any) => fmtMoney(p.value) },
          animation: false,
        },
      ],
    }
  }, [data])

  if (error) {
    return (
      <Card>
        <Typography.Text type="danger">{error}</Typography.Text>
      </Card>
    )
  }
  if (!data) {
    return (
      <Card>
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin tip="加载看板数据…" />
        </div>
      </Card>
    )
  }

  const k = data.kpi

  return (
    <Spin spinning={loading}>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {/* 时间范围 */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            运营看板
          </Typography.Title>
          <Segmented
            size="small"
            value={days}
            onChange={(v) => setDays(v as number)}
            options={[
              { label: '近 30 天', value: 30 },
              { label: '近 90 天', value: 90 },
              { label: '近 180 天', value: 180 },
              { label: '近一年', value: 365 },
            ]}
          />
        </div>

        {/* KPI 行 */}
        <Row gutter={[12, 12]}>
          <Col xs={12} md={6}>
            <Kpi icon={<DatabaseOutlined />} label="GMV" value={fmtMoney(k.gmv)} tint="linear-gradient(135deg,#1E40AF,#3B82F6)" />
          </Col>
          <Col xs={12} md={6}>
            <Kpi icon={<ShoppingCartOutlined />} label="订单数" value={k.orders.toLocaleString()} sub={`客单价 ¥${k.avg_order_value.toLocaleString()}`} tint="linear-gradient(135deg,#16A34A,#4ADE80)" />
          </Col>
          <Col xs={12} md={6}>
            <Kpi icon={<ThunderboltOutlined />} label="发货时效(中位)" value={`${k.median_ship_hours}h`} sub="付款→发货" tint="linear-gradient(135deg,#D97706,#FBBF24)" />
          </Col>
          <Col xs={12} md={6}>
            <Kpi icon={<DatabaseOutlined />} label="毛利" value={fmtMoney(k.gross_profit)} sub={`毛利率 ${k.gross_margin}%`} tint="linear-gradient(135deg,#7C3AED,#A78BFA)" />
          </Col>
        </Row>

        {data.trend.length === 0 && (
          <Card size="small">
            <Typography.Text type="secondary">
              所选时间范围内没有发货数据 —— 试着切换到更大的时间范围
              （旺店通历史数据集中在 2026 年 5–7 月）。
            </Typography.Text>
          </Card>
        )}

        {/* 趋势（通栏） */}
        <Card size="small" title="GMV 与订单趋势">
          <ReactECharts option={trendOption} style={{ height: 280 }} notMerge lazyUpdate />
        </Card>

        {/* 渠道 + 区域 */}
        <Row gutter={[12, 12]}>
          <Col xs={24} lg={14}>
            <Card size="small" title="渠道排行（GMV）">
              <ReactECharts option={channelOption} style={{ height: 320 }} notMerge lazyUpdate />
            </Card>
          </Col>
          <Col xs={24} lg={10}>
            <Card size="small" title="区域分布（GMV）">
              <ReactECharts option={regionOption} style={{ height: 320 }} notMerge lazyUpdate />
            </Card>
          </Col>
        </Row>

        <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
          口径：GMV = 实付金额合计（已排除退款单）；毛利 = GMV − 商品总成本；
          发货时效 = 付款到发货的小时数中位数。数据按你的权限裁剪。
        </Typography.Text>
      </Space>
    </Spin>
  )
}

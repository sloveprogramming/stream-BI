/**
 * 销售看板 —— 渠道排行、商品结构、退货率、业务员业绩。
 */
import { useEffect, useMemo, useState } from 'react'
import { Card, Col, Row, Segmented, Space, Spin, Statistic, Typography } from 'antd'
import { ShopOutlined } from '@ant-design/icons'
import ReactECharts from 'echarts-for-react'
import { fmtMoney, salesOverview, type SalesOverview } from '../../api/dashboards'

const BLUE = '#1E40AF'
const GREEN = '#16A34A'
const PURPLE = '#7C3AED'

export default function SalesDashboard() {
  // 与运营看板一致：默认近一年（旺店通历史数据集中在 2026 年 5–7 月）
  const [days, setDays] = useState(365)
  const [data, setData] = useState<SalesOverview | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError('')
    salesOverview(days)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e?.response?.data?.detail || '加载失败'))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [days])

  const shopOption = useMemo(() => {
    if (!data) return {}
    const rows = [...data.by_shop].sort((a, b) => a.gmv - b.gmv)
    return {
      grid: { left: 8, right: 40, top: 8, bottom: 0, containLabel: true },
      tooltip: { trigger: 'axis', valueFormatter: (v: number) => fmtMoney(v) },
      xAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: fmtMoney } },
      yAxis: {
        type: 'category',
        data: rows.map((r) => r.name),
        axisLabel: { fontSize: 10.5, width: 130, overflow: 'truncate' },
      },
      series: [
        {
          type: 'bar',
          data: rows.map((r) => r.gmv),
          barMaxWidth: 14,
          itemStyle: { color: BLUE, borderRadius: [0, 3, 3, 0] },
          animation: false,
          label: { show: true, position: 'right', fontSize: 10, formatter: (p: any) => fmtMoney(p.value) },
        },
      ],
    }
  }, [data])

  const goodsOption = useMemo(() => {
    if (!data) return {}
    const rows = [...data.by_goods].sort((a, b) => a.gmv - b.gmv).slice(0, 15)
    return {
      grid: { left: 8, right: 40, top: 8, bottom: 0, containLabel: true },
      tooltip: {
        trigger: 'axis',
        formatter: (ps: any[]) => {
          const r = rows[ps[0].dataIndex]
          return `${r?.name}<br/>${r?.brand}<br/>${ps[0].marker} ${fmtMoney(ps[0].value)}（${r?.qty} 件）`
        },
      },
      xAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: fmtMoney } },
      yAxis: {
        type: 'category',
        data: rows.map((r) => r.name),
        axisLabel: { fontSize: 10, width: 160, overflow: 'truncate' },
      },
      series: [
        {
          type: 'bar',
          data: rows.map((r) => r.gmv),
          barMaxWidth: 14,
          itemStyle: { color: PURPLE, borderRadius: [0, 3, 3, 0] },
          animation: false,
        },
      ],
    }
  }, [data])

  const salesmanOption = useMemo(() => {
    if (!data) return {}
    const rows = [...data.by_salesman].filter((r) => r.name && r.name !== '未分配').sort((a, b) => a.gmv - b.gmv)
    return {
      grid: { left: 8, right: 40, top: 8, bottom: 0, containLabel: true },
      tooltip: { trigger: 'axis', valueFormatter: (v: number) => fmtMoney(v) },
      xAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: fmtMoney } },
      yAxis: { type: 'category', data: rows.map((r) => r.name), axisLabel: { fontSize: 10.5 } },
      series: [
        {
          type: 'bar',
          data: rows.map((r) => r.gmv),
          barMaxWidth: 14,
          itemStyle: { color: GREEN, borderRadius: [0, 3, 3, 0] },
          animation: false,
          label: { show: true, position: 'right', fontSize: 10, formatter: (p: any) => fmtMoney(p.value) },
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
          <Spin tip="加载销售数据…" />
        </div>
      </Card>
    )
  }

  return (
    <Spin spinning={loading}>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Typography.Title level={5} style={{ margin: 0 }}>
            销售看板
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

        {/* 退货概览 */}
        <Row gutter={[12, 12]}>
          <Col xs={8} md={6}>
            <Card size="small">
              <Statistic title="总订单" value={data.total_orders.toLocaleString()} />
            </Card>
          </Col>
          <Col xs={8} md={6}>
            <Card size="small">
              <Statistic
                title="退款订单"
                value={data.refunded_orders.toLocaleString()}
                valueStyle={{ color: data.refunded_orders ? 'var(--semantic-warning)' : undefined }}
              />
            </Card>
          </Col>
          <Col xs={8} md={6}>
            <Card size="small" className="bisql-stat">
              <Statistic
                title="退货率"
                value={data.refund_rate}
                suffix="%"
                precision={2}
                valueStyle={{
                  color: data.refund_rate > 5 ? 'var(--semantic-error)' : 'var(--semantic-success)',
                }}
              />
            </Card>
          </Col>
          <Col xs={8} md={6}>
            <Card size="small">
              <Statistic
                title="活跃店铺"
                value={data.by_shop.length}
                prefix={<ShopOutlined />}
              />
            </Card>
          </Col>
        </Row>

        <Row gutter={[12, 12]}>
          <Col xs={24} lg={14}>
            <Card size="small" title="店铺排行（GMV）">
              <ReactECharts option={shopOption} style={{ height: 340 }} notMerge lazyUpdate />
            </Card>
          </Col>
          <Col xs={24} lg={10}>
            <Card size="small" title="商品结构（GMV Top 15）">
              <ReactECharts option={goodsOption} style={{ height: 340 }} notMerge lazyUpdate />
            </Card>
          </Col>
        </Row>

        <Card size="small" title="业务员业绩（GMV）">
          <ReactECharts option={salesmanOption} style={{ height: 260 }} notMerge lazyUpdate />
        </Card>

        <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
          口径：GMV = 实付金额合计；退货率 = 退款单数 ÷ 总单数。数据按你的权限裁剪。
        </Typography.Text>
      </Space>
    </Spin>
  )
}

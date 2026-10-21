import http from './http'

export interface DashKpi {
  gmv: number
  orders: number
  items: number
  discount: number
  cost: number
  gross_profit: number
  gross_margin: number
  avg_order_value: number
  median_ship_hours: number
}

export interface TrendPoint {
  date: string
  orders: number
  gmv: number
}

export interface NameValue {
  name: string
  orders?: number
  gmv: number
  discount?: number
  qty?: number
  brand?: string
}

export interface OpsOverview {
  range_days: number
  kpi: DashKpi
  trend: TrendPoint[]
  by_channel: NameValue[]
  by_region: NameValue[]
}

export interface SalesOverview {
  range_days: number
  by_shop: NameValue[]
  refund_rate: number
  refunded_orders: number
  total_orders: number
  by_goods: NameValue[]
  by_salesman: NameValue[]
}

export interface BoardInfo {
  key: string
  title: string
  description: string
  path: string
}

/** 金额格式化：业务看板上「216.7万」比「21670000.00」好读得多。 */
export function fmtMoney(v: number): string {
  const a = Math.abs(v)
  if (a >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  if (a >= 1e4) return `${(v / 1e4).toFixed(1)} 万`
  return v.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

export const opsOverview = (days: number) =>
  http.get<OpsOverview>('/dashboards/ops/overview', { params: { days } }).then((r) => r.data)

export const salesOverview = (days: number) =>
  http.get<SalesOverview>('/dashboards/sales/overview', { params: { days } }).then((r) => r.data)

export const dashboardCatalog = () =>
  http.get<BoardInfo[]>('/dashboards/catalog').then((r) => r.data)

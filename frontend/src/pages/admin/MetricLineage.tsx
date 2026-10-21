import { useState } from 'react'
import { Alert, Button, Card, Descriptions, Empty, Input, Space, Table, Tag, Typography, message } from 'antd'
import { metricLineage, type MetricLineage } from '../../api/governance'

const { Text, Paragraph } = Typography

export default function MetricLineage() {
  const [name, setName] = useState('')
  const [data, setData] = useState<MetricLineage | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const search = async () => {
    const key = name.trim()
    if (!key) {
      message.warning('请输入指标名（机器名）')
      return
    }
    setLoading(true)
    setError('')
    try {
      setData(await metricLineage(key))
    } catch (e: any) {
      setData(null)
      setError(e?.response?.data?.detail ?? '查询失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Card title="指标血缘与影响分析">
        <Space.Compact style={{ width: 480 }}>
          <Input
            placeholder="指标机器名，例如 gross_margin"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onPressEnter={() => void search()}
          />
          <Button type="primary" loading={loading} onClick={() => void search()}>
            追溯
          </Button>
        </Space.Compact>
        <Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
          改一个口径会砸到谁：这里列出引用了该指标的全部流程、它们的产物、
          以及产物的下游。用来回答「改这个公式之前我得先通知谁」。
        </Paragraph>
      </Card>

      {error && <Alert type="error" showIcon message={error} />}

      {data && (
        <>
          <Card title={`指标：${data.metric.label}（${data.metric.name}）`}>
            <Descriptions column={2} size="small">
              <Descriptions.Item label="口径公式">
                <Text code>{data.metric.expr}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="锚定数据集">{data.metric.dataset}</Descriptions.Item>
              <Descriptions.Item label="负责人">{data.metric.owner}</Descriptions.Item>
              <Descriptions.Item label="被引用次数">{data.used_by_count}</Descriptions.Item>
            </Descriptions>
          </Card>

          <Card title={`引用该指标的流程（${data.used_by_count}）`}>
            {data.used_by_count === 0 ? (
              <Empty description="还没有流程引用这个指标。改了它暂时不会影响任何产物。" />
            ) : (
              <Table
                rowKey="flow_id"
                dataSource={data.used_by}
                pagination={false}
                columns={[
                  { title: '流程', dataIndex: 'flow_name' },
                  {
                    title: '状态', dataIndex: 'enabled', width: 90,
                    render: (v: boolean) => (
                      <Tag color={v ? 'success' : 'default'}>{v ? '启用' : '停用'}</Tag>
                    ),
                  },
                  {
                    title: '产物表', dataIndex: 'target_table', width: 220,
                    render: (v: string) => <Text code>{v}</Text>,
                  },
                  {
                    title: '产物数据集', dataIndex: 'output_dataset', width: 180,
                    render: (v: string | null) => v ?? <Text type="secondary">未注册</Text>,
                  },
                  {
                    title: '下游影响', dataIndex: 'downstream',
                    render: (layers: { name: string; via?: string | null }[][]) => {
                      const flat = (layers ?? []).flat()
                      if (!flat.length) return <Text type="secondary">无下游</Text>
                      return (
                        <Space wrap size={4}>
                          {flat.map((n) => (
                            <Tag key={n.name}>{n.name}</Tag>
                          ))}
                        </Space>
                      )
                    },
                  },
                ]}
              />
            )}
          </Card>
        </>
      )}
    </Space>
  )
}

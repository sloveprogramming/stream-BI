import { Button, Drawer, Empty, Select, Space, Tag, Typography } from 'antd'
import { DeleteOutlined, PlayCircleOutlined } from '@ant-design/icons'
import type { DatasetOption } from '../../api/flows'
import { useFlowStore } from '../../store/flowStore'
import SchemaForm from './SchemaForm'

export default function ConfigDrawer({
  datasets,
  onPreview,
  previewing,
}: {
  datasets: DatasetOption[]
  onPreview: (nodeId: string) => void
  previewing: boolean
}) {
  const selectedId = useFlowStore((s) => s.selectedId)
  const nodes = useFlowStore((s) => s.nodes)
  const updateParams = useFlowStore((s) => s.updateParams)
  const removeNode = useFlowStore((s) => s.removeNode)
  const select = useFlowStore((s) => s.select)

  const node = nodes.find((n) => n.id === selectedId)
  if (!node) {
    return (
      <Drawer
        title="节点配置"
        placement="right"
        width={420}
        open={false}
        onClose={() => select(null)}
      />
    )
  }

  const { data } = node
  const isSource = data.opType === 'source.dataset'

  return (
    <Drawer
      title={
        <Space>
          <span>{data.title}</span>
          <Tag>{data.opType}</Tag>
        </Space>
      }
      placement="right"
      width={460}
      open
      onClose={() => select(null)}
      extra={
        <Button
          danger
          size="small"
          icon={<DeleteOutlined />}
          onClick={() => removeNode(node.id)}
        >
          删除节点
        </Button>
      }
      footer={
        <Space>
          <Button
            icon={<PlayCircleOutlined />}
            loading={previewing}
            onClick={() => onPreview(node.id)}
          >
            预览本步结果
          </Button>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            只跑「到这一步为止」的链路，取前 20 行
          </Typography.Text>
        </Space>
      }
    >
      {isSource ? (
        <>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
            只能选你有权限的数据集；行级与字段级权限在运行时自动套用。
          </Typography.Paragraph>
          {datasets.length === 0 ? (
            <Empty description="你还没有可见的数据集" />
          ) : (
            <Select
              style={{ width: '100%' }}
              showSearch
              optionFilterProp="label"
              value={data.params.dataset_id as number | undefined}
              placeholder="选择数据集"
              options={datasets.map((d) => ({
                value: d.id,
                label: `${d.name}${d.title ? `（${d.title}）` : ''} · ${d.columns?.length ?? 0} 列`,
              }))}
              onChange={(v) => updateParams(node.id, { ...data.params, dataset_id: v })}
            />
          )}
        </>
      ) : (
        <>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
            {data.schema.description ?? ''}
          </Typography.Paragraph>
          <SchemaForm
            schema={data.schema}
            value={data.params}
            onChange={(params) => updateParams(node.id, params)}
          />
        </>
      )}
    </Drawer>
  )
}

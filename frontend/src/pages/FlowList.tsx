import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  errorText,
  flowApi,
  type DatasetOption,
  type FlowDSL,
  type FlowSummary,
} from '../api/flows'

export default function FlowList() {
  const nav = useNavigate()
  const [flows, setFlows] = useState<FlowSummary[]>([])
  const [datasets, setDatasets] = useState<DatasetOption[]>([])
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const refresh = async () => {
    setLoading(true)
    try {
      const [list, ds] = await Promise.all([flowApi.list(), flowApi.datasets()])
      setFlows(list)
      setDatasets(ds)
    } catch (err) {
      message.error(errorText(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  const create = async () => {
    const values = await form.validateFields()
    // 新建时先落一条「读取数据集 → 物化」的最小流程：
    // 后端要求必须有且仅有一个输入与一个输出，这样一建出来就是合法的。
    const dsl: FlowDSL = {
      name: values.name,
      title: values.title || values.name,
      description: values.description || '',
      nodes: [
        {
          id: 'n1',
          type: 'source.dataset',
          params: { dataset_id: values.dataset_id },
          position: { x: 60, y: 180 },
        },
        { id: 'n2', type: 'sink.materialize', params: {}, position: { x: 380, y: 180 } },
      ],
      edges: [{ source: 'n1', target: 'n2' }],
      output: {
        materialization: 'table',
        target_schema: values.target_schema,
        target_name: values.target_name,
        pk_columns: [],
        register_dataset: true,
        share_with: [],
      },
    }
    setSaving(true)
    try {
      const flow = await flowApi.create(dsl)
      message.success('已创建，接下来把算子拖到画布上编排')
      nav(`/flows/${flow.id}`)
    } catch (err) {
      Modal.error({ title: '创建失败', content: errorText(err) })
    } finally {
      setSaving(false)
    }
  }

  const toggle = async (row: FlowSummary) => {
    try {
      await flowApi.setEnabled(row.id, !row.enabled)
      await refresh()
    } catch (err) {
      message.error(errorText(err))
    }
  }

  return (
    <Card
      title="数据编排"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={refresh}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>
            新建流程
          </Button>
        </Space>
      }
    >
      <Typography.Paragraph type="secondary">
        把算子拖到画布上编排数据加工：清洗、去重、关联、聚合，以及 LLM 打标。
        产物会注册成数据集，自动继承来源的行级权限，并被 API / MCP / 问数 / 导出四类出口消费。
      </Typography.Paragraph>

      <Table<FlowSummary>
        rowKey="id"
        loading={loading}
        dataSource={flows}
        pagination={false}
        locale={{ emptyText: '还没有流程，点右上角新建' }}
        columns={[
          { title: '流程', dataIndex: 'name', render: (v, r) => <a onClick={() => nav(`/flows/${r.id}`)}>{v}</a> },
          { title: '标题', dataIndex: 'title' },
          { title: '产物表', dataIndex: 'target', render: (v: string) => <code>{v}</code> },
          { title: '版本', dataIndex: 'current_version', width: 70 },
          {
            title: '数据集',
            dataIndex: 'output_dataset_id',
            width: 90,
            render: (v: number | null) => (v ? <Tag color="green">已上架</Tag> : <Tag>未跑过</Tag>),
          },
          {
            title: '启用',
            dataIndex: 'enabled',
            width: 90,
            render: (v: boolean) => (v ? <Tag color="blue">启用</Tag> : <Tag>停用</Tag>),
          },
          {
            title: '操作',
            width: 150,
            render: (_, row) => (
              <Space>
                <Button size="small" onClick={() => nav(`/flows/${row.id}`)}>
                  打开
                </Button>
                <Button size="small" onClick={() => toggle(row)}>
                  {row.enabled ? '停用' : '启用'}
                </Button>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title="新建流程"
        open={creating}
        onCancel={() => setCreating(false)}
        onOk={create}
        confirmLoading={saving}
        okText="创建并打开"
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ target_schema: 'dwd', title: '', description: '' }}
        >
          <Form.Item
            name="name"
            label="流程标识"
            rules={[
              { required: true, message: '必填' },
              {
                pattern: /^[A-Za-z_][A-Za-z0-9_]*$/,
                message: '只能字母、数字、下划线，且不以数字开头',
              },
            ]}
          >
            <Input placeholder="clean_sales_order" />
          </Form.Item>
          <Form.Item name="title" label="标题">
            <Input placeholder="销售订单清洗" />
          </Form.Item>
          <Form.Item
            name="dataset_id"
            label="第一个输入数据集"
            rules={[{ required: true, message: '必选' }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="选一个你有权限的数据集"
              options={datasets.map((d) => ({ value: d.id, label: d.name }))}
            />
          </Form.Item>
          <Space.Compact block>
            <Form.Item name="target_schema" label="落哪层" style={{ width: 140 }}>
              <Select options={['dwd', 'dws', 'ads'].map((v) => ({ value: v, label: v }))} />
            </Form.Item>
            <Form.Item
              name="target_name"
              label="目标表名"
              style={{ flex: 1, marginLeft: 8 }}
              rules={[
                { required: true, message: '必填' },
                {
                  pattern: /^[A-Za-z_][A-Za-z0-9_]*$/,
                  message: '只能字母、数字、下划线',
                },
              ]}
            >
              <Input placeholder="sales_order_clean" />
            </Form.Item>
          </Space.Compact>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

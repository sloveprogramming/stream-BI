import { useEffect, useState } from 'react'
import {
  Alert, AutoComplete, Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, message,
} from 'antd'
import {
  DeleteOutlined, EditOutlined, ExperimentOutlined, StarOutlined,
} from '@ant-design/icons'
import http from '../../api/http'

// 厂商模板：只是「自动填」的快捷方式，地址与模型全部可手改。
// 全部走 OpenAI 兼容协议 —— 这也是本地服务（Ollama / vLLM / LM Studio）的协议。
const VENDORS = [
  { key: 'deepseek', label: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', models: ['deepseek-chat', 'deepseek-reasoner'] },
  { key: 'dashscope', label: '通义千问（阿里）', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', models: ['qwen-plus', 'qwen-max', 'qwen-turbo', 'text-embedding-v3'] },
  { key: 'zhipu', label: '智谱 GLM', base_url: 'https://open.bigmodel.cn/api/paas/v4', models: ['glm-4-plus', 'glm-4-flash', 'embedding-3'] },
  { key: 'moonshot', label: '月之暗面 Kimi', base_url: 'https://api.moonshot.cn/v1', models: ['moonshot-v1-8k', 'moonshot-v1-32k'] },
  { key: 'openai', label: 'OpenAI', base_url: 'https://api.openai.com/v1', models: ['gpt-4o-mini', 'gpt-4o', 'text-embedding-3-small'] },
  { key: 'siliconflow', label: '硅基流动', base_url: 'https://api.siliconflow.cn/v1', models: ['deepseek-ai/DeepSeek-V3', 'Qwen/Qwen2.5-7B-Instruct', 'BAAI/bge-m3'] },
  { key: 'ollama', label: 'Ollama（本机）', base_url: 'http://localhost:11434/v1', models: [] },
  { key: 'local-service', label: 'vLLM / LM Studio（本机）', base_url: 'http://localhost:8000/v1', models: [] },
  { key: 'custom', label: '自定义', base_url: '', models: [] },
]

export default function ModelProviders() {
  const [items, setItems] = useState<any[]>([])
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<any | null>(null)
  const [modelOptions, setModelOptions] = useState<{ value: string }[] | null>(null)
  const [testing, setTesting] = useState(false)
  const [form] = Form.useForm()

  const kind = Form.useWatch('kind', form)
  const vendor = Form.useWatch('vendor', form)

  const load = () => {
    http.get('/admin/providers').then((r) => setItems(r.data))
  }
  useEffect(() => {
    load()
  }, [])

  const openCreate = () => {
    setEditing(null)
    setModelOptions(null)
    form.resetFields()
    form.setFieldsValue({ kind: 'llm', provider_kind: 'api', vendor: 'deepseek', enabled: true })
    setOpen(true)
  }

  const openEdit = (row: any) => {
    setEditing(row)
    setModelOptions(null)
    form.resetFields()
    form.setFieldsValue({ ...row, api_key: '' })
    setOpen(true)
  }

  // 选了厂商模板：自动填地址与常见模型（可手改）
  const pickVendor = (key: string) => {
    const v = VENDORS.find((x) => x.key === key)
    if (!v) return
    form.setFieldsValue({ base_url: v.base_url })
    setModelOptions(v.models.length ? v.models.map((m) => ({ value: m })) : null)
  }

  const submit = async () => {
    const v = await form.validateFields()
    const payload: any = {
      name: v.name,
      kind: v.kind,
      provider_kind: v.provider_kind,
      base_url: v.base_url,
      model: v.model,
      planner_model: v.planner_model,
      enabled: v.enabled,
    }
    if (v.api_key) payload.api_key = v.api_key
    else if (!editing) payload.api_key = '' // 新建本地服务时允许不填 key
    try {
      if (editing) await http.put(`/admin/providers/${editing.id}`, payload)
      else await http.post('/admin/providers', payload)
      message.success(editing ? '配置已更新' : '配置已创建')
      setOpen(false)
      load()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '保存失败')
    }
  }

  // 测试连接：编辑且没填新 key 时，用已存的 key 测（后端解密）
  const runTest = async () => {
    const v = await form.validateFields()
    setTesting(true)
    try {
      const r =
        editing && !v.api_key
          ? await http.post(`/admin/providers/${editing.id}/test`)
          : await http.post('/admin/providers/test', {
              kind: v.kind,
              provider_kind: v.provider_kind,
              base_url: v.base_url,
              api_key: v.api_key,
              model: v.model,
            })
      if (r.data.ok) message.success(`连接成功${r.data.dim ? `，向量维度 ${r.data.dim}` : ''}`)
      else message.error(r.data.error || '连接失败')
    } catch (e: any) {
      message.error(e.response?.data?.detail || '测试失败')
    } finally {
      setTesting(false)
    }
  }

  const fetchModels = async () => {
    const v = await form.getFieldsValue()
    try {
      const r =
        editing && !v.api_key
          ? await http.post(`/admin/providers/${editing.id}/models`)
          : await http.post('/admin/providers/models', { base_url: v.base_url, api_key: v.api_key })
      if (r.data.ok) {
        setModelOptions(r.data.models.map((m: string) => ({ value: m })))
        message.success(`拉到 ${r.data.models.length} 个模型`)
      } else {
        message.error(`${r.data.error || '拉取失败'}${r.data.hint ? `；${r.data.hint}` : ''}`)
      }
    } catch (e: any) {
      message.error(e.response?.data?.detail || '拉取失败')
    }
  }

  const setDefault = async (row: any) => {
    try {
      await http.post(`/admin/providers/${row.id}/default`)
      message.success(`「${row.name}」已设为默认`)
      load()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '操作失败')
    }
  }

  const toggleEnabled = async (row: any) => {
    try {
      await http.put(`/admin/providers/${row.id}`, {
        name: row.name,
        kind: row.kind,
        provider_kind: row.provider_kind,
        base_url: row.base_url,
        model: row.model,
        planner_model: row.planner_model,
        enabled: !row.enabled,
      })
      load()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '操作失败')
    }
  }

  const remove = async (row: any) => {
    try {
      await http.delete(`/admin/providers/${row.id}`)
      message.success('已删除（运行时将回退到环境变量配置）')
      load()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '删除失败')
    }
  }

  return (
    <Card
      title="模型配置"
      extra={
        <Button type="primary" onClick={openCreate}>
          新增提供方
        </Button>
      }
    >
      <Alert
        style={{ marginBottom: 16 }}
        type="info"
        showIcon
        message="「默认」配置对问数助手与 AI 算子立即生效，无需重启；没有默认配置时回退到服务端环境变量。API key 加密存储，界面上只显示末 4 位。"
      />
      <Table
        rowKey="id"
        dataSource={items}
        columns={[
          { title: '名称', dataIndex: 'name' },
          {
            title: '用途',
            dataIndex: 'kind',
            width: 90,
            render: (v) => (v === 'llm' ? <Tag color="purple">LLM</Tag> : <Tag color="geekblue">向量</Tag>),
          },
          {
            title: '来源',
            dataIndex: 'provider_kind',
            width: 80,
            render: (v) => (v === 'local' ? '本地' : 'API'),
          },
          { title: '地址', dataIndex: 'base_url', ellipsis: true },
          { title: '模型', dataIndex: 'model', ellipsis: true },
          {
            title: 'Key',
            dataIndex: 'api_key_masked',
            width: 140,
            ellipsis: true,
            render: (v) => v || <span style={{ color: 'var(--neutral-text-tertiary)' }}>（无）</span>,
          },
          {
            title: '默认',
            dataIndex: 'is_default',
            width: 70,
            render: (v) => (v ? <Tag color="gold">默认</Tag> : ''),
          },
          {
            title: '启用',
            dataIndex: 'enabled',
            width: 70,
            render: (v, row) => <Switch size="small" checked={v} onChange={() => toggleEnabled(row)} />,
          },
          {
            title: '操作',
            width: 220,
            render: (_, row) => (
              <Space>
                <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)}>
                  编辑
                </Button>
                {!row.is_default && (
                  <Button size="small" icon={<StarOutlined />} onClick={() => setDefault(row)}>
                    设默认
                  </Button>
                )}
                <Popconfirm
                  title={`确认删除「${row.name}」？`}
                  description="删除后运行时回退到环境变量配置。"
                  onConfirm={() => remove(row)}
                >
                  <Button danger size="small" icon={<DeleteOutlined />} />
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        open={open}
        title={editing ? `编辑「${editing.name}」` : '新增模型提供方'}
        onOk={submit}
        onCancel={() => setOpen(false)}
        width={640}
        footer={[
          <Button key="test" icon={<ExperimentOutlined />} loading={testing} onClick={runTest}>
            测试连接
          </Button>,
          <Button key="cancel" onClick={() => setOpen(false)}>
            取消
          </Button>,
          <Button key="ok" type="primary" onClick={submit}>
            保存
          </Button>,
        ]}
      >
        <Form form={form} layout="vertical">
          <Space size="middle" style={{ display: 'flex' }}>
            <Form.Item name="kind" label="用途" rules={[{ required: true }]}>
              <Select
                style={{ width: 160 }}
                onChange={() => setModelOptions(null)}
                options={[
                  { value: 'llm', label: 'LLM（问数 / AI 算子）' },
                  { value: 'embedding', label: 'Embedding（向量）' },
                ]}
              />
            </Form.Item>
            <Form.Item name="provider_kind" label="来源" rules={[{ required: true }]}>
              <Select
                style={{ width: 140 }}
                options={[
                  { value: 'api', label: '厂商 API' },
                  { value: 'local', label: '本地 / 自建' },
                ]}
              />
            </Form.Item>
          </Space>

          <Form.Item name="vendor" label="厂商模板（自动填地址与常见模型，可手改）">
            <Select
              showSearch
              options={VENDORS.map((v) => ({ value: v.key, label: v.label }))}
              onChange={pickVendor}
            />
          </Form.Item>

          <Form.Item
            name="name"
            label="名称"
            rules={[{ required: true, message: '起个能认出来的名字' }]}
          >
            <Input placeholder="如：DeepSeek 主力 / 本机 Ollama" />
          </Form.Item>

          <Form.Item
            name="base_url"
            label="API 地址"
            rules={[{ required: vendor !== 'custom', message: 'API 来源必须填地址（通常以 /v1 结尾）' }]}
          >
            <Input placeholder="https://api.deepseek.com/v1" />
          </Form.Item>

          <Form.Item
            name="api_key"
            label={editing ? `API Key（当前 ${editing.api_key_masked || '无'}，留空则不修改）` : 'API Key（本地服务可留空）'}
          >
            <Input.Password placeholder="sk-…" autoComplete="new-password" />
          </Form.Item>

          <Form.Item label="模型" required style={{ marginBottom: 0 }}>
            <Space.Compact style={{ width: '100%' }}>
              <Form.Item
                name="model"
                noStyle
                rules={[{ required: true, message: '填模型名，或先点「拉取模型列表」' }]}
              >
                {/* AutoComplete：既可手填，也能从拉取到的清单里选 */}
                <AutoComplete
                  options={modelOptions || []}
                  placeholder="如 deepseek-chat；点右侧按钮可从厂商拉取"
                  style={{ width: 'calc(100% - 140px)' }}
                />
              </Form.Item>
              <Button style={{ width: 140 }} onClick={fetchModels}>
                拉取模型列表
              </Button>
            </Space.Compact>
          </Form.Item>

          {kind === 'llm' && (
            <Form.Item
              name="planner_model"
              label="规划用模型（可选）"
              tooltip="Agent 的规划与反思用推理模型更稳；留空则与上面同一个模型"
            >
              <Input placeholder="如 deepseek-reasoner" />
            </Form.Item>
          )}

          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

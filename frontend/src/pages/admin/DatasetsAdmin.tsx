import { useEffect, useState } from 'react'
import { Button, Card, Checkbox, Form, Input, Modal, Select, Space, Table, message } from 'antd'
import http from '../../api/http'

const OPS = ['eq', 'ne', 'gt', 'ge', 'lt', 'le', 'like']
const OP_LABEL: Record<string, string> = {
  eq: '等于', ne: '不等于', gt: '大于', ge: '大于等于', lt: '小于', le: '小于等于', like: '包含',
}

export default function DatasetsAdmin() {
  const [datasets, setDatasets] = useState<any[]>([])
  const [roles, setRoles] = useState<any[]>([])
  const [dsOpen, setDsOpen] = useState(false)
  const [grantOpen, setGrantOpen] = useState(false)
  const [grantTarget, setGrantTarget] = useState<any>(null)
  const [whitelist, setWhitelist] = useState<string[]>([])
  const [rules, setRules] = useState<any[]>([])
  const [form] = Form.useForm()
  const [roleOpen, setRoleOpen] = useState(false)
  const [roleName, setRoleName] = useState('')
  const [roleDesc, setRoleDesc] = useState('')

  const load = () => {
    http.get('/admin/datasets').then((r) => setDatasets(r.data))
    http.get('/admin/roles').then((r) => setRoles(r.data))
  }
  useEffect(() => {
    load()
  }, [])

  const register = async () => {
    const v = await form.validateFields()
    try {
      await http.post('/admin/datasets', {
        ...v,
        pk_columns: ['id'],
        columns: v.columns.split(',').map((s: string) => s.trim()).filter(Boolean),
      })
      message.success('数据集已注册')
      setDsOpen(false)
      form.resetFields()
      load()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '注册失败')
    }
  }

  const openGrant = (ds: any) => {
    setGrantTarget(ds)
    setWhitelist(ds.columns)
    setRules([])
    setGrantOpen(true)
  }

  const saveGrant = async (roleId: number) => {
    try {
      await http.post('/admin/grants', {
        role_id: roleId,
        dataset_id: grantTarget.id,
        row_rules: rules.filter((r) => r.field && r.value !== undefined),
        field_whitelist: whitelist.length ? whitelist : null,
      })
      message.success('授权已保存')
      setGrantOpen(false)
    } catch (e: any) {
      message.error(e.response?.data?.detail || '保存失败')
    }
  }

  return (
    <Card
      title="数据集与授权"
      extra={
        <Space>
          <Button onClick={() => setRoleOpen(true)}>新建角色</Button>
          <Button type="primary" onClick={() => setDsOpen(true)}>注册数据集</Button>
        </Space>
      }
    >
      <Table
        rowKey="id"
        dataSource={datasets}
        columns={[
          { title: '名称', dataIndex: 'name' },
          { title: '源表', dataIndex: 'source_table' },
          { title: '字段', dataIndex: 'columns', render: (c) => c?.join('、') },
          {
            title: '操作',
            render: (_, r) => (
              <Button size="small" onClick={() => openGrant(r)}>
                配置角色授权
              </Button>
            ),
          },
        ]}
      />
      <Modal
        open={roleOpen}
        title="新建角色"
        onOk={async () => {
          try {
            await http.post('/admin/roles', { name: roleName, description: roleDesc || null })
            message.success('角色已创建')
            setRoleOpen(false)
            setRoleName('')
            setRoleDesc('')
            load()
          } catch (e: any) {
            message.error(e.response?.data?.detail || '创建失败')
          }
        }}
        onCancel={() => setRoleOpen(false)}
      >
        <Form layout="vertical">
          <Form.Item label="角色名称（英文标识）" required>
            <Input placeholder="east_sales" value={roleName} onChange={(e) => setRoleName(e.target.value)} />
          </Form.Item>
          <Form.Item label="描述">
            <Input placeholder="华东区销售" value={roleDesc} onChange={(e) => setRoleDesc(e.target.value)} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal open={dsOpen} title="注册数据集" onOk={register} onCancel={() => setDsOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="数据集名称（英文标识）" rules={[{ required: true }]}>
            <Input placeholder="sales_order" />
          </Form.Item>
          <Form.Item name="source_table" label="源表（ods.xxx）" rules={[{ required: true }]}>
            <Input placeholder="ods.sales_order" />
          </Form.Item>
          <Form.Item name="columns" label="字段（逗号分隔，含 id 主键列）" rules={[{ required: true }]}>
            <Input placeholder="id, region, amount, cost" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        open={grantOpen}
        title={`授权配置：${grantTarget?.name || ''}`}
        footer={null}
        onCancel={() => setGrantOpen(false)}
        width={720}
      >
        <p>选择角色：</p>
        <Space direction="vertical" style={{ width: '100%' }}>
          {roles.map((role) => (
            <Card key={role.id} size="small" title={role.name}>
              <p>字段白名单（不勾选 = 全部字段）：</p>
              <Checkbox.Group
                value={whitelist}
                onChange={(v) => setWhitelist(v as string[])}
                options={grantTarget?.columns || []}
              />
              <p style={{ marginTop: 12 }}>行级过滤规则（全部为 AND）：</p>
              {rules.map((rule, i) => (
                <Space key={i} style={{ marginBottom: 4 }}>
                  <Select
                    style={{ width: 130 }}
                    placeholder="字段"
                    value={rule.field}
                    onChange={(v) => setRules(rules.map((r, j) => (j === i ? { ...r, field: v } : r)))}
                    options={(grantTarget?.columns || []).map((c: string) => ({ value: c, label: c }))}
                  />
                  <Select
                    style={{ width: 110 }}
                    value={rule.op}
                    onChange={(v) => setRules(rules.map((r, j) => (j === i ? { ...r, op: v } : r)))}
                    options={OPS.map((o) => ({ value: o, label: OP_LABEL[o] }))}
                  />
                  <Input
                    style={{ width: 160 }}
                    placeholder="值"
                    value={rule.value}
                    onChange={(e) =>
                      setRules(rules.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))
                    }
                  />
                  <Button danger size="small" onClick={() => setRules(rules.filter((_, j) => j !== i))}>
                    删除
                  </Button>
                </Space>
              ))}
              <div style={{ marginTop: 8 }}>
                <Button
                  size="small"
                  onClick={() => setRules([...rules, { field: undefined, op: 'eq', value: '' }])}
                >
                  添加规则
                </Button>
                <Button
                  type="primary"
                  size="small"
                  style={{ marginLeft: 8 }}
                  onClick={() => saveGrant(role.id)}
                >
                  保存此角色授权
                </Button>
              </div>
            </Card>
          ))}
        </Space>
      </Modal>
    </Card>
  )
}

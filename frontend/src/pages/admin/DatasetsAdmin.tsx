import { useEffect, useState } from 'react'
import { Button, Card, Checkbox, Divider, Form, Input, Modal, Popconfirm, Radio, Select, Space, Table, Tag, Typography, message } from 'antd'
import http from '../../api/http'

const OPS = ['eq', 'ne', 'gt', 'ge', 'lt', 'le', 'like']
const OP_LABEL: Record<string, string> = {
  eq: '等于', ne: '不等于', gt: '大于', ge: '大于等于', lt: '小于', le: '小于等于', like: '包含',
}

export default function DatasetsAdmin() {
  const [datasets, setDatasets] = useState<any[]>([])
  const [roles, setRoles] = useState<any[]>([])
  const [users, setUsers] = useState<any[]>([])
  const [dsOpen, setDsOpen] = useState(false)
  const [grantOpen, setGrantOpen] = useState(false)
  const [grantTarget, setGrantTarget] = useState<any>(null)
  const [grantsList, setGrantsList] = useState<any[]>([])
  const [targetType, setTargetType] = useState<'role' | 'user'>('role')
  const [targetId, setTargetId] = useState<number | undefined>()
  const [whitelist, setWhitelist] = useState<string[]>([])
  const [rules, setRules] = useState<any[]>([])
  const [form] = Form.useForm()
  const [roleOpen, setRoleOpen] = useState(false)
  const [roleName, setRoleName] = useState('')
  const [roleDesc, setRoleDesc] = useState('')
  const [editOpen, setEditOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<any>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editDesc, setEditDesc] = useState('')

  const load = () => {
    http.get('/admin/datasets').then((r) => setDatasets(r.data))
    http.get('/admin/roles').then((r) => setRoles(r.data))
    http.get('/admin/users').then((r) => setUsers(r.data))
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
    setTargetType('role')
    setTargetId(undefined)
    http.get('/admin/grants', { params: { dataset_id: ds.id } }).then((r) => setGrantsList(r.data))
    setGrantOpen(true)
  }

  const saveGrant = async () => {
    if (!targetId) {
      message.warning('请先选择授权对象')
      return
    }
    try {
      const payload: any = {
        dataset_id: grantTarget.id,
        row_rules: rules.filter((r) => r.field && r.value !== undefined),
        field_whitelist: whitelist.length ? whitelist : null,
      }
      if (targetType === 'role') payload.role_id = targetId
      else payload.user_id = targetId
      await http.post('/admin/grants', payload)
      message.success('授权已保存')
      http.get('/admin/grants', { params: { dataset_id: grantTarget.id } }).then((r) => setGrantsList(r.data))
      setWhitelist(grantTarget.columns)
      setRules([])
      setTargetId(undefined)
    } catch (e: any) {
      message.error(e.response?.data?.detail || '保存失败')
    }
  }

  const removeGrant = async (id: number) => {
    try {
      await http.delete(`/admin/grants/${id}`)
      message.success('授权已删除')
      setGrantsList(grantsList.filter((g) => g.id !== id))
    } catch (e: any) {
      message.error(e.response?.data?.detail || '删除失败')
    }
  }

  const openEdit = (ds: any) => {
    setEditTarget(ds)
    setEditTitle(ds.title || '')
    setEditDesc(ds.description || '')
    setEditOpen(true)
  }

  const saveEdit = async () => {
    try {
      await http.patch(`/admin/datasets/${editTarget.id}`, {
        title: editTitle || null,
        description: editDesc || null,
      })
      message.success('已保存')
      setEditOpen(false)
      load()
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
          {
            title: '名称',
            render: (_, r) => (
              <span>
                {r.title || r.name}
                {r.title && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {' '}
                    ({r.name})
                  </Typography.Text>
                )}
              </span>
            ),
          },
          { title: '源表', dataIndex: 'source_table' },
          { title: '字段', dataIndex: 'columns', render: (c) => c?.join('、') },
          {
            title: '操作',
            render: (_, r) => (
              <Space>
                <Button size="small" onClick={() => openEdit(r)}>
                  编辑
                </Button>
                <Button size="small" onClick={() => openGrant(r)}>
                  管理授权
                </Button>
              </Space>
            ),
          },
        ]}
      />
      <Modal
        open={editOpen}
        title="编辑数据集"
        onOk={saveEdit}
        onCancel={() => setEditOpen(false)}
      >
        <Form layout="vertical">
          <Form.Item label="显示名称（中文标题）">
            <Input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
          </Form.Item>
          <Form.Item label="描述">
            <Input value={editDesc} onChange={(e) => setEditDesc(e.target.value)} />
          </Form.Item>
        </Form>
      </Modal>
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
        title={`授权管理：${grantTarget?.name || ''}`}
        footer={null}
        onCancel={() => setGrantOpen(false)}
        width={780}
      >
        <p>当前授权（{grantsList.length} 条）：</p>
        <Table
          size="small"
          rowKey="id"
          dataSource={grantsList}
          pagination={false}
          columns={[
            {
              title: '授权对象',
              dataIndex: 'target',
              render: (t: string) =>
                t.startsWith('user:') ? <Tag color="blue">{t.replace('user:', '用户：')}</Tag> : <Tag color="green">{t.replace('role:', '角色：')}</Tag>,
            },
            {
              title: '可见字段',
              dataIndex: 'field_whitelist',
              render: (f: string[] | null) => (f ? f.join('、') : '全部字段'),
            },
            {
              title: '行级规则',
              dataIndex: 'row_rules',
              render: (rules: any[]) => (rules?.length ? `${rules.length} 条` : '不限'),
            },
            {
              title: '操作',
              render: (_, g) => (
                <Popconfirm title="确认删除该授权？" onConfirm={() => removeGrant(g.id)}>
                  <Button danger size="small">删除</Button>
                </Popconfirm>
              ),
            },
          ]}
        />
        <Divider />
        <p>新增授权：</p>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space>
            <Radio.Group
              value={targetType}
              onChange={(e) => {
                setTargetType(e.target.value)
                setTargetId(undefined)
              }}
              options={[
                { value: 'role', label: '按角色授权' },
                { value: 'user', label: '按账号授权' },
              ]}
              optionType="button"
            />
            <Select
              style={{ width: 220 }}
              placeholder={targetType === 'role' ? '选择角色' : '选择账号'}
              value={targetId}
              onChange={(v) => setTargetId(v)}
              options={(targetType === 'role' ? roles : users).map((x) => ({
                value: x.id,
                label: targetType === 'role' ? `${x.name}（${x.description || '无描述'}）` : `${x.username}（${x.display_name || '无' }）`,
              }))}
              showSearch
              optionFilterProp="label"
            />
          </Space>
          <div>
            <p>字段白名单（不勾选 = 全部字段）：</p>
            <Checkbox.Group
              value={whitelist}
              onChange={(v) => setWhitelist(v as string[])}
              options={grantTarget?.columns || []}
            />
          </div>
          <div>
            <p>行级过滤规则（全部为 AND 关系）：</p>
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
            <Space style={{ marginTop: 8 }}>
              <Button size="small" onClick={() => setRules([...rules, { field: undefined, op: 'eq', value: '' }])}>
                添加规则
              </Button>
              <Button type="primary" onClick={saveGrant}>
                保存授权
              </Button>
            </Space>
          </div>
        </Space>
      </Modal>
    </Card>
  )
}

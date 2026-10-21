import { useEffect, useState } from 'react'
import { Alert, Button, Card, Form, Input, Modal, Space, Switch, Table, Tag, message } from 'antd'
import { createTenant, listTenants, updateTenant, type Tenant } from '../../api/governance'

export default function Tenants() {
  const [rows, setRows] = useState<Tenant[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()
  const user = JSON.parse(localStorage.getItem('user') || 'null')

  const load = async () => {
    setLoading(true)
    try {
      setRows(await listTenants())
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '加载租户失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const submit = async () => {
    const v = await form.validateFields()
    try {
      await createTenant(v)
      message.success('租户已创建')
      setOpen(false)
      form.resetFields()
      void load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '创建失败')
    }
  }

  const toggle = async (row: Tenant, enabled: boolean) => {
    try {
      await updateTenant(row.id, { enabled })
      message.success(enabled ? '已启用' : '已停用')
      void load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '更新失败')
    }
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="租户（组织）隔离"
        description={
          <>
            数据集、用户、授权、流程都归属某个租户，跨租户互不可见。
            默认租户 <Tag>default</Tag> 承接全部存量数据。
            {!user?.is_superadmin && ' 只有平台超管能新建租户。'}
          </>
        }
      />

      <Card
        title="租户列表"
        loading={loading}
        extra={
          <Space>
            <Button onClick={() => void load()}>刷新</Button>
            {user?.is_superadmin && (
              <Button type="primary" onClick={() => { form.resetFields(); setOpen(true) }}>
                新建租户
              </Button>
            )}
          </Space>
        }
      >
        <Table
          rowKey="id"
          dataSource={rows}
          pagination={false}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 80 },
            { title: '名称', dataIndex: 'name' },
            {
              title: 'slug', dataIndex: 'slug', width: 200,
              render: (v: string) => <Tag>{v}</Tag>,
            },
            {
              title: '状态', dataIndex: 'enabled', width: 100,
              render: (v: boolean) => (
                <Tag color={v ? 'success' : 'default'}>{v ? '启用' : '停用'}</Tag>
              ),
            },
            {
              title: '创建时间', dataIndex: 'created_at', width: 180,
              render: (v: string | null) => (v ? new Date(v).toLocaleString() : '-'),
            },
            {
              title: '操作', width: 120,
              render: (_: unknown, row: Tenant) =>
                row.slug === 'default' ? (
                  <span style={{ color: 'var(--neutral-text-tertiary)' }}>不可停用</span>
                ) : (
                  <Switch
                    checked={row.enabled}
                    size="small"
                    disabled={!user?.is_superadmin}
                    onChange={(c) => void toggle(row, c)}
                  />
                ),
            },
          ]}
        />
      </Card>

      <Modal
        title="新建租户"
        open={open}
        onCancel={() => { setOpen(false); form.resetFields() }}
        onOk={() => void submit()}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '填个名字' }]}>
            <Input placeholder="例如：华东事业部" />
          </Form.Item>
          <Form.Item
            name="slug"
            label="slug"
            extra="用于接口头 X-Tenant-Id，只能字母/数字/连字符/下划线"
            rules={[{ required: true, message: '填个 slug' }]}
          >
            <Input placeholder="例如：east-region" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}

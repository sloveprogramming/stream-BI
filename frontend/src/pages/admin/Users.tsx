import { useEffect, useState } from 'react'
import { Button, Card, Form, Input, Modal, Select, Switch, Table, message } from 'antd'
import http from '../../api/http'

export default function Users() {
  const [items, setItems] = useState<any[]>([])
  const [roles, setRoles] = useState<any[]>([])
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const load = () => {
    http.get('/admin/users').then((r) => setItems(r.data))
    http.get('/admin/roles').then((r) => setRoles(r.data))
  }
  useEffect(() => {
    load()
  }, [])

  const create = async () => {
    const v = await form.validateFields()
    try {
      await http.post('/admin/users', v)
      message.success('用户已创建')
      setOpen(false)
      form.resetFields()
      load()
    } catch (e: any) {
      message.error(e.response?.data?.detail || '创建失败')
    }
  }

  return (
    <Card
      title="用户管理"
      extra={<Button type="primary" onClick={() => setOpen(true)}>新建用户</Button>}
    >
      <Table
        rowKey="id"
        dataSource={items}
        columns={[
          { title: 'ID', dataIndex: 'id' },
          { title: '用户名', dataIndex: 'username' },
          { title: '显示名', dataIndex: 'display_name' },
          {
            title: '角色',
            dataIndex: 'role_id',
            render: (rid) => roles.find((r) => r.id === rid)?.name || '-',
          },
          {
            title: '管理员',
            dataIndex: 'is_admin',
            render: (v) => (v ? '是' : '否'),
          },
          {
            title: '状态',
            dataIndex: 'enabled',
            render: (v) => (v ? '启用' : '停用'),
          },
        ]}
      />
      <Modal open={open} title="新建用户" onOk={create} onCancel={() => setOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 6 }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="display_name" label="显示名">
            <Input />
          </Form.Item>
          <Form.Item name="role_id" label="角色">
            <Select allowClear options={roles.map((r) => ({ value: r.id, label: r.name }))} />
          </Form.Item>
          <Form.Item name="is_admin" label="设为管理员" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

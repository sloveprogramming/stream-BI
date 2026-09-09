import { useEffect, useState } from 'react'
import { Button, Card, Input, message, Modal, Popconfirm, Table, Tag } from 'antd'
import http from '../api/http'

export default function MyKeys() {
  const [items, setItems] = useState<any[]>([])
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [created, setCreated] = useState<{ key_id: string; secret: string } | null>(null)

  const load = () => http.get('/my/keys').then((r) => setItems(r.data))
  useEffect(() => {
    load()
  }, [])

  const create = async () => {
    const { data } = await http.post('/my/keys', { name: name || null })
    setCreated(data)
    setOpen(false)
    setName('')
    load()
  }

  const revoke = async (id: number) => {
    await http.post(`/my/keys/${id}/revoke`)
    message.success('已撤销')
    load()
  }

  const copy = (text: string) => {
    navigator.clipboard.writeText(text)
    message.success('已复制')
  }

  return (
    <Card
      title="我的 API Key"
      extra={<Button type="primary" onClick={() => setOpen(true)}>新建 Key</Button>}
    >
      <Table
        rowKey="id"
        dataSource={items}
        columns={[
          { title: 'Key ID', dataIndex: 'key_id' },
          { title: '备注', dataIndex: 'name' },
          {
            title: '状态',
            dataIndex: 'enabled',
            render: (v) => (v ? <Tag color="green">启用</Tag> : <Tag color="red">已撤销</Tag>),
          },
          { title: '创建时间', dataIndex: 'created_at' },
          {
            title: '操作',
            render: (_, r) =>
              r.enabled ? (
                <Popconfirm title="确认撤销？" onConfirm={() => revoke(r.id)}>
                  <Button danger size="small">撤销</Button>
                </Popconfirm>
              ) : null,
          },
        ]}
      />
      <Modal
        open={open}
        title="新建 API Key"
        onOk={create}
        onCancel={() => setOpen(false)}
      >
        <Input placeholder="备注（可选）" value={name} onChange={(e) => setName(e.target.value)} />
      </Modal>
      <Modal
        open={!!created}
        title="请立即保存（仅显示一次）"
        footer={null}
        onCancel={() => setCreated(null)}
      >
        {created && (
          <>
            <p>完整 Key（key_id.secret）：</p>
            <Input.TextArea readOnly value={`${created.key_id}.${created.secret}`} rows={2} />
            <Button
              type="primary"
              style={{ marginTop: 8 }}
              onClick={() => copy(`${created.key_id}.${created.secret}`)}
            >
              复制完整 Key
            </Button>
          </>
        )}
      </Modal>
    </Card>
  )
}

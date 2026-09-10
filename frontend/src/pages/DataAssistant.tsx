import { useRef, useState } from 'react'
import { Alert, Button, Card, Collapse, Input, Space, Spin, Table, Typography, message } from 'antd'
import { SendOutlined, DownloadOutlined } from '@ant-design/icons'

type Msg = {
  role: 'user' | 'assistant'
  text: string
  sql?: string
  columns?: string[]
  rows?: any[][]
  error?: string
  loading?: boolean
}

export default function DataAssistant() {
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)

  const patchLast = (fn: (m: Msg) => Msg) => {
    setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? fn(m) : m)))
  }

  const exportCsv = (m: Msg) => {
    if (!m.columns || !m.rows) return
    const escape = (v: any) => `"${String(v ?? '').replace(/"/g, '""')}"`
    const csv =
      '\ufeff' +
      [m.columns.map(escape).join(','), ...m.rows.map((r) => r.map(escape).join(','))].join('\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'assistant_result.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  const send = async () => {
    const question = input.trim()
    if (!question || busy) return
    setInput('')
    setBusy(true)
    setMessages((prev) => [
      ...prev,
      { role: 'user', text: question },
      { role: 'assistant', text: '', loading: true },
    ])
    try {
      const resp = await fetch('/api/v1/assistant/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('token')}`,
        },
        body: JSON.stringify({ question }),
      })
      if (!resp.body) throw new Error('无响应流')
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      const handle = (ev: any) => {
        if (ev.type === 'sql') patchLast((m) => ({ ...m, sql: ev.sql }))
        else if (ev.type === 'result')
          patchLast((m) => ({ ...m, columns: ev.columns, rows: ev.rows, loading: false }))
        else if (ev.type === 'error') patchLast((m) => ({ ...m, error: ev.message, loading: false }))
        else if (ev.type === 'done') patchLast((m) => ({ ...m, loading: false }))
      }
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const parts = buf.split('\n\n')
        buf = parts.pop() || ''
        for (const part of parts) {
          const line = part.trim().replace(/^data: /, '')
          if (line) handle(JSON.parse(line))
        }
      }
      patchLast((m) => ({ ...m, loading: false }))
    } catch (e: any) {
      message.error('请求失败')
      patchLast((m) => ({ ...m, error: e.message, loading: false }))
    } finally {
      setBusy(false)
      setTimeout(() => listRef.current?.scrollTo(0, listRef.current.scrollHeight), 50)
    }
  }

  return (
    <Card title="数据助手" style={{ height: 'calc(100vh - 160px)', display: 'flex', flexDirection: 'column' }}>
      <div ref={listRef} style={{ flex: 1, overflowY: 'auto', paddingRight: 8 }}>
        {messages.length === 0 && (
          <Typography.Paragraph type="secondary">
            用自然语言提问，例如：“把订单和库存按商品 ID 合并，看各商品的销量和库存”。
          </Typography.Paragraph>
        )}
        <Space direction="vertical" style={{ width: '100%' }}>
          {messages.map((m, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
              <Card size="small" style={{ maxWidth: '85%', background: m.role === 'user' ? '#e6f4ff' : '#fff' }}>
                {m.role === 'user' ? (
                  m.text
                ) : (
                  <>
                    {m.loading && <Spin size="small" />}
                    {m.error && <Alert type="error" message={m.error} showIcon />}
                    {m.sql && (
                      <Collapse
                        size="small"
                        items={[{ key: 'sql', label: '生成的 SQL', children: <pre style={{ whiteSpace: 'pre-wrap' }}>{m.sql}</pre> }]}
                      />
                    )}
                    {m.columns && m.rows && (
                      <>
                        <Table
                          size="small"
                          style={{ marginTop: 8 }}
                          rowKey={(_, idx) => String(idx)}
                          scroll={{ x: 'max-content' }}
                          pagination={{ pageSize: 10 }}
                          dataSource={m.rows.map((r) => Object.fromEntries(m.columns!.map((c, j) => [c, r[j]])))}
                          columns={m.columns.map((c) => ({ title: c, dataIndex: c, key: c, ellipsis: true, width: 150 }))}
                        />
                        <Button size="small" icon={<DownloadOutlined />} style={{ marginTop: 8 }} onClick={() => exportCsv(m)}>
                          导出 CSV（{m.rows.length} 行）
                        </Button>
                      </>
                    )}
                  </>
                )}
              </Card>
            </div>
          ))}
        </Space>
      </div>
      <Space.Compact style={{ width: '100%', marginTop: 16 }}>
        <Input
          placeholder="输入你的问题，例如：按商品统计销量和库存"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onPressEnter={send}
          disabled={busy}
        />
        <Button type="primary" icon={<SendOutlined />} onClick={send} loading={busy}>
          发送
        </Button>
      </Space.Compact>
    </Card>
  )
}

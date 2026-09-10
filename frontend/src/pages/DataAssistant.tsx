import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Collapse, Input, Space, Spin, Table, Typography, message } from 'antd'
import { DownloadOutlined, SendOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons'
import { getFieldLabels, labelOf } from '../api/fieldLabels'

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
  const [labels, setLabels] = useState<Record<string, string>>({})
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getFieldLabels().then(setLabels)
  }, [])

  const patchLast = (fn: (m: Msg) => Msg) => {
    setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? fn(m) : m)))
  }

  const scrollToBottom = () => {
    setTimeout(() => {
      if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
    }, 50)
  }

  const exportCsv = (m: Msg) => {
    if (!m.columns || !m.rows) return
    const escape = (v: any) => `"${String(v ?? '').replace(/"/g, '""')}"`
    const header = m.columns.map((c) => escape(labelOf(labels, c))).join(',')
    const body = m.rows.map((r) => r.map(escape).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob(['\ufeff' + header + '\n' + body], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'assistant_result.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  const buildHistory = (msgs: Msg[]) => {
    const pairs: { question: string; sql: string }[] = []
    for (let i = 0; i < msgs.length - 1; i++) {
      if (msgs[i].role === 'user' && msgs[i + 1].role === 'assistant' && msgs[i + 1].sql) {
        pairs.push({ question: msgs[i].text, sql: msgs[i + 1].sql as string })
      }
    }
    return pairs.slice(-3)
  }

  const send = async () => {
    const question = input.trim()
    if (!question || busy) return
    const history = buildHistory(messages)
    setInput('')
    setBusy(true)
    setMessages((prev) => [
      ...prev,
      { role: 'user', text: question },
      { role: 'assistant', text: '', loading: true },
    ])
    scrollToBottom()
    try {
      const resp = await fetch('/api/v1/assistant/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('token')}`,
        },
        body: JSON.stringify({ question, history }),
      })
      if (resp.status === 401) {
        localStorage.clear()
        location.href = '/login'
        return
      }
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
        scrollToBottom()
      }
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const parts = buf.split('\n\n')
        buf = parts.pop() || ''
        for (const part of parts) {
          const line = part.trim().replace(/^data: /, '')
          if (line) {
            try {
              handle(JSON.parse(line))
            } catch {
              /* 忽略不完整分片 */
            }
          }
        }
      }
      patchLast((m) => ({ ...m, loading: false }))
    } catch (e: any) {
      message.error('请求失败')
      patchLast((m) => ({ ...m, error: e.message, loading: false }))
    } finally {
      setBusy(false)
      scrollToBottom()
    }
  }

  return (
    <Card
      title="数据助手"
      styles={{
        body: { display: 'flex', flexDirection: 'column', height: 'calc(100vh - 220px)', padding: 16 },
      }}
    >
      <div
        ref={listRef}
        style={{ flex: 1, minHeight: 0, overflowY: 'auto', paddingRight: 8, background: '#fafafa', borderRadius: 8, padding: 12 }}
      >
        {messages.length === 0 && (
          <Typography.Paragraph type="secondary">
            用自然语言提问，例如：“把订单和库存按商品 ID 合并，看各商品的销量和库存”。可以连续追问，比如接着问“那按仓库分组呢”。
          </Typography.Paragraph>
        )}
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          {messages.map((m, i) => (
            <div
              key={i}
              style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start' }}
            >
              <Card
                size="small"
                style={{
                  maxWidth: '90%',
                  background: m.role === 'user' ? '#e6f4ff' : '#fff',
                  overflow: 'hidden',
                }}
              >
                <Space size={6} style={{ marginBottom: 4 }}>
                  {m.role === 'user' ? <UserOutlined /> : <RobotOutlined />}
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {m.role === 'user' ? '我' : '数据助手'}
                  </Typography.Text>
                </Space>
                {m.role === 'user' ? (
                  <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.text}</div>
                ) : (
                  <>
                    {m.loading && (
                      <Space>
                        <Spin size="small" />
                        <Typography.Text type="secondary">正在理解问题并查询…</Typography.Text>
                      </Space>
                    )}
                    {m.error && <Alert type="error" message={m.error} showIcon style={{ marginTop: 4 }} />}
                    {m.sql && (
                      <Collapse
                        size="small"
                        style={{ marginTop: 8 }}
                        items={[
                          {
                            key: 'sql',
                            label: '查看生成的 SQL',
                            children: (
                              <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0, fontSize: 12 }}>
                                {m.sql}
                              </pre>
                            ),
                          },
                        ]}
                      />
                    )}
                    {m.columns && m.rows && (
                      <>
                        <Table
                          size="small"
                          style={{ marginTop: 8, maxWidth: '100%' }}
                          rowKey={(_, idx) => String(idx)}
                          scroll={{ x: 'max-content' }}
                          pagination={{ pageSize: 10, size: 'small' }}
                          dataSource={m.rows.map((r) =>
                            Object.fromEntries(m.columns!.map((c, j) => [c, r[j]])),
                          )}
                          columns={m.columns.map((c) => ({
                            title: labelOf(labels, c),
                            dataIndex: c,
                            key: c,
                            ellipsis: true,
                            width: 150,
                          }))}
                        />
                        <Button
                          size="small"
                          icon={<DownloadOutlined />}
                          style={{ marginTop: 8 }}
                          onClick={() => exportCsv(m)}
                        >
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
      <Space.Compact style={{ width: '100%', marginTop: 12 }}>
        <Input
          size="large"
          placeholder="输入你的问题，可连续追问…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onPressEnter={send}
          disabled={busy}
        />
        <Button size="large" type="primary" icon={<SendOutlined />} onClick={send} loading={busy}>
          发送
        </Button>
      </Space.Compact>
    </Card>
  )
}

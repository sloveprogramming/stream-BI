import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Button, Card, Col, Collapse, Empty, Input, List, Popconfirm, Row, Space, Spin,
  Table, Tabs, Tag, Tooltip, Typography, message,
} from 'antd'
import {
  CopyOutlined, DeleteOutlined, DownloadOutlined, PlusOutlined, RobotOutlined,
  SendOutlined, UserOutlined,
} from '@ant-design/icons'
import { getFieldLabels, labelOf } from '../api/fieldLabels'

type Msg = {
  role: 'user' | 'assistant'
  text: string
  sql?: string
  columns?: string[]
  rows?: any[][]
  rowCount?: number
  error?: string
  loading?: boolean
  api?: any
}

const API_KEY_PLACEHOLDER = '<key_id>.<secret>'

/** 用当前站点域名 + 反解出的参数，拼出可直接复制的调用示例。 */
function buildSnippets(api: any): Record<string, string> {
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  const qs = new URLSearchParams(api.params || {}).toString()
  const url = `${origin}${api.endpoint}${qs ? `?${qs}` : ''}`
  return {
    curl: `curl -s "${url}" \\\n  -H "X-API-Key: ${API_KEY_PLACEHOLDER}"`,
    python: `import httpx

resp = httpx.get(
    "${url}",
    headers={"X-API-Key": "${API_KEY_PLACEHOLDER}"},
)
resp.raise_for_status()
data = resp.json()  # {dataset, columns, rows, total}
print(data["total"], data["rows"][:1])`,
    javascript: `const resp = await fetch("${url}", {
  headers: { "X-API-Key": "${API_KEY_PLACEHOLDER}" },
});
const data = await resp.json(); // {dataset, columns, rows, total}
console.log(data.total, data.rows.slice(0, 1));`,
    mcp: `${JSON.stringify(api.mcp, null, 2)}

// 端点：${origin}/mcp   请求头：X-API-Key: ${API_KEY_PLACEHOLDER}`,
  }
}

function CodeBlock({ code }: { code: string }) {
  return (
    <div style={{ position: 'relative' }}>
      <Button
        size="small"
        icon={<CopyOutlined />}
        style={{ position: 'absolute', top: 6, right: 6, zIndex: 1 }}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(code)
            message.success('已复制')
          } catch {
            message.error('复制失败，请手动选择文本')
          }
        }}
      >
        复制
      </Button>
      <pre
        style={{
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
          margin: 0,
          fontSize: 12,
          background: '#f6f6f6',
          padding: 12,
          paddingTop: 34,
          borderRadius: 6,
        }}
      >
        {code}
      </pre>
    </div>
  )
}

type Session = {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  messages: Msg[]
}

const STORE_KEY = 'assistant_sessions_v1'
const ACTIVE_KEY = 'assistant_active_session_v1'
const MAX_SESSIONS = 20
const PERSIST_ROWS = 20

function newId(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

function makeSession(): Session {
  const now = Date.now()
  return { id: newId(), title: '新会话', createdAt: now, updatedAt: now, messages: [] }
}

/** 持久化时裁剪结果行，避免撑爆 localStorage 配额。 */
function slim(sessions: Session[]): Session[] {
  return sessions.slice(0, MAX_SESSIONS).map((s) => ({
    ...s,
    messages: s.messages
      .filter((m) => !m.loading)
      .map((m) =>
        m.rows && m.rows.length > PERSIST_ROWS
          ? { ...m, rows: m.rows.slice(0, PERSIST_ROWS) }
          : m,
      ),
  }))
}

function loadSessions(): Session[] {
  try {
    const raw = localStorage.getItem(STORE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function persistSessions(list: Session[]) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(slim(list)))
  } catch {
    try {
      const noRows = list.map((s) => ({
        ...s,
        messages: s.messages.map((m) => ({ ...m, rows: undefined, columns: undefined })),
      }))
      localStorage.setItem(STORE_KEY, JSON.stringify(noRows.slice(0, MAX_SESSIONS)))
    } catch {
      /* 放弃持久化，不影响当前会话 */
    }
  }
}

export default function DataAssistant() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [activeId, setActiveId] = useState('')
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [labels, setLabels] = useState<Record<string, string>>({})
  const listRef = useRef<HTMLDivElement>(null)

  const active = useMemo(
    () => sessions.find((s) => s.id === activeId) || sessions[0],
    [sessions, activeId],
  )
  const messages = active?.messages || []

  useEffect(() => {
    getFieldLabels().then(setLabels)
    const stored = loadSessions()
    if (stored.length === 0) {
      const s = makeSession()
      setSessions([s])
      setActiveId(s.id)
      return
    }
    const savedActive = localStorage.getItem(ACTIVE_KEY) || ''
    setSessions(stored)
    setActiveId(stored.some((s) => s.id === savedActive) ? savedActive : stored[0].id)
  }, [])

  useEffect(() => {
    if (sessions.length) persistSessions(sessions)
  }, [sessions])

  useEffect(() => {
    if (activeId) localStorage.setItem(ACTIVE_KEY, activeId)
  }, [activeId])

  const updateActive = (fn: (msgs: Msg[]) => Msg[], touchTitle = false) => {
    setSessions((prev) =>
      prev.map((s) => {
        if (s.id !== activeId) return s
        const next = fn(s.messages)
        const firstQ = next.find((m) => m.role === 'user')?.text
        return {
          ...s,
          messages: next,
          title: touchTitle && firstQ ? firstQ.slice(0, 24) : s.title,
          updatedAt: Date.now(),
        }
      }),
    )
  }

  const patchLast = (fn: (m: Msg) => Msg) => {
    updateActive((msgs) => msgs.map((m, i) => (i === msgs.length - 1 ? fn(m) : m)))
  }

  const scrollToBottom = () => {
    setTimeout(() => {
      if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
    }, 50)
  }

  const createSession = () => {
    const s = makeSession()
    setSessions((prev) => [s, ...prev])
    setActiveId(s.id)
    setInput('')
  }

  const removeSession = (id: string) => {
    setSessions((prev) => {
      const next = prev.filter((s) => s.id !== id)
      if (next.length === 0) {
        const s = makeSession()
        setActiveId(s.id)
        return [s]
      }
      if (id === activeId) setActiveId(next[0].id)
      return next
    })
  }

  const copySessionId = async () => {
    if (!active) return
    try {
      await navigator.clipboard.writeText(active.id)
      message.success('会话 ID 已复制')
    } catch {
      message.info(active.id)
    }
  }

  const exportCsv = (m: Msg) => {
    if (!m.columns || !m.rows) return
    const escape = (v: any) => `"${String(v ?? '').replace(/"/g, '""')}"`
    const header = m.columns.map((c) => escape(labelOf(labels, c))).join(',')
    const body = m.rows.map((r) => r.map(escape).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob(['\ufeff' + header + '\n' + body], { type: 'text/csv' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `assistant_${active?.id.slice(0, 8) || 'result'}.csv`
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
    if (!question || busy || !active) return
    const history = buildHistory(messages)
    const sessionId = active.id
    setInput('')
    setBusy(true)
    updateActive(
      (msgs) => [
        ...msgs,
        { role: 'user', text: question },
        { role: 'assistant', text: '', loading: true },
      ],
      true,
    )
    scrollToBottom()
    try {
      const resp = await fetch('/api/v1/assistant/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('token')}`,
        },
        body: JSON.stringify({ question, history, session_id: sessionId }),
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
          patchLast((m) => ({
            ...m,
            columns: ev.columns,
            rows: ev.rows,
            rowCount: ev.row_count ?? ev.rows?.length,
            loading: false,
          }))
        else if (ev.type === 'error') patchLast((m) => ({ ...m, error: ev.message, loading: false }))
        else if (ev.type === 'api') patchLast((m) => ({ ...m, api: ev.api }))
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
      title={
        <Space size={12} wrap>
          <span>数据助手</span>
          {active && (
            <Tooltip title={`会话 ID：${active.id}`}>
              <Tag icon={<CopyOutlined />} color="blue" style={{ cursor: 'pointer' }} onClick={copySessionId}>
                会话 {active.id.slice(0, 8)}
              </Tag>
            </Tooltip>
          )}
        </Space>
      }
      extra={
        <Button icon={<PlusOutlined />} onClick={createSession}>
          新建会话
        </Button>
      }
      styles={{
        body: { display: 'flex', flexDirection: 'column', height: 'calc(100vh - 220px)', padding: 16 },
      }}
    >
      <Row gutter={12} style={{ flex: 1, minHeight: 0 }}>
        <Col xs={0} md={7} lg={6} style={{ height: '100%' }}>
          <div style={{ height: '100%', overflowY: 'auto', borderRight: '1px solid #f0f0f0', paddingRight: 8 }}>
            <List
              size="small"
              dataSource={sessions}
              locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无会话" /> }}
              renderItem={(s) => (
                <List.Item
                  style={{
                    cursor: 'pointer',
                    background: s.id === activeId ? '#e6f4ff' : undefined,
                    borderRadius: 6,
                    paddingInline: 8,
                  }}
                  onClick={() => setActiveId(s.id)}
                  actions={[
                    <Popconfirm
                      key="del"
                      title="删除该会话？"
                      onConfirm={() => removeSession(s.id)}
                      okText="删除"
                      cancelText="取消"
                    >
                      <DeleteOutlined onClick={(e) => e.stopPropagation()} />
                    </Popconfirm>,
                  ]}
                >
                  <List.Item.Meta
                    title={
                      <Typography.Text ellipsis style={{ fontSize: 13 }}>
                        {s.title}
                      </Typography.Text>
                    }
                    description={
                      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                        {new Date(s.updatedAt).toLocaleString()} · {s.messages.filter((m) => m.role === 'user').length} 问
                      </Typography.Text>
                    }
                  />
                </List.Item>
              )}
            />
          </div>
        </Col>
        <Col xs={24} md={17} lg={18} style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
          <div
            ref={listRef}
            style={{ flex: 1, minHeight: 0, overflowY: 'auto', background: '#fafafa', borderRadius: 8, padding: 12 }}
          >
            {messages.length === 0 && (
              <Typography.Paragraph type="secondary">
                用自然语言提问，例如：“把订单和库存按商品 ID 合并，看各商品的销量和库存”。可以连续追问，比如接着问“那按仓库分组呢”。
              </Typography.Paragraph>
            )}
            <Space direction="vertical" style={{ width: '100%' }} size={12}>
              {messages.map((m, i) => (
                <div key={i} style={{ display: 'flex', justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
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
                            {m.rowCount != null && m.rowCount > m.rows.length && (
                              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                历史记录仅保留前 {m.rows.length} 行（共 {m.rowCount} 行）
                              </Typography.Text>
                            )}
                            <Button
                              size="small"
                              icon={<DownloadOutlined />}
                              style={{ marginTop: 8 }}
                              onClick={() => exportCsv(m)}
                            >
                              导出 CSV（{m.rowCount ?? m.rows.length} 行）
                            </Button>
                          </>
                        )}
                        {m.api && m.api.callable && (
                          <Collapse
                            size="small"
                            style={{ marginTop: 8 }}
                            items={[
                              {
                                key: 'api',
                                label: '生成接口（可直接调用）',
                                children: (
                                  <>
                                    <Space size={6} wrap style={{ marginBottom: 8 }}>
                                      <Tag color="green">GET</Tag>
                                      <Typography.Text code style={{ fontSize: 12 }}>
                                        {m.api.endpoint}?
                                        {new URLSearchParams(m.api.params || {}).toString()}
                                      </Typography.Text>
                                    </Space>
                                    <Tabs
                                      size="small"
                                      items={Object.entries(buildSnippets(m.api)).map(
                                        ([key, code]) => ({
                                          key,
                                          label:
                                            {
                                              curl: 'cURL',
                                              python: 'Python',
                                              javascript: 'JavaScript',
                                              mcp: 'MCP',
                                            }[key] || key,
                                          children: <CodeBlock code={code} />,
                                        }),
                                      )}
                                    />
                                    {m.api.caveats?.length > 0 && (
                                      <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
                                        {m.api.caveats.map((c: string, i: number) => (
                                          <li key={i}>
                                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                              {c}
                                            </Typography.Text>
                                          </li>
                                        ))}
                                      </ul>
                                    )}
                                  </>
                                ),
                              },
                            ]}
                          />
                        )}
                        {m.api && !m.api.callable && (
                          <Typography.Text
                            type="secondary"
                            style={{ fontSize: 12, display: 'block', marginTop: 8 }}
                          >
                            本次查询{m.api.reason}，无法直接转成单一数据集接口；可改用 MCP 拆解，
                            或把问题改成「单表 + 等值筛选」的形式。
                          </Typography.Text>
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
        </Col>
      </Row>
    </Card>
  )
}

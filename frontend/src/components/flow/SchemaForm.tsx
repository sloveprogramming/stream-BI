import { useState } from 'react'
import { Button, Form, Input, InputNumber, Select, Switch, Tooltip } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import type { JsonSchema } from '../../api/flows'

/**
 * JSON Schema 驱动的表单。
 *
 * 这是「加一个算子不用改前端」的落地方式：后端的每个算子用 Pydantic 声明参数，
 * `operator_catalog()` 把它的 JSON Schema 吐给前端，这里按类型渲染控件。
 *
 * 覆盖的类型：string / integer / number / boolean / enum / array[string] /
 * array[array]（元组，如 JOIN 条件）/ object（键值对）。
 * 其余复杂结构降级成 JSON 文本框 —— **宁可难看也不要猜错**。
 */

function resolveType(schema: JsonSchema): string {
  if (typeof schema.type === 'string') return schema.type
  if (Array.isArray(schema.type)) {
    return schema.type.find((t) => t !== 'null') ?? 'string'
  }
  const first = schema.anyOf?.find((o) => o.type && o.type !== 'null')
  if (first) return resolveType(first)
  return 'anyOf' in schema ? 'scalar' : 'string'
}

/** 联合类型（str|int|bool|None）没有单一类型，用文本输入 + 智能解析。 */
function isUnion(schema: JsonSchema): boolean {
  return !schema.type && Array.isArray(schema.anyOf)
}

function parseScalar(raw: string): unknown {
  const t = raw.trim()
  if (t === '') return ''
  if (t === 'null') return null
  if (t === 'true') return true
  if (t === 'false') return false
  if (/^-?\d+$/.test(t)) return Number(t)
  if (/^-?\d*\.\d+$/.test(t)) return Number(t)
  return raw
}

function formatScalar(v: unknown): string {
  if (v === null) return 'null'
  if (v === undefined) return ''
  return String(v)
}

// ---------- 具体控件 ----------

function StringField({
  schema,
  value,
  onChange,
}: {
  schema: JsonSchema
  value: unknown
  onChange: (v: unknown) => void
}) {
  const text = value === undefined || value === null ? '' : String(value)
  const long =
    text.length > 60 ||
    /表达式|expr|SQL|条件|说明|提示/.test(schema.description ?? '') ||
    /expr|instructions|criteria|description/.test(schema.title ?? '')
  if (long) {
    return (
      <Input.TextArea
        value={text}
        rows={3}
        autoSize={{ minRows: 2, maxRows: 6 }}
        onChange={(e) => onChange(e.target.value)}
      />
    )
  }
  return <Input value={text} onChange={(e) => onChange(e.target.value)} />
}

function TupleListField({
  value,
  arity,
  onChange,
}: {
  value: unknown
  arity: number
  onChange: (v: unknown) => void
}) {
  const rows = (Array.isArray(value) ? value : []) as unknown[][]
  const setRow = (ri: number, ci: number, v: string) => {
    const next = rows.map((r, i) => (i === ri ? r.map((c, j) => (j === ci ? v : c)) : r))
    onChange(next)
  }
  const blank = () => Array.from({ length: arity }, () => '')
  return (
    <div>
      {rows.map((row, ri) => (
        <div key={ri} style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
          {Array.from({ length: arity }).map((_, ci) => (
            <Input
              key={ci}
              size="small"
              placeholder={ci === 0 ? '左' : '右'}
              value={String(row[ci] ?? '')}
              onChange={(e) => setRow(ri, ci, e.target.value)}
            />
          ))}
          <Button
            size="small"
            type="text"
            danger
            icon={<DeleteOutlined />}
            onClick={() => onChange(rows.filter((_, i) => i !== ri))}
          />
        </div>
      ))}
      <Button size="small" icon={<PlusOutlined />} onClick={() => onChange([...rows, blank()])}>
        加一行
      </Button>
    </div>
  )
}

function ArrayField({
  schema,
  value,
  onChange,
}: {
  schema: JsonSchema
  value: unknown
  onChange: (v: unknown) => void
}) {
  const items = schema.items ?? {}
  if (items.type === 'array' || items.prefixItems) {
    const arity = items.prefixItems?.length ?? items.minItems ?? 2
    return <TupleListField value={value} arity={arity} onChange={onChange} />
  }
  if (!items.type || items.type === 'string') {
    return (
      <Select
        mode="tags"
        value={(Array.isArray(value) ? value : []) as string[]}
        onChange={onChange}
        placeholder="输入后回车"
        tokenSeparators={[',', '，']}
        open={false}
      />
    )
  }
  return <JsonField value={value} onChange={onChange} />
}

function KVField({
  schema,
  value,
  onChange,
}: {
  schema: JsonSchema
  value: unknown
  onChange: (v: unknown) => void
}) {
  const entries = Object.entries((value as Record<string, unknown>) ?? {})
  const valueSchema = (schema.additionalProperties as JsonSchema) ?? {}
  const valueIsUnion = isUnion(valueSchema)
  const valueIsNumber = valueSchema.type === 'integer' || valueSchema.type === 'number'
  const valueIsBool = valueSchema.type === 'boolean'

  const commit = (rows: [string, unknown][]) => {
    const out: Record<string, unknown> = {}
    rows.forEach(([k, v]) => {
      if (k !== '') out[k] = v
    })
    onChange(out)
  }
  const setAt = (i: number, k: string, v: unknown) => {
    const rows = entries.map(([ek, ev], idx) => (idx === i ? [k, v] : [ek, ev])) as [
      string,
      unknown,
    ][]
    commit(rows)
  }

  return (
    <div>
      {entries.map(([k, v], i) => (
        <div key={`${k}-${i}`} style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
          <Input
            size="small"
            style={{ flex: 1 }}
            placeholder="列名"
            value={k}
            onChange={(e) => setAt(i, e.target.value, v)}
          />
          {valueIsBool ? (
            <Switch checked={!!v} onChange={(checked) => setAt(i, k, checked)} />
          ) : valueIsNumber ? (
            <InputNumber
              size="small"
              style={{ flex: 1 }}
              value={v as number}
              onChange={(n) => setAt(i, k, n)}
            />
          ) : (
            <Input
              size="small"
              style={{ flex: 1 }}
              placeholder={valueIsUnion ? '值（数字/文本/true/false）' : '值'}
              value={formatScalar(v)}
              onChange={(e) => setAt(i, k, valueIsUnion ? parseScalar(e.target.value) : e.target.value)}
            />
          )}
          <Button
            size="small"
            type="text"
            danger
            icon={<DeleteOutlined />}
            onClick={() => commit(entries.filter((_, idx) => idx !== i) as [string, unknown][])}
          />
        </div>
      ))}
      <Button
        size="small"
        icon={<PlusOutlined />}
        onClick={() => onChange({ ...((value as Record<string, unknown>) ?? {}), '': '' })}
      >
        加一项
      </Button>
    </div>
  )
}

/** 兜底：复杂结构直接编辑 JSON。改了才算，改坏了不会污染上层。 */
function JsonField({ value, onChange }: { value: unknown; onChange: (v: unknown) => void }) {
  const [text, setText] = useState(() => JSON.stringify(value ?? null, null, 2))
  const [bad, setBad] = useState(false)
  return (
    <Tooltip title={bad ? 'JSON 格式有误，尚未生效' : ''} open={bad}>
      <Input.TextArea
        value={text}
        rows={4}
        status={bad ? 'error' : undefined}
        onChange={(e) => {
          setText(e.target.value)
          try {
            onChange(JSON.parse(e.target.value))
            setBad(false)
          } catch {
            setBad(true)
          }
        }}
      />
    </Tooltip>
  )
}

function Field({
  schema,
  value,
  onChange,
}: {
  schema: JsonSchema
  value: unknown
  onChange: (v: unknown) => void
}) {
  if (schema.enum) {
    const options = schema.enum.map((v) => ({ value: String(v), label: String(v) }))
    return (
      <Select
        value={value === undefined ? undefined : String(value)}
        options={options}
        onChange={(v) => {
          const original = schema.enum?.find((x) => String(x) === v)
          onChange(original ?? v)
        }}
      />
    )
  }

  if (isUnion(schema)) {
    return (
      <StringField
        schema={schema}
        value={value}
        onChange={(v) => onChange(parseScalar(String(v ?? '')))}
      />
    )
  }

  switch (resolveType(schema)) {
    case 'boolean':
      return <Switch checked={!!value} onChange={onChange} />
    case 'integer':
    case 'number':
      return (
        <InputNumber
          style={{ width: '100%' }}
          min={schema.minimum}
          max={schema.maximum}
          value={value as number}
          onChange={(v) => onChange(v)}
        />
      )
    case 'array':
      return <ArrayField schema={schema} value={value} onChange={onChange} />
    case 'object':
      if (schema.additionalProperties && typeof schema.additionalProperties === 'object') {
        return <KVField schema={schema} value={value} onChange={onChange} />
      }
      return <JsonField value={value} onChange={onChange} />
    default:
      return <StringField schema={schema} value={value} onChange={onChange} />
  }
}

export default function SchemaForm({
  schema,
  value,
  onChange,
}: {
  schema: JsonSchema
  value: Record<string, unknown>
  onChange: (v: Record<string, unknown>) => void
}) {
  const properties = schema.properties ?? {}
  const required = new Set(schema.required ?? [])
  const keys = Object.keys(properties)

  if (!keys.length) {
    return <span style={{ color: '#999' }}>这个算子没有可配置参数</span>
  }

  return (
    <Form layout="vertical" size="small">
      {keys.map((key) => {
        const prop = properties[key]
        return (
          <Form.Item
            key={key}
            style={{ marginBottom: 14 }}
            label={
              <span>
                <code style={{ fontSize: 12 }}>{key}</code>
                {required.has(key) && <span style={{ color: '#ff4d4f' }}> *</span>}
              </span>
            }
            help={prop.description ? <span style={{ fontSize: 12 }}>{prop.description}</span> : undefined}
          >
            <Field
              schema={prop}
              value={value?.[key]}
              onChange={(v) => onChange({ ...(value ?? {}), [key]: v })}
            />
          </Form.Item>
        )
      })}
    </Form>
  )
}

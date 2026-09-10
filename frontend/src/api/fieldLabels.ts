import http from './http'

let cache: Record<string, string> | null = null
let pending: Promise<Record<string, string>> | null = null

export async function getFieldLabels(): Promise<Record<string, string>> {
  if (cache) return cache
  if (!pending) {
    pending = http
      .get('/meta/field-labels')
      .then((r) => {
        cache = r.data
        return r.data as Record<string, string>
      })
      .catch(() => ({}))
      .finally(() => {
        pending = null
      })
  }
  return pending
}

export function labelOf(labels: Record<string, string>, field: string): string {
  return labels[field] || field
}

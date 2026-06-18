// Thin wrapper around the backend API. All network access goes through here so
// it's easy to find and mock. In dev, Vite proxies /api to the FastAPI backend.

const BASE = '/api'

async function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1])
    reader.onerror = () => reject(new Error('Could not read file'))
    reader.readAsDataURL(file)
  })
}

export async function extractDocuments(files) {
  const docs = await Promise.all(
    Array.from(files).map(async (f) => ({
      filename: f.name,
      mime: f.type || 'application/octet-stream',
      b64: await fileToBase64(f),
    }))
  )
  const res = await fetch(`${BASE}/extract`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(docs),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Extraction failed (${res.status})`)
  }
  return res.json()
}

export async function memoHtml(terms, extraction) {
  const res = await fetch(`${BASE}/memo/html`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ terms, extraction }),
  })
  if (!res.ok) throw new Error(`Memo render failed (${res.status})`)
  return res.text()
}

async function downloadBlob(path, terms, extraction, filename) {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ terms, extraction }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Export failed (${res.status})`)
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export function downloadPdf(terms, extraction) {
  const name = (terms.name || 'Borrower').replace(/\s+/g, '_')
  return downloadBlob('/memo/pdf', terms, extraction, `Credit_Memorandum_${name}.pdf`)
}

export function downloadWord(terms, extraction) {
  const name = (terms.name || 'Borrower').replace(/\s+/g, '_')
  return downloadBlob('/memo/word', terms, extraction, `Credit_Memorandum_${name}.doc`)
}

export async function health() {
  const res = await fetch(`${BASE}/health`)
  return res.json()
}

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

// --- Participation Agreement Builder ---------------------------------------

export async function paExtract(files) {
  const docs = await Promise.all(
    Array.from(files).map(async (f) => ({
      filename: f.name,
      mime: f.type || 'application/octet-stream',
      b64: await fileToBase64(f),
    }))
  )
  const res = await fetch(`${BASE}/pa/extract`, {
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

// Parse a Participant Breakdown .xlsx -> { deal, participants }.
export async function paBreakdown(files) {
  const docs = await Promise.all(
    Array.from(files).map(async (f) => ({
      filename: f.name,
      mime: f.type || 'application/octet-stream',
      b64: await fileToBase64(f),
    }))
  )
  const res = await fetch(`${BASE}/pa/breakdown`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(docs),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Could not read breakdown (${res.status})`)
  }
  return res.json()
}

// Returns the response blob for a generated agreement, or throws with the
// backend's error detail (e.g. PDF requested but LibreOffice missing).
async function paBlob(path, terms, agreementType) {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ terms, agreement_type: agreementType }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Generation failed (${res.status})`)
  }
  return res.blob()
}

export function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

function paFilename(terms, ext) {
  const base = (terms.borrower_name || 'Participation').replace(/[^\w.-]+/g, '_').replace(/^_+|_+$/g, '')
  return `Participation_Agreement_${base || 'Participation'}.${ext}`
}

export async function paDownloadDocx(terms, agreementType) {
  const blob = await paBlob('/pa/docx', terms, agreementType)
  triggerDownload(blob, paFilename(terms, 'docx'))
}

export async function paDownloadPdf(terms, agreementType) {
  const blob = await paBlob('/pa/pdf', terms, agreementType)
  triggerDownload(blob, paFilename(terms, 'pdf'))
}

// Generate the PDF and return an object URL for inline preview (caller revokes).
export async function paPreviewPdf(terms, agreementType) {
  const blob = await paBlob('/pa/pdf', terms, agreementType)
  return URL.createObjectURL(blob)
}

// --- Loan Documents Builder --------------------------------------------------

export async function loanDocsDefaults() {
  const res = await fetch(`${BASE}/loandocs/defaults`)
  if (!res.ok) return {}
  return res.json()
}

// Extract Team & Contract fields from an uploaded player contract / deal docs.
export async function loanDocsExtract(files) {
  const docs = await Promise.all(
    Array.from(files).map(async (f) => ({
      filename: f.name,
      mime: f.type || 'application/octet-stream',
      b64: await fileToBase64(f),
    }))
  )
  const res = await fetch(`${BASE}/loandocs/extract`, {
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

// Read a previously generated credit memo -> deal-level fields.
export async function loanDocsReadMemo(files) {
  const docs = await Promise.all(
    Array.from(files).map(async (f) => ({
      filename: f.name,
      mime: f.type || 'application/octet-stream',
      b64: await fileToBase64(f),
    }))
  )
  const res = await fetch(`${BASE}/loandocs/memo`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(docs),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Could not read the memo (${res.status})`)
  }
  return res.json()
}

// Parse a Balloon / Fully Amortized workbook -> settlement lines + schedule.
export async function loanDocsSettlement(files) {
  const docs = await Promise.all(
    Array.from(files).map(async (f) => ({
      filename: f.name,
      mime: f.type || 'application/octet-stream',
      b64: await fileToBase64(f),
    }))
  )
  const res = await fetch(`${BASE}/loandocs/settlement`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(docs),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Could not read the workbook (${res.status})`)
  }
  return res.json()
}

export async function loanDocsHtml(terms, include) {
  const res = await fetch(`${BASE}/loandocs/html`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ terms, include }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Loan documents render failed (${res.status})`)
  }
  return res.text()
}

async function loanDocsBlob(path, terms, include) {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ terms, include }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Export failed (${res.status})`)
  }
  return res.blob()
}

function loanDocsFilename(terms, ext) {
  const base = (terms.borrower_name || 'Borrower').replace(/[^\w.-]+/g, '_').replace(/^_+|_+$/g, '')
  return `Loan_Documents_${base || 'Borrower'}.${ext}`
}

export async function loanDocsDownloadPdf(terms, include) {
  const blob = await loanDocsBlob('/loandocs/pdf', terms, include)
  triggerDownload(blob, loanDocsFilename(terms, 'pdf'))
}

export async function loanDocsDownloadWord(terms, include) {
  const blob = await loanDocsBlob('/loandocs/word', terms, include)
  triggerDownload(blob, loanDocsFilename(terms, 'doc'))
}

// --- Closing Binder -----------------------------------------------------------

// Read deal documents (credit memo, loan docs, term sheet) -> binder cover fields.
export async function binderExtract(files) {
  const docs = await Promise.all(
    Array.from(files).map(async (f) => ({
      filename: f.name,
      mime: f.type || 'application/octet-stream',
      b64: await fileToBase64(f),
    }))
  )
  const res = await fetch(`${BASE}/binder/extract`, {
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

// Sort the signed closing package (+ insurance PDFs) into binder sections.
export async function binderSort(files) {
  const docs = await Promise.all(
    Array.from(files).map(async (f) => ({
      filename: f.name,
      mime: f.type || 'application/pdf',
      b64: await fileToBase64(f),
    }))
  )
  const res = await fetch(`${BASE}/binder/sort`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(docs),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Sorting failed (${res.status})`)
  }
  return res.json()
}

// Merge the sections into one indexed binder; returns the PDF blob so the
// caller can both preview it and save it without generating twice. Each
// section is a list of parts {file, from, to} — a file is base64-encoded
// only once however many sections it was split into.
export async function binderPdf(info, docs, tabPages) {
  const encoded = new Map()
  const enc = (file) => {
    if (!encoded.has(file)) encoded.set(file, fileToBase64(file))
    return encoded.get(file)
  }
  const documents = await Promise.all(
    docs.map(async (d) => ({
      title: d.title,
      parts: await Promise.all(
        d.parts.map(async (p) => ({
          filename: p.file.name,
          mime: p.file.type || 'application/pdf',
          b64: await enc(p.file),
          page_from: p.from || null,
          page_to: p.to || null,
        }))
      ),
    }))
  )
  const res = await fetch(`${BASE}/binder/pdf`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ info, documents, tab_pages: tabPages }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `Binder generation failed (${res.status})`)
  }
  return res.blob()
}

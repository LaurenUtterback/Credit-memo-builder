<script setup>
import { ref, reactive } from 'vue'
import { binderPdf, triggerDownload } from './lib/api.js'

// Deal terms from the Credit Memo tab, for "Pull deal info".
const props = defineProps({ memoTerms: Object, memoExtraction: Object })

const info = reactive({
  borrower_name: '', loan_amount: null, loan_number: '', closing_date: '',
})
const docs = ref([]) // { file, title } — binder order, top to bottom
const tabPages = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const previewUrl = ref('')
let pdfBlob = null

function pullFromMemo() {
  const t = props.memoTerms || {}
  if (t.name) info.borrower_name = t.name
  if (t.loan) info.loan_amount = t.loan
  if (t.fund) info.closing_date = t.fund
}

// "Loan_and_Security_Agreement (executed).pdf" -> an editable default title
function titleFrom(name) {
  return name.replace(/\.[^.]+$/, '').replace(/_+/g, ' ').replace(/\s+/g, ' ').trim()
}

function onFiles(e) {
  notice.value = ''
  const skipped = []
  const seen = new Set(docs.value.map((d) => d.file.name + ':' + d.file.size))
  for (const f of Array.from(e.target.files)) {
    const isPdf = f.type === 'application/pdf' || /\.pdf$/i.test(f.name)
    if (!isPdf) { skipped.push(f.name); continue }
    const key = f.name + ':' + f.size
    if (!seen.has(key)) { docs.value.push({ file: f, title: titleFrom(f.name) }); seen.add(key) }
  }
  if (skipped.length) {
    notice.value = `Skipped (not a PDF): ${skipped.join(', ')} — export or scan to PDF first.`
  }
  e.target.value = ''
}

function move(i, delta) {
  const j = i + delta
  if (j < 0 || j >= docs.value.length) return
  const a = docs.value
  ;[a[i], a[j]] = [a[j], a[i]]
}

function removeDoc(i) { docs.value.splice(i, 1) }

async function generate() {
  error.value = ''
  busy.value = true
  try {
    const payload = {
      ...info,
      loan_amount: Number(info.loan_amount) || null,
      closing_date: info.closing_date || null,
    }
    pdfBlob = await binderPdf(payload, docs.value, tabPages.value)
    if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = URL.createObjectURL(pdfBlob)
  } catch (err) {
    error.value = err.message
  }
  busy.value = false
}

function download() {
  if (!pdfBlob) return
  const base = (info.borrower_name || 'Borrower').replace(/[^\w.-]+/g, '_').replace(/^_+|_+$/g, '')
  triggerDownload(pdfBlob, `Closing_Binder_${base || 'Borrower'}.pdf`)
}
</script>

<template>
  <section class="card">
    <h2><span class="step">1</span> Binder information</h2>
    <button type="button" class="ghost" style="margin-top:0" @click="pullFromMemo">↩ Pull deal info from Credit Memo</button>
    <div class="grid" style="margin-top:12px">
      <label>Borrower name <input v-model="info.borrower_name" /></label>
      <label>Loan amount <input v-model.number="info.loan_amount" type="number" /></label>
      <label>Loan number <input v-model="info.loan_number" /></label>
      <label>Closing date <input v-model="info.closing_date" type="date" /></label>
    </div>
  </section>

  <section class="card">
    <h2><span class="step">2</span> Add the executed documents (PDF)</h2>
    <input type="file" multiple accept="application/pdf,.pdf" @change="onFiles" />
    <p class="hint">Add the signed PDFs in binder order — reorder below. Each becomes a section in the clickable Table of Contents; the titles are editable.</p>
    <p v-if="notice" class="status err">⚠ {{ notice }}</p>
    <ul v-if="docs.length" class="filelist">
      <li v-for="(d, i) in docs" :key="d.file.name + d.file.size">
        <span class="tabno">{{ i + 1 }}.</span>
        <input class="title-in" v-model="d.title" title="Shown in the Table of Contents and on the section title page" />
        <span class="fname hint">{{ d.file.name }}</span>
        <span class="ord">
          <button type="button" class="mini" :disabled="i === 0" @click="move(i, -1)" title="Move up">↑</button>
          <button type="button" class="mini" :disabled="i === docs.length - 1" @click="move(i, 1)" title="Move down">↓</button>
          <button type="button" class="rm" @click="removeDoc(i)" title="Remove">✕</button>
        </span>
      </li>
    </ul>
    <label class="chk"><input type="checkbox" v-model="tabPages" /> Add a title page in front of each document</label>
  </section>

  <section class="card">
    <h2><span class="step">3</span> Generate binder</h2>
    <p v-if="error" class="status err">⚠ {{ error }}</p>
    <button :disabled="!docs.length || busy" @click="generate">
      {{ busy ? 'Assembling…' : 'Generate closing binder' }}
    </button>
    <button v-if="previewUrl" class="ghost" @click="download">📕 Download PDF</button>
  </section>

  <section v-if="previewUrl" class="card">
    <h2>Preview</h2>
    <iframe :src="previewUrl" class="preview" title="Closing binder preview"></iframe>
  </section>
</template>

<style scoped>
.chk { flex-direction: row; align-items: center; gap: 6px; margin-top: 12px; }
.chk input { width: auto; }
.tabno { font-weight: 700; color: var(--navy); white-space: nowrap; font-size: 12px; }
.title-in { flex: 1; min-width: 140px; }
.filelist .fname { max-width: 220px; }
.ord { display: flex; gap: 3px; align-items: center; }
button.mini { background: #fff; color: var(--navy); border: 1px solid #cdd3da; border-radius: 5px; padding: 2px 7px; margin: 0; font-size: 12px; }
button.mini:disabled { opacity: .35; }
</style>

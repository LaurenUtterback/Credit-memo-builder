<script setup>
import { ref, reactive, computed } from 'vue'
import { extractDocuments, memoHtml, downloadPdf, downloadWord } from './lib/api.js'

// --- state -----------------------------------------------------------------
const files = ref([])
const extracting = ref(false)
const status = reactive({ type: '', msg: '' })
const extraction = ref(null)

const terms = reactive({
  name: '', dob: '', addr: '', phone: '', team: '', league: '', sport: '',
  ssn: '', dl: '', agent: '',
  loan: null, rate: null, fee: null, salary: null,
  fund: '', mat: '', loan_type: 'Single-Pay Balloon',
})

const memoReady = ref(false)
const memoHtmlContent = ref('')
const genError = ref('')

// --- derived ---------------------------------------------------------------
const canGenerate = computed(() => terms.loan && terms.salary)

// --- handlers --------------------------------------------------------------
function onFiles(e) {
  files.value = Array.from(e.target.files)
}

async function runExtract() {
  if (!files.value.length) return
  extracting.value = true
  status.type = 'info'
  status.msg = `Analyzing ${files.value.length} document(s) with Claude…`
  try {
    const ed = await extractDocuments(files.value)
    extraction.value = ed
    // auto-fill empty fields from the extraction
    const map = {
      name: ed.borrower_name, dob: ed.dob, addr: ed.address, phone: ed.phone,
      team: ed.team, league: ed.league, sport: ed.sport, ssn: ed.ssn_masked,
      dl: ed.drivers_license, agent: ed.agent,
    }
    for (const [k, v] of Object.entries(map)) if (v && !terms[k]) terms[k] = v
    if (ed.salary && !terms.salary) terms.salary = ed.salary
    status.type = 'ok'
    status.msg = '✓ Extracted — confirm deal terms and generate'
  } catch (err) {
    status.type = 'err'
    status.msg = 'Extraction failed: ' + err.message
  }
  extracting.value = false
}

function buildTermsPayload() {
  // normalize empty date strings to null for the backend
  return {
    ...terms,
    loan: Number(terms.loan) || 0,
    rate: Number(terms.rate) || 0,
    fee: Number(terms.fee) || 0,
    salary: Number(terms.salary) || 0,
    fund: terms.fund || null,
    mat: terms.mat || null,
  }
}

async function generate() {
  genError.value = ''
  try {
    memoHtmlContent.value = await memoHtml(buildTermsPayload(), extraction.value)
    memoReady.value = true
  } catch (err) {
    genError.value = err.message
  }
}

async function exportPdf() {
  try { await downloadPdf(buildTermsPayload(), extraction.value) }
  catch (err) { genError.value = err.message }
}
async function exportWord() {
  try { await downloadWord(buildTermsPayload(), extraction.value) }
  catch (err) { genError.value = err.message }
}
</script>

<template>
  <div class="wrap">
    <header class="masthead">
      <div>
        <div class="brand">South River Capital</div>
        <div class="tag">Credit Memorandum Builder</div>
      </div>
    </header>

    <!-- Step 1: upload + extract -->
    <section class="card">
      <h2><span class="step">1</span> Upload deal documents</h2>
      <input type="file" multiple @change="onFiles" />
      <p v-if="files.length" class="hint">{{ files.length }} file(s) selected</p>
      <button :disabled="!files.length || extracting" @click="runExtract">
        {{ extracting ? 'Analyzing…' : 'Extract with Claude' }}
      </button>
      <p v-if="status.msg" :class="['status', status.type]">{{ status.msg }}</p>
    </section>

    <!-- Step 2: confirm terms -->
    <section class="card">
      <h2><span class="step">2</span> Confirm deal terms</h2>
      <div class="grid">
        <label>Borrower name <input v-model="terms.name" /></label>
        <label>Team <input v-model="terms.team" /></label>
        <label>League <input v-model="terms.league" /></label>
        <label>Sport <input v-model="terms.sport" /></label>
        <label>Guaranteed salary <input v-model.number="terms.salary" type="number" /></label>
        <label>Loan amount <input v-model.number="terms.loan" type="number" /></label>
        <label>Rate (% p.a.) <input v-model.number="terms.rate" type="number" step="0.01" /></label>
        <label>Origination fee (%) <input v-model.number="terms.fee" type="number" step="0.01" /></label>
        <label>Funding date <input v-model="terms.fund" type="date" /></label>
        <label>Maturity date <input v-model="terms.mat" type="date" /></label>
        <label>Tax ID (last 4) <input v-model="terms.ssn" placeholder="XXX-XX-1234" /></label>
        <label>Agent <input v-model="terms.agent" /></label>
      </div>
    </section>

    <!-- Step 3: generate -->
    <section class="card">
      <h2><span class="step">3</span> Generate memo</h2>
      <p v-if="genError" class="status err">⚠ {{ genError }}</p>
      <button :disabled="!canGenerate" @click="generate">Generate credit memo</button>
      <template v-if="memoReady">
        <button class="ghost" @click="exportPdf">📕 PDF</button>
        <button class="ghost" @click="exportWord">📄 Word</button>
      </template>
    </section>

    <section v-if="memoReady" class="card">
      <h2>Preview</h2>
      <iframe :srcdoc="memoHtmlContent" class="preview" title="Credit memo preview"></iframe>
    </section>
  </div>
</template>

<style>
:root { --navy: #0f2a43; --gold: #b9952b; }
body { margin: 0; background: #eceae3; font-family: system-ui, sans-serif; color: #1a1a1a; }
.wrap { max-width: 1000px; margin: 0 auto; padding: 20px; }
.masthead { border-bottom: 3px solid var(--navy); padding-bottom: 12px; margin-bottom: 18px; }
.brand { font-size: 20px; font-weight: 700; color: var(--navy); letter-spacing: .04em; text-transform: uppercase; }
.tag { font-size: 11px; letter-spacing: .25em; color: var(--gold); text-transform: uppercase; margin-top: 4px; }
.card { background: #fff; border: 1px solid #ddd; border-radius: 10px; padding: 16px 18px; margin-bottom: 14px; }
.card h2 { font-size: 14px; margin: 0 0 12px; display: flex; align-items: center; gap: 8px; }
.step { background: var(--navy); color: #fff; width: 22px; height: 22px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-size: 12px; }
.grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
label { display: flex; flex-direction: column; font-size: 12px; gap: 4px; color: #555; }
input { padding: 6px 8px; border: 1px solid #ccc; border-radius: 6px; font-size: 13px; }
button { background: var(--navy); color: #fff; border: 0; border-radius: 6px; padding: 8px 14px; font-size: 13px; font-weight: 600; cursor: pointer; margin-right: 8px; margin-top: 8px; }
button:disabled { opacity: .5; cursor: not-allowed; }
button.ghost { background: #fff; color: var(--navy); border: 1px solid var(--navy); }
.hint { font-size: 12px; color: #888; }
.status { font-size: 12px; padding: 8px 10px; border-radius: 6px; margin-top: 10px; }
.status.info { background: #e6f1fb; color: #0c447c; }
.status.ok { background: #e8f5ee; color: #0f6e56; }
.status.err { background: #fdecea; color: #b00020; }
.preview { width: 100%; height: 720px; border: 1px solid #ddd; border-radius: 6px; }
@media (max-width: 640px) { .grid { grid-template-columns: 1fr; } }
</style>

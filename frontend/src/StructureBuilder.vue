<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { structureCadences, structureCadence, structureExtract, structurePropose, structureSelect, structureSummaryPdf, structureProposeTerms, structureDebtService } from './lib/api.js'

// Deal terms/extraction confirmed on the Credit Memo tab, plus the App-owned
// Loan Documents store this tab pushes the SELECTED structure into.
const props = defineProps({
  memoTerms: { type: Object, default: () => ({}) },
  memoExtraction: { type: Object, default: null },
  loanDocsTerms: { type: Object, default: null },
  // The App-owned deal-document list, shared with the Credit Memo tab: a deal
  // is uploaded ONCE, here, and the memo extracts from the same files.
  dealFiles: { type: Array, default: null },
})

// --- state -----------------------------------------------------------------
const inputs = reactive({
  borrower_name: '', league: '', team: '',
  salary: null, other_income: null, other_debt_annual: null,
  contract_end: '', salary_guaranteed: true,
  cadence: null,
  bonus_events: [],
  loan_amount: null, interest_rate: null, origination_fee_pct: null,
  funding_date: '', target_term_months: 12,
  expected_exit_date: '', expected_exit_label: '',
  agent_pct: 0, min_coverage: 1.25,
  presented_type: '', presented_term_months: null,
})

// Document upload — this is the primary way the tab is filled. Everything below
// is editable afterwards, but nothing should have to be typed from scratch.
// Backed by the App-owned shared list when present, so these same documents are
// already waiting on the Credit Memo tab (mutated in place — never reassigned,
// which would break the parent's reference).
const localFiles = ref([])
const files = computed(() => props.dealFiles || localFiles.value)
const reading = ref(false)
const readStatus = reactive({ type: '', msg: '' })
const extractNotes = ref('')
const memoPush = ref('')
// Maturity as presented in the documents — carried to the memo's terms grid.
const presentedMaturity = ref('')

const cadences = ref([])
const cadence = reactive({})          // the editable cadence for this deal
const result = ref(null)
const running = ref(false)
const status = reactive({ type: '', msg: '' })
const pullMsg = ref('')

// Selection is the gate: a candidate is chosen, its rows are shown for review,
// and only then can it be applied to the Loan Documents tab.
const selectedKey = ref('')
const pendingPush = ref(null)
const applied = ref('')

const exporting = ref(false)
const exportErr = ref('')

// Proposed terms — how much the contract can carry, and whether it can be
// repaid. Kept separate from `inputs` so the underwriter sees the proposal
// before it overwrites anything they typed.
const terms = ref(null)
const proposing = ref(false)
const termsErr = ref('')
const contractRemaining = ref(null)
const pfsNote = ref('')

const FREQ_LABELS = {
  weekly: 'Weekly', semimonthly: 'Semi-monthly (1st & 15th)', monthly: 'Monthly',
}
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

onMounted(async () => {
  cadences.value = await structureCadences()
})

// --- derived ---------------------------------------------------------------
const hasMemo = computed(() => {
  const t = props.memoTerms || {}
  return !!(t.name || t.loan)
})

const canRun = computed(() => Number(inputs.loan_amount) > 0)

const selected = computed(
  () => (result.value?.candidates || []).find((c) => c.key === selectedKey.value) || null
)

// Scale for the cash-flow bars — the shape of the year is the whole point, so
// it is drawn, not just tabulated.
const flowScale = computed(() => {
  const rows = result.value?.cash_flow || []
  return Math.max(1, ...rows.map((m) => Math.abs(m.available)))
})

function money(n, dp = 0) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD',
    minimumFractionDigits: dp, maximumFractionDigits: dp })
}

function pct(n) {
  return n === null || n === undefined ? '—' : `${n.toFixed(2)}x`
}

// --- pull from the Credit Memo tab -----------------------------------------
async function pullFromMemo() {
  const t = props.memoTerms || {}
  const ed = props.memoExtraction || {}

  if (t.name) inputs.borrower_name = t.name
  if (t.team) inputs.team = t.team
  if (t.league) inputs.league = t.league
  if (t.salary) inputs.salary = t.salary
  if (t.loan) inputs.loan_amount = t.loan
  if (t.rate) inputs.interest_rate = t.rate
  if (t.fee) inputs.origination_fee_pct = t.fee
  if (t.fund) inputs.funding_date = t.fund

  // Timing/certainty detail the extraction already carries.
  if (ed.other_income) inputs.other_income = ed.other_income
  if (ed.contract_end) inputs.contract_end = normalizeDate(ed.contract_end)
  if (ed.salary_guarantee_note) {
    // "Yes — one-way SPC" / "Conditional …" — anything qualified is NOT a
    // clean guarantee, and that flips which structures make sense.
    inputs.salary_guaranteed = /^\s*yes\b/i.test(ed.salary_guarantee_note)
      && !/conditional/i.test(ed.salary_guarantee_note)
  }

  // Annual non-facility debt service, computed by the BACKEND using the credit
  // memo's own PFS handling — same rows the memo counts, and a stale statement
  // is rolled forward to the funding date, so a debt repaid since the statement
  // stops being counted here too.
  const ds = await structureDebtService({ ...t, fund: inputs.funding_date || t.fund || null }, ed)
  if (ds && ds.annual) inputs.other_debt_annual = ds.annual
  pfsNote.value = (ds && ds.note) || ''
  if (ed.contract_remaining) contractRemaining.value = ed.contract_remaining

  // Term as presented, for the side-by-side.
  if (t.fund && t.mat) {
    const f = new Date(t.fund), m = new Date(t.mat)
    const months = (m.getFullYear() - f.getFullYear()) * 12 + m.getMonth() - f.getMonth()
    if (months > 0) {
      inputs.target_term_months = months
      inputs.presented_term_months = months
    }
  }

  await loadCadence()
  pullMsg.value = '✓ Pulled from the Credit Memo tab — fill in the timing fields below'
}

function normalizeDate(v) {
  if (!v) return ''
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 10)
}

function monthsBetween(a, b) {
  const f = new Date(a), m = new Date(b)
  if (Number.isNaN(f.getTime()) || Number.isNaN(m.getTime())) return 0
  return (m.getFullYear() - f.getFullYear()) * 12 + m.getMonth() - f.getMonth()
}

// --- read the deal documents ------------------------------------------------
function onFiles(e) {
  const seen = new Set(files.value.map((f) => f.name + ':' + f.size))
  for (const f of Array.from(e.target.files)) {
    const key = f.name + ':' + f.size
    if (!seen.has(key)) { files.value.push(f); seen.add(key) }
  }
  e.target.value = ''
}

function removeFile(i) {
  files.value.splice(i, 1)
}

// The documents were uploaded FOR these fields, so an extracted value wins over
// what is currently in the form; anything the documents don't state is left
// alone. Everything stays editable below.
async function readDocuments() {
  if (!files.value.length) return
  reading.value = true
  readStatus.type = 'info'
  readStatus.msg = `Reading ${files.value.length} document(s) with Claude…`
  extractNotes.value = ''
  try {
    const ed = await structureExtract(files.value)

    const direct = {
      borrower_name: ed.borrower_name, team: ed.team, league: ed.league,
      salary: ed.salary, other_income: ed.other_income,
      other_debt_annual: ed.other_debt_annual,
      loan_amount: ed.loan_amount, interest_rate: ed.interest_rate,
      origination_fee_pct: ed.origination_fee_pct,
      presented_type: ed.presented_type,
      expected_exit_label: ed.expected_exit_label,
    }
    for (const [k, v] of Object.entries(direct)) {
      if (v !== null && v !== undefined && v !== '') inputs[k] = v
    }
    for (const k of ['contract_end', 'funding_date', 'expected_exit_date']) {
      if (ed[k]) inputs[k] = normalizeDate(ed[k])
    }
    if (ed.salary_guaranteed !== null && ed.salary_guaranteed !== undefined) {
      inputs.salary_guaranteed = ed.salary_guaranteed
    }
    // Term as presented, from funding -> maturity.
    if (ed.maturity_date) presentedMaturity.value = normalizeDate(ed.maturity_date)
    if (ed.funding_date && ed.maturity_date) {
      const months = monthsBetween(ed.funding_date, ed.maturity_date)
      if (months > 0) {
        inputs.target_term_months = months
        inputs.presented_term_months = months
      }
    }

    // Cadence: start from the league default, then apply anything the documents
    // actually state. A stated cadence outranks the league default.
    await loadCadence()
    if (ed.pay_frequency) cadence.pay_frequency = ed.pay_frequency
    if (ed.season_start) {
      const d = new Date(ed.season_start)
      cadence.season_start_month = d.getUTCMonth() + 1
      cadence.season_start_day = d.getUTCDate()
    }
    if (ed.season_end) {
      const d = new Date(ed.season_end)
      cadence.season_end_month = d.getUTCMonth() + 1
      cadence.season_end_day = d.getUTCDate()
    }
    if (ed.season_start || ed.season_end || ed.pay_frequency) {
      cadence.label = 'From the contract'
    }

    if (Array.isArray(ed.bonus_events) && ed.bonus_events.length) {
      inputs.bonus_events = ed.bonus_events
        .filter((b) => b.date || b.amount)
        .map((b) => ({
          label: b.label || '', date: normalizeDate(b.date),
          amount: b.amount, guaranteed: b.guaranteed !== false,
        }))
    }

    const gaps = []
    if (!inputs.salary) gaps.push('guaranteed salary')
    if (!inputs.loan_amount) gaps.push('loan amount')
    if (!inputs.funding_date) gaps.push('funding date')
    if (!ed.pay_frequency && !ed.season_start) {
      gaps.push(`pay cadence (using the ${cadence.league || 'default'} league default)`)
    }

    extractNotes.value = [ed.notes, ed.salary_guarantee_note, ed.pay_election_note]
      .filter(Boolean).join(' · ')
    readStatus.type = gaps.length ? 'info' : 'ok'
    readStatus.msg = '✓ Read the documents — review below'
      + (gaps.length ? `. Still needed: ${gaps.join(', ')}.` : '. Ready to run.')

    // Carry the deal terms forward to the Credit Memo tab straight away, so the
    // memo starts filled in. Blanks only — anything already typed there wins.
    sendToMemo()
  } catch (err) {
    readStatus.type = 'err'
    readStatus.msg = err.message
  }
  reading.value = false
}

// --- carry the deal forward to the Credit Memo tab --------------------------
// Fills the memo's Step 2 terms grid from what we read here. Following the PA /
// Loan Docs / Binder precedent, it fills ONLY empty fields — a value already
// typed on the memo tab is never overwritten.
function sendToMemo() {
  const t = props.memoTerms
  if (!t) return
  const map = {
    name: inputs.borrower_name,
    team: inputs.team,
    league: inputs.league,
    salary: Number(inputs.salary) || null,
    loan: Number(inputs.loan_amount) || null,
    rate: Number(inputs.interest_rate) || null,
    fee: Number(inputs.origination_fee_pct) || null,
    fund: inputs.funding_date,
    mat: presentedMaturity.value,
  }
  let filled = 0
  for (const [k, v] of Object.entries(map)) {
    if (v !== null && v !== undefined && v !== '' && !t[k]) { t[k] = v; filled++ }
  }
  memoPush.value = filled
    ? `✓ ${filled} deal term(s) carried to the Credit Memo tab`
      + (files.value.length ? `, along with the ${files.value.length} uploaded document(s).` : '.')
    : 'The Credit Memo tab already has these terms — nothing overwritten.'
  return filled
}

// --- cadence ---------------------------------------------------------------
async function loadCadence() {
  const c = await structureCadence(inputs.league || 'none')
  if (c) Object.assign(cadence, c)
}

function resetCadence() {
  loadCadence()
}

// --- bonus events ----------------------------------------------------------
function addBonus() {
  inputs.bonus_events.push({ label: '', date: '', amount: null, guaranteed: true })
}
function removeBonus(i) {
  inputs.bonus_events.splice(i, 1)
}

// --- run -------------------------------------------------------------------
function payload() {
  return {
    ...inputs,
    salary: Number(inputs.salary) || 0,
    other_income: Number(inputs.other_income) || 0,
    other_debt_annual: Number(inputs.other_debt_annual) || 0,
    loan_amount: Number(inputs.loan_amount) || 0,
    interest_rate: Number(inputs.interest_rate) || 0,
    origination_fee_pct: Number(inputs.origination_fee_pct) || 0,
    target_term_months: Number(inputs.target_term_months) || 12,
    agent_pct: Number(inputs.agent_pct) || 0,
    min_coverage: Number(inputs.min_coverage) || 1.25,
    presented_term_months: Number(inputs.presented_term_months) || 0,
    contract_end: inputs.contract_end || null,
    funding_date: inputs.funding_date || null,
    expected_exit_date: inputs.expected_exit_date || null,
    cadence: cadence.pay_frequency ? { ...cadence } : null,
    bonus_events: inputs.bonus_events
      .filter((b) => b.date && Number(b.amount))
      .map((b) => ({ ...b, amount: Number(b.amount), date: b.date })),
  }
}

async function run() {
  running.value = true
  status.type = 'info'
  status.msg = 'Projecting cash flow and scoring structures…'
  selectedKey.value = ''
  pendingPush.value = null
  applied.value = ''
  try {
    result.value = await structurePropose(payload())
    const rec = result.value.candidates.find((c) => c.recommended)
    status.type = 'ok'
    status.msg = `✓ ${result.value.candidates.length} structures scored`
      + (rec ? ` — recommended: ${rec.name}` : '')
  } catch (err) {
    status.type = 'err'
    status.msg = err.message
  }
  running.value = false
}

// --- propose the terms -----------------------------------------------------
async function proposeTerms() {
  proposing.value = true
  termsErr.value = ''
  try {
    terms.value = await structureProposeTerms(payload(), Number(contractRemaining.value) || null)
  } catch (err) {
    termsErr.value = err.message
  }
  proposing.value = false
}

function applyTerms() {
  if (!terms.value) return
  inputs.loan_amount = terms.value.loan_amount
  inputs.interest_rate = terms.value.interest_rate
  inputs.origination_fee_pct = terms.value.origination_fee_pct
  inputs.target_term_months = terms.value.target_term_months
}

// --- export ----------------------------------------------------------------
// The sendable artifact: every option considered, the recommendation, and the
// cash flow behind it, in the credit memorandum's house design.
async function exportPdf() {
  exporting.value = true
  exportErr.value = ''
  try {
    await structureSummaryPdf(payload())
  } catch (err) {
    exportErr.value = err.message
  }
  exporting.value = false
}

// --- selection & push ------------------------------------------------------
async function choose(key) {
  selectedKey.value = key
  applied.value = ''
  try {
    pendingPush.value = await structureSelect(payload(), key)
  } catch (err) {
    status.type = 'err'
    status.msg = err.message
    pendingPush.value = null
  }
}

// Writes into the App-owned Loan Documents store, where repayment_schedule
// already outranks the computed Exhibit A schedule.
function applyToLoanDocs() {
  const store = props.loanDocsTerms
  const push = pendingPush.value
  if (!store || !push) return
  store.repayment_schedule = push.repayment_schedule
  store.schedule_source = 'Structure tab'
  store.amortization_type = push.amortization_type
  if (push.maturity_date) store.maturity_date = push.maturity_date
  if (!store.loan_amount && inputs.loan_amount) store.loan_amount = Number(inputs.loan_amount)
  if (!store.interest_rate && inputs.interest_rate) store.interest_rate = Number(inputs.interest_rate)
  applied.value = `✓ ${push.name} applied — ${push.repayment_schedule.length} payment(s) `
    + 'sent to the Loan Documents tab as the Note\'s Exhibit A.'
}
</script>

<template>
  <!-- Step 1: upload the deal documents -->
  <section class="card">
    <h2><span class="step">1</span> Upload deal documents</h2>
    <input type="file" multiple @change="onFiles" />
    <p class="hint">
      The player contract (with its pay schedule), the term sheet, and the personal
      financial statement. Claude reads the pay cadence, guarantee language, bonus
      dates and deal terms — everything below fills in from these.
    </p>
    <ul v-if="files.length" class="filelist">
      <li v-for="(f, i) in files" :key="f.name + f.size">
        <span class="fname">{{ f.name }}</span>
        <button type="button" class="rm" @click="removeFile(i)" title="Remove">✕</button>
      </li>
    </ul>
    <button :disabled="!files.length || reading" @click="readDocuments">
      {{ reading ? 'Reading…' : 'Read documents with Claude' }}
    </button>
    <button v-if="hasMemo" class="ghost" @click="pullFromMemo">↙ Or pull from the Credit Memo tab</button>
    <p v-if="readStatus.msg" :class="['status', readStatus.type]">{{ readStatus.msg }}</p>
    <p v-if="extractNotes" class="hint note">📄 {{ extractNotes }}</p>
    <p v-if="pfsNote" class="hint note">🧾 {{ pfsNote }}</p>
    <p v-if="memoPush" class="status ok">{{ memoPush }}</p>
    <p v-if="pullMsg" class="status ok">{{ pullMsg }}</p>
    <p v-if="files.length" class="hint">
      These documents are shared with the Credit Memo tab — no need to upload them
      again there. The memo runs its own, fuller extraction over the same files
      (assets, liabilities, expenditures, uses of funds), so hit
      <strong>Extract with Claude</strong> once you're there.
    </p>
  </section>

  <!-- Step 2: confirm what was read -->
  <section class="card">
    <h2><span class="step">2</span> Deal &amp; borrower</h2>
    <p class="hint">Everything here is editable — correct anything the documents got wrong.</p>

    <div class="grid">
      <label>Borrower <input v-model="inputs.borrower_name" /></label>
      <label>Team <input v-model="inputs.team" /></label>
      <label>League
        <input v-model="inputs.league" list="leagues" @change="loadCadence" placeholder="NFL / NBA / MLB / NHL / MLS" />
        <datalist id="leagues">
          <option v-for="c in cadences" :key="c.league" :value="c.league" />
        </datalist>
      </label>
      <label>Guaranteed season salary <input v-model.number="inputs.salary" type="number" /></label>
      <label>Other income (annual) <input v-model.number="inputs.other_income" type="number" /></label>
      <label>Other debt service (annual) <input v-model.number="inputs.other_debt_annual" type="number" /></label>
      <label>Contract end <input v-model="inputs.contract_end" type="date" /></label>
      <label class="inline chk">
        <input type="checkbox" v-model="inputs.salary_guaranteed" />
        Salary is fully guaranteed
      </label>
    </div>
    <p class="hint">
      Guarantee status decides as much as timing does: non-guaranteed income argues for
      amortizing fast while the money flows, guaranteed money can carry a balloon.
    </p>
  </section>

  <!-- Step 2: terms being tested -->
  <section class="card">
    <h2><span class="step">3</span> Terms being tested</h2>
    <div class="grid">
      <label>Total remaining contract value
        <input v-model.number="contractRemaining" type="number" placeholder="LTC basis — else season salary" /></label>
    </div>
    <button class="ghost" :disabled="proposing" @click="proposeTerms">
      {{ proposing ? 'Working…' : '✨ Propose terms from the contract' }}
    </button>
    <p v-if="termsErr" class="status err">⚠ {{ termsErr }}</p>

    <div v-if="terms" class="proposed">
      <div class="pt-head">
        <span class="pt-amt">{{ money(terms.loan_amount) }}</span>
        <span class="pt-sub">at {{ terms.interest_rate }}% · {{ terms.origination_fee_pct }} pts ·
          {{ terms.target_term_months }} months</span>
        <button class="ghost sm" @click="applyTerms">Use these</button>
      </div>
      <div class="metrics">
        <div><span class="k">Policy limit (LTC)</span><span class="v">{{ money(terms.policy_cap) }}</span></div>
        <div><span class="k">Cash-flow capacity</span><span class="v">{{ money(terms.cash_capacity) }}</span></div>
        <div><span class="k">Binding constraint</span><span class="v">{{ terms.binding_constraint }}</span></div>
        <div><span class="k">Can they repay it?</span>
          <span class="v" :class="{ neg: !terms.can_repay }">{{ terms.can_repay ? 'Yes' : 'No' }}</span></div>
      </div>
      <p class="rationale">{{ terms.repayment_note }}</p>
      <p v-for="w in terms.warnings" :key="w" class="warn">⚠ {{ w }}</p>
      <p class="hint">Rate and points: {{ terms.rate_basis }}</p>
    </div>

    <p class="hint">
      The amount is the lower of South River's Loan-to-Contract policy limit and what the
      borrower's own earnings can actually retire. Rate and points are house defaults —
      nothing here prices risk, so edit them per deal.
    </p>
    <div class="grid">
      <label>Loan amount <input v-model.number="inputs.loan_amount" type="number" /></label>
      <label>Rate (% p.a.) <input v-model.number="inputs.interest_rate" type="number" step="0.01" /></label>
      <label>Points / origination (%) <input v-model.number="inputs.origination_fee_pct" type="number" step="0.01" /></label>
      <label>Funding date <input v-model="inputs.funding_date" type="date" /></label>
      <label>Target term (months) <input v-model.number="inputs.target_term_months" type="number" /></label>
      <label>Structure as presented <input v-model="inputs.presented_type" placeholder="e.g. Full balloon" /></label>
    </div>

    <h3 class="sub">Event-driven exit</h3>
    <div class="grid">
      <label>Expected exit date <input v-model="inputs.expected_exit_date" type="date" /></label>
      <label>What the exit is <input v-model="inputs.expected_exit_label" placeholder="e.g. contract extension signing" /></label>
    </div>
    <p class="hint">
      Fill these in when repayment depends on an event rather than on income —
      a contract signing, an endorsement close, a sale. That is what turns a
      balloon from a default into the right answer.
    </p>

    <h3 class="sub">Underwriting assumptions</h3>
    <div class="grid">
      <label>Minimum coverage <input v-model.number="inputs.min_coverage" type="number" step="0.05" /></label>
      <label>Agent commission (%) <input v-model.number="inputs.agent_pct" type="number" step="0.5" /></label>
    </div>
    <p class="hint">
      Taxes (45%) and living expenses (10%) follow the credit memo's rules. Agent
      commission defaults to 0% so this projection ties out to the memo exactly —
      setting it deliberately runs below the memo.
    </p>

    <button class="ghost" @click="sendToMemo">→ Send deal terms to Credit Memo</button>
    <p class="hint">Fills any blank on the memo's terms grid. Corrections you make here can be re-sent.</p>
  </section>

  <!-- Step 3: pay cadence -->
  <section class="card">
    <h2><span class="step">4</span> Pay cadence</h2>
    <p v-if="cadence.label" class="cad-label">{{ cadence.league || 'Custom' }} — {{ cadence.label }}</p>
    <div class="grid">
      <label>Pay frequency
        <select v-model="cadence.pay_frequency">
          <option v-for="(lab, v) in FREQ_LABELS" :key="v" :value="v">{{ lab }}</option>
        </select>
      </label>
      <label class="inline chk">
        <input type="checkbox" v-model="cadence.year_round" />
        Paid year round (not season-only)
      </label>
      <label>Season starts
        <div class="pair">
          <select v-model.number="cadence.season_start_month">
            <option v-for="(m, i) in MONTHS" :key="m" :value="i + 1">{{ m }}</option>
          </select>
          <input v-model.number="cadence.season_start_day" type="number" min="1" max="31" />
        </div>
      </label>
      <label>Season ends
        <div class="pair">
          <select v-model.number="cadence.season_end_month">
            <option v-for="(m, i) in MONTHS" :key="m" :value="i + 1">{{ m }}</option>
          </select>
          <input v-model.number="cadence.season_end_day" type="number" min="1" max="31" />
        </div>
      </label>
    </div>
    <p v-if="cadence.notes" class="hint">{{ cadence.notes }}</p>
    <button class="ghost" @click="resetCadence">↻ Reset to the league default</button>

    <h3 class="sub">Bonuses &amp; dated lump payments</h3>
    <table v-if="inputs.bonus_events.length" class="cover-tbl">
      <thead>
        <tr><th>Label</th><th>Date</th><th>Amount</th><th>Guaranteed</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="(b, i) in inputs.bonus_events" :key="i">
          <td><input v-model="b.label" placeholder="Signing bonus installment" /></td>
          <td><input v-model="b.date" type="date" /></td>
          <td><input v-model.number="b.amount" type="number" /></td>
          <td class="ctr"><input type="checkbox" v-model="b.guaranteed" /></td>
          <td class="ctr"><button type="button" class="rm" @click="removeBonus(i)">✕</button></td>
        </tr>
      </tbody>
    </table>
    <button class="ghost" @click="addBonus">+ Add a bonus / lump payment</button>
    <p class="hint">
      Signing-bonus installments, roster bonuses, endorsement milestones. These land
      on their own date rather than following the salary cadence — an NHL July 1
      bonus is often the largest cash event of the year, and a natural balloon date.
    </p>
  </section>

  <!-- Step 4: propose -->
  <section class="card">
    <h2><span class="step">5</span> Propose structures</h2>
    <button :disabled="!canRun || running" @click="run">
      {{ running ? 'Working…' : 'Project cash flow &amp; score structures' }}
    </button>
    <p v-if="!canRun" class="hint">A loan amount is required.</p>
    <p v-if="status.msg" :class="['status', status.type]">{{ status.msg }}</p>

    <template v-if="result">
      <div class="topline">
        <div><span class="k">Annual gross</span><span class="v">{{ money(result.annual_gross) }}</span></div>
        <div><span class="k">Available after tax, living &amp; existing debt</span>
             <span class="v">{{ money(result.annual_available) }}</span></div>
        <div><span class="k">Loan vs. annual available</span>
             <span class="v">{{ result.annual_available > 0
               ? (inputs.loan_amount / result.annual_available).toFixed(1) + 'x' : '—' }}</span></div>
      </div>

      <h3 class="sub">Projected cash flow</h3>
      <table class="cover-tbl flow">
        <thead>
          <tr><th>Month</th><th>Season</th><th>Gross</th><th>Available</th>
              <th>Cumulative</th><th class="wide">Shape</th></tr>
        </thead>
        <tbody>
          <tr v-for="m in result.cash_flow" :key="m.label" :class="{ dry: !m.in_season }">
            <td class="nw">{{ m.label }}</td>
            <td class="ctr">{{ m.in_season ? '●' : '' }}</td>
            <td class="num">{{ money(m.gross) }}</td>
            <td class="num" :class="{ neg: m.available < 0 }">{{ money(m.available) }}</td>
            <td class="num" :class="{ neg: m.cumulative < 0 }">{{ money(m.cumulative) }}</td>
            <td class="bar-cell">
              <div class="bar-track">
                <div class="bar" :class="{ neg: m.available < 0 }"
                     :style="{ width: (Math.abs(m.available) / flowScale * 50) + '%',
                               marginLeft: m.available < 0
                                 ? (50 - Math.abs(m.available) / flowScale * 50) + '%' : '50%' }"></div>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="hint" v-for="n in result.notes" :key="n">· {{ n }}</p>

      <h3 class="sub">Candidate structures</h3>
      <p class="hint">
        <button class="ghost" :disabled="exporting" @click="exportPdf">
          {{ exporting ? 'Building…' : '📕 Export all options as PDF' }}
        </button>
        A one-look summary for credit — every option including the ones that fail,
        the recommendation and its reasoning, and the cash flow behind it.
      </p>
      <p v-if="exportErr" class="status err">⚠ {{ exportErr }}</p>
      <div v-for="c in result.candidates" :key="c.key"
           :class="['cand', { rec: c.recommended, chosen: selectedKey === c.key }]">
        <div class="cand-head">
          <div>
            <span class="cand-name">{{ c.name }}</span>
            <span :class="['badge', c.passes ? 'pass' : 'fail']">{{ c.passes ? 'PASS' : 'FAIL' }}</span>
            <span v-if="c.recommended" class="badge rec">RECOMMENDED</span>
          </div>
          <button class="ghost sm" @click="choose(c.key)">
            {{ selectedKey === c.key ? '✓ Selected' : 'Select this structure' }}
          </button>
        </div>
        <p class="rationale">{{ c.rationale }}</p>
        <div class="metrics">
          <div><span class="k">Payments</span><span class="v">{{ c.payments.length }}</span></div>
          <div><span class="k">Matures</span><span class="v">{{ c.maturity_date || '—' }}</span></div>
          <div><span class="k">Total interest</span><span class="v">{{ money(c.total_interest) }}</span></div>
          <div v-if="c.interest_reserve">
            <span class="k">Interest reserve</span><span class="v">{{ money(c.interest_reserve) }}</span></div>
          <div><span class="k">Coverage, tightest month</span>
               <span class="v" :class="{ neg: c.min_coverage < inputs.min_coverage }">
                 {{ pct(c.min_coverage) }}<em v-if="c.tightest_month"> ({{ c.tightest_month }})</em></span></div>
          <div><span class="k">Coverage on banked cash</span>
               <span class="v" :class="{ neg: c.min_cushion_coverage < 1 }">{{ pct(c.min_cushion_coverage) }}</span></div>
        </div>
        <p v-for="w in c.warnings" :key="w" class="warn">⚠ {{ w }}</p>
      </div>
    </template>
  </section>

  <!-- Step 5: review the rows, then push -->
  <section v-if="selected && pendingPush" class="card">
    <h2><span class="step">6</span> Review &amp; apply</h2>
    <p class="hint">
      These are the exact rows that will become the Note's Exhibit A. Check the payment
      dates and where the balloon lands before applying — that is the only place an
      off-by-one date is visible.
    </p>
    <table class="cover-tbl">
      <thead>
        <tr><th>#</th><th>Date</th><th>Interest</th><th>Principal</th><th>Total</th>
            <th>Cash that month</th><th>Coverage</th></tr>
      </thead>
      <tbody>
        <tr v-for="(p, i) in selected.payments" :key="i" :class="{ balloon: p.is_balloon }">
          <td>{{ i + 1 }}</td>
          <td class="nw">{{ p.date }}</td>
          <td class="num">{{ money(p.interest) }}</td>
          <td class="num">{{ money(p.principal) }}</td>
          <td class="num"><strong>{{ money(p.total) }}</strong></td>
          <td class="num" :class="{ neg: p.month_available < 0 }">{{ money(p.month_available) }}</td>
          <td class="num" :class="{ neg: p.coverage < inputs.min_coverage }">{{ pct(p.coverage) }}</td>
        </tr>
      </tbody>
    </table>
    <p class="hint">
      Applies as <strong>{{ pendingPush.amortization_type }}</strong>, maturing
      <strong>{{ pendingPush.maturity_date || '—' }}</strong>.
    </p>
    <button @click="applyToLoanDocs">→ Apply to Loan Documents</button>
    <p v-if="applied" class="status ok">{{ applied }}</p>
  </section>
</template>

<style scoped>
.sub { font-size: 12px; text-transform: uppercase; letter-spacing: .08em;
  color: var(--navy); margin: 18px 0 8px; }
.chk { flex-direction: row; align-items: center; gap: 6px; font-size: 13px; color: #1a1a1a; }
.pair { display: flex; gap: 6px; }
.pair select { flex: 1; }
.pair input { width: 62px; }
.cad-label { font-size: 12px; color: #555; margin: 0 0 10px; font-style: italic; }
.proposed { border: 1px solid var(--gold); background: #fefcf5; border-radius: 8px;
  padding: 10px 12px; margin-top: 10px; }
.pt-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.pt-amt { font-size: 20px; font-weight: 700; color: var(--navy); }
.pt-sub { font-size: 12px; color: #555; flex: 1; }
.note { background: #f4f6f8; border-left: 3px solid var(--navy); padding: 7px 10px;
  border-radius: 0 4px 4px 0; color: #444; line-height: 1.5; margin-top: 8px; }

.topline { display: flex; flex-wrap: wrap; gap: 20px; background: #f4f6f8;
  border: 1px solid #e0e4e9; border-radius: 8px; padding: 10px 14px; margin-top: 12px; }
.topline .k, .metrics .k { display: block; font-size: 10px; text-transform: uppercase;
  letter-spacing: .06em; color: #777; }
.topline .v, .metrics .v { font-size: 14px; font-weight: 700; color: var(--navy); }

.flow .num { text-align: right; white-space: nowrap; }
.flow .ctr, .cover-tbl .ctr { text-align: center; }
.flow .nw, .cover-tbl .nw { white-space: nowrap; }
.flow tr.dry td { background: #fbfaf3; color: #6b6b6b; }
.num.neg, .v.neg { color: #b00020; }
.bar-cell { width: 34%; padding: 2px 6px; }
.bar-track { position: relative; height: 12px; background: #eef1f4; border-radius: 2px; }
.bar-track::before { content: ''; position: absolute; left: 50%; top: 0; bottom: 0;
  width: 1px; background: #c3cad2; }
.bar { height: 12px; background: var(--navy); border-radius: 2px; }
.bar.neg { background: #d9737f; }

.cand { border: 1px solid #e0e4e9; border-radius: 8px; padding: 12px 14px; margin-top: 10px; }
.cand.rec { border-color: var(--gold); background: #fefcf5; }
.cand.chosen { border-color: var(--navy); box-shadow: 0 0 0 2px rgba(15, 42, 67, .12); }
.cand-head { display: flex; justify-content: space-between; align-items: center;
  gap: 12px; flex-wrap: wrap; }
.cand-name { font-size: 14px; font-weight: 700; color: var(--navy); margin-right: 8px; }
.badge { font-size: 9px; font-weight: 700; letter-spacing: .08em; padding: 2px 6px;
  border-radius: 3px; margin-right: 4px; }
.badge.pass { background: #e8f5ee; color: #0f6e56; }
.badge.fail { background: #fdecea; color: #b00020; }
.badge.rec { background: var(--gold); color: #fff; }
.rationale { font-size: 12px; color: #444; line-height: 1.5; margin: 8px 0; }
.metrics { display: flex; flex-wrap: wrap; gap: 18px; margin: 10px 0 4px; }
.metrics em { font-style: normal; font-weight: 400; font-size: 11px; color: #777; }
.warn { font-size: 11.5px; color: #8a5b00; background: #fdf8ec; border-left: 3px solid #d9a441;
  padding: 5px 9px; border-radius: 0 4px 4px 0; margin: 5px 0; line-height: 1.45; }
button.sm { padding: 5px 10px; font-size: 12px; margin: 0; }
.cover-tbl tr.balloon td { background: #f4f6f8; font-weight: 600; }
.rm { background: transparent; color: #b00020; border: 0; padding: 0 4px; margin: 0;
  font-size: 13px; cursor: pointer; }
</style>

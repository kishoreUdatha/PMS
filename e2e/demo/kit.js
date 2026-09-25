// Shared machinery for the recorded end-to-end demo.
//
// Three jobs: drive the UI the way a person would (sidebar clicks, form
// fields found by their visible labels), explain what is happening (a caption
// bar and a spoken narration line for every step), and keep score (every step
// is PASS/FAIL with a screenshot, so the recording doubles as a test run).
const fs = require('fs')
const path = require('path')
const { spawn } = require('child_process')

const BASE = process.env.APP_URL || 'http://localhost:5173'
const OUT = process.env.DEMO_OUT || path.join(__dirname, '..', 'out')
const MAIL_DIR = process.env.MAIL_DIR || '/tmp/pmsmail'
const VOICE = process.env.PIPER_VOICE || '/opt/voice/en-us-lessac-medium.onnx'
const PY = process.env.PYTHON || '/opt/pmsvenv/bin/python'
fs.mkdirSync(path.join(OUT, 'audio'), { recursive: true })
fs.mkdirSync(path.join(OUT, 'shots'), { recursive: true })

// ------------------------------------------------------------ narration --
class Voice {
  constructor() {
    this.proc = spawn(PY, [path.join(__dirname, 'tts_server.py'), VOICE],
      { stdio: ['pipe', 'pipe', 'inherit'] })
    this.buf = ''
    this.waiters = []
    this.ready = new Promise((res) => { this.onReady = res })
    this.proc.stdout.on('data', (d) => {
      this.buf += d
      let i
      while ((i = this.buf.indexOf('\n')) >= 0) {
        const msg = JSON.parse(this.buf.slice(0, i)); this.buf = this.buf.slice(i + 1)
        if (msg.ready) this.onReady(); else this.waiters.shift()(msg)
      }
    })
    this.n = 0
  }
  async speak(text) {
    await this.ready
    const out = path.join(OUT, 'audio', `clip_${String(++this.n).padStart(4, '0')}.wav`)
    return new Promise((res) => {
      this.waiters.push(res)
      this.proc.stdin.write(JSON.stringify({ text, out }) + '\n')
    })
  }
  close() { this.proc.stdin.end() }
}

// The caption bar and chapter pill live in localStorage and are redrawn by an
// init script on every navigation, so they survive page loads mid-sentence.
const OVERLAY = () => {
  const draw = () => {
    if (!document.body) return
    let bar = document.getElementById('__demo_bar')
    if (!bar) {
      bar = document.createElement('div'); bar.id = '__demo_bar'
      bar.innerHTML = '<div id="__demo_chap"></div><div id="__demo_cap"></div>'
      const st = document.createElement('style')
      st.textContent = `#__demo_bar{position:fixed;left:0;right:0;bottom:0;z-index:2147483646;pointer-events:none;font-family:system-ui,sans-serif}
#__demo_cap{margin:0 auto 14px;max-width:1180px;background:rgba(10,30,36,.88);color:#fff;font-size:21px;line-height:1.35;padding:12px 22px;border-radius:12px;box-shadow:0 6px 24px rgba(0,0,0,.35);text-align:center}
#__demo_cap:empty{display:none}
#__demo_chap{position:fixed;top:10px;left:50%;transform:translateX(-50%);background:#e8590c;color:#fff;font-weight:700;font-size:14px;padding:5px 14px;border-radius:999px;letter-spacing:.02em;box-shadow:0 2px 8px rgba(0,0,0,.3)}
#__demo_chap:empty{display:none}
.__demo_hl{position:fixed;z-index:2147483645;border:3px solid #ff3b30;border-radius:8px;box-shadow:0 0 0 4px rgba(255,59,48,.25);pointer-events:none;transition:all .15s}`
      document.head.appendChild(st); document.body.appendChild(bar)
    }
    let s = {}
    try { s = JSON.parse(localStorage.getItem('__demo') || '{}') } catch (e) { /* ignore */ }
    document.getElementById('__demo_cap').textContent = s.cap || ''
    document.getElementById('__demo_chap').textContent = s.chap || ''
  }
  window.__demoDraw = draw
  document.addEventListener('DOMContentLoaded', draw)
  setInterval(draw, 400)
}

class Session {
  constructor(page, label, voice, results) {
    this.p = page; this.label = label; this.voice = voice; this.results = results
    this.t0 = Date.now(); this.clips = []; this.chap = ''
  }
  async _state(obj) {
    await this.p.evaluate((o) => {
      const s = JSON.parse(localStorage.getItem('__demo') || '{}')
      localStorage.setItem('__demo', JSON.stringify({ ...s, ...o }))
      window.__demoDraw && window.__demoDraw()
    }, obj).catch(() => {})
  }
  async chapter(text) { this.chap = text; await this._state({ chap: text }) }
  /** Caption + narration, then wait until the line has been spoken. */
  async say(text, { caption, pause = 350 } = {}) {
    const clip = await this.voice.speak(text)
    await this._state({ cap: caption || text })
    this.clips.push({ t: (Date.now() - this.t0) / 1000, wav: clip.out, text })
    await this.p.waitForTimeout(clip.seconds * 1000 + pause)
  }
  async clear() { await this._state({ cap: '' }) }

  /** Box an element in red for a moment, so the viewer sees where we act. */
  async mark(loc, ms = 650) {
    try {
      await loc.scrollIntoViewIfNeeded({ timeout: 5000 })
      const b = await loc.boundingBox()
      if (!b) return
      await this.p.evaluate(({ x, y, width, height, ms }) => {
        const d = document.createElement('div'); d.className = '__demo_hl'
        Object.assign(d.style, { left: `${x - 4}px`, top: `${y - 4}px`, width: `${width + 8}px`, height: `${height + 8}px` })
        document.body.appendChild(d); setTimeout(() => d.remove(), ms)
      }, { ...b, ms })
      await this.p.waitForTimeout(Math.min(ms, 450))
    } catch (e) { /* highlighting is decoration */ }
  }
  async click(loc, wait = 900) { await this.mark(loc); await loc.click(); await this.p.waitForTimeout(wait) }
  async type(loc, value) { await this.mark(loc, 400); await loc.fill(''); await loc.pressSequentially(String(value), { delay: 18 }) }

  /** A custom select: open it, choose the option (listbox or button list). */
  async pick(trigger, option) {
    const t = typeof trigger === 'string' ? this.p.getByText(trigger, { exact: true }).first() : trigger
    await this.click(t, 350)
    const opt = this.p.getByRole('option', { name: option })
    if (await opt.count()) await this.click(opt.first(), 400)
    else await this.click(this.p.getByRole('button', { name: option, exact: typeof option === 'string' }).last(), 400)
  }
  async pickIn(label, option) {
    const lab = this.p.locator('label').filter({ hasText: new RegExp('^\\s*' + label) }).first()
    const btn = lab.getByRole('button').first()
    return this.pick((await btn.count()) ? btn : lab.locator('..').getByRole('button').first(), option)
  }
  async field(label, what = 'input') {
    const inLabel = this.p.locator('label').filter({ hasText: label }).last().locator(what)
    if (await inLabel.count()) return inLabel.first()
    const ex = this.p.getByText(label, { exact: true })
    if (await ex.count()) return ex.last().locator(`xpath=following::${what}[1]`)
    return this.p.getByText(label).last().locator(`xpath=following::${what}[1]`)
  }
  async fillAfter(label, value, what = 'input') { await this.type(await this.field(label, what), value) }

  /** Click a left-hand menu entry, opening its group first if it has one. */
  async menu(label, group) {
    const aside = this.p.locator('aside')
    if (group) {
      const g = aside.getByRole('button', { name: group, exact: true })
      if ((await g.getAttribute('aria-expanded')) !== 'true') await this.click(g, 400)
    }
    const esc = label.replace(/[.*+?^${}()|[\]\\&]/g, '\\$&')
    await this.click(aside.getByRole('link', { name: new RegExp(`^${esc}(\\s*\\d+)?$`) }).first(), 300)
    await this.p.waitForLoadState('networkidle').catch(() => {})
    await this.p.waitForTimeout(700)
  }

  /** One scored test step. A failure is recorded and the run carries on. */
  async step(name, fn, { critical = false } = {}) {
    const errors = []
    const onResp = (r) => {
      if (r.url().includes('/api/') && r.status() >= 500) errors.push(`${r.status()} ${r.request().method()} ${r.url().replace(/.*\/api/, '').split('?')[0]}`)
    }
    const onErr = (e) => errors.push('JS error: ' + e.message.slice(0, 120))
    this.p.on('response', onResp); this.p.on('pageerror', onErr)
    const shot = path.join(OUT, 'shots', `${this.label}_${String(this.results.length + 1).padStart(3, '0')}.png`)
    let ok = true; let detail = ''
    try { await fn() } catch (e) { ok = false; detail = e.message.split('\n')[0].slice(0, 240) }
    this.p.off('response', onResp); this.p.off('pageerror', onErr)
    if (ok && errors.length) { ok = false; detail = errors.join('; ') }
    await this.p.screenshot({ path: shot }).catch(() => {})
    this.results.push({ tenant: this.label, step: name, ok, detail, shot: path.relative(OUT, shot) })
    console.log(`${ok ? 'PASS' : 'FAIL'}  [${this.label}] ${name}${detail ? '  -- ' + detail : ''}`)
    if (!ok && critical) throw new Error(`critical step failed: ${name}`)
    return ok
  }
}

async function newSession(browser, label, voice, results) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    recordVideo: { dir: path.join(OUT, 'video'), size: { width: 1440, height: 900 } },
  })
  await ctx.addInitScript(OVERLAY)
  const page = await ctx.newPage()
  const s = new Session(page, label, voice, results)
  return { ctx, page, s }
}

function latestOtp(email, after) {
  const files = fs.readdirSync(MAIL_DIR).filter((f) => parseFloat(f) * 1000 >= after).sort().reverse()
  for (const f of files) {
    const t = fs.readFileSync(path.join(MAIL_DIR, f), 'utf8')
    if (t.includes('To: ' + email)) { const m = t.match(/code is:\s+(\d{6})/); if (m) return m[1] }
  }
  return null
}
async function waitOtp(email, after) {
  for (let i = 0; i < 60; i++) { const c = latestOtp(email, after); if (c) return c; await new Promise((r) => setTimeout(r, 250)) }
  throw new Error('no verification email arrived for ' + email)
}

module.exports = { BASE, OUT, Voice, newSession, waitOtp }

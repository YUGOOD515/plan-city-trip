/* 旅游 Agent 网页：只读 /api 显示状态，用户操作写回 /api。所有业务逻辑在 AI 和 trip.py 那边。 */
'use strict';
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = n => Math.round(+n || 0).toLocaleString('en-US');
const app = $('#app');
const LOAD_T = Date.now() / 1000;
let META = null, timers = [], V = {}, focusSeen = LOAD_T;

async function api(path, body) {
  const r = await fetch('/api/' + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { cache: 'no-store' });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
const every = (ms, fn) => { fn(); timers.push(setInterval(fn, ms)); };
const clearTimers = () => { timers.forEach(clearInterval); timers = []; };

/* ---------- 文案 ---------- */
const STAGE_HEAD = { prepare: '准备出行条件', login: '检查登录', collect: '正在收集攻略', digest: '正在提炼要点', plan: '正在排方案', build: '正在生成地图' };
const BY_TAG = { ai: ['ai', 'AI 已填'], chat: ['ai', '来自对话'], profile: ['', '上次使用'] };
const md = d => d ? (+d.slice(5, 7)) + '/' + (+d.slice(8, 10)) : '';
function summary(c) {
  const v = Object.fromEntries(Object.entries(c.form || {}).map(([k, x]) => [k, x.value]));
  const src = (v.sources || []).map(id => (META.sources.find(s => s.id === id) || {}).name || id);
  return [v.date_start && (md(v.date_start) + '–' + md(v.date_end)), v.people && (v.people + ' 人'),
    v.origin && (v.origin + ' 出发'), v.budget && ('人均 ¥' + fmt(v.budget) + ' 内'), src.join(' / ')].filter(Boolean).join(' · ');
}
function aiPill(now, seen, waiting) {
  const el = $('#ai'), age = now - (seen || 0);
  let s = 'off', t = 'AI 未连接';
  if (seen && age < 90) { s = waiting ? 'idle' : 'on'; t = waiting ? 'AI 等你确认' : 'AI 工作中'; }
  else if (seen && age < 3600) { s = 'idle'; t = 'AI 上次动作 ' + Math.max(1, Math.round(age / 60)) + ' 分钟前'; }
  el.dataset.s = s; el.lastElementChild.textContent = t;
}

/* ---------- 路由 ---------- */
async function route() {
  clearTimers(); V = {};
  if (!META) META = await api('meta');
  const m = location.hash.match(/^#\/case\/([\w-]+)/);
  if (m) caseView(m[1]); else home();
  window.scrollTo(0, 0);
}
window.addEventListener('hashchange', route);
function watchFocus(currentId) {  // AI 新建或切换案例时，网页自动跟过去
  every(3000, async () => {
    try {
      const h = await api('home');
      if (h.focus && h.focus_t > focusSeen && h.focus !== currentId) { focusSeen = h.focus_t; location.hash = '#/case/' + h.focus; }
      else if (h.focus_t > focusSeen) focusSeen = h.focus_t;
      if (!currentId) { aiPill(h.now, h.ai_seen); renderHist(h.cases); }
    } catch (e) { /* 服务暂时不可用时静默重试 */ }
  });
}

/* ---------- 首页 ---------- */
function home() {
  document.title = '旅游 Agent';
  app.innerHTML = `
  <section class="hero">
    <p class="kicker">旅游 Agent · 从真人攻略到能走的路线</p>
    <h1 class="display">今天去哪儿旅游？</h1>
    <form class="ask-city" id="cityForm" autocomplete="off">
      <input id="city" name="city" aria-label="城市" placeholder="输入一个城市，比如 大连" maxlength="20" autofocus>
      <button class="btn pri" type="submit">开始规划</button>
    </form>
    <p class="err" id="cityErr" role="alert"></p>
    <p class="hint">AI 会读哔哩哔哩攻略视频的字幕，提炼热门地点和避坑，再结合车票、酒店和天气，排出几套带地图和人均费用的方案。</p>
  </section>
  <section class="hist">
    <div class="sec-h"><h2>做过的规划</h2><span class="muted small tn" id="histN"></span></div>
    <div id="hist"></div>
  </section>`;
  $('#cityForm').addEventListener('submit', async e => {
    e.preventDefault();
    const city = $('#city').value.trim();
    if (!city) { $('#cityErr').textContent = '先输入一个城市'; $('#city').focus(); return; }
    try { const r = await api('cases', { city }); focusSeen = Date.now() / 1000; location.hash = '#/case/' + r.id; }
    catch (err) { $('#cityErr').textContent = err.message; }
  });
  watchFocus(null);
}
function renderHist(cases) {
  const box = $('#hist'); if (!box) return;
  const key = JSON.stringify(cases.map(c => [c.id, c.status, c.stage, c.updated]));
  if (V.hist === key) return; V.hist = key;
  $('#histN').textContent = cases.length ? cases.length + ' 个' : '';
  if (!cases.length) { box.innerHTML = '<div class="empty">还没有规划过。输入一个城市，第一份就从这里开始。</div>'; return; }
  box.innerHTML = '<div class="grid-cards">' + cases.map(c => {
    const st = !c.claimed ? ['run', '等待 AI'] : c.status === 'done' ? ['ok', '完成'] : c.status === 'failed' ? ['bad', '中止'] : ['run', '进行中'];
    const totals = ((c.result || {}).plans || []).map(p => p.total).filter(Boolean);
    const price = totals.length ? `<b class="tn">¥${fmt(Math.min(...totals))}</b>人均起 · ${totals.length} 套方案`
      : c.status === 'done' ? '已完成' : '进行到：' + (META.stages.find(s => s[0] === c.stage) || ['', ''])[1];
    return `<a class="card case-card" href="#/case/${esc(c.id)}">
      <div class="top"><span class="tag ${st[0]}">${st[1]}</span><span class="meta tn">${esc(new Date(c.created * 1000).toLocaleDateString('zh-CN'))}</span></div>
      <h3>${esc(c.city)}</h3>
      <div class="meta tn">${esc([c.date_start && md(c.date_start) + '–' + md(c.date_end), c.people && c.people + ' 人'].filter(Boolean).join(' · '))}</div>
      <div class="price">${price}</div></a>`;
  }).join('') + '</div>';
}

/* ---------- 案例页 ---------- */
function caseView(id) {
  V = { id, key: null, seq: 0, fetched: 0, c: null, dirty: {}, lastAct: 0 };
  app.innerHTML = '<p class="muted" style="padding:48px 0">加载中…</p>';
  every(1000, async () => {
    try { const c = await api('case/' + id); V.c = c; V.fetched = Date.now() / 1000; render(c); }
    catch (e) { if (!V.c) app.innerHTML = `<div class="empty" style="margin-top:48px">${esc(e.message)}。<a href="#/">回首页</a></div>`; }
  });
  timers.push(setInterval(tickAuto, 250));
  watchFocus(id);
}
function modeOf(c) {
  if (c.status === 'done' && c.result && c.result.map) return 'result';
  if (c.stage === 'prepare' && c.stages.prepare.status !== 'done') return 'prepare';
  return 'work';
}
function nowKind(c) {
  const lg = Object.entries(c.login || {}).find(([, v]) => v.state !== 'ok');
  if (c.stage === 'login' && lg) return 'login:' + lg[0];
  if (c.ask && c.ask.kind !== 'prepare') return 'ask:' + c.ask.id;
  return 'stage';
}
function render(c) {
  document.title = c.city + ' · 旅游 Agent';
  aiPill(c.now, c.ai_seen, !!c.ask);
  const mode = modeOf(c);
  const key = mode + (mode === 'work' ? ':' + nowKind(c) + ':' + c.status : '') + (mode === 'prepare' ? ':' + (c.ask ? c.ask.id : '-') + ':' + c.claimed : '');
  if (key !== V.key) {
    V.key = key; V.seq = 0;
    if (mode === 'prepare') buildPrepare(c); else if (mode === 'result') buildResult(c); else buildWork(c);
  }
  if (mode === 'prepare') updatePrepare(c); else if (mode === 'work') updateWork(c);
  updateFeed(c);
  tickAuto();
}
function head(c, kicker) {
  return `<header class="case-h"><div><p class="kicker">${esc(kicker)}</p><h1 class="display">${esc(c.city)}</h1>
    <p class="sum tn" id="sum">${esc(summary(c))}</p></div><div class="res-actions" id="hAct"></div></header>`;
}
function steps(c) {
  return '<ol class="steps" id="steps" style="list-style:none;padding:0">' + META.stages.map(([k, t], i) =>
    `<li class="step" data-k="${k}" data-s="${c.stages[k].status}"><span class="n">${String(i + 1).padStart(2, '0')}</span><b>${t}</b></li>`).join('') + '</ol>';
}
function updateSteps(c) { $$('#steps .step').forEach(el => { el.dataset.s = c.stages[el.dataset.k].status; }); }

/* 用户在操作时告诉服务（倒计时重置） */
let actT = 0;
function activity() {
  if (!V.c || !V.c.ask) return;
  V.lastAct = Date.now() / 1000;
  if (Date.now() - actT > 3000) { actT = Date.now(); api('case/' + V.id + '/activity', {}).catch(() => {}); }
}
['input', 'keydown', 'pointerdown', 'focusin'].forEach(ev => document.addEventListener(ev, e => { if (e.target.closest && e.target.closest('#app')) activity(); }, true));

/* 自动继续条 */
function autoBarHTML() { return '<div class="auto" id="auto" hidden><div class="txt" id="autoTxt"></div><button class="btn sm" id="autoBtn" type="button"></button><i class="line" id="autoLine"></i></div>'; }
function tickAuto() {
  const el = $('#auto'), c = V.c; if (!el || !c) return;
  const a = c.ask;
  if (!a || a.answer != null || $('#qr')) { el.hidden = true; return; }  // 登录不自动跳过，扫码时不显示倒计时
  el.hidden = false;
  const btn = $('#autoBtn');
  if (a.paused) {
    el.dataset.s = 'paused'; $('#autoTxt').textContent = '已暂停自动继续，确认好了点下面的按钮。'; btn.textContent = '恢复自动继续'; btn.dataset.p = '0';
    $('#autoLine').style.width = '0'; return;
  }
  const since = Date.now() / 1000 - V.fetched;
  let remain = Math.max(0, (a.remaining ?? a.timeout) - since);
  if (V.lastAct > V.fetched) remain = Math.max(remain, a.timeout - (Date.now() / 1000 - V.lastAct));
  const miss = a.kind === 'prepare' ? (c.missing || []) : [];
  btn.textContent = '先别继续'; btn.dataset.p = '1';
  if (miss.length && (remain <= 0 || a.blocked)) {
    el.dataset.s = 'blocked'; $('#autoTxt').textContent = '还差 ' + miss.join('、') + '，填好后会自动开始。'; $('#autoLine').style.width = '100%'; return;
  }
  el.dataset.s = 'run';
  const what = a.kind === 'prepare' ? '按当前条件开始' : a.kind === 'pick' ? '按当前勾选继续' : '继续';
  const n = a.kind === 'prepare' ? Object.values(c.form).filter(f => f.by === 'ai' || f.by === 'chat').length : 0;
  $('#autoTxt').innerHTML = (n ? 'AI 已根据对话填好 ' + n + ' 项。' : '') + '<b class="tn">' + Math.ceil(remain) + '</b> 秒内没有操作，就' + what + '；你一动，倒计时就重置。';
  $('#autoLine').style.width = (100 * remain / a.timeout).toFixed(1) + '%';
}
document.addEventListener('click', async e => {
  const b = e.target.closest('#autoBtn'); if (!b || !V.c) return;
  try { await api('case/' + V.id + '/pause', { paused: b.dataset.p === '1' }); } catch (err) { /* 下一次轮询会刷新 */ }
});

/* ---------- 准备：出行条件 + 数据源 ---------- */
function field(f) {
  const id = 'f-' + f.key, req = f.required ? '' : '<span class="req">选填</span>';
  const lab = `<label class="l" for="${id}">${esc(f.label)}${req}<span class="tag ai" data-by hidden></span></label>`;
  let ctl = '';
  if (f.type === 'select') ctl = `<select id="${id}" data-k="${f.key}">${f.options.map(o => `<option>${esc(o)}</option>`).join('')}</select>`;
  else if (f.type === 'tags') ctl = `<div class="chips" id="${id}" data-k="${f.key}" role="group" aria-label="${esc(f.label)}">${f.options.map(o => `<button type="button" class="chip" data-v="${esc(o)}" aria-pressed="false">${esc(o)}</button>`).join('')}</div>`;
  else if (f.type === 'textarea') ctl = `<textarea id="${id}" data-k="${f.key}" placeholder="${esc(f.placeholder || '')}"></textarea>`;
  else {
    ctl = `<input id="${id}" data-k="${f.key}" type="${f.type === 'number' ? 'number' : f.type}" ${f.min != null ? 'min="' + f.min + '"' : ''} ${f.max != null ? 'max="' + f.max + '"' : ''} placeholder="${esc(f.placeholder || '')}" ${f.type === 'number' ? 'inputmode="numeric"' : ''}>`;
    if (f.unit) ctl = `<div class="unit">${ctl}<span>${esc(f.unit)}</span></div>`;
  }
  const full = ['origin', 'interests', 'note'].includes(f.key) ? ' full' : '';
  return `<div class="fld${full}" data-f="${f.key}">${lab}${ctl}</div>`;
}
function buildPrepare(c) {
  const F = k => META.fields.find(f => f.key === k);
  const left = ['origin', 'date_start', 'date_end', 'people', 'budget', 'pace', 'transport', 'interests', 'note'];
  app.innerHTML = head(c, c.claimed ? '第 1 步 · 准备' : '已记下 · 等 AI 接手') + steps(c) + autoBarHTML() +
    (c.claimed ? '' : `<div class="auto" data-s="paused" style="margin-bottom:24px"><div class="txt">AI 还没接手这个案例。可以先把条件填好，AI 连上后会从这里继续。还没打开 AI 的话，在你的 AI 工具里说「用旅游 Agent 规划」。</div></div>`) +
    `<div class="cols prep">
      <section class="card panel"><h2>出行条件</h2><div class="fgrid">${left.map(k => field(F(k))).join('')}</div></section>
      <section>
        <div class="card panel"><h2>数据源</h2><div id="srcs">${META.sources.map(s => `
          <label class="src" data-id="${s.id}" ${s.available ? '' : 'aria-disabled="true"'}>
            <input type="checkbox" value="${s.id}" ${s.available ? '' : 'disabled'}>
            <span><b>${esc(s.name)}</b><small>${esc(s.desc)}</small></span>
            ${s.available ? '<span class="tag" data-login hidden></span>' : '<span class="tag">即将支持</span>'}
          </label>`).join('')}</div>
          <div class="src-n">${field(F('per_keyword'))}</div>
          <p class="note" style="margin-top:16px">下一步会检查哔哩哔哩登录。没登录时这里会出现二维码，用手机 App 扫一下就行；登录信息只存在你自己的电脑上。</p>
        </div>
        <div class="prep-actions"><button class="btn pri" id="go" type="button">开始规划</button><span class="small muted" id="goMsg" role="status"></span></div>
      </section>
    </div>` + feedHTML();
  const send = (k, v) => api('case/' + c.id + '/form', { key: k, value: v }).then(() => { V.dirty[k] = false; }).catch(e => { $('#goMsg').textContent = e.message; });
  const deb = {};
  $$('[data-k]', app).forEach(el => {
    const k = el.dataset.k;
    if (el.classList.contains('chips')) {
      el.addEventListener('click', e => {
        const b = e.target.closest('.chip'); if (!b) return;
        b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') !== 'true');
        V.dirty[k] = true; send(k, $$('.chip[aria-pressed="true"]', el).map(x => x.dataset.v));
      });
      return;
    }
    el.addEventListener('input', () => { V.dirty[k] = true; clearTimeout(deb[k]); deb[k] = setTimeout(() => send(k, el.value), 500); });
    el.addEventListener('change', () => { clearTimeout(deb[k]); V.dirty[k] = true; send(k, el.value); });
  });
  $('#srcs').addEventListener('change', () => {
    V.dirty.sources = true; send('sources', $$('#srcs input:checked').map(x => x.value));
  });
  $('#go').addEventListener('click', async () => {
    $('#go').disabled = true; $('#go').dataset.sent = '1';
    try {
      await Promise.all(Object.keys(deb).map(k => { clearTimeout(deb[k]); const el = $('#f-' + k); return V.dirty[k] && el ? send(k, el.value) : null; }));
      await api('case/' + c.id + '/answer', {});
      $('#goMsg').textContent = V.c && V.c.ask ? '已确认，AI 开始工作' : '已记下，AI 接手后马上开始';
    } catch (e) { $('#goMsg').textContent = e.message; $('#go').disabled = false; delete $('#go').dataset.sent; }
  });
  V.filled = false;
}
function updatePrepare(c) {
  $('#sum').textContent = summary(c);
  updateSteps(c);
  for (const f of META.fields) {
    const st = c.form[f.key] || {}, wrap = $(`[data-f="${f.key}"]`);
    if (f.type === 'sources') {
      $$('#srcs .src').forEach(l => { const i = $('input', l); if (!V.dirty.sources) i.checked = (st.value || []).includes(l.dataset.id); l.dataset.on = i.checked; });
      Object.entries(c.login || {}).forEach(([sid, lg]) => { const t = $(`.src[data-id="${sid}"] [data-login]`); if (t) { t.hidden = lg.state !== 'ok'; t.textContent = '已登录'; t.className = 'tag ok'; } });
      continue;
    }
    if (!wrap) continue;
    const el = $('#f-' + f.key), focused = el && (document.activeElement === el || el.contains(document.activeElement));
    if (!V.dirty[f.key] && !(focused && V.filled)) {
      if (f.type === 'tags') $$('.chip', el).forEach(b => b.setAttribute('aria-pressed', (st.value || []).includes(b.dataset.v)));
      else if (el.value !== String(st.value ?? '')) el.value = st.value ?? '';
    }
    const tag = $('[data-by]', wrap), t = BY_TAG[st.by];
    tag.hidden = !t; if (t) { tag.className = 'tag ' + t[0]; tag.textContent = t[1]; }
    wrap.classList.toggle('miss', !!f.required && (c.missing || []).includes(f.label));
  }
  V.filled = true;
}

/* ---------- 工作中 ---------- */
function buildWork(c) {
  app.innerHTML = head(c, c.status === 'failed' ? '已中止' : 'AI 正在规划') + steps(c) + autoBarHTML() +
    `<div class="cols"><section class="card now" id="now"></section>${feedHTML(true)}</div>`;
  const nk = nowKind(c), box = $('#now');
  if (c.status === 'failed') {
    const st = c.stages[c.stage] || {};
    box.innerHTML = `<p class="stage-t">${esc((META.stages.find(s => s[0] === c.stage) || [])[1] || '')}</p><h2>规划中止</h2><p class="last">${esc(st.note || '')}</p><p class="note" style="margin-top:16px">在 AI 工具里说「继续规划 ${esc(c.city)}」，可以从这一步接着做。</p>`;
    return;
  }
  if (nk.startsWith('ask:')) return buildAsk(c, box);
  if (nk.startsWith('login:')) return buildLogin(c, box, nk.slice(6));
  box.innerHTML = `<p class="stage-t" id="stT"></p><h2 id="stH"></h2><p class="last" id="stL"></p>
    <div id="stP" hidden><div class="bar-p"><i id="stPi"></i></div><div class="pcount"><span id="stPt"></span><span class="tn" id="stPn"></span></div></div>`;
}
function updateWork(c) {
  $('#sum').textContent = summary(c);
  updateSteps(c);
  if (!$('#stH')) { updateLogin(c); return; }
  const i = META.stages.findIndex(s => s[0] === c.stage);
  $('#stT').textContent = '第 ' + (i + 1) + ' 步 · ' + META.stages[i][1];
  $('#stH').textContent = c.status === 'done' ? '规划完成' : STAGE_HEAD[c.stage] || '';
  const last = [...(c.log || [])].reverse().find(l => l.kind !== 'user');
  $('#stL').textContent = last ? last.text : '';
  const p = c.progress;
  $('#stP').hidden = !p || !p.total;
  if (p && p.total) { $('#stPi').style.width = (100 * p.done / p.total) + '%'; $('#stPt').textContent = p.text || ''; $('#stPn').textContent = p.done + ' / ' + p.total; }
}
function buildAsk(c, box) {
  const a = c.ask, isVid = a.tag === 'videos';
  if (a.kind === 'confirm') {
    box.innerHTML = `<p class="stage-t">需要你确认</p><h2>${esc(a.title)}</h2>${a.desc ? `<p class="opt-desc">${esc(a.desc)}</p>` : ''}
      <div class="fld" style="margin-top:24px"><label class="l" for="askNote">补充说明<span class="req">选填</span></label><textarea id="askNote"></textarea></div>
      <div class="prep-actions"><button class="btn pri" id="askGo">确认继续</button><span class="small muted" id="askMsg"></span></div>`;
    $('#askGo').onclick = () => submitAsk({ ok: true, note: $('#askNote').value });
    return;
  }
  const sel = new Set(a.options.filter(o => o.selected).map(o => o.id));
  const meta = o => isVid ? [o.author, o.views != null ? (o.views >= 10000 ? (o.views / 10000).toFixed(1) + ' 万播放' : o.views + ' 播放') : '', o.pubdate].filter(Boolean).join(' · ') : (o.desc || '');
  box.innerHTML = `<p class="stage-t">需要你确认</p><h2>${esc(a.title)}</h2><p class="opt-desc">${esc(a.desc || (isVid ? 'AI 按标题、播放量和发布时间挑好了。取消勾选不想参考的视频，留下的会去下载字幕。' : '勾选想要的选项。'))}</p>
    <div class="opt-tools"><span class="sel tn" id="selN"></span><span><button class="btn sm ghost" id="selAll" type="button">全选</button><button class="btn sm ghost" id="selNone" type="button">全不选</button></span></div>
    <div class="opts" id="opts">${a.options.map(o => `<label class="opt" data-id="${esc(o.id)}"><input type="checkbox" value="${esc(o.id)}">
      <span><span class="t">${esc(o.title)}</span><span class="m">${esc(meta(o))}${o.reason ? ' · ' + esc(o.reason) : ''}${o.url ? ` · <a href="${esc(o.url)}" target="_blank" rel="noopener">打开</a>` : ''}</span></span>
      <span class="r">${esc(o.duration || '')}</span></label>`).join('')}</div>
    ${isVid ? '' : '<div class="fld" style="margin-top:16px"><label class="l" for="askNote">想调整的地方<span class="req">选填</span></label><textarea id="askNote" placeholder="比如：B 方案改成海边为主"></textarea></div>'}
    <div class="prep-actions"><button class="btn pri" id="askGo"></button><span class="small muted" id="askMsg"></span></div>`;
  const sync = () => {
    $$('#opts .opt').forEach(l => { const on = sel.has(l.dataset.id); $('input', l).checked = on; l.dataset.on = on; });
    $('#selN').textContent = '已选 ' + sel.size + ' / ' + a.options.length;
    $('#askGo').textContent = isVid ? '用选中的 ' + sel.size + ' 个继续' : '确认';
    $('#askGo').disabled = !sel.size;
  };
  $('#opts').addEventListener('change', e => { const i = e.target; if (i.checked) sel.add(i.value); else sel.delete(i.value); sync(); });
  $('#selAll').onclick = () => { a.options.forEach(o => sel.add(o.id)); sync(); };
  $('#selNone').onclick = () => { sel.clear(); sync(); };
  $('#askGo').onclick = () => submitAsk({ ids: [...sel], note: $('#askNote') ? $('#askNote').value : '' });
  sync();
}
async function submitAsk(answer) {
  $('#askGo').disabled = true;
  try { await api('case/' + V.id + '/answer', { answer }); $('#askMsg').textContent = '已确认，AI 继续工作'; }
  catch (e) { $('#askMsg').textContent = e.message; $('#askGo').disabled = false; }
}
function buildLogin(c, box, sid) {
  const name = (META.sources.find(s => s.id === sid) || {}).name || sid;
  box.innerHTML = `<p class="stage-t">第 2 步 · 登录</p><h2>用${esc(name)} App 扫码登录</h2>
    <p class="opt-desc">读取视频字幕需要登录。登录信息只保存在你自己的电脑上，不会上传。</p>
    <div class="qr"><div class="code" id="qr" aria-label="登录二维码"></div>
      <div><b id="qrS">等待扫码</b><ol><li>打开手机上的${esc(name)} App</li><li>点右上角「扫一扫」，对准这个二维码</li><li>在手机上点「确认登录」</li></ol>
      <p class="note" style="margin-top:12px">二维码过期会自动刷新。</p></div></div>
    <details class="cookie-box" style="margin-top:24px"><summary class="small muted" style="cursor:pointer">扫不了？改用 cookie 导入</summary>
      <p class="note" style="margin-top:8px">在已登录${esc(name)}的浏览器里，按 F12 → 应用 / Application → Cookie，复制 SESSDATA 等字段，整行粘贴到这里。</p>
      <textarea id="ck" placeholder="SESSDATA=...; bili_jct=...; DedeUserID=..."></textarea>
      <div class="prep-actions" style="margin-top:8px"><button class="btn sm" id="ckGo" type="button">导入</button><span class="small muted" id="ckMsg"></span></div></details>`;
  V.qr = null; V.sid = sid;
  $('#ckGo').onclick = async () => {
    try { const r = await api('case/' + c.id + '/cookie', { source: sid, text: $('#ck').value }); $('#ckMsg').textContent = '已登录：' + r.uname; $('#ck').value = ''; }
    catch (e) { $('#ckMsg').textContent = e.message; }
  };
}
function updateLogin(c) {
  const lg = (c.login || {})[V.sid]; if (!lg || !$('#qr')) return;
  if (lg.qr && lg.qr !== V.qr && window.qrcode) {
    V.qr = lg.qr; const q = qrcode(0, 'M'); q.addData(lg.qr); q.make();
    $('#qr').innerHTML = q.createSvgTag({ cellSize: 4, margin: 0, scalable: true });
  }
  $('#qrS').textContent = { need_scan: '等待扫码', scanned: '已扫码，请在手机上点「确认登录」', ok: '已登录' + (lg.uname ? '：' + lg.uname : ''), error: '登录出错：' + (lg.message || '') }[lg.state] || lg.state;
}

/* ---------- 动态 ---------- */
function feedHTML(side) {
  const inner = `<h3><span>AI 动态</span><span class="tn" id="feedN"></span></h3><ol id="feed"></ol>`;
  return side ? `<aside class="card feed">${inner}</aside>` : `<details class="card feed" style="margin-top:24px;position:static"><summary class="small muted" style="cursor:pointer">查看 AI 动态</summary><div style="margin-top:12px">${inner}</div></details>`;
}
function updateFeed(c) {
  const ol = $('#feed'); if (!ol) return;
  const fresh = (c.log || []).filter(l => l.seq > V.seq); if (!fresh.length) return;
  const atEnd = ol.scrollHeight - ol.scrollTop - ol.clientHeight < 24;
  ol.insertAdjacentHTML('beforeend', fresh.map(l => `<li data-k="${esc(l.kind)}"><time>${new Date(l.t * 1000).toLocaleTimeString('zh-CN', { hour12: false })}</time><span>${esc(l.text)}</span></li>`).join(''));
  V.seq = fresh[fresh.length - 1].seq;
  $('#feedN').textContent = c.log.length + ' 条';
  if (atEnd || fresh.length === c.log.length) ol.scrollTop = ol.scrollHeight;
}

/* ---------- 结果 ---------- */
function buildResult(c) {
  const r = c.result, ps = (r.plans || []).filter(p => p.total), min = Math.min(...ps.map(p => p.total));
  const src = '/files/' + encodeURIComponent(c.id) + '/' + r.map;
  app.innerHTML = head(c, '规划完成') + (r.summary ? `<p class="lede">${esc(r.summary)}</p>` : '') +
    `<div class="plans">${ps.map(p => `<div class="pchip${ps.length > 1 && p.total === min ? ' best' : ''}"><span class="c">方案 ${esc(p.code)}</span><b>${esc(p.name)}</b><span class="v">¥${fmt(p.total)}</span><span class="small muted"> 人均（${esc(r.pax)} 人）</span></div>`).join('')}</div>
    <iframe class="mapframe" id="mf" src="${esc(src)}" title="${esc(c.city)}行程地图"></iframe>` + feedHTML();
  $('#hAct').innerHTML = `<a class="btn" href="${esc(src)}" target="_blank" rel="noopener">全屏打开</a><button class="btn" id="again" type="button">沿用条件重新规划</button>`;
  $('#again').onclick = async () => { const n = await api('cases', { city: c.city, copy_from: c.id }); focusSeen = Date.now() / 1000; location.hash = '#/case/' + n.id; };
  const mf = $('#mf');
  mf.addEventListener('load', () => {
    try {
      const d = mf.contentDocument, fit = () => { mf.style.height = d.documentElement.scrollHeight + 'px'; };
      fit(); new ResizeObserver(fit).observe(d.body);
    } catch (e) { mf.style.height = '1600px'; }
  });
}

route();
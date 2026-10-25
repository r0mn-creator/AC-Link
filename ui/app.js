'use strict';
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const api = async (path, body) => {
  const r = await fetch('/api/' + path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return r.json();
};
const img = (k, id, l = '') => `/img?k=${k}&id=${encodeURIComponent(id)}&l=${encodeURIComponent(l)}`;
const toast = msg => { const t = $('#toast'); t.textContent = msg; t.classList.add('on'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('on'), 2200); };
const fmtHour = h => { const hh = Math.floor(h), mm = Math.round((h - hh) * 60); const ap = hh < 12 ? 'AM' : 'PM'; return `${(hh % 12) || 12}:${String(mm).padStart(2, '0')} ${ap}`; };

// ?demo: screenshot mode for the README, shows a made-up address/code instead of the real ones
const DEMO = new URLSearchParams(location.search).has('demo');
let S = null, META = null, CONTENT = { tracks: [], cars: [] }, SCHEMAS = null, STATUS = null;

// ------------------------------------------------------------------ nav
$$('#menu a').forEach(a => a.onclick = () => go(a.dataset.page));
function go(page) {
  $$('#menu a').forEach(x => x.classList.toggle('on', x.dataset.page === page));
  $$('.page').forEach(p => p.classList.toggle('on', p.id === 'page-' + page));
  if (page === 'settings') renderSettings();
  if (page === 'join') renderRecent();
}
$$('#tabs a').forEach(a => a.onclick = () => tab(a.dataset.tab));
function tab(t) {
  $$('#tabs a').forEach(x => x.classList.toggle('on', x.dataset.tab === t));
  $$('.tab').forEach(x => x.classList.toggle('on', x.id === 'tab-' + t));
  if (t === 'log') loadLog();
  if (t === 'files') renderFiles();
}
document.addEventListener('click', e => { const o = e.target.closest('[data-open]'); if (o) api('open', { what: o.dataset.open }); });

let saveT = null;
function save(part) {
  Object.assign(S, part);
  clearTimeout(saveT);
  saveT = setTimeout(() => api('settings', part), 250);
}

// ------------------------------------------------------------------ status
async function poll() {
  try { STATUS = await api('status'); } catch { return; }
  if (DEMO && STATUS.tailscale.ip) { STATUS.tailscale.ip = '100.101.102.103'; STATUS.tailscale.name = 'gaming-pc'; STATUS.code = 'K7Q2-9XMA'; STATUS.address = '100.101.102.103:8081'; }
  const ts = STATUS.tailscale, el = $('#ts');
  el.className = 'ts ' + (ts.running ? 'ok' : 'bad');
  el.innerHTML = `<i></i>${ts.running ? 'Tailscale ' + esc(ts.ip) : ts.installed ? 'Tailscale is off' : 'Tailscale not installed'}`;
  $('#fw').classList.toggle('hidden', STATUS.firewall !== 'blocked');
  const sv = STATUS.server, on = sv.running;
  document.body.classList.toggle('is-hosting', on);
  $('#hosting').classList.toggle('hidden', !on && !sv.error);
  if (on || sv.error) {
    $('#h-code').textContent = STATUS.code || STATUS.address || 'no Tailscale';
    const st = $('#h-state');
    if (sv.error) st.innerHTML = `<span class="err">The server stopped: ${esc(sv.error)}</span> <a data-goto-log>See the log</a>`;
    else if (!sv.ready) st.textContent = 'Starting the server...';
    else {
      const n = sv.info.clients;
      st.innerHTML = `<b>Ready.</b> ${n ? n + (n === 1 ? ' player' : ' players') + ' connected' : 'Waiting for players.'}`;
    }
    $('#h-joinme').disabled = !sv.ready;
    $('#h-stop').textContent = on ? 'Stop hosting' : 'Close';
    const s = sv.summary;
    if (s) $('#h-facts').innerHTML = [['Track', s.track], ['Weather', s.weather + ', ' + fmtHour(+s.hour)], ['Player spots', s.players],
      ['Traffic', s.traffic ? s.traffic + ' cars' : 'off'], ['Connected', sv.info ? (sv.info.clients || 'nobody yet') : '-'],
      ['Sharing', STATUS.sharing ? 'friends can download missing cars and the track from you' : 'off (needs Tailscale)']]
      .map(([k, v]) => `<div class="k">${k}</div><div>${esc(v)}</div>`).join('');
    $('#h-warn').classList.toggle('hidden', !(sv.warnings || []).length); $('#h-warn').textContent = (sv.warnings || []).join('\n');
  }
}
document.addEventListener('click', e => { if (e.target.closest('[data-goto-log]')) { go('settings'); tab('log'); } });
setInterval(() => api('ping'), 5000);

// ------------------------------------------------------------------ host
const H = () => S.host;
function trackKey(t) { return t.id + '|' + t.layout; }
function currentTrack() { return CONTENT.tracks.find(t => t.id === H().track && t.layout === H().layout); }

function trackName(id) {
  const lays = CONTENT.tracks.filter(x => x.id === id);
  const main = lays.find(l => !l.layout);
  if (main && main.name) return main.name;
  const words = lays.map(l => l.name.split(/\s+/));
  const pre = [];
  for (let i = 0; words.every(w => w[i] && w[i].toLowerCase() === (words[0][i] || '').toLowerCase()); i++) pre.push(words[0][i]);
  return pre.join(' ').replace(/[\s\-–|:,]+$/, '') || id.replace(/^ks_/, '').replace(/_/g, ' ');
}
function renderTracks() {
  const q = $('#t-search').value.trim().toLowerCase();
  const ids = [...new Set(CONTENT.tracks.map(t => t.id))].map(id => ({ id, name: trackName(id) }))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
  const rows = [];
  for (const { id, name } of ids) {             // one row per track; layouts are chips
    const lays = CONTENT.tracks.filter(x => x.id === id);
    const main = lays[0];
    if (q && !(name + ' ' + id + ' ' + lays.map(l => l.name).join(' ')).toLowerCase().includes(q)) continue;
    const traffic = lays.some(l => l.traffic);
    rows.push(`<a data-id="${esc(id)}" class="${id === H().track ? 'on' : ''}"><img loading="lazy" src="${img('preview', main.id, main.layout)}" onerror="this.style.visibility='hidden'">
      <div style="min-width:0"><div class="n">${esc(name)}${traffic ? '<span class="tag">TRAFFIC</span>' : ''}</div>
      <div class="t">${lays.length > 1 ? lays.length + ' layouts' : esc(main.country)}</div></div></a>`);
  }
  $('#t-list').innerHTML = rows.join('') || '<div class="muted" style="padding:12px">No tracks found.</div>';
  $$('#t-list a').forEach(a => a.onclick = () => {
    const lays = CONTENT.tracks.filter(x => x.id === a.dataset.id);
    const pick = lays.find(l => l.traffic) || lays[0];
    setHost({ track: pick.id, layout: pick.layout });
    renderTracks();
  });
}
$('#t-search').oninput = renderTracks;

function setHost(part) { Object.assign(S.host, part); save({ host: S.host }); renderHost(); }

function renderHost() {
  const t = currentTrack();
  $('#t-name').textContent = t ? t.name : 'Pick a track';
  const pv = $('#t-preview'), ol = $('#t-outline');
  if (t) {
    pv.src = img('preview', t.id, t.layout); ol.src = img('outline', t.id, t.layout);
    pv.style.visibility = ol.style.visibility = 'visible';
    pv.onerror = () => pv.style.visibility = 'hidden'; ol.onerror = () => ol.style.visibility = 'hidden';
    $('#bg').style.backgroundImage = `url("${img('preview', t.id, t.layout)}")`; $('#bg').classList.add('on');
  } else { pv.style.visibility = ol.style.visibility = 'hidden'; }
  const lays = t ? CONTENT.tracks.filter(x => x.id === t.id) : [];
  $('#t-layouts').innerHTML = lays.length > 1 ? lays.map(l => `<a data-l="${esc(l.layout)}" class="${l.layout === t.layout ? 'on' : ''}">${esc(l.name)}${l.traffic ? ' · traffic' : ''}</a>`).join('') : '';
  $$('#t-layouts a').forEach(a => a.onclick = () => setHost({ layout: a.dataset.l }));

  $('#c-hour').value = H().hour; $('#c-hour-v').textContent = fmtHour(+H().hour);
  $('#c-weather').value = H().weather;
  const can = !!(t && t.traffic);
  $('#c-traffic').checked = can && H().traffic; $('#c-traffic').disabled = !can;
  $('#c-density').classList.toggle('off', !(can && H().traffic));
  $$('#c-density a').forEach(a => a.classList.toggle('on', a.dataset.v === H().density));
  $('#c-traffic-hint').textContent = t && !can ? 'This track has no traffic lanes.' : '';
  $('#c-players').value = H().max_players;

  const byId = Object.fromEntries(CONTENT.cars.map(c => [c.id, c]));
  $('#c-cars').innerHTML = H().cars.map(id => {
    const c = byId[id] || { id, name: id };
    return `<div class="car" data-id="${esc(id)}"><img src="${img('car', id)}" onerror="this.style.visibility='hidden'"><div class="n" title="${esc(c.name)}">${esc(c.name)}</div><a class="x" title="Remove">×</a></div>`;
  }).join('') + '<a class="car add" id="c-add">+ Add cars</a>';
  $$('#c-cars .x').forEach(x => x.onclick = () => setHost({ cars: H().cars.filter(c => c !== x.parentElement.dataset.id) }));
  $('#c-add').onclick = () => pickCars(H().cars, false, ids => setHost({ cars: ids }));
}
$('#c-hour').oninput = e => { $('#c-hour-v').textContent = fmtHour(+e.target.value); };
$('#c-hour').onchange = e => setHost({ hour: +e.target.value });
$('#c-weather').onchange = e => setHost({ weather: e.target.value });
$('#c-traffic').onchange = e => setHost({ traffic: e.target.checked });
$$('#c-density a').forEach(a => a.onclick = () => setHost({ density: a.dataset.v }));
$('#c-players').onchange = e => setHost({ max_players: Math.max(1, +e.target.value || 1) });

$('#h-host').onclick = async () => {
  $('#h-err').textContent = '';
  $('#h-host').disabled = true;
  const r = await api('host', S.host);
  $('#h-host').disabled = false;
  if (r.error) { $('#h-err').textContent = r.error; return; }
  poll();
};
$('#fw-fix').onclick = async () => {
  $('#fw-fix').disabled = true; $('#fw-fix').textContent = 'Waiting for Windows...';
  const r = await api('fixfw', {});
  $('#fw-fix').disabled = false; $('#fw-fix').textContent = 'Fix firewall';
  toast(r.firewall === 'blocked' ? 'Still blocked. Did you click Yes?' : 'Firewall fixed');
  poll();
};
$('#h-stop').onclick = async () => { await api('stop', {}); poll(); };
$('#h-copy').onclick = () => { navigator.clipboard.writeText($('#h-code').textContent); toast('Code copied'); };
$('#h-joinme').onclick = () => api('join', {}).then(r => r.error ? toast(r.error) : toast('Opening Content Manager...'));

// ------------------------------------------------------------------ car picker
function pickCars(selected, traffic, done) {
  const sel = new Set(selected);
  $('#m-title').textContent = traffic ? 'traffic cars' : 'cars';
  $('#m-traffic').checked = traffic; $('#m-search').value = '';
  const draw = () => {
    const q = $('#m-search').value.trim().toLowerCase(), showT = $('#m-traffic').checked;
    const list = CONTENT.cars.filter(c => (showT || !c.traffic || sel.has(c.id)) && (!q || (c.name + ' ' + c.brand + ' ' + c.id).toLowerCase().includes(q)));
    $('#m-list').innerHTML = list.slice(0, 600).map(c => `<div class="car ${sel.has(c.id) ? 'on' : ''}" data-id="${esc(c.id)}"><img loading="lazy" src="${img('car', c.id)}" onerror="this.style.visibility='hidden'"><div class="n" title="${esc(c.name)}">${esc(c.name)}</div><div class="b">${esc(c.brand || c.id)}${c.traffic ? ' · traffic' : ''}</div></div>`).join('');
    $$('#m-list .car').forEach(el => el.onclick = () => { const id = el.dataset.id; sel.has(id) ? sel.delete(id) : sel.add(id); el.classList.toggle('on'); });
  };
  $('#m-search').oninput = draw; $('#m-traffic').onchange = draw;
  $('#m-done').onclick = () => { $('#modal').classList.add('hidden'); done([...sel]); };
  $('#modal').onclick = e => { if (e.target.id === 'modal') $('#m-done').onclick(); };
  $('#modal').classList.remove('hidden'); draw(); $('#m-search').focus();
}

// ------------------------------------------------------------------ join
const jc = $('#j-code');
jc.oninput = () => {
  const v = jc.value.toUpperCase();
  if (/^[0-9A-Z\- ]*$/.test(v) && !v.includes('.')) {
    const raw = v.replace(/[\s-]/g, '').slice(0, 8);
    jc.value = raw.length > 4 ? raw.slice(0, 4) + '-' + raw.slice(4) : raw;
  }
};
jc.onkeydown = e => { if (e.key === 'Enter') find(); };
$('#j-find').onclick = () => find();
async function find(code) {
  if (code) jc.value = code;
  $('#j-err').textContent = ''; $('#j-result').classList.add('hidden');
  $('#j-find').disabled = true; $('#j-find').textContent = 'Looking...';
  const r = await api('probe', { code: jc.value });
  if (DEMO) jc.value = 'K7Q2-9XMA';
  $('#j-find').disabled = false; $('#j-find').textContent = 'Find';
  if (r.error) { $('#j-err').textContent = r.error; return; }
  const i = r.info, name = String(i.name || '').replace(/\s*ℹ\d+$/, '');
  $('#j-name').textContent = name;
  const tr = CONTENT.tracks.find(t => t.id.toLowerCase() === r.track.toLowerCase());
  $('#j-meta').textContent = `${tr ? tr.name : r.track} · ${i.clients} connected${i.pass ? ' · password' : ''}`;
  $('#j-preview').style.visibility = tr ? 'visible' : 'hidden';
  $('#j-preview').src = tr ? img('preview', tr.id, tr.layout) : '';
  const shared = Object.fromEntries(((r.share && r.share.items) || []).concat((r.share && r.share.skipped) || []).map(i => [i.id, i.name]));
  r.cars.forEach(c => { if (!c.installed && shared[c.id] && shared[c.id] !== c.id) c.name = shared[c.id]; });
  const players = r.cars.filter(c => !c.traffic), traffic = r.cars.filter(c => c.traffic);
  $('#j-cars').innerHTML = players.map(c => `<div class="car ${c.installed ? '' : 'missing'}"><img src="${img('car', c.id)}" onerror="this.style.visibility='hidden'"><div class="n" title="${esc(c.name)}">${esc(c.name)}</div></div>`).join('');
  const miss = [];
  if (!r.track_installed) miss.push(`Track: ${r.track}`);
  const mp = players.filter(c => !c.installed), mt = traffic.filter(c => !c.installed);
  if (mp.length) miss.push('Cars: ' + mp.map(c => c.id).join(', '));
  if (mt.length) miss.push(`Traffic cars (${mt.length}): ` + mt.map(c => c.id).join(', '));
  LAST = r;
  if (r.share) {
    renderShare(r);
    const off = r.share.skipped.filter(x => x.why === 'official' && !(x.kind === 'car' ? CONTENT.cars.some(c => c.id === x.id) : CONTENT.tracks.some(t => t.id.toLowerCase() === x.id.toLowerCase())));
    miss.length = 0;
    if (off.length) miss.push('Official content you need from Steam (Kunos/DLC, not shared): ' + off.map(x => x.name).join(', '));
  } else {
    $('#j-dl').classList.add('hidden');
    if (miss.length) miss.unshift("The host isn't sharing content (older AC Link or blocked port), so get these yourself:");
  }
  $('#j-missing').classList.toggle('hidden', !miss.length);
  $('#j-missing').textContent = miss.join('\n');
  $('#j-result').classList.remove('hidden');
  renderRecent();
}
let LAST = null, dlTimer = null;
const mb = b => b >= 1e9 ? (b / 1e9).toFixed(2) + ' GB' : (b / 1e6).toFixed(b < 1e7 ? 1 : 0) + ' MB';
function renderShare(r) {
  const items = r.share.items, need = items.filter(i => i.state !== 'ok');
  const word = { ok: 'up to date', missing: 'missing', different: 'different version' };
  $('#j-items').innerHTML = items.map(i => `<div class="item"><div>${esc(i.name)} <span class="hint">${i.kind}</span></div><div class="st ${i.state}">${word[i.state]}</div><div class="sz">${i.state === 'ok' ? '' : mb(i.need)}</div></div>`).join('');
  const total = need.reduce((a, i) => a + i.need, 0);
  $('#j-dl').classList.toggle('hidden', !need.length);
  $('#j-get').textContent = `Download ${need.length} item${need.length > 1 ? 's' : ''} (${mb(total)})`;
  $('#j-join').disabled = need.length > 0;
  $('#j-join').title = need.length ? 'Download the missing content first' : '';
}
$('#j-get').onclick = async () => {
  const r = await api('fetch', { code: jc.value });
  if (r.error) { toast(r.error); return; }
  watchFetch();
};
$('#j-cancel').onclick = () => api('fetchcancel', {});
function watchFetch() {
  clearInterval(dlTimer);
  const tick = async () => {
    const st = await api('fetchstatus');
    const on = st.running;
    $('#j-bar').classList.toggle('hidden', !on && !st.finished);
    $('#j-get').classList.toggle('hidden', on); $('#j-cancel').classList.toggle('hidden', !on);
    if (st.total) $('#j-bar i').style.width = (100 * st.done / st.total).toFixed(1) + '%';
    if (on) {
      const left = st.speed ? Math.round((st.total - st.done) / st.speed) : 0;
      $('#j-dlstate').textContent = `${esc(st.item)}: ${mb(st.done)} of ${mb(st.total)}` + (st.speed ? ` · ${mb(st.speed)}/s · ${left > 90 ? Math.round(left / 60) + ' min' : left + ' s'} left` : '');
    } else {
      clearInterval(dlTimer);
      $('#j-dlstate').textContent = st.error || (st.finished ? 'Done. Everything is installed.' : '');
      if (st.finished) {
        const c = await api('content'); if (!c.error) CONTENT = c;
        find(jc.value);
      }
    }
  };
  tick(); dlTimer = setInterval(tick, 700);
}
$('#j-join').onclick = () => api('join', { code: jc.value }).then(r => r.error ? toast(r.error) : toast('Opening Content Manager...'));
function renderRecent() {
  const rc = DEMO ? [] : (S && S.recent_codes) || [];
  $('#j-recent').innerHTML = rc.length ? '<h3>RECENT</h3>' + rc.map(r => `<a data-code="${esc(r.code)}">${esc(r.code)} · ${esc(String(r.name || '').replace(/\s*ℹ\d+$/, ''))}</a>`).join('') : '';
  $$('#j-recent a').forEach(a => a.onclick = () => find(a.dataset.code));
}

// ------------------------------------------------------------------ settings
function renderSettings() {
  renderGeneral(); renderRules(); renderTraffic(); renderAssetto(); renderPlugins(); renderCsp();
}

function optRow({ name, key, desc, input, changed, onReset }) {
  const d = document.createElement('div');
  d.className = 'opt' + (changed ? ' changed' : '');
  d.innerHTML = `<div class="name">${esc(name)}${key ? `<span class="key">${esc(key)}</span>` : ''}</div><div class="ctl"></div><a class="reset" title="Back to default">↺</a>${desc ? `<div class="desc">${esc(desc)}</div>` : ''}`;
  d.querySelector('.ctl').append(input);
  d.querySelector('.reset').onclick = onReset || (() => {});
  return d;
}
function el(html) { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; }

function renderGeneral() {
  const g = $('#tab-general'); g.innerHTML = '';
  const ts = STATUS ? STATUS.tailscale : {};
  g.append(el(`<div class="helpbox"><b>Tailscale</b>: ${ts.running ? `on, this PC is <code>${esc(ts.ip)}</code> (${esc(ts.name || '')})` : ts.installed ? '<span class="err">installed but not connected. Open Tailscale and log in.</span>' : '<span class="err">not installed.</span> Get it from tailscale.com/download.'}
    <ol><li><b>Host:</b> in the Tailscale admin page (Machines), click ⋯ next to this PC, then <b>Share</b>, and send it to your friend.</li>
    <li><b>Friend:</b> accepts the share with their own Tailscale account. Your PC then shows up in their Tailscale.</li>
    <li>Optional, recommended: in <b>Access controls</b>, limit shared users to the game ports:<br><code>{"action":"accept","src":["autogroup:shared"],"dst":["${esc(ts.name || 'this-pc')}:${S.ports.tcp},${S.ports.http},${S.ports.http + 1}"]}</code></li>
    <li>The first time you host, Windows asks to allow AssettoServer and AC Link through the firewall: allow both.</li>
    <li>While you host, friends can download the cars and track they're missing from you (port ${S.ports.http + 1}, Tailscale only). Official Kunos cars, tracks and DLC are never shared.</li></ol></div>`));
  const txt = (val, on, type = 'text') => { const i = el(`<input type="${type}">`); i.value = val; i.onchange = () => on(type === 'number' ? +i.value : i.value); return i; };
  g.append(optRow({ name: 'Assetto Corsa folder', desc: STATUS && STATUS.ac_path ? 'Found: ' + STATUS.ac_path : 'Not found', input: txt(S.ac_path, v => save({ ac_path: v })), changed: !!S.ac_path, onReset: () => { save({ ac_path: '' }); renderGeneral(); } }));
  g.append(optRow({ name: 'Server name', desc: 'Shown to your friends in Content Manager.', input: txt(S.server_name, v => save({ server_name: v })), changed: !!S.server_name, onReset: () => { save({ server_name: '' }); renderGeneral(); } }));
  g.append(optRow({ name: 'Game port (TCP + UDP)', input: txt(S.ports.tcp, v => save({ ports: { ...S.ports, tcp: v } }), 'number'), changed: S.ports.tcp !== 9600, onReset: () => { save({ ports: { ...S.ports, tcp: 9600 } }); renderGeneral(); } }));
  g.append(optRow({ name: 'HTTP port', desc: 'Part of the join code. Change both ports only if 9600/8081 are taken.', input: txt(S.ports.http, v => save({ ports: { ...S.ports, http: v } }), 'number'), changed: S.ports.http !== 8081, onReset: () => { save({ ports: { ...S.ports, http: 8081 } }); renderGeneral(); } }));
  g.append(optRow({ name: 'Session length (minutes)', desc: 'Free roam session; it restarts after this.', input: txt(S.sessions.practice_minutes, v => save({ sessions: { ...S.sessions, practice_minutes: v } }), 'number'), changed: S.sessions.practice_minutes !== 600, onReset: () => { save({ sessions: { practice_minutes: 600 } }); renderGeneral(); } }));
  const rowb = el('<div class="row" style="margin-top:14px"><button id="g-setup">Check setup</button><button data-open="server">Open server folder</button></div>');
  g.append(rowb); rowb.querySelector('#g-setup').onclick = () => setupCheck(true);
}

function renderRules() {
  const g = $('#tab-rules'); g.innerHTML = '';
  for (const [k, m] of Object.entries(META.server_keys)) {
    const cur = k in S.server ? S.server[k] : m.default;
    let input;
    const set = v => { const s = { ...S.server }; if (v === m.default) delete s[k]; else s[k] = v; save({ server: s }); row.classList.toggle('changed', k in s); };
    if (m.kind === 'bool') { input = el(`<label class="toggle"><input type="checkbox"><span></span></label>`); const c = input.querySelector('input'); c.checked = !!+cur; c.onchange = () => set(c.checked ? 1 : 0); }
    else if (Array.isArray(m.kind)) { input = el('<select></select>'); input.innerHTML = m.kind.map(o => { const [v, l] = o.split(':'); return `<option value="${v}">${esc(l)}</option>`; }).join(''); input.value = cur; input.onchange = () => set(+input.value); }
    else { input = el(`<input type="${m.kind === 'int' ? 'number' : 'text'}">`); input.value = cur; input.onchange = () => set(m.kind === 'int' ? +input.value : input.value); }
    const row = optRow({ name: m.label, key: k, desc: m.help, input, changed: k in S.server, onReset: () => { const s = { ...S.server }; delete s[k]; save({ server: s }); renderRules(); } });
    g.append(row);
  }
}

// --- schema-driven forms (AssettoServer ships JSON schemas for every option)
function resolve(schema, p) {
  if (p.$ref) return schema.definitions[p.$ref.split('/').pop()];
  if (p.oneOf && p.oneOf.length === 1 && p.oneOf[0].$ref) return { ...p, ...schema.definitions[p.oneOf[0].$ref.split('/').pop()], description: p.description, default: p.default };
  if (p.allOf && p.allOf.length === 1 && p.allOf[0].$ref) return { ...p, ...schema.definitions[p.allOf[0].$ref.split('/').pop()], description: p.description, default: p.default };
  return p;
}
const getPath = (o, path) => path.reduce((a, k) => (a && typeof a === 'object' ? a[k] : undefined), o);
function setPath(o, path, v) {
  const last = path[path.length - 1], parent = path.slice(0, -1).reduce((a, k) => (a[k] = a[k] && typeof a[k] === 'object' ? a[k] : {}), o);
  if (v === undefined) delete parent[last]; else parent[last] = v;
  for (let i = path.length - 1; i > 0; i--) { const pp = getPath(o, path.slice(0, i)); if (pp && !Object.keys(pp).length) { const q = getPath(o, path.slice(0, i - 1)) || o; delete (i === 1 ? o : q)[path[i - 1]]; } }
}
function schemaForm(container, schema, props, path, overrides, base, onChange, skip = []) {
  for (const [k, raw] of Object.entries(props)) {
    if (skip.includes(k)) continue;
    const p = resolve(schema, raw), fp = [...path, k];
    const ov = getPath(overrides, fp), bs = getPath(base, fp);
    const types = [].concat(p.type || []);
    if (p.properties && (types.includes('object') || !types.length)) {
      container.append(el(`<div class="group">${esc(k.replace(/([a-z])([A-Z])/g, '$1 $2').toUpperCase())}</div>`));
      if (p.description) container.append(el(`<div class="hint">${esc(p.description)}</div>`));
      schemaForm(container, schema, p.properties, fp, overrides, base, onChange);
      continue;
    }
    const def = bs !== undefined ? bs : p.default;
    const cur = ov !== undefined ? ov : def;
    const changed = ov !== undefined;
    const set = v => { setPath(overrides, fp, JSON.stringify(v) === JSON.stringify(def) ? undefined : v); onChange(); row.classList.toggle('changed', getPath(overrides, fp) !== undefined); };
    let input;
    const enumv = p.enum;
    if (enumv) { input = el('<select></select>'); input.innerHTML = enumv.map(v => `<option>${esc(v)}</option>`).join(''); input.value = cur; input.onchange = () => set(input.value); }
    else if (types.includes('boolean') && types.filter(t => t !== 'null').length === 1) { input = el('<label class="toggle"><input type="checkbox"><span></span></label>'); const c = input.querySelector('input'); c.checked = !!cur; c.onchange = () => set(c.checked); }
    else if ((types.includes('integer') || types.includes('number')) && !types.includes('array')) {
      input = el('<input type="number">'); input.value = cur ?? ''; if (types.includes('number')) input.step = 'any';
      input.onchange = () => set(input.value === '' ? (types.includes('null') ? null : def) : +input.value);
    } else if (types.includes('string') && types.filter(t => t !== 'null').length === 1) { input = el('<input type="text">'); input.value = cur ?? ''; input.onchange = () => set(input.value === '' && types.includes('null') ? null : input.value); }
    else {                                          // arrays / objects / mixed: JSON
      input = el('<textarea spellcheck="false"></textarea>'); input.value = cur == null ? '' : JSON.stringify(cur, null, 1);
      input.onchange = () => { if (input.value.trim() === '') return set(null); try { set(JSON.parse(input.value)); input.style.borderColor = ''; } catch { input.style.borderColor = 'var(--err)'; } };
    }
    const row = optRow({ name: k.replace(/([a-z])([A-Z])/g, '$1 $2'), key: k, desc: p.description, input, changed, onReset: () => { setPath(overrides, fp, undefined); onChange(); container.dispatchEvent(new Event('rerender')); } });
    container.append(row);
  }
}

async function ensureSchemas() { if (!SCHEMAS) SCHEMAS = await api('schemas'); return SCHEMAS; }

async function renderAssetto() {
  const g = $('#tab-assetto'); const sc = (await ensureSchemas()).extra;
  g.innerHTML = '<div class="helpbox">Every AssettoServer option, straight from its own schema. Options you change get a red mark; ↺ puts one back. Traffic options are in the TRAFFIC tab and plugins in PLUGINS. Changes apply the next time you host.</div>';
  const box = document.createElement('div'); g.append(box);
  schemaForm(box, sc, sc.properties || {}, [], S.extra, META.extra_base, () => save({ extra: S.extra }), ['AiParams', 'EnablePlugins', 'EnableAi']);
  box.addEventListener('rerender', renderAssetto, { once: true });
}

async function renderTraffic() {
  const g = $('#tab-traffic'); const sc = (await ensureSchemas()).extra;
  g.innerHTML = '';
  const byId = Object.fromEntries(CONTENT.cars.map(c => [c.id, c]));
  const tc = S.traffic_cars.length ? S.traffic_cars : [];
  g.append(el(`<div class="helpbox">Traffic needs a track with an AI traffic spline (<code>ai/fast_lane.aip</code>); tracks that have one are tagged TRAFFIC. Everyone who joins needs the same traffic cars installed.</div>`));
  const cnt = el('<input type="number" min="0" max="200">'); cnt.value = S.traffic_count; cnt.onchange = () => save({ traffic_count: +cnt.value });
  g.append(optRow({ name: 'Traffic cars on the server', desc: 'AI car slots. More = denser traffic, but each needs a pit spot on the track.', input: cnt, changed: S.traffic_count !== 30, onReset: () => { save({ traffic_count: 30 }); renderTraffic(); } }));
  const models = el(`<div class="carchips"></div>`);
  models.innerHTML = (tc.length ? tc.map(id => `<div class="car"><img src="${img('car', id)}" onerror="this.style.visibility='hidden'"><div class="n">${esc((byId[id] || {}).name || id)}</div></div>`).join('') : '<div class="muted" style="padding:8px 0">Automatic: the biggest traffic pack you have installed.</div>') + '<a class="car add" id="tc-add">Choose traffic cars</a>';
  g.append(el('<h3>TRAFFIC CAR MODELS</h3>')); g.append(models);
  $('#tc-add').onclick = () => pickCars(tc, true, ids => { save({ traffic_cars: ids }); renderTraffic(); });
  if (tc.length) { const b = el('<button style="margin-top:8px">Back to automatic</button>'); b.onclick = () => { save({ traffic_cars: [] }); renderTraffic(); }; g.append(b); }
  const ai = resolve(sc, sc.properties.AiParams);
  const box = document.createElement('div'); g.append(el('<h3>TRAFFIC BEHAVIOUR</h3>')); g.append(box);
  if (!S.extra.AiParams) S.extra.AiParams = {};
  const base = { AiParams: { TrafficDensity: 1, TwoWayTraffic: true } };
  schemaForm(box, sc, ai.properties, ['AiParams'], S.extra, base, () => save({ extra: S.extra }));
  box.addEventListener('rerender', renderTraffic, { once: true });
}

async function renderPlugins() {
  const g = $('#tab-plugins'); const pl = (await ensureSchemas()).plugins;
  g.innerHTML = '<div class="helpbox">AssettoServer plugins that come with it. Turn one on to show its options.</div>';
  for (const p of pl) {
    const st = S.plugins[p.name] || { enabled: false, cfg: {} };
    const card = el(`<div class="plugin"><div class="plugin-head"><div class="pn">${esc(p.name.replace(/Plugin$/, '').replace(/([a-z])([A-Z])/g, '$1 $2'))}</div><label class="toggle"><input type="checkbox"><span></span></label></div><div class="pbody"></div></div>`);
    const cb = card.querySelector('input'), body = card.querySelector('.pbody');
    cb.checked = !!st.enabled;
    const draw = () => {
      body.innerHTML = '';
      if (cb.checked && p.schema && p.schema.properties) {
        st.cfg = st.cfg || {};
        schemaForm(body, p.schema, p.schema.properties, [], st.cfg, {}, () => { S.plugins[p.name] = st; save({ plugins: S.plugins }); });
        body.addEventListener('rerender', draw, { once: true });
      }
    };
    cb.onchange = () => { st.enabled = cb.checked; S.plugins[p.name] = st; save({ plugins: S.plugins }); draw(); };
    draw(); g.append(card);
  }
}

function renderCsp() {
  const g = $('#tab-csp'); g.innerHTML = '';
  g.append(el('<div class="helpbox">Raw <code>csp_extra_options.ini</code> sent to players with CSP (for example <code>[EXTRA_RULES]</code> with <code>DISABLE_RAIN_PHYSICS=1</code>). Leave empty for none.</div>'));
  const t = el('<textarea spellcheck="false" style="width:100%;min-height:320px;font:12px Consolas,monospace"></textarea>');
  t.value = S.csp_extra || ''; t.onchange = () => save({ csp_extra: t.value }); g.append(t);
}

async function renderFiles() {
  const g = $('#tab-files'); g.innerHTML = '<div class="muted">Building...</div>';
  await new Promise(r => setTimeout(r, 300));
  const r = await api('preview', {});
  if (r.error) { g.innerHTML = `<div class="err">${esc(r.error)}</div>`; return; }
  const names = Object.keys(r.files);
  g.innerHTML = '<div class="helpbox">What Host writes for AssettoServer with the current choices (read only; change things in the other tabs).</div><div class="filetabs"></div><pre></pre><div class="row"><button data-open="server">Open server folder</button></div>';
  const ft = g.querySelector('.filetabs'), pre = g.querySelector('pre');
  ft.innerHTML = names.map((n, i) => `<a class="${i ? '' : 'on'}">${esc(n)}</a>`).join('');
  pre.textContent = r.files[names[0]] || '';
  [...ft.children].forEach((a, i) => a.onclick = () => { [...ft.children].forEach(x => x.classList.remove('on')); a.classList.add('on'); pre.textContent = r.files[names[i]]; });
  if (r.warnings.length) g.prepend(el(`<div class="warn">${esc(r.warnings.join('\n'))}</div>`));
}

async function loadLog() {
  const r = await api('log?n=400');
  const pre = $('#log'); const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
  pre.textContent = r.lines.join('\n') || 'No log yet. Host a game first.';
  if (atEnd) pre.scrollTop = pre.scrollHeight;
}
$('#log-refresh').onclick = loadLog;
setInterval(() => { if ($('#tab-log').classList.contains('on') && $('#page-settings').classList.contains('on')) loadLog(); }, 2000);

// ------------------------------------------------------------------ first-run setup
async function setupCheck(force) {
  const st = await api('setup');
  const ts = st.tailscale;
  if (DEMO && ts.ip) { ts.ip = '100.101.102.103'; st.ac = String.raw`C:\Program Files (x86)\Steam\steamapps\common\assettocorsa`; }
  const rows = [
    ['server', st.server, 'AssettoServer', st.server ? `Installed (${st.server_tag})` : 'The game server AC Link runs for you. About 55 MB from its official GitHub release.',
      st.server ? '' : `<button class="primary" id="setup-dl" ${st.install.running ? 'disabled' : ''}>${st.install.running ? esc(st.install.stage) + '...' : 'Download'}</button>`],
    ['ac', !!st.ac, 'Assetto Corsa', st.ac ? esc(st.ac) : "Not found. Install it from Steam, or set its folder in Settings > General.", st.ac ? '' : '<a class="link" data-link="ac">Steam page</a>'],
    ['cm', st.cm, 'Content Manager', st.cm ? 'Installed' : 'Needed to join games. Download it, run it once, and point it at Assetto Corsa.', st.cm ? '' : '<a class="link" data-link="cm">Download</a>'],
    ['csp', st.csp, 'Custom Shaders Patch', st.csp ? 'Installed' : 'Needed for traffic and rain. In Content Manager: Settings > Custom Shaders Patch > Install.', st.csp ? '' : '<a class="link" data-link="csp">About CSP</a>'],
    ['tailscale', ts.running, 'Tailscale', ts.running ? `Connected (${esc(ts.ip)})` : ts.installed ? 'Installed but not connected. Open Tailscale and sign in.' : 'Connects you and your friends privately. Each player uses their own free account.',
      ts.installed ? '' : '<a class="link" data-link="tailscale">Download</a>'],
  ];
  $('#setup-list').innerHTML = rows.map(([id, ok, name, d, act]) => `<div class="req"><span class="${ok ? 'ok' : 'no'}">${ok ? '✓' : '✗'}</span><b>${name}</b><span>${act}</span><span class="d">${d}</span></div>`).join('');
  $$('#setup-list [data-link]').forEach(a => a.onclick = () => api('link', { id: a.dataset.link }));
  const dl = $('#setup-dl'); if (dl) dl.onclick = async () => { await api('install_server', {}); watchInstall(); };
  $('#setup-err').textContent = st.install.error || '';
  $('#setup-done').disabled = !st.server;
  const missing = rows.some(r => !r[1]);
  if (force || missing) $('#setup-modal').classList.remove('hidden');
  if (st.install.running) watchInstall();
  return st;
}
let instT = null;
function watchInstall() {
  clearInterval(instT);
  instT = setInterval(async () => {
    const st = await api('setup'), i = st.install;
    $('#setup-bar').classList.toggle('hidden', !i.running);
    if (i.total) $('#setup-bar i').style.width = (100 * i.done / i.total).toFixed(1) + '%';
    const b = $('#setup-dl'); if (b) { b.disabled = i.running; b.textContent = i.running ? i.stage + '...' : 'Download'; }
    if (!i.running) { clearInterval(instT); SCHEMAS = null; setupCheck(); if (st.server) toast('AssettoServer installed'); }
  }, 600);
}
$('#setup-recheck').onclick = () => setupCheck(true);
$('#setup-done').onclick = () => $('#setup-modal').classList.add('hidden');

// ------------------------------------------------------------------ closing while hosting
window.confirmClose = () => $('#confirm').classList.remove('hidden');
$('#cf-keep').onclick = () => $('#confirm').classList.add('hidden');
$('#cf-close').onclick = () => { $('#cf-close').disabled = true; $('#cf-close').textContent = 'Stopping...'; api('quit', {}); };

// ------------------------------------------------------------------ start
(async () => {
  const st = await api('settings'); S = st.settings; META = st;
  $('#c-weather').innerHTML = META.weathers.map(w => `<option value="${w.id}">${esc(w.name)}</option>`).join('');
  await poll(); setInterval(poll, 2000);
  if (!DEMO || location.hash === '#setup') setupCheck(location.hash === '#setup');
  $('#t-list').innerHTML = '<div class="muted" style="padding:12px">Loading your tracks and cars...</div>';
  const c = await api('content');
  if (c.error) { $('#t-list').innerHTML = `<div class="err" style="padding:12px">${esc(c.error)}</div>`; renderHost(); return; }
  CONTENT = c;
  CONTENT.tracks.sort((a, b) => (a.layout ? 1 : 0) - (b.layout ? 1 : 0));
  renderTracks(); renderHost();
  const [pg, tb] = location.hash.slice(1).split('/');
  if (pg && pg !== 'setup') go(pg); if (tb === 'confirm') confirmClose(); else if (tb) pg === 'join' ? find(decodeURIComponent(tb)) : tab(tb);
  const on = $('#t-list a.on'); if (on) $('#t-list').scrollTop = on.offsetTop - $('#t-list').clientHeight / 2;
})();

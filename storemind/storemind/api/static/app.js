/* StoreMind dashboard - vanilla JS, no build step, no dependencies.
 *
 * Live updates come over a WebSocket. If it drops (store Wi-Fi is not
 * data-centre Wi-Fi) the page keeps polling /api/state, so it degrades to
 * "a few seconds behind" instead of "blank".
 */
'use strict';

const $ = (id) => document.getElementById(id);
const POLL_MS = 3000;

let socketAlive = false;
let lastUpdate = 0;

/* ---------- small helpers ---------- */

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function secs(value) {
  if (value === null || value === undefined) return '-';
  const s = Math.round(value);
  if (s < 60) return s + ' s';
  const m = Math.floor(s / 60);
  return m + ' m ' + String(s % 60).padStart(2, '0') + ' s';
}

function rupees(value) {
  if (!value) return '₹0';
  return '₹' + Math.round(value).toLocaleString('en-IN');
}

function clockText(iso) {
  if (!iso) return '-';
  return iso.slice(11, 19);
}

/* ---------- rendering ---------- */

function renderTiles(s) {
  $('store-name').textContent = s.store + ' · ' + s.node;
  $('clock').textContent = clockText(s.now);
  $('v-entries').textContent = s.footfall.entries;
  $('v-exits').textContent = s.footfall.exits;
  $('v-occupancy').textContent = s.footfall.occupancy;
  $('privacy').textContent = 'Video stored: ' + (s.video_bytes_stored || 0) + ' bytes';

  const f = s.forecast;
  const tile = $('tile-forecast');
  if (f) {
    $('v-counters').textContent = f.recommended_counters;
    const needMore = f.recommended_counters > f.open_counters;
    tile.classList.toggle('alert', needMore);
    let sub = f.basis === 'warmup'
      ? 'warming up - not enough door history yet'
      : ('λ̂ ' + f.lambda_hat_per_min + '/min · ' + f.open_counters + ' open');
    if (needMore && f.eta_min) sub += ' · in ~' + Math.round(f.eta_min) + ' min';
    if (f.pred_wait_s) sub += ' · predicted wait ' + secs(f.pred_wait_s);
    $('v-forecast-sub').textContent = sub;
  } else {
    $('v-counters').textContent = '-';
    tile.classList.remove('alert');
  }

  const risk = s.lost_sales ? s.lost_sales.total_lost_sale_risk_inr : 0;
  $('v-risk').textContent = rupees(risk);
  $('tile-risk').classList.toggle('alert', risk > 0);
}

function renderAlerts(s) {
  const list = $('alerts');
  // Alerts from other processes (e.g. the sensor bridge: SENSOR_LINK) join the list.
  const external = (s.platform && s.platform.external_alerts) || [];
  s = Object.assign({}, s, { alerts: s.alerts.concat(external) });
  if (!s.alerts.length) {
    list.innerHTML = '<li class="empty">No alerts</li>';
    return;
  }
  list.innerHTML = s.alerts.map((a) => `
    <li class="${esc(a.severity)}${a.ack ? ' acked' : ''}">
      <span class="sev">${esc(a.severity)}</span>
      <span class="msg">${esc(a.message)}</span>
      <button class="ack" data-id="${esc(a.alert_id)}" ${a.ack ? 'disabled' : ''}>
        ${a.ack ? 'acknowledged' : 'acknowledge'}
      </button>
    </li>`).join('');
  list.querySelectorAll('button.ack').forEach((button) => {
    button.addEventListener('click', async () => {
      button.disabled = true;
      button.textContent = 'acknowledged';
      button.closest('li').classList.add('acked');
      await fetch('/api/alerts/' + encodeURIComponent(button.dataset.id) + '/ack',
                  { method: 'POST' }).catch(() => {});
    });
  });
}

function renderCounters(s) {
  const box = $('counters');
  const names = Object.keys(s.counters);
  if (!names.length) {
    box.innerHTML = '<p class="empty">No counter camera configured</p>';
    return;
  }
  box.innerHTML = names.map((name) => {
    const c = s.counters[name];
    const width = Math.min(100, (c.queue_len_smooth / 8) * 100);
    return `
      <div class="counter${c.congested ? ' congested' : ''}">
        <span class="name">${esc(name)}${c.open ? '' : ' (closed)'}</span>
        <span>${c.queue_len} waiting</span>
        <div class="queuebar"><span style="width:${width}%"></span></div>
        <div class="stats">
          <span>median wait ${secs(c.median_wait_s)}</span>
          <span>median service ${secs(c.median_service_s)}</span>
          <span>${c.services} served</span>
        </div>
      </div>`;
  }).join('');
}

function renderShelves(s) {
  const box = $('shelves');
  const names = Object.keys(s.shelves);
  if (!names.length) {
    box.innerHTML = '<p class="empty">No shelf camera configured</p>';
    return;
  }
  box.innerHTML = names.map((shelfName) => {
    const shelf = s.shelves[shelfName];
    const slots = Object.entries(shelf.slots || {}).map(([slotName, slot]) => `
      <div class="slot ${esc(slot.state)}">
        <span class="sku">${esc(slot.sku || slotName)}</span>
        <span class="state">${esc(slot.state)}</span>
        <div class="muted">${slot.fill === null ? '' : 'fill ' + Math.round(slot.fill * 100) + '%'}</div>
      </div>`).join('');
    return `<div class="shelf">
        <h3>${esc(shelfName)}</h3>
        <div class="slots">${slots}</div>
        <button class="restock" data-cam="${esc(shelf.camera)}" data-shelf="${esc(shelfName)}">
          Mark restocked
        </button>
      </div>`;
  }).join('');
  box.querySelectorAll('button.restock').forEach((button) => {
    button.addEventListener('click', async () => {
      button.textContent = 'capturing reference...';
      const url = '/api/restock?camera=' + encodeURIComponent(button.dataset.cam)
                + '&shelf=' + encodeURIComponent(button.dataset.shelf);
      const response = await fetch(url, { method: 'POST' }).catch(() => null);
      const body = response ? await response.json().catch(() => null) : null;
      button.textContent = body && body.ok
        ? 'reference captured (' + body.slots_referenced + ' slots)'
        : 'could not capture - is the camera live?';
      setTimeout(() => { button.textContent = 'Mark restocked'; }, 4000);
    });
  });
}

function renderHealth(s) {
  const rows = [];
  const h = s.health;
  if (h) {
    const fps = Object.entries(h.fps || {}).map(([k, v]) => k + ' ' + v).join(', ');
    rows.push(['Frames per second', fps || '-']);
    rows.push(['CPU', h.cpu_percent === null ? '-' : h.cpu_percent + ' %']);
    rows.push(['CPU temperature', h.cpu_temp_c === null ? 'not available' : h.cpu_temp_c + ' °C']);
    rows.push(['Memory', h.mem_percent === null ? '-' : h.mem_percent + ' %']);
    rows.push(['Uptime', secs(h.uptime_s)]);
  }
  rows.push(['Events emitted', s.events_emitted]);
  rows.push(['Video bytes stored', String(s.video_bytes_stored || 0)]);
  $('health').innerHTML = '<tbody>' + rows.map(([k, v]) =>
    `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('') + '</tbody>';

  $('cameras').innerHTML = (s.cameras || []).map((c) => `
    <div class="cam${c.tamper ? ' tamper' : ''}">
      <span class="n">${esc(c.name)}</span>
      <span class="muted"> ${esc(c.role)} · ${c.tracks_now} tracked</span>
      ${c.infer_ms ? '<span class="muted"> · ' + c.infer_ms + ' ms</span>' : ''}
      ${c.tamper ? '<span class="bad"> · TAMPER</span>' : ''}
    </div>`).join('');
}

/* ---------- platform panels (M7) ---------- */

function kvTable(node, rows) {
  node.innerHTML = '<tbody>' + rows.map(([k, v]) =>
    `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('') + '</tbody>';
}

function na(value, unit) {
  return value === null || value === undefined ? 'not measured' : value + (unit || '');
}

function renderCamHealth(p) {
  const cams = p.camera_health || [];
  $('camhealth').innerHTML = cams.length ? cams.map((c) => `
    <div class="camrow">
      <span class="led ${esc(c.colour)}" title="${esc(c.state)}"></span>
      <span class="n">${esc(c.cam)}</span>
      <span class="muted">${esc(c.state)}</span>
      <span>${c.fps === null || c.fps === undefined ? '' : c.fps + ' fps'}</span>
      <span class="muted">${c.lag_ms ? Math.round(c.lag_ms) + ' ms lag' : ''}</span>
      <span class="muted">${c.reconnects ? c.reconnects + ' reconnects' : ''}</span>
    </div>`).join('') : '<p class="empty">No cameras</p>';
}

function renderSensors(p) {
  const s = p.sensors;
  const box = $('sensors');
  if (!s || !s.enabled) {
    box.innerHTML = '<p class="empty">Sensor node not enabled (sensors.enabled in the config)</p>';
    $('nodebuttons').hidden = true;
    return;
  }
  const nodes = s.nodes.map((n) => `
    <div class="camrow">
      <span class="led ${esc(n.colour)}"></span>
      <span class="n">${esc(n.node)}</span>
      <span class="muted">link ${esc(n.link)} &middot; up ${secs(n.uptime_s)} &middot; heap ${n.free_heap} B &middot;
        stack ${n.min_stack_words} words &middot; reset ${esc(n.reset_cause)}</span>
      <span class="${n.crc_err || n.uart_err || n.i2c_err ? 'bad' : 'muted'}">
        errors: crc ${n.crc_err} &middot; uart ${n.uart_err} &middot; i2c ${n.i2c_err}</span>
    </div>`).join('') || '<p class="empty">No heartbeat from the node yet</p>';
  const latest = s.latest || {};
  const rows = [];
  (latest.WEIGHT || []).forEach((w) => rows.push(['Load cell ' + w.slot, w.grams + ' g' + (w.stable ? '' : ' (moving)')]));
  (latest.ENVIRONMENT || []).forEach((e) => rows.push(['Light / climate',
    [e.lux === null ? null : e.lux + ' lux', e.temp_c === null ? null : e.temp_c + ' °C',
     e.rh_pct === null ? null : e.rh_pct + ' %RH'].filter(Boolean).join(' · ') || 'not fitted']));
  (latest.PRESENCE || []).forEach((z) => rows.push(['PIR ' + z.zone, z.active ? 'motion' : 'quiet']));
  Object.entries(s.doors || {}).forEach(([door, c]) => rows.push(['Door ' + door + ' (IR beams)', c.in + ' in · ' + c.out + ' out']));
  (latest.SHELF_MOTION || []).forEach((m) => rows.push(['Shelf ' + m.shelf + ' (MEMS ' + m.node + ')', m.kind + ' · ' + m.peak_mg + ' mg']));
  (latest.CAMERA_MOUNT || []).forEach((m) => rows.push(['Camera ' + m.cam + ' bracket', m.kind + (m.tilt_deg ? ' ' + m.tilt_deg + '°' : '')]));
  const table = rows.length ? '<table class="kv"><tbody>' + rows.map(([k, v]) =>
    `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('') + '</tbody></table>' : '';
  box.innerHTML = nodes + table;
  $('nodebuttons').hidden = !s.bridge;
}

function renderHardware(p) {
  const h = p.hardware || {};
  const fps = Object.entries(h.fps || {}).map(([k, v]) => k + ' ' + v).join(', ') || '-';
  const infer = Object.entries(h.infer_ms || {}).map(([k, v]) => `${k} ${v.p50}/${v.p95}`).join(', ') || '-';
  kvTable($('hardware'), [
    ['Detector', h.detector || '-'],
    ['Accelerator', h.accelerator || '-'],
    ['Frames per second', fps],
    ['Inference ms (p50 / p95)', infer],
    ['CPU temperature', na(h.cpu_temp_c, ' °C')],
    ['Throttled now', h.throttled === null || h.throttled === undefined ? 'not measured' : (h.throttled ? 'YES' : 'no')],
    ['Power (Pi 5 PMIC, corrected)', na(h.power_w, ' W')],
    ['Energy per frame', na(h.mj_per_frame, ' mJ')],
  ]);
}

function renderPrivacy(p) {
  const v = p.privacy || {};
  kvTable($('privacy-panel'), [
    ['Video stored', (v.video_bytes_stored || 0) + ' bytes'],
    ['Frames on disk', String(v.frames_on_disk || 0)],
    ['Face recognition', v.face_recognition ? 'ON' : 'off (not in the code)'],
    ['Re-identification', v.reidentification ? 'ON' : 'off (not in the code)'],
    ['Track IDs', v.track_ids || '-'],
    ['Audio', v.audio ? 'ON' : 'none (no microphone)'],
    ['Event retention', (v.retention_days || '-') + ' days'],
  ]);
}

let reorderText = '';
function renderReorder(p) {
  const r = p.reorder || { open: [] };
  reorderText = r.whatsapp || '';
  $('reorder').innerHTML = r.open.length ? r.open.map((d) => `
    <li class="${d.state === 'EMPTY' ? 'CRITICAL' : 'WARN'}">
      <span class="sev">${d.state === 'EMPTY' ? 'OUT' : 'LOW'}</span>
      <span class="msg">${esc(d.sku || d.shelf + '/' + d.slot)} · since ${esc(clockText(d.opened_ts))}
        ${d.suggested_qty ? ' · qty ' + d.suggested_qty : ''}</span>
    </li>`).join('') : '<li class="empty">Nothing to reorder</li>';
  $('copy-reorder').hidden = !r.open.length;
}

function renderPlatform(s) {
  const p = s.platform;
  if (!p) return;
  renderCamHealth(p);
  renderSensors(p);
  renderHardware(p);
  renderPrivacy(p);
  renderReorder(p);
}

function wirePlatformButtons() {
  document.querySelectorAll('#nodebuttons button').forEach((button) => {
    button.addEventListener('click', async () => {
      const url = '/api/node/' + button.dataset.cmd + '?pattern=' + encodeURIComponent(button.dataset.pattern);
      const response = await fetch(url, { method: 'POST' }).catch(() => null);
      $('nodecmd-result').textContent = response && response.ok ? 'sent' : 'not sent';
    });
  });
  $('copy-reorder').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(reorderText);
      $('copy-result').textContent = 'copied - paste it into WhatsApp';
    } catch (error) {
      $('copy-result').textContent = 'copy blocked by the browser';
    }
  });
}

/* ---------- Ask your store + daily summary (M10) ---------- */

function renderAnswer(a) {
  const box = $('ask-answer');
  if (!a) { box.innerHTML = ''; return; }
  const head = a.columns && a.columns.length
    ? '<tr>' + a.columns.map((c) => `<th>${esc(c)}</th>`).join('') + '</tr>' : '';
  const body = (a.rows || []).slice(0, 20).map((r) =>
    '<tr>' + r.map((v) => `<td>${esc(v)}</td>`).join('') + '</tr>').join('');
  box.innerHTML = `
    <p class="answer">${esc(a.text)}</p>
    ${a.model_query ? '<p class="warnline">Query written by the local model: check it asks what you meant.</p>' : ''}
    ${a.sql ? `<details open><summary>query and rows (${(a.rows || []).length})</summary>
       <code class="sql">${esc(a.sql)}</code>
       <table class="kv rows">${head}${body}</table></details>` : ''}
    ${(a.notes || []).length ? `<p class="muted small">${esc(a.notes.join(' · '))}</p>` : ''}
    <p class="muted small">${esc(a.backend || 'no backend')} · ${a.elapsed_ms} ms</p>`;
}

function wireAsk() {
  $('ask-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const q = $('ask-q').value.trim();
    if (!q) return;
    $('ask-answer').innerHTML = '<p class="muted">thinking...</p>';
    try {
      const response = await fetch('/api/ask?q=' + encodeURIComponent(q));
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || response.status);
      renderAnswer(body);
    } catch (error) {
      $('ask-answer').innerHTML = `<p class="bad">${esc(String(error.message || error))}</p>`;
    }
  });
  $('summary-day').value = new Date().toISOString().slice(0, 10);
  $('summary-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const url = '/api/summary?day=' + encodeURIComponent($('summary-day').value)
              + '&lang=' + encodeURIComponent($('summary-lang').value);
    try {
      const response = await fetch(url);
      const body = await response.json();
      $('summary-text').textContent = response.ok ? body.text : (body.detail || 'not available');
    } catch (error) {
      $('summary-text').textContent = 'not available';
    }
  });
}

/* ---------- inline SVG charts (no library) ---------- */

function barChart(node, pairs, formatLabel) {
  if (!pairs.length) {
    node.innerHTML = '<p class="empty">No data yet</p>';
    return;
  }
  const W = 560, H = 170, padL = 30, padB = 26, padT = 8;
  const max = Math.max(1, ...pairs.map((p) => p[1]));
  const bw = (W - padL - 8) / pairs.length;
  const bars = pairs.map((p, i) => {
    const h = (p[1] / max) * (H - padB - padT);
    const x = padL + i * bw;
    const y = H - padB - h;
    const label = formatLabel ? formatLabel(p[0]) : p[0];
    return `<rect class="bar-rect" x="${x + 1}" y="${y}" width="${Math.max(1, bw - 3)}" height="${h}">
              <title>${esc(label)}: ${p[1]}</title></rect>`
         + (pairs.length <= 14 || i % Math.ceil(pairs.length / 10) === 0
            ? `<text class="tick" x="${x + bw / 2}" y="${H - padB + 12}" text-anchor="middle">${esc(label)}</text>`
            : '');
  }).join('');
  node.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img">
      <line class="axis" x1="${padL}" y1="${H - padB}" x2="${W}" y2="${H - padB}"/>
      <text class="tick" x="2" y="${padT + 8}">${max}</text>
      <text class="tick" x="2" y="${H - padB}">0</text>
      ${bars}
    </svg>`;
}

async function renderCharts(s) {
  try {
    const response = await fetch('/api/series/entries?by=hour');
    const rows = await response.json();
    barChart($('chart-footfall'), rows.map((r) => [r.t.slice(11, 13) + 'h', r.v]));
  } catch (error) {
    $('chart-footfall').innerHTML = '<p class="empty">No data yet</p>';
  }
  const zones = Object.entries(s.zone_dwell_s || {});
  barChart($('chart-zones'), zones.map(([k, v]) => [k, Math.round(v)]));
}

function refreshHeatmap() {
  const img = $('heatmap');
  const probe = new Image();
  probe.onload = () => { img.src = probe.src; img.hidden = false; $('heatmap-empty').hidden = true; };
  probe.onerror = () => { img.hidden = true; $('heatmap-empty').hidden = false; };
  probe.src = '/api/heatmap.png?t=' + Date.now();
}

async function renderEvents() {
  try {
    const rows = await (await fetch('/api/events?limit=40')).json();
    $('events').innerHTML = rows.length ? rows.map((e) => `
      <li><span class="t">${esc(clockText(e.ts))}</span>
          <span class="ty">${esc(e.type)}</span>
          <span class="d">${esc(JSON.stringify(e.data)).slice(0, 120)}</span></li>`).join('')
      : '<li class="empty">No events yet</li>';
  } catch (error) { /* keep whatever is on screen */ }
}

/* ---------- wiring ---------- */

function render(state) {
  lastUpdate = Date.now();
  renderTiles(state);
  renderAlerts(state);
  renderCounters(state);
  renderShelves(state);
  renderHealth(state);
  renderPlatform(state);
  renderCharts(state);
}

async function poll() {
  try {
    const state = await (await fetch('/api/state')).json();
    render(state);
    $('live-dot').className = 'dot live';
  } catch (error) {
    $('live-dot').className = 'dot stale';
  }
}

function connect() {
  let socket;
  try {
    socket = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
  } catch (error) {
    return;
  }
  socket.onopen = () => { socketAlive = true; $('live-dot').className = 'dot live'; };
  socket.onmessage = (message) => {
    try {
      const payload = JSON.parse(message.data);
      if (payload.type === 'STATE') render(payload.data);
      // Individual events only mark the page dirty; the next poll carries the
      // full picture. Re-rendering everything per event would melt a Pi.
    } catch (error) { /* ignore malformed frames */ }
  };
  socket.onclose = () => { socketAlive = false; setTimeout(connect, 4000); };
  socket.onerror = () => { socket.close(); };
}

wirePlatformButtons();
wireAsk();
poll();
renderEvents();
refreshHeatmap();
connect();
setInterval(poll, POLL_MS);
setInterval(renderEvents, 5000);
setInterval(refreshHeatmap, 30000);

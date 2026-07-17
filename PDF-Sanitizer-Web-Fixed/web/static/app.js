'use strict';

const state = {
  sid: null,
  queue: [],
  uploaded: [],
  scans: [],
  selected: new Set(),
  filter: 'all',
  dpi: 150,
  jobId: null,
  websocket: null,
  pollingTimer: null,
};

const $ = (id) => document.getElementById(id);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const pageMeta = {
  upload: ['Upload PDF', 'Unggah satu atau beberapa PDF yang akan diperiksa.'],
  scan: ['Scan & Analisa', 'Periksa objek interaktif dan konstruksi berbahaya.'],
  sanitize: ['Sanitize PDF', 'Render ulang dan verifikasi hasil PDF bersih.'],
};

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatEta(seconds) {
  const value = Math.max(0, Number(seconds || 0));
  if (!value) return '—';
  if (value < 60) return `${Math.round(value)} dtk`;
  const minutes = Math.floor(value / 60);
  const remainder = Math.round(value % 60);
  return `${minutes} mnt ${String(remainder).padStart(2, '0')} dtk`;
}

function toast(message, type = 'info') {
  const node = document.createElement('div');
  node.className = `toast ${type}`;
  node.textContent = message;
  $('toastContainer').appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

async function readJson(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || data.error || `HTTP ${response.status}`);
  }
  return data;
}

function navigate(step) {
  $$('.step-panel').forEach((panel) => panel.classList.toggle('active', panel.id === `step-${step}`));
  $$('.nav-item').forEach((button) => button.classList.toggle('active', button.dataset.step === step));
  $('pageTitle').textContent = pageMeta[step][0];
  $('pageSubtitle').textContent = pageMeta[step][1];
  closeSidebar();
}

function openSidebar() {
  $('sidebar').classList.add('open');
  $('sidebarOverlay').classList.add('open');
}

function closeSidebar() {
  $('sidebar').classList.remove('open');
  $('sidebarOverlay').classList.remove('open');
}

async function checkHealth() {
  const pill = $('healthPill');
  try {
    const data = await readJson(await fetch('/api/health'));
    pill.className = 'health-pill online';
    pill.querySelector('span:last-child').textContent = `Server aktif · v${data.version}`;
    $('uploadLimit').textContent = `Maksimal ${data.max_file_mb} MB per file · ${data.max_files} file per sesi`;
  } catch (error) {
    pill.className = 'health-pill offline';
    pill.querySelector('span:last-child').textContent = 'Server tidak terhubung';
  }
}

function addFiles(fileList) {
  const files = [...fileList].filter((file) => file.name.toLowerCase().endsWith('.pdf'));
  if (!files.length) {
    toast('Hanya file PDF yang dapat dipilih.', 'error');
    return;
  }

  let duplicates = 0;
  for (const file of files) {
    const exists = state.queue.some((item) => item.name === file.name && item.size === file.size);
    if (exists) {
      duplicates += 1;
      continue;
    }
    state.queue.push({ id: crypto.randomUUID(), file, name: file.name, size: file.size });
  }
  if (duplicates) toast(`${duplicates} file duplikat dilewati.`);
  renderQueue();
}

function renderQueue() {
  const visible = state.queue.length > 0;
  $('queue').hidden = !visible;
  $('queueCount').textContent = String(state.queue.length);
  $('queueList').innerHTML = state.queue.map((item) => `
    <div class="queue-item">
      <div class="file-icon">PDF</div>
      <div class="file-info">
        <strong title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong>
        <span>Siap diupload</span>
      </div>
      <span class="file-size">${formatBytes(item.size)}</span>
      <button class="remove-file" type="button" data-remove-id="${item.id}" aria-label="Hapus ${escapeHtml(item.name)}">×</button>
    </div>
  `).join('');
  $$('[data-remove-id]').forEach((button) => {
    button.addEventListener('click', () => {
      state.queue = state.queue.filter((item) => item.id !== button.dataset.removeId);
      renderQueue();
    });
  });
}

function uploadFiles() {
  if (!state.queue.length) return;
  const form = new FormData();
  state.queue.forEach((item) => form.append('files', item.file));
  if (state.sid) form.append('sid', state.sid);

  const xhr = new XMLHttpRequest();
  const button = $('uploadButton');
  button.disabled = true;
  $('clearQueueButton').disabled = true;
  $('uploadProgress').hidden = false;
  $('uploadProgressText').textContent = 'Mengupload file…';

  xhr.upload.addEventListener('progress', (event) => {
    if (!event.lengthComputable) return;
    const percent = Math.round((event.loaded / event.total) * 100);
    $('uploadBar').style.width = `${percent}%`;
    $('uploadPercent').textContent = `${percent}%`;
    $('uploadProgressText').textContent = `${formatBytes(event.loaded)} / ${formatBytes(event.total)}`;
  });

  xhr.addEventListener('load', () => {
    try {
      const data = JSON.parse(xhr.responseText || '{}');
      if (xhr.status < 200 || xhr.status >= 300) throw new Error(data.detail || 'Upload gagal');
      state.sid = data.sid;
      state.uploaded.push(...data.uploaded);
      state.scans = state.uploaded.map((item) => ({ ...item, scanned: false }));
      state.queue = [];
      renderQueue();
      renderScanTable();
      $('uploadBar').style.width = '100%';
      $('uploadPercent').textContent = '100%';
      $('uploadProgressText').textContent = `${data.uploaded.length} file berhasil diupload`;
      toast(`${data.uploaded.length} file berhasil diupload.`, 'success');
      setTimeout(() => {
        $('uploadProgress').hidden = true;
        navigate('scan');
      }, 500);
    } catch (error) {
      toast(error.message, 'error');
    }
  });

  xhr.addEventListener('error', () => toast('Koneksi terputus saat upload.', 'error'));
  xhr.addEventListener('loadend', () => {
    button.disabled = false;
    $('clearQueueButton').disabled = false;
  });
  xhr.open('POST', '/api/upload');
  xhr.send(form);
}

function visibleScans() {
  const scanned = state.scans.filter((item) => item.scanned);
  if (state.filter === 'safe') return scanned.filter((item) => item.safe);
  if (state.filter === 'unsafe') return scanned.filter((item) => !item.safe);
  return scanned;
}

function issueTags(item) {
  if (!item.scanned) return '<span class="tag neutral">Belum discan</span>';
  if (item.error) return `<span class="tag">${escapeHtml(item.error)}</span>`;
  if (!item.issues?.length) return '<span class="tag neutral">Tidak ada</span>';
  return `<div class="issue-list">${item.issues.map((issue) => `<span class="tag">${escapeHtml(issue)}</span>`).join('')}</div>`;
}

function renderScanTable() {
  const rows = visibleScans();
  const scannedCount = state.scans.filter((item) => item.scanned).length;
  const body = $('scanTableBody');

  if (!rows.length) {
    body.innerHTML = `<tr class="empty-row"><td colspan="6">${scannedCount ? 'Tidak ada file pada filter ini.' : 'Klik “Scan Semua File” untuk memulai pemeriksaan.'}</td></tr>`;
  } else {
    body.innerHTML = rows.map((item) => `
      <tr>
        <td class="check-cell"><input class="row-check" type="checkbox" data-fid="${item.fid}" ${state.selected.has(item.fid) ? 'checked' : ''}></td>
        <td class="file-name-cell">${escapeHtml(item.name)}</td>
        <td>${escapeHtml(item.size_fmt || formatBytes(item.size))}</td>
        <td>${item.pages ?? '—'}</td>
        <td>${issueTags(item)}</td>
        <td><span class="status-badge ${item.safe ? 'safe' : 'unsafe'}">${item.safe ? 'Aman' : 'Perlu dibersihkan'}</span></td>
      </tr>
    `).join('');
  }

  $$('.row-check').forEach((checkbox) => {
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) state.selected.add(checkbox.dataset.fid);
      else state.selected.delete(checkbox.dataset.fid);
      updateSelection();
    });
  });
  updateStats();
  updateSelection();
}

function updateStats() {
  const scanned = state.scans.filter((item) => item.scanned);
  $('statTotal').textContent = String(scanned.length);
  $('statUnsafe').textContent = String(scanned.filter((item) => !item.safe).length);
  $('statSafe').textContent = String(scanned.filter((item) => item.safe).length);
  $('statUri').textContent = String(scanned.filter((item) => item.has_uri).length);
  $('statJs').textContent = String(scanned.filter((item) => item.has_js).length);
}

function updateSelection() {
  const count = state.selected.size;
  $('selectionLabel').textContent = `${count} file dipilih`;
  $('continueButton').disabled = count === 0;
  $('sanitizeFileCount').textContent = String(count);
  const visibleIds = visibleScans().map((item) => item.fid);
  $('selectAll').checked = visibleIds.length > 0 && visibleIds.every((fid) => state.selected.has(fid));
}

async function scanFiles() {
  if (!state.sid) {
    toast('Upload PDF terlebih dahulu.', 'error');
    return;
  }
  const button = $('scanButton');
  button.disabled = true;
  $('scanProgress').hidden = false;
  $('scanBar').style.width = '12%';
  $('scanPercent').textContent = '12%';
  $('scanProgressText').textContent = 'Menganalisa struktur PDF…';

  try {
    const data = await readJson(await fetch('/api/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sid: state.sid }),
    }));

    const total = data.results.length || 1;
    for (let index = 0; index < data.results.length; index += 1) {
      const result = data.results[index];
      const target = state.scans.findIndex((item) => item.fid === result.fid);
      const merged = { ...(state.scans[target] || {}), ...result, scanned: true };
      if (target >= 0) state.scans[target] = merged;
      else state.scans.push(merged);
      const percent = Math.round(((index + 1) / total) * 100);
      $('scanBar').style.width = `${percent}%`;
      $('scanPercent').textContent = `${percent}%`;
      $('scanProgressText').textContent = `Memeriksa ${result.name}`;
      await new Promise((resolve) => requestAnimationFrame(resolve));
    }

    state.selected.clear();
    state.scans.filter((item) => item.scanned && !item.safe && !item.error).forEach((item) => state.selected.add(item.fid));
    renderScanTable();
    toast(`Scan selesai: ${data.results.length} file diperiksa.`, 'success');
    setTimeout(() => { $('scanProgress').hidden = true; }, 900);
  } catch (error) {
    toast(`Scan gagal: ${error.message}`, 'error');
    $('scanProgressText').textContent = 'Scan gagal';
  } finally {
    button.disabled = false;
  }
}

function appendLog(entry) {
  const log = $('jobLog');
  if (log.dataset.started !== 'true') {
    log.innerHTML = '';
    log.dataset.started = 'true';
  }
  const line = document.createElement('p');
  line.className = entry.status === 'success' ? 'ok' : 'fail';
  line.textContent = `${entry.status === 'success' ? '✓' : '✕'} ${entry.name} — ${entry.message}`;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

function updateJob(data) {
  const total = Math.max(1, Number(data.total || state.selected.size || 1));
  const done = Number(data.done || 0);
  const percent = Number(data.pct ?? Math.round((done / total) * 100));
  $('progressRing').style.setProperty('--progress', String(percent));
  $('jobPercent').textContent = `${percent}%`;
  $('jobSuccess').textContent = String(data.success ?? $('jobSuccess').textContent);
  $('jobFailed').textContent = String(data.failed ?? $('jobFailed').textContent);
  $('jobEta').textContent = formatEta(data.eta_seconds);
  if (data.status) $('jobStatusText').textContent = statusText(data.status);
  if (data.entry) appendLog(data.entry);

  if (data.download_ready || (data.type === 'done' && Number(data.success) > 0)) {
    const link = $('downloadButton');
    link.classList.remove('disabled');
    link.setAttribute('aria-disabled', 'false');
    link.href = `/api/download/${state.jobId}`;
  }
}

function statusText(status) {
  return ({ queued: 'Menunggu proses…', running: 'Sedang membersihkan PDF…', done: 'Selesai dan siap diunduh.', failed: 'Pekerjaan gagal.' })[status] || status;
}

function connectWebSocket(jobId) {
  if (state.websocket) state.websocket.close();
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${location.host}/ws/${jobId}`);
  state.websocket = socket;
  let pingTimer;

  socket.addEventListener('open', () => {
    pingTimer = setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send('ping');
    }, 20000);
  });
  socket.addEventListener('message', (event) => {
    const data = JSON.parse(event.data);
    updateJob(data);
    if (data.type === 'done' || data.type === 'failed') {
      clearInterval(pingTimer);
      $('sanitizeButton').disabled = false;
      if (data.type === 'done' && Number(data.success) > 0) toast('Sanitasi selesai. Hasil siap diunduh.', 'success');
      else if (data.type === 'failed') toast(data.message || data.error || 'Sanitasi gagal.', 'error');
    }
  });
  socket.addEventListener('close', () => {
    clearInterval(pingTimer);
    startPolling(jobId);
  });
  socket.addEventListener('error', () => socket.close());
}

function startPolling(jobId) {
  clearInterval(state.pollingTimer);
  state.pollingTimer = setInterval(async () => {
    try {
      const data = await readJson(await fetch(`/api/jobs/${jobId}`));
      updateJob(data);
      if (['done', 'failed'].includes(data.status)) {
        clearInterval(state.pollingTimer);
        $('sanitizeButton').disabled = false;
      }
    } catch (_) {
      clearInterval(state.pollingTimer);
    }
  }, 1500);
}

async function sanitizeFiles() {
  if (!state.sid || !state.selected.size) {
    toast('Pilih minimal satu file.', 'error');
    return;
  }
  $('sanitizeButton').disabled = true;
  $('downloadButton').classList.add('disabled');
  $('downloadButton').setAttribute('aria-disabled', 'true');
  $('downloadButton').href = '#';
  $('jobLog').innerHTML = '<p>Menyiapkan pekerjaan…</p>';
  $('jobLog').dataset.started = 'false';
  $('jobStatusText').textContent = 'Mengirim pekerjaan ke server…';
  $('progressRing').style.setProperty('--progress', '0');
  $('jobPercent').textContent = '0%';
  $('jobSuccess').textContent = '0';
  $('jobFailed').textContent = '0';
  $('jobEta').textContent = '—';

  try {
    const data = await readJson(await fetch('/api/sanitize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sid: state.sid, fids: [...state.selected], dpi: state.dpi }),
    }));
    state.jobId = data.job_id;
    connectWebSocket(state.jobId);
  } catch (error) {
    $('sanitizeButton').disabled = false;
    toast(`Sanitasi gagal dimulai: ${error.message}`, 'error');
  }
}

async function clearSession() {
  if (!state.sid) return;
  if (!window.confirm('Hapus seluruh file pada sesi ini?')) return;
  try {
    await readJson(await fetch(`/api/session/${state.sid}`, { method: 'DELETE' }));
  } catch (_) { /* local state tetap dibersihkan */ }
  state.sid = null;
  state.queue = [];
  state.uploaded = [];
  state.scans = [];
  state.selected.clear();
  renderQueue();
  renderScanTable();
  navigate('upload');
  toast('Sesi berhasil dibersihkan.');
}

function initializeEvents() {
  $$('.nav-item').forEach((button) => button.addEventListener('click', () => navigate(button.dataset.step)));
  $('menuButton').addEventListener('click', openSidebar);
  $('sidebarOverlay').addEventListener('click', closeSidebar);

  const dropzone = $('dropzone');
  const input = $('fileInput');
  dropzone.addEventListener('click', (event) => {
    if (!event.target.closest('#chooseButton')) input.click();
  });
  dropzone.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') input.click();
  });
  $('chooseButton').addEventListener('click', (event) => {
    event.stopPropagation();
    input.click();
  });
  input.addEventListener('change', () => {
    addFiles(input.files);
    input.value = '';
  });
  ['dragenter', 'dragover'].forEach((name) => dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.add('dragover');
  }));
  ['dragleave', 'drop'].forEach((name) => dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    dropzone.classList.remove('dragover');
  }));
  dropzone.addEventListener('drop', (event) => addFiles(event.dataTransfer.files));

  $('clearQueueButton').addEventListener('click', () => { state.queue = []; renderQueue(); });
  $('uploadButton').addEventListener('click', uploadFiles);
  $('scanButton').addEventListener('click', scanFiles);
  $('clearSessionButton').addEventListener('click', clearSession);

  $$('.filter-button').forEach((button) => button.addEventListener('click', () => {
    $$('.filter-button').forEach((item) => item.classList.remove('active'));
    button.classList.add('active');
    state.filter = button.dataset.filter;
    renderScanTable();
  }));

  $('selectAll').addEventListener('change', (event) => {
    visibleScans().forEach((item) => {
      if (event.target.checked) state.selected.add(item.fid);
      else state.selected.delete(item.fid);
    });
    renderScanTable();
  });

  $('continueButton').addEventListener('click', () => {
    $('sanitizeFileCount').textContent = String(state.selected.size);
    navigate('sanitize');
  });

  $$('#dpiOptions button').forEach((button) => button.addEventListener('click', () => {
    $$('#dpiOptions button').forEach((item) => item.classList.remove('active'));
    button.classList.add('active');
    state.dpi = Number(button.dataset.dpi);
    $('sanitizeDpi').textContent = `${state.dpi} DPI`;
  }));
  $('sanitizeButton').addEventListener('click', sanitizeFiles);
  $('downloadButton').addEventListener('click', (event) => {
    if (event.currentTarget.classList.contains('disabled')) event.preventDefault();
  });
}

initializeEvents();
renderQueue();
renderScanTable();
checkHealth();

// Media Downloader -- local web UI.
//
// One state object, one render(). Downloads are started with a POST and then
// observed by polling the job, which keeps this file small and means there is
// no connection to re-establish if the page is left open.

const TOKEN = document.querySelector('meta[name="md-token"]').content;
const POLL_MS = 500;

const state = {
  config: null,
  tools: [],
  dismissed: new Set(),
  offer: null,
  mode: 'video',
  job: null,
  history: [],
  error: null,
  busy: false,
};

const $ = (id) => document.getElementById(id);
const el = {
  form: $('form'), url: $('url'), paste: $('paste'),
  modeVideo: $('mode-video'), modeAudio: $('mode-audio'),
  qualityField: $('quality-field'), quality: $('quality'),
  audioField: $('audio-format-field'), audioFormat: $('audio-format'),
  submit: $('submit'), status: $('status'),
  recent: $('recent'), recentList: $('recent-list'),
  services: $('services'), downloadDir: $('download-dir'), openFolder: $('open-folder'),
  tool: $('tool'),
  exportReport: $('export-report'), copyDiagnostics: $('copy-diagnostics'),
  openLogs: $('open-logs'), helpResult: $('help-result'),
};

// --- api -----------------------------------------------------------

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'X-MD-Token': TOKEN, ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
  });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw Object.assign(new Error(payload?.error?.message || `Request failed (${response.status})`), {
      hint: payload?.error?.hint || null,
    });
  }
  return payload;
}

// --- formatting ----------------------------------------------------

const PHASES = {
  queued: 'Queued', preparing: 'Preparing…', downloading: 'Downloading',
  processing: 'Processing…', completed: 'Done', failed: 'Failed',
};

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return null;
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes, unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

function formatEta(seconds) {
  if (seconds == null) return null;
  const total = Math.max(0, Math.round(seconds));
  const mins = Math.floor(total / 60);
  return mins ? `${mins}m ${String(total % 60).padStart(2, '0')}s left` : `${total}s left`;
}

function describe(job) {
  const p = job.progress || {};
  if (job.state !== 'downloading') return '';
  const bits = [];
  if (p.downloaded_bytes != null) {
    bits.push(p.total_bytes ? `${formatBytes(p.downloaded_bytes)} of ${formatBytes(p.total_bytes)}` : formatBytes(p.downloaded_bytes));
  }
  if (p.speed_bps) bits.push(`${formatBytes(p.speed_bps)}/s`);
  const eta = formatEta(p.eta_seconds);
  if (eta) bits.push(eta);
  return bits.join(' · ');
}

// --- rendering -----------------------------------------------------

function renderStatus() {
  const { job, error } = state;
  if (!job && !error) { el.status.hidden = true; return; }
  el.status.hidden = false;
  el.status.className = 'card status';

  if (error) {
    el.status.classList.add('status--error');
    el.status.innerHTML = '';
    el.status.append(
      row('Could not download', ''),
      text('status__name', error.message),
      ...(error.hint ? [text('status__hint', error.hint)] : []),
    );
    return;
  }

  const name = job.progress?.filename || job.result?.filename || job.title || job.url;
  const percent = job.progress?.percent;

  if (job.state === 'completed') {
    el.status.classList.add('status--done');
    el.status.innerHTML = '';
    el.status.append(row('Done', ''), text('status__name', job.result?.filename || name));
    return;
  }
  if (job.state === 'failed') {
    el.status.classList.add('status--error');
    el.status.innerHTML = '';
    el.status.append(
      row('Failed', ''),
      text('status__name', job.error?.message || 'The download failed.'),
      ...(job.error?.hint ? [text('status__hint', job.error.hint)] : []),
      // A short code the user can quote; the same one is in the log.
      ...(job.error?.error_id ? [text('status__id', `Error ID: ${job.error.error_id}`)] : []),
    );
    return;
  }

  el.status.innerHTML = '';
  const bar = document.createElement('div');
  bar.className = percent == null ? 'bar bar--indeterminate' : 'bar';
  const fill = document.createElement('div');
  fill.className = 'bar__fill';
  if (percent != null) fill.style.width = `${percent.toFixed(1)}%`;
  bar.append(fill);

  el.status.append(
    row(PHASES[job.state] || job.state, percent == null ? '' : `${percent.toFixed(0)}%`),
    text('status__name', name),
    bar,
    ...(describe(job) ? [text('status__hint', describe(job))] : []),
  );
}

function row(phase, meta) {
  const wrap = document.createElement('div');
  wrap.className = 'status__row';
  wrap.append(text('status__phase', phase), text('status__meta', meta));
  return wrap;
}

function text(className, value) {
  const node = document.createElement('div');
  node.className = className;
  node.textContent = value; // textContent, never innerHTML: titles are untrusted
  return node;
}

function renderRecent() {
  const finished = state.history.filter((job) => job.state === 'completed' || job.state === 'failed');
  el.recent.hidden = finished.length === 0;
  el.recentList.innerHTML = '';
  for (const job of finished.slice(0, 8)) {
    const item = document.createElement('li');
    item.className = 'recent__item';
    const dot = document.createElement('span');
    dot.className = `recent__dot recent__dot--${job.state}`;
    const name = text('recent__name', job.result?.filename || job.title || job.url);
    item.append(dot, name);
    el.recentList.append(item);
  }
}

function renderControls() {
  const audio = state.mode === 'audio';
  el.modeVideo.classList.toggle('is-active', !audio);
  el.modeAudio.classList.toggle('is-active', audio);
  el.modeVideo.setAttribute('aria-checked', String(!audio));
  el.modeAudio.setAttribute('aria-checked', String(audio));
  el.qualityField.hidden = audio;
  el.audioField.hidden = !audio;
  el.submit.disabled = state.busy;
  el.submit.textContent = state.busy ? 'Downloading…' : 'Download';
}

function formatSize(bytes) {
  return bytes ? `${Math.round(bytes / 1e6)} MB` : '';
}

// The prompt appears only when a tool is genuinely relevant: FFmpeg when the
// user picks a mode that needs it, Deno only once a YouTube download has shown
// it is required. Never at startup, and never twice after "Not now".
function renderTool() {
  const name = state.offer;
  const tool = name && state.tools.find((t) => t.tool === name);
  if (!tool || state.dismissed.has(name) || (tool.available && tool.state !== 'installing')) {
    el.tool.hidden = true;
    return;
  }

  el.tool.hidden = false;
  el.tool.className = 'card tool';
  el.tool.innerHTML = '';

  const label = name === 'ffmpeg' ? 'FFmpeg' : 'a JavaScript runtime (Deno)';

  if (tool.state === 'installing') {
    el.tool.classList.add('tool--busy');
    el.tool.append(text('tool__title', `Downloading ${label}…`));
    el.tool.append(text('tool__why', 'This happens once. You can keep using the app meanwhile.'));
    return;
  }
  if (tool.state === 'unsupported') {
    el.tool.append(text('tool__title', `${label} cannot be installed automatically here`));
    el.tool.append(text('tool__why', 'Install it with your system package manager instead.'));
    return;
  }

  if (tool.error) el.tool.classList.add('tool--error');
  el.tool.append(text('tool__title', tool.error ? `Could not install ${label}` : `Install ${label}?`));
  el.tool.append(text('tool__why', tool.error || tool.purpose));

  const meta = [tool.version, formatSize(tool.size_bytes), tool.licence]
    .filter(Boolean).join(' · ');
  if (meta) el.tool.append(text('tool__meta', meta));

  const actions = document.createElement('div');
  actions.className = 'tool__actions';

  const install = document.createElement('button');
  install.type = 'button';
  install.className = 'button button--primary';
  install.textContent = tool.error ? 'Try again' : `Download ${label.split(' ')[0]}`;
  install.addEventListener('click', () => installTool(name));

  const dismiss = document.createElement('button');
  dismiss.type = 'button';
  dismiss.className = 'button button--ghost';
  dismiss.textContent = 'Not now';
  dismiss.addEventListener('click', () => {
    state.dismissed.add(name);
    render();
  });

  actions.append(install, dismiss);
  el.tool.append(actions);
}

async function refreshTools() {
  try {
    state.tools = (await api('/api/tools')).tools;
  } catch {
    state.tools = [];
  }
}

// Offer a tool only when something has actually shown it is needed.
function considerOffering(name) {
  const tool = state.tools.find((t) => t.tool === name);
  if (tool && !tool.available && !state.dismissed.has(name)) {
    state.offer = name;
  }
}

async function installTool(name) {
  try {
    await api(`/api/tools/${encodeURIComponent(name)}/install`, {
      method: 'POST',
      body: '{}',
    });
  } catch (err) {
    state.error = { message: err.message, hint: err.hint };
    render();
    return;
  }
  // Poll until the install settles, then re-render.
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    await refreshTools();
    render();
    const tool = state.tools.find((t) => t.tool === name);
    if (!tool || tool.state !== 'installing') return;
  }
}

function render() {
  renderControls();
  renderStatus();
  renderTool();
  renderRecent();
}

// --- actions -------------------------------------------------------

async function loadConfig() {
  state.config = await api('/api/config');
  const label = (value) => (value === 'best' ? 'Best available' : value === 'worst' ? 'Smallest' : `${value}p`);
  el.quality.innerHTML = '';
  for (const value of state.config.quality_choices) {
    el.quality.append(new Option(label(value), value));
  }
  el.audioFormat.innerHTML = '';
  for (const value of state.config.audio_formats) {
    el.audioFormat.append(new Option(value === 'best' ? 'Original (no re-encode)' : value.toUpperCase(), value));
  }
  el.services.textContent = `Supports ${state.config.supported_services.join(' · ')} and other public links`;
  el.downloadDir.textContent = state.config.download_dir;
}

async function poll(jobId) {
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    let job;
    try {
      job = await api(`/api/downloads/${encodeURIComponent(jobId)}`);
    } catch {
      continue; // a transient blip should not abandon a running download
    }
    state.job = job;
    render();
    if (job.state === 'completed' || job.state === 'failed') return job;
  }
}

async function startDownload(event) {
  event.preventDefault();
  if (state.busy) return;

  state.busy = true;
  state.error = null;
  state.job = null;
  render();

  try {
    const job = await api('/api/downloads', {
      method: 'POST',
      body: JSON.stringify({
        url: el.url.value.trim(),
        audio_only: state.mode === 'audio',
        quality: el.quality.value,
        audio_format: el.audioFormat.value,
      }),
    });
    state.job = job;
    render();
    const finished = await poll(job.id);
    await refreshTools();
    // A YouTube download is the only thing that reveals a JS runtime is needed.
    if (/youtu\.?be/i.test(el.url.value || finished.url || '')) considerOffering('deno');
    state.history = [finished, ...state.history.filter((j) => j.id !== finished.id)];
    if (finished.state === 'completed') el.url.value = '';
  } catch (err) {
    state.error = { message: err.message, hint: err.hint };
  } finally {
    state.busy = false;
    render();
  }
}

async function pasteFromClipboard() {
  try {
    el.url.value = (await navigator.clipboard.readText()).trim();
    el.url.focus();
  } catch {
    el.url.focus(); // permission denied or unsupported: let the user paste manually
  }
}

// --- wiring --------------------------------------------------------

el.form.addEventListener('submit', startDownload);
el.modeVideo.addEventListener('click', () => { state.mode = 'video'; render(); });
el.modeAudio.addEventListener('click', () => {
  state.mode = 'audio';
  // Converting audio is exactly when FFmpeg starts to matter.
  considerOffering('ffmpeg');
  render();
});
el.openFolder.addEventListener('click', async () => {
  try { await api('/api/open-folder', { method: 'POST', body: '{}' }); }
  catch (err) { state.error = { message: err.message, hint: err.hint }; render(); }
});

function helpMessage(message, isError = false) {
  el.helpResult.textContent = message;
  el.helpResult.className = isError ? 'help__result help__result--error' : 'help__result';
}

el.exportReport.addEventListener('click', async () => {
  helpMessage('Writing report…');
  try {
    const result = await api('/api/diagnostics/export', { method: 'POST', body: '{}' });
    helpMessage(`Saved ${result.filename} to your downloads folder.`);
  } catch (err) {
    helpMessage(err.message, true);
  }
});

el.copyDiagnostics.addEventListener('click', async () => {
  try {
    const data = await api('/api/diagnostics');
    await navigator.clipboard.writeText(data.report);
    helpMessage('Diagnostics copied to the clipboard.');
  } catch (err) {
    helpMessage(err.message || 'Could not copy diagnostics.', true);
  }
});

el.openLogs.addEventListener('click', async () => {
  try {
    await api('/api/open-logs', { method: 'POST', body: '{}' });
    helpMessage('Opened the log folder.');
  } catch (err) {
    helpMessage(err.message, true);
  }
});

if (navigator.clipboard?.readText) {
  el.paste.hidden = false;
  el.paste.addEventListener('click', pasteFromClipboard);
}

loadConfig().then(refreshTools).then(() => {
  // Startup never prompts. FFmpeg is only surfaced here if it is missing and
  // therefore already limiting every download the user is about to make.
  if (state.config && !state.config.ffmpeg_available) considerOffering('ffmpeg');
  render();
}).catch((err) => {
  state.error = { message: err.message, hint: err.hint };
  render();
});

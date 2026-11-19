/* A股雷达 · StockLens frontend */
'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const PAGE_META = {
  overview: { eyebrow: 'MARKET PULSE', title: '市场总览' },
  leaders: { eyebrow: 'LEADER RADAR', title: '龙头雷达' },
  picks: { eyebrow: 'DAILY WATCHLIST', title: '今日候选' },
  watch: { eyebrow: 'LIVE MONITOR', title: '自选监控' },
  learning: { eyebrow: 'MODEL LEARNING', title: '学习复盘' },
  method: { eyebrow: 'MODEL CARD', title: '方法与风险' },
};

const PROFILE_LABELS = { conservative: '稳健', balanced: '均衡', aggressive: '进攻' };
const CONFIDENCE_LABELS = { high: '信号一致性较高', medium: '信号中性', low: '信号一致性偏弱' };

const state = {
  route: 'overview',
  profile: localStorage.getItem('stocklens.profile') || 'balanced',
  overview: null,
  leaders: null,
  picks: null,
  watch: null,
  learning: null,
  method: null,
  loading: { overview: false, leaders: false, picks: false, watch: false, learning: false, method: false },
  errors: {},
  chart: null,
  searchTimer: null,
  pollTick: 0,
  watchEditing: false,
  liveMode: localStorage.getItem('stocklens.liveMode') !== 'off',
  live: null,
  liveLoading: false,
  liveError: null,
  guides: { picksGuide: false, watchGuide: false },
  toastTimer: null,
};

/* ------------------------------------------------------------------ utils */
function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function fmtNum(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtPct(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

function fmtSigned(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}`;
}

function fmtYi(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  if (n >= 10000) return `${(n / 10000).toFixed(2)}万亿`;
  if (n >= 100) return `${n.toFixed(0)}亿`;
  return `${n.toFixed(1)}亿`;
}

function fmtCompact(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  if (n >= 100000000) return `${(n / 100000000).toFixed(1)}亿`;
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`;
  return String(Math.round(n));
}

function dirClass(value) {
  const n = Number(value || 0);
  return n > 0.0001 ? 'up' : n < -0.0001 ? 'down' : 'flat';
}

function pctClass(value) {
  return dirClass(value);
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function sparkline(values, color, width = 160, height = 42) {
  const points = (values || []).map(Number).filter((value) => Number.isFinite(value));
  if (points.length < 2) return '<span class="micro">暂无走势</span>';
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const pad = 3;
  const step = (width - pad * 2) / (points.length - 1);
  const coords = points.map((value, index) => {
    const x = pad + index * step;
    const y = pad + (height - pad * 2) * (1 - (value - min) / span);
    return [x, y];
  });
  const line = coords.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const area = `M ${coords[0][0].toFixed(1)} ${height - pad} L ${line.replaceAll(' ', ' L ')} L ${coords[coords.length - 1][0].toFixed(1)} ${height - pad} Z`;
  const id = `sg-${Math.random().toString(36).slice(2, 9)}`;
  return `
    <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${color}" stop-opacity="0.22"/>
          <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <path d="${area}" fill="url(#${id})"></path>
      <polyline points="${line}" fill="none" stroke="${color}" stroke-width="1.4" vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"></polyline>
      <circle cx="${coords[coords.length - 1][0].toFixed(1)}" cy="${coords[coords.length - 1][1].toFixed(1)}" r="2" fill="${color}"></circle>
    </svg>`;
}

function icon(name) {
  return `<svg class="icon"><use href="#i-${name}"></use></svg>`;
}

function toast(message, type = 'ok') {
  const el = $('#toast');
  el.textContent = message;
  el.className = `toast show ${type === 'error' ? 'error' : ''}`;
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => {
    el.className = 'toast';
  }, 3200);
}

function setLoading(key, value) {
  state.loading[key] = value;
  if (key === 'overview' || key === 'picks') {
    $('#refreshBtn')?.classList.toggle('spinning', Object.values(state.loading).some(Boolean));
  }
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(path, {
      ...options,
      signal: controller.signal,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `请求失败 (${response.status})`);
    }
    return payload;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('行情请求超时，请稍后重试');
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

/* ------------------------------------------------------------------ live */
function updateLiveBadge(live = null, error = null) {
  const button = $('#liveToggle');
  const text = $('#liveText');
  if (!button || !text) return;
  if (!state.liveMode) {
    button.className = 'live-toggle off';
    text.textContent = '已暂停';
    button.title = '低延迟模式已暂停；点击恢复 1 秒刷新';
    return;
  }
  if (!['overview', 'watch'].includes(state.route)) {
    button.className = 'live-toggle off';
    text.textContent = '非实时页';
    button.title = '候选页按 90 秒刷新；1 秒低延迟模式仅用于市场总览和自选监控';
    return;
  }
  if (error) {
    button.className = 'live-toggle error';
    text.textContent = '连接中断';
    button.title = error;
    return;
  }
  if (!live) {
    button.className = 'live-toggle active';
    text.textContent = '1s 刷新';
    button.title = '1 秒刷新；行情源自身延迟仍存在';
    return;
  }
  const open = Boolean(live.market?.is_open);
  if (!open) {
    button.className = 'live-toggle off';
    text.textContent = '已收盘';
  } else {
    button.className = 'live-toggle active';
    text.textContent = '1s 刷新';
  }
  const fetchedAt = String(live.asOf || '').replace('T', ' ');
  button.title = `最近抓取 ${fetchedAt || '—'} · 请求 ${live.serverMs ?? '—'}ms · 1 秒轮询；公开源非逐笔，不承诺零延迟`;
}

function updateLiveIndices(indices) {
  (indices || []).forEach((index) => {
    const card = document.querySelector(`[data-index-secid="${index.secid}"]`);
    if (!card) return;
    const cls = pctClass(index.pct);
    card.className = `index-card ${cls}`;
    const price = card.querySelector('.js-index-price');
    const pct = card.querySelector('.js-index-pct');
    const change = card.querySelector('.js-index-change');
    if (price) price.textContent = fmtNum(index.price);
    if (pct) {
      pct.className = `js-index-pct ${cls}`;
      pct.textContent = fmtPct(index.pct);
    }
    if (change) {
      change.className = `js-index-change ${cls}`;
      change.textContent = fmtSigned(index.change);
    }
  });
}

function applyLive(data) {
  if (state.route === 'watch' && (data.watch?.items || []).length) {
    const rows = document.querySelectorAll('tr[data-watch-code]').length;
    if (rows === data.watch.items.length) updateWatchLive(data.watch.items);
  }
  if (state.route === 'overview') updateLiveIndices(data.indices);
}

async function loadLive(force = false) {
  if (!state.liveMode || state.liveLoading || document.hidden) return;
  if (!['overview', 'watch'].includes(state.route)) return;
  state.liveLoading = true;
  try {
    const data = await api('/api/live');
    state.live = data;
    state.liveError = null;
    updateLiveBadge(data);
    applyLive(data);
  } catch (error) {
    state.liveError = error.message;
    updateLiveBadge(null, error.message);
  } finally {
    state.liveLoading = false;
  }
}

/* ------------------------------------------------------------------ routing */
function parseRoute() {
  const hash = window.location.hash.replace(/^#\/?/, '');
  return PAGE_META[hash] ? hash : 'overview';
}

function navigate(route) {
  if (!PAGE_META[route]) route = 'overview';
  if (window.location.hash !== `#/${route}`) {
    window.location.hash = `#/${route}`;
  } else {
    state.route = route;
    renderRoute();
  }
}

function updateRouteChrome() {
  const meta = PAGE_META[state.route];
  $('#pageEyebrow').textContent = meta.eyebrow;
  $('#pageTitle').textContent = meta.title;
  $$('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.route === state.route));
}

function renderRoute() {
  const nextRoute = parseRoute();
  const changed = nextRoute !== state.route;
  state.route = nextRoute;
  updateRouteChrome();
  hideSearch();
  updateLiveBadge(state.live);
  if (changed) window.scrollTo({ top: 0, behavior: 'auto' });
  const root = $('#viewRoot');
  if (state.route === 'overview') {
    if (!state.overview) {
      root.innerHTML = skeletonOverview();
      loadOverview();
    } else {
      renderOverview();
    }
    return;
  }
  if (state.route === 'leaders') {
    if (!state.leaders) {
      root.innerHTML = skeletonLeaders();
      loadLeaders();
    } else {
      renderLeaders();
    }
    return;
  }
  if (state.route === 'picks') {
    if (!state.picks) {
      root.innerHTML = skeletonPicks();
      loadPicks();
    } else {
      renderPicks();
    }
    return;
  }
  if (state.route === 'watch') {
    if (!state.watch) {
      root.innerHTML = skeletonWatch();
      loadWatch();
    } else {
      renderWatch();
    }
    return;
  }
  if (state.route === 'learning') {
    if (!state.learning) {
      root.innerHTML = skeletonLearning();
      loadLearning();
    } else {
      renderLearning();
    }
    return;
  }
  if (state.route === 'method') {
    if (!state.method) {
      root.innerHTML = skeletonMethod();
      loadMethod();
    } else {
      renderMethod();
    }
  }
}

/* ------------------------------------------------------------------ loading */
async function loadOverview(force = false, silent = false) {
  if (state.loading.overview) return;
  setLoading('overview', true);
  if (!silent && !state.overview) $('#viewRoot').innerHTML = skeletonOverview();
  try {
    const data = await api(`/api/overview${force ? '?refresh=1' : ''}`);
    state.overview = data;
    state.errors.overview = null;
    updateSidebar(data);
    if (state.route === 'overview') renderOverview();
  } catch (error) {
    state.errors.overview = error.message;
    if (state.route === 'overview') {
      if (state.overview) {
        showNotice(`行情刷新失败，展示的是缓存数据：${error.message}`, 'danger');
      } else {
        $('#viewRoot').innerHTML = errorState('市场数据加载失败', error.message, 'overview');
      }
    }
  } finally {
    setLoading('overview', false);
  }
}

async function loadLeaders(force = false) {
  if (state.loading.leaders) return;
  setLoading('leaders', true);
  if (!state.leaders && state.route === 'leaders') $('#viewRoot').innerHTML = skeletonLeaders();
  try {
    const data = await api(`/api/leaders${force ? '?refresh=1' : ''}`);
    state.leaders = data;
    state.errors.leaders = null;
    if (state.route === 'leaders') renderLeaders();
  } catch (error) {
    state.errors.leaders = error.message;
    if (state.route === 'leaders') {
      if (state.leaders) showNotice(`龙头数据刷新失败，展示缓存：${error.message}`, 'danger');
      else $('#viewRoot').innerHTML = errorState('龙头雷达加载失败', error.message, 'leaders');
    }
  } finally {
    setLoading('leaders', false);
  }
}

async function loadPicks(force = false, silent = false) {  if (state.loading.picks) return;
  setLoading('picks', true);
  if (!silent && !state.picks && state.route === 'picks') $('#viewRoot').innerHTML = skeletonPicks();
  try {
    const data = await api(`/api/picks?profile=${encodeURIComponent(state.profile)}${force ? '&refresh=1' : ''}`);
    state.picks = data;
    state.errors.picks = null;
    if (state.route === 'picks') renderPicks();
    if (state.route === 'overview') renderOverview();
  } catch (error) {
    state.errors.picks = error.message;
    if (state.route === 'picks') {
      if (state.picks) {
        showNotice(`候选刷新失败，展示的是缓存数据：${error.message}`, 'danger');
      } else {
        $('#viewRoot').innerHTML = errorState('候选数据加载失败', error.message, 'picks');
      }
    }
  } finally {
    setLoading('picks', false);
  }
}

async function loadWatch(silent = false) {
  if (state.loading.watch) return;
  setLoading('watch', true);
  if (!silent && !state.watch && state.route === 'watch') $('#viewRoot').innerHTML = skeletonWatch();
  try {
    const data = await api('/api/watchlist');
    state.watch = data;
    state.errors.watch = null;
    if (state.route === 'watch' && !state.watchEditing && document.activeElement?.id !== 'watchCode') {
      const renderedRows = document.querySelectorAll('tr[data-watch-code]').length;
      if ((data.items || []).length && renderedRows === data.items.length) {
        updateWatchLive(data.items);
      } else {
        renderWatch();
      }
    }
  } catch (error) {
    state.errors.watch = error.message;
    if (state.route === 'watch' && !state.watch) {
      $('#viewRoot').innerHTML = errorState('自选加载失败', error.message, 'watch');
    }
  } finally {
    setLoading('watch', false);
  }
}

async function loadLearning(force = false) {
  if (state.loading.learning) return;
  setLoading('learning', true);
  if (!state.learning && state.route === 'learning') $('#viewRoot').innerHTML = skeletonLearning();
  try {
    const data = await api(`/api/learning${force ? '?refresh=1' : ''}`);
    state.learning = data;
    state.errors.learning = null;
    if (state.route === 'learning') renderLearning();
  } catch (error) {
    state.errors.learning = error.message;
    if (state.route === 'learning') {
      if (state.learning) showNotice(`学习数据刷新失败，展示缓存：${error.message}`, 'danger');
      else $('#viewRoot').innerHTML = errorState('学习复盘加载失败', error.message, 'learning');
    }
  } finally {
    setLoading('learning', false);
  }
}

async function loadMethod() {
  if (state.loading.method) return;
  setLoading('method', true);
  try {
    const data = await api('/api/method');
    state.method = data;
    state.errors.method = null;
    if (state.route === 'method') renderMethod();
  } catch (error) {
    state.errors.method = error.message;
    if (state.route === 'method') {
      $('#viewRoot').innerHTML = errorState('方法说明加载失败', error.message, 'method');
    }
  } finally {
    setLoading('method', false);
  }
}

function errorState(title, message, scope) {
  return `
    <div class="pick-empty">
      ${icon('alert')}
      <h3>${esc(title)}</h3>
      <p>${esc(message)}。请检查网络或行情源状态，然后重试。</p>
      <button class="primary-btn" data-action="retry" data-scope="${esc(scope)}">重新加载</button>
    </div>`;
}

function showNotice(message, type = '') {
  const el = $('#globalNotice');
  el.className = `notice ${type === 'danger' ? 'danger' : ''}`;
  el.innerHTML = `${icon('alert')}<div>${esc(message)}</div>`;
}

function friendlySourceError(message) {
  if (!message) return '';
  if (/snapshot:|request failed|RemoteDisconnected|Connection aborted|ConnectionError|Read timed out/i.test(String(message))) {
    return '行情源暂时不可用，正在展示最近一次成功快照；数据恢复后会自动刷新。';
  }
  return String(message);
}

function hideNotice() {
  $('#globalNotice').className = 'notice hidden';
}

function updateSidebar(overview) {
  const market = overview.market || {};
  const status = $('#sidebarStatus');
  status.className = `market-status ${market.is_open ? 'open' : 'closed'}`;
  status.innerHTML = `<span class="status-dot"></span><strong>${esc(market.label || '—')}</strong><small>${esc((overview.asOf || '').slice(11, 16))}</small>`;
  $('#miniUp').textContent = overview.breadth?.up ?? '—';
  $('#miniDown').textContent = overview.breadth?.down ?? '—';
  $('#miniAmount').textContent = fmtYi(overview.breadth?.amount_yi);
  const dot = $('#sourceDot');
  dot.className = `live-dot ${overview.dataQuality?.stale ? 'stale' : ''}`;
  const todayCN = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date());
  const quoteDate = overview.market?.quoteDate || overview.dataQuality?.quoteDate;
  if (overview.market?.phaseNote) {
    showNotice(overview.market.phaseNote, 'danger');
  } else if (overview.dataQuality?.error) {
    showNotice(`行情源提示：${friendlySourceError(overview.dataQuality.error)}`, 'danger');
  } else if (quoteDate && quoteDate !== todayCN && overview.market?.is_trading_day) {
    showNotice(`行情最新日期为 ${quoteDate}，当前可能休市或数据源尚未更新。`, 'danger');
  } else if (overview.dataQuality?.coverage !== null && overview.dataQuality?.coverage !== undefined && overview.dataQuality.coverage < 0.95) {
    showNotice(`全市场快照覆盖 ${(overview.dataQuality.coverage * 100).toFixed(1)}%，部分页面抓取失败，宽度与成交额可能偏低。`, 'danger');
  } else if (overview.dataQuality?.stale) {
    showNotice('行情源暂时不稳定，当前展示最近一次成功快照。', 'danger');
  } else {
    hideNotice();
  }
}

/* ------------------------------------------------------------------ overview */
function renderOverview() {
  const o = state.overview;
  if (!o) return;
  const picks = state.picks?.picks || [];
  const temp = o.temperature || {};
  const breadth = o.breadth || {};
  const total = Math.max(1, breadth.universe || 1);
  const upW = ((breadth.up || 0) / total) * 100;
  const flatW = ((breadth.flat || 0) / total) * 100;
  const downW = ((breadth.down || 0) / total) * 100;
  const maxSector = Math.max(1, ...(o.sectors?.leaders || []).map((item) => Math.abs(item.pct)), ...(o.sectors?.laggards || []).map((item) => Math.abs(item.pct)));
  const upColor = '#ff5d6d';
  const downColor = '#22c58b';
  const flatColor = '#7f91a9';

  const indexCards = (o.indices || []).map((index) => {
    const cls = pctClass(index.pct);
    const color = cls === 'up' ? upColor : cls === 'down' ? downColor : flatColor;
    return `
      <article class="index-card ${cls}" data-index-secid="${esc(index.secid)}">
        <div>
          <div class="index-name">${esc(index.name)}<span>${esc(index.code)}</span></div>
          <div class="index-price js-index-price">${fmtNum(index.price)}</div>
          <div class="index-change">
            <span class="js-index-pct ${cls}">${fmtPct(index.pct)}</span>
            <span class="js-index-change ${cls}">${fmtSigned(index.change)}</span>
          </div>
        </div>
        <div class="index-spark">${sparkline(index.spark, color, 98, 42)}</div>
      </article>`;
  }).join('');

  const preview = picks.length
    ? picks.slice(0, 3).map((pick) => `
        <button class="preview-pick" data-action="detail" data-code="${esc(pick.code)}">
          <span class="preview-rank">${String(pick.rank).padStart(2, '0')}</span>
          <span class="preview-main">
            <strong>${esc(pick.name)}</strong>
            <small>${esc(pick.industry)} · 综合 ${fmtNum(pick.score, 1)} · ${esc(CONFIDENCE_LABELS[pick.confidence] || '')}</small>
          </span>
          <span class="preview-right">
            <b>${fmtNum(pick.price)}</b>
            <small class="${pctClass(pick.pct)}">${fmtPct(pick.pct)}</small>
          </span>
        </button>`).join('')
    : `<div class="preview-empty">${state.picks ? '今日没有同时满足模型与风控条件的候选，空仓等待也是策略。' : '候选计算中…'}</div>`;

  const previewMetrics = state.picks?.model?.metrics;
  const previewHealth = state.picks ? `
    <div class="preview-health">
      <div><span>日度截面 AUC</span><b>${previewMetrics?.daily_auc ?? '—'}</b><small>池化 ${previewMetrics?.auc ?? '—'}</small></div>
      <div><span>Rank IC</span><b>${previewMetrics?.rank_ic ?? '—'}</b><small>${previewMetrics?.ic_days ?? 0} 个交易日</small></div>
      <div><span>通过筛选</span><b>${picks.length}<i> / ${state.picks.candidateCount ?? '—'}</i></b><small>候选池</small></div>
    </div>
    <div class="preview-note">${icon('shield')}<span>不足 2 只时不强行凑数；空仓与降低仓位也是有效决策。</span></div>` : '';

  const sectorRows = (o.sectors?.leaders || []).slice(0, 6).map((item) => `
    <div class="sector-row">
      <span class="sector-name" title="${esc(item.name)}">${esc(item.name)}</span>
      <span class="sector-track"><i class="sector-fill" style="width:${clamp(Math.abs(item.pct) / maxSector * 100, 4, 100)}%"></i></span>
      <span class="sector-pct ${pctClass(item.pct)}">${fmtPct(item.pct)}</span>
      <span class="sector-leader">领涨 <b>${esc(item.leader || '—')}</b>${item.leader_pct !== null && item.leader_pct !== undefined ? ` ${fmtPct(item.leader_pct)}` : ''}</span>
    </div>`).join('');

  const laggardRows = (o.sectors?.laggards || []).slice(0, 4).map((item) => `
    <div class="sector-row neg">
      <span class="sector-name" title="${esc(item.name)}">${esc(item.name)}</span>
      <span class="sector-track"><i class="sector-fill" style="width:${clamp(Math.abs(item.pct) / maxSector * 100, 4, 100)}%"></i></span>
      <span class="sector-pct ${pctClass(item.pct)}">${fmtPct(item.pct)}</span>
    </div>`).join('');

  const movers = (o.movers?.active || []).slice(0, 5).map((item) => `
    <button class="mover-row" data-action="detail" data-code="${esc(item.code)}">
      <span>${esc(item.name)} <small class="micro">${esc(item.industry)}</small></span>
      <b class="${pctClass(item.pct)}">${fmtPct(item.pct)}</b>
    </button>`).join('');

  const gainers = (o.movers?.gainers || []).slice(0, 5).map((item) => `
    <button class="mover-row" data-action="detail" data-code="${esc(item.code)}">
      <span>${esc(item.name)} <small class="micro">${fmtYi(item.amount_yi)}</small></span>
      <b class="${pctClass(item.pct)}">${fmtPct(item.pct)}</b>
    </button>`).join('');

  $('#viewRoot').innerHTML = `
    <section class="view overview-view">
      <div class="index-grid">${indexCards}</div>
      <div class="overview-grid">
        <article class="card pulse-card">
          <div class="card-head">
            <div><div class="eyebrow">MARKET TEMPERATURE</div><h2>市场温度</h2></div>
            <span class="badge ${temp.label === '过热' || temp.label === '偏热' ? 'hot' : temp.label === '冰点' || temp.label === '偏冷' ? 'cold' : 'gold'}">${esc(temp.label || '—')}</span>
          </div>
          <div class="pulse-body">
            <div>
              <div class="temp-ring" style="--temp:${clamp(Number(temp.score) || 0, 0, 100)}">
                <div><strong>${fmtNum(temp.score, 1)}</strong><small>/ 100</small></div>
              </div>
              <div class="temp-label">${esc(temp.label || '—')} · 情绪描述</div>
            </div>
            <div class="breadth">
              <div class="breadth-head"><span>涨跌家数 · 全市场 ${breadth.universe || '—'} 只</span><b>${fmtNum(breadth.up_ratio * 100, 1)}% 上涨</b></div>
              <div class="breadth-bar">
                <i class="b-up" style="width:${upW}%"></i>
                <i class="b-flat" style="width:${flatW}%"></i>
                <i class="b-down" style="width:${downW}%"></i>
              </div>
              <div class="breadth-legend">
                <span>上涨 <b class="up">${breadth.up ?? '—'}</b></span>
                <span>平盘 <b>${breadth.flat ?? '—'}</b></span>
                <span>下跌 <b class="down">${breadth.down ?? '—'}</b></span>
              </div>
              <div class="stat-grid">
                <div class="stat-cell"><span>涨停</span><b class="up">${breadth.limit_up ?? '—'}</b></div>
                <div class="stat-cell"><span>跌停</span><b class="down">${breadth.limit_down ?? '—'}</b></div>
                <div class="stat-cell"><span>涨超5%</span><b class="up">${breadth.strong ?? '—'}</b></div>
                <div class="stat-cell"><span>中位涨幅</span><b class="${pctClass(breadth.median_pct)}">${fmtPct(breadth.median_pct)}</b></div>
              </div>
            </div>
          </div>
          <div class="temp-note">${icon('alert')}<span>${esc(temp.note || '情绪温度是描述性指标，不是涨跌预测。')}</span></div>
        </article>

        <article class="card">
          <div class="card-head">
            <div><div class="eyebrow">DAILY WATCHLIST</div><h2>今日候选</h2></div>
            <button class="text-btn" data-route="picks">查看全部 ${icon('arrow')}</button>
          </div>
          <div class="pick-preview-list">${preview}</div>
          ${previewHealth}
          <div class="temp-note">${icon('shield')}<span>候选按模型相对优势 + 因子质量 + 风险约束排序，不是买入指令。</span></div>
        </article>
      </div>

      <div class="two-col">
        <article class="card">
          <div class="card-head">
            <div><div class="eyebrow">SECTOR ROTATION</div><h2>板块热度</h2></div>
            <span class="badge">东方财富行业</span>
          </div>
          <div class="section-split">
            <div><div class="mini-title"><span class="dot"></span>领涨板块</div>${sectorRows || '<p class="micro">暂无板块数据</p>'}</div>
            <div><div class="mini-title"><span class="dot green"></span>弱势板块</div>${laggardRows || '<p class="micro">暂无板块数据</p>'}</div>
          </div>
        </article>

        <article class="card">
          <div class="card-head">
            <div><div class="eyebrow">LIQUIDITY & BREADTH</div><h2>成交与活跃度</h2></div>
            <span class="badge">${esc(o.market?.label || '—')}</span>
          </div>
          <div class="card-body" style="padding-top:12px">
            <div class="flow-stats">
              <div class="flow-stat"><span>两市成交额</span><b>${fmtYi(o.turnover?.amount_yi)}</b><small>5日均值 ${fmtYi(o.turnover?.avg5_yi)}</small></div>
              <div class="flow-stat"><span>量能比（折算）</span><b>${o.turnover?.ratio ? o.turnover.ratio.toFixed(2) : '—'}</b><small>进度 ${fmtNum(o.turnover?.progressPct, 0)}% · 折算 ${fmtYi(o.turnover?.projected_yi)}</small></div>
              <div class="flow-stat"><span>涨跌比</span><b>${breadth.advance_decline_ratio ?? '—'}</b><small>上涨 / 下跌</small></div>
              <div class="flow-stat"><span>情绪分位</span><b>${temp.score ?? '—'}</b><small>${esc(temp.label || '—')}</small></div>
            </div>
            <div class="mini-title" style="margin-top:14px"><span class="dot"></span>成交额活跃</div>
            <div class="mover-list">${movers || '<p class="micro">暂无数据</p>'}</div>
            <div class="mini-title" style="margin-top:10px"><span class="dot"></span>涨幅居前</div>
            <div class="mover-list">${gainers || '<p class="micro">暂无数据</p>'}</div>
          </div>
        </article>
      </div>

      <article class="card disclosure-card">
        ${icon('shield')}
        <p><b>先看风险，再看机会。</b> 本页的“温度”和“候选”都是对公开行情的整理，不预测确定涨跌。候选计划包含止损与仓位约束；若模型验证转弱或数据过期，页面会明确提示。${esc(o.dataQuality?.latencyNote || '')}</p>
      </article>
    </section>`;
}

/* ------------------------------------------------------------------ leaders */
function renderLeaders() {
  const data = state.leaders;
  if (!data) return;
  const leader = data.marketLeader;
  const stats = data.stats || {};
  const candidates = data.candidates || [];
  const sectors = data.sectorLeaders || [];
  const types = data.types || [];
  const fallback = data.isFallback || !leader;
  const notice = data.poolError
    ? `<div class="notice danger">${icon('alert')}<div>涨停池数据暂时不可用，已用成交/趋势信号兜底：${esc(data.poolError)}</div></div>`
    : fallback
      ? `<div class="notice">${icon('alert')}<div>当前没有可用的涨停池（可能未开盘/休市），以下以成交额、趋势和资金信号兜底。</div></div>`
      : '';
  const leaderCard = leader ? `
    <div class="leader-hero">
      <div class="leader-hero-glow"></div>
      <div class="leader-hero-main">
        <div class="eyebrow">MARKET LEADER · ${esc(data.tradeDate)}</div>
        <div class="leader-name-line">
          <h2>${esc(leader.name)}</h2>
          <span class="leader-code">${esc(leader.code)}</span>
          <span class="badge gold">${leader.board_count ? `${leader.board_count} 连板` : '趋势/成交候选'}</span>
        </div>
        <p class="leader-sub">${esc(leader.industry)} · ${leader.first_time ? `首封 ${esc(leader.first_time)}` : '非涨停股'} · ${esc(leader.reason)}</p>
        <div class="leader-price-line">
          <strong>${fmtNum(leader.price)}</strong>
          <span class="${pctClass(leader.pct)}">${fmtPct(leader.pct)}</span>
          <span class="leader-metric">成交 ${fmtYi(leader.amount_yi)}</span>
          <span class="leader-metric">换手 ${fmtNum(leader.turnover, 2)}%</span>
        </div>
        <div class="chips">${(leader.tags || []).map((tag) => `<span class="chip good">${esc(tag)}</span>`).join('')}</div>
        <div class="chips">${(leader.risks || []).slice(0, 2).map((risk) => `<span class="chip warn">${esc(risk)}</span>`).join('')}</div>
        <button class="primary-btn leader-detail-btn" data-action="detail" data-code="${esc(leader.code)}">查看个股详情 ${icon('arrow')}</button>
      </div>
      <div class="leader-hero-side">
        <div class="leader-score" style="--score:${clamp(Number(leader.score) || 0, 0, 100)}"><strong>${fmtNum(leader.score, 0)}</strong><small>龙头分</small></div>
        <div class="leader-side-stats">
          <div><span>封单</span><b>${fmtYi(leader.seal_fund_yi)}</b></div>
          <div><span>开板</span><b>${leader.open_times} 次</b></div>
          <div><span>主力净流入</span><b class="${Number(leader.main_inflow_yi) >= 0 ? 'up' : 'down'}">${fmtSigned(leader.main_inflow_yi)}亿</b></div>
        </div>
      </div>
    </div>` : `<div class="pick-empty">${icon('crown')}<h3>今天没有明确的龙头</h3><p>涨停池为空或行情源未更新，先看市场总览，不强行找龙头。</p></div>`;

  const typeCards = types.map((type) => {
    const stock = type.stock;
    if (!stock) {
      return `<div class="leader-type muted"><span>${esc(type.label)}</span><small>${esc(type.hint)}</small><strong>暂无</strong><em>—</em></div>`;
    }
    return `
      <button class="leader-type" data-action="detail" data-code="${esc(stock.code)}">
        <span>${esc(type.label)}</span>
        <small>${esc(type.hint)}</small>
        <strong>${esc(stock.name)}</strong>
        <em>${esc(type.metric || '')}</em>
        <b class="${pctClass(stock.pct)}">${fmtPct(stock.pct)}</b>
      </button>`;
  }).join('');

  const sectorRows = sectors.map((stock, index) => `
    <button class="leader-sector-row" data-action="detail" data-code="${esc(stock.code)}">
      <span class="leader-rank">${String(index + 1).padStart(2, '0')}</span>
      <span class="leader-sector-main"><strong>${esc(stock.sector_name)}</strong><small>板块 ${fmtPct(stock.sector_pct)} · 涨 ${stock.sector_up ?? '—'} / 跌 ${stock.sector_down ?? '—'}</small></span>
      <span class="leader-sector-stock"><b>${esc(stock.name)}</b><small>${stock.board_count ? `${stock.board_count}连板` : '非涨停'} · ${fmtYi(stock.amount_yi)}</small></span>
      <span class="leader-sector-pct ${pctClass(stock.pct)}">${fmtPct(stock.pct)}</span>
    </button>`).join('');

  const candidateRows = candidates.map((stock, index) => `
    <button class="leader-candidate" data-action="detail" data-code="${esc(stock.code)}">
      <span class="leader-rank">${String(index + 1).padStart(2, '0')}</span>
      <span class="leader-candidate-main"><strong>${esc(stock.name)} <small>${esc(stock.code)}</small></strong><small>${esc(stock.industry)} · ${esc(stock.reason)}</small></span>
      <span class="leader-candidate-meta"><b>${stock.board_count ? `${stock.board_count}板` : '趋势'}</b><small>${fmtYi(stock.amount_yi)}</small></span>
      <span class="leader-candidate-score">${fmtNum(stock.score, 0)}<small>龙头分</small></span>
    </button>`).join('');

  $('#viewRoot').innerHTML = `
    <section class="view leaders-view">
      <div class="leader-stats">
        <div><span>涨停家数</span><b class="up">${stats.limitUpCount ?? '—'}</b><small>可分析 ${stats.matchedCount ?? stats.poolTotal ?? '—'} 只</small></div>
        <div><span>最高连板</span><b>${stats.maxBoard ?? '—'}</b><small>${esc(stats.maxBoardName || '—')}</small></div>
        <div><span>板块龙头</span><b>${stats.sectorCount ?? '—'}</b><small>领涨行业</small></div>
        <div><span>数据日期</span><b>${esc(stats.tradeDate || data.tradeDate || '—')}</b><small>${esc(data.market?.label || '—')}</small></div>
      </div>
      ${notice}
      ${leaderCard}
      <div class="leader-type-grid">${typeCards}</div>
      <div class="leader-grid">
        <article class="card">
          <div class="card-head"><div><div class="eyebrow">SECTOR LEADERS</div><h2>板块龙头</h2></div><span class="badge">领涨行业</span></div>
          <div class="leader-sector-list">${sectorRows || '<p class="micro" style="padding:16px">暂无板块龙头数据。</p>'}</div>
        </article>
        <article class="card">
          <div class="card-head"><div><div class="eyebrow">LEADER CANDIDATES</div><h2>龙头候选</h2></div><span class="badge">按龙头分排序</span></div>
          <div class="leader-candidate-list">${candidateRows || '<p class="micro" style="padding:16px">暂无候选，等待涨停池恢复。</p>'}</div>
        </article>
      </div>
      <article class="card leader-guide">
        <div class="card-head"><div><div class="eyebrow">HOW TO READ</div><h2>怎么用这张龙头榜</h2></div></div>
        <div class="leader-guide-grid">
          <div><b>01 先看总龙头</b><p>它代表当前市场情绪的高度。总龙头断板/大幅回落，通常意味着短线情绪退潮，其他候选也要降级。</p></div>
          <div><b>02 再看板块龙头</b><p>板块涨幅靠前 + 个股在板块内领涨，才算“板块龙头”。只有个股涨、板块不跟，持续性要打折。</p></div>
          <div><b>03 最后看能否参与</b><p>连板越高、开板越多，次日分歧越大。不要因为“它是龙头”就追高；用关注区间、止损和仓位约束。</p></div>
        </div>
        <div class="leader-risk">${icon('alert')}<span>龙头是情绪与资金的结果，不是买入理由。高位连板、开板反复、换手过高都属于高风险特征；本页只做识别与观察，不构成投资建议。</span></div>
      </article>
    </section>`;
}

/* ------------------------------------------------------------------ picks */
function renderPicks() {
  const data = state.picks;
  if (!data) return;
  const picks = data.picks || [];
  const model = data.model || {};
  const metrics = model.metrics || {};
  const profiles = data.profiles || Object.entries(PROFILE_LABELS).map(([key, label]) => ({ key, label }));
  const dailyAuc = metrics.daily_auc;
  const rankIc = metrics.rank_ic;
  const icDays = metrics.ic_days;
  const modelWeak = !metrics.usable || (dailyAuc !== undefined && dailyAuc !== null && Number(dailyAuc) < 0.52);
  const baseRate = Number(data.baseRate || metrics.base_rate || 0.5);
  const noticeClass = picks.length === 0 ? ' danger' : '';
  const staleNotice = data.stale
    ? `<div class="notice danger">${icon('alert')}<div>行情源暂不可用，当前展示的是上一次成功候选；恢复后刷新即可更新。</div></div>`
    : '';
  const notice = picks.length === 0
    ? ''
    : data.noPickReason
      ? `<div class="notice${noticeClass}">${icon('alert')}<div>${esc(data.noPickReason)}</div></div>`
      : modelWeak
        ? `<div class="notice">${icon('alert')}<div>模型未通过可用性门槛：日度截面 AUC ${dailyAuc ?? '—'}，Rank IC ${rankIc ?? '—'}（${icDays ?? 0} 个交易日）；池化 AUC ${metrics.auc ?? '—'} 只作参考。候选当前按规则质量 + 风险排序，不按模型概率排序。</div></div>`
        : '';
  const modelConfidence = CONFIDENCE_LABELS[model.confidence] || '—';
  const learning = data.learning || {};
  const learningSamples = Number(learning.samples || 0);
  const learningAdjustment = Number((learning.calibration || {}).adjustmentPpAt50 || 0);
  const learningStrip = `
    <div class="learning-strip">
      <div class="learning-strip-icon">${icon('loop')}</div>
      <div><strong>${learningSamples ? `已复盘 ${learningSamples} 个候选样本` : '学习复盘等待样本'}</strong><span>${learningSamples ? `实际命中率 ${learning.hitRate !== null && learning.hitRate !== undefined ? (Number(learning.hitRate) * 100).toFixed(1) + '%' : '—'}，概率校准 ${learningAdjustment >= 0 ? '+' : ''}${learningAdjustment.toFixed(1)}pp` : '先记录候选，下一交易日收盘后自动复盘并校准概率。'}</span></div>
      <button class="text-btn" data-route="learning">查看学习复盘 ${icon('arrow')}</button>
    </div>`;
  const paper = data.paper || {};
  const paperPnl = Number(paper.totalPnl || 0);
  const paperLatest = paper.latest;
  const paperPending = paper.latestPending;
  const paperStrip = `
    <div class="paper-strip">
      <div class="paper-strip-main"><span>1 万元模拟盘</span><strong class="${paperPnl >= 0 ? 'up' : 'down'}">${paperPnl >= 0 ? '+' : ''}${fmtNum(paperPnl, 2)}</strong><small>累计 · 权益 ${fmtNum(paper.equity || 10000, 2)}</small></div>
      <div class="paper-strip-note">${paperLatest ? `最近结算 ${esc(paperLatest.signalDate)}：${paperLatest.totalReturnPct >= 0 ? '+' : ''}${fmtNum(paperLatest.totalReturnPct, 2)}%` : paperPending ? `已记录 ${esc(paperPending.signalDate)} 的候选，等下一交易日收盘结算` : '生成候选后自动记录模拟交易'}</div>
      <button class="text-btn" data-route="learning">查看模拟记录 ${icon('arrow')}</button>
    </div>`;
  const usageGuide = `
    <div class="usage-guide ${state.guides.picksGuide ? 'collapsed' : ''}" id="picksGuide">
      <div class="usage-guide-head">
        <div><div class="eyebrow">HOW TO USE · 3 STEPS</div><h2>先看环境，再等价格，最后才谈买入</h2></div>
        <button class="text-btn" data-action="toggle-guide" data-target="picksGuide">${state.guides.picksGuide ? '展开说明' : '收起说明'}</button>
      </div>
      <div class="usage-steps">
        <div class="usage-step"><b>1</b><div><strong>先看市场温度</strong><p>偏冷/过热都降低仓位；大盘放量跳水时，今天不执行新计划。温度不是涨跌预测，只决定你要不要更保守。</p></div></div>
        <div class="usage-step"><b>2</b><div><strong>等价格进入“关注区间”</strong><p>关注区间不是立即买入价。现价高于上沿就加入自选等回踩；进入区间后再分 2 笔，不追高。</p></div></div>
        <div class="usage-step"><b>3</b><div><strong>先执行止损，再谈目标</strong><p>跌破止损就停止计划，不补仓、不摊薄。单笔风险默认不超过总资金 0.5%，仓位按计划上限再减半起步。</p></div></div>
      </div>
      <div class="usage-foot">
        <span class="state-legend"><i class="state-dot action"></i>可分批 <i class="state-dot wait"></i>等回踩 <i class="state-dot observe"></i>只观察 <i class="state-dot danger"></i>计划失效</span>
        <span>所有动作都是条件式研究计划，不是收益承诺。</span>
      </div>
    </div>`;
  const funnelCard = picks.length === 1 ? `
    <aside class="card filter-card">
      <div class="card-head"><div><div class="eyebrow">FILTER FUNNEL</div><h2>为什么只有 1 只</h2></div><span class="badge cold">不凑数</span></div>
      <div class="card-body">
        <div class="funnel-row"><span>全市场股票</span><b>${fmtNum(data.universeCount, 0)}</b></div>
        <div class="funnel-row"><span>流动性 / 趋势初筛</span><b>${fmtNum(data.candidateCount, 0)}</b></div>
        <div class="funnel-row"><span>模型不弱于基准 + 因子质量</span><b>${fmtNum(data.qualifiedCount, 0)}</b></div>
        <div class="funnel-row emphasis"><span>最终通过风险与行业约束</span><b>${picks.length}</b></div>
        <p class="micro" style="margin-top:12px">当通过数量不足 2 只时，系统不降标准补位。单只标的的个股风险更集中，建议把仓位降到计划上限的一半以下，或继续等待。</p>
        <div class="funnel-cta">
          <button class="ghost-btn" data-route="overview">看市场温度</button>
          <button class="ghost-btn" data-action="refresh">重新筛选</button>
        </div>
      </div>
    </aside>` : '';

  const cards = picks.map((pick) => {
    const plan = pick.plan || {};
    const execution = pick.execution || {};
    const conf = pick.confidence || 'medium';
    const reasons = (pick.reasons || []).slice(0, 2).map((text) => `<span class="chip good">${esc(text)}</span>`).join('');
    const risks = (pick.risks || []).slice(0, 2).map((text) => `<span class="chip warn">${esc(text)}</span>`).join('');
    const flags = (pick.flags || []).map((flag) => `<span class="chip bad">${esc(flag)}</span>`).join('');
    const weakEdge = Number(pick.relative_edge || 0) < 0.005;
    const edgeText = Number.isFinite(Number(pick.relative_edge)) ? `${Number(pick.relative_edge) >= 0 ? '+' : ''}${(Number(pick.relative_edge) * 100).toFixed(1)}pp` : '—';
    const edgeChip = !metrics.usable
      ? '<span class="chip warn">模型未通过门槛，按规则排序</span>'
      : weakEdge
        ? '<span class="chip warn">模型优势微弱</span>'
        : '';
    const probabilityLine = metrics.usable
      ? `${esc(model.probabilityLabel || '10日上涨概率')} <b>${fmtNum(pick.probability_pct, 1)}%</b> · 基准 ${(baseRate * 100).toFixed(1)}% · 相对 ${edgeText}`
      : `规则分 <b>${fmtNum(pick.rule_score, 1)}</b> · 风险分 <b>${fmtNum(pick.risk_score, 1)}</b> · 模型未通过门槛`;
    const learnAdjust = Number(pick.probability_adjustment_pp || 0);
    const learnChip = Math.abs(learnAdjust) >= 0.1 ? `<span class="chip">学习校准 ${learnAdjust >= 0 ? '+' : ''}${learnAdjust.toFixed(1)}pp</span>` : '';
    const execSteps = (execution.steps || []).map((step) => `<li>${esc(step)}</li>`).join('');
    return `
      <article class="pick-card">
        <div class="pick-top">
          <span class="pick-rank">${String(pick.rank).padStart(2, '0')}</span>
          <div class="pick-title">
            <h3>${esc(pick.name)} <span>${esc(pick.code)}</span></h3>
            <p>${esc(pick.industry)} · ${probabilityLine}</p>
          </div>
          <div class="score-orb" style="--score:${clamp(Number(pick.score) || 0, 0, 100)}"><strong>${fmtNum(pick.score, 0)}</strong><small>综合</small></div>
        </div>
        <div class="pick-price">
          <strong>${fmtNum(pick.price)}</strong>
          <span class="pct ${pctClass(pick.pct)}">${fmtPct(pick.pct)}</span>
          <span class="meta">${metrics.usable ? `模型分位 Top ${Math.max(1, Math.round((1 - (pick.model_percentile || 0)) * 100))}% · ` : ''}量比 ${fmtNum(pick.volume_ratio, 2)}</span>
        </div>
        <div class="pick-spark">${sparkline(pick.spark, pctClass(pick.pct) === 'up' ? '#ff5d6d' : '#22c58b', 420, 36)}</div>
        <div class="execution-box ${esc(execution.tone || 'observe')}">
          <div class="execution-head">
            <span class="execution-label">${esc(execution.label || '先观察')}</span>
            <strong>${esc(execution.headline || '先加入自选，等价格与量能给出更明确的触发条件。')}</strong>
          </div>
          <ol class="execution-steps">${execSteps}</ol>
          <div class="execution-foot">
            <span>${esc(execution.position || '')}</span>
            <span class="execution-avoid">${esc(execution.avoid || '')}</span>
          </div>
        </div>
        <div class="plan-grid">
          <div class="plan-cell"><span>关注区间</span><b>${fmtNum(plan.entry_low)}–${fmtNum(plan.entry_high)}</b></div>
          <div class="plan-cell"><span>止损</span><b class="down">${fmtNum(plan.stop)}</b></div>
          <div class="plan-cell"><span>目标</span><b class="up">${fmtNum(plan.target1)}</b></div>
          <div class="plan-cell"><span>建议仓位</span><b>≤${fmtNum(plan.suggested_weight_pct, 1)}%</b></div>
        </div>
        <div class="plan-caption">价格进入区间才执行；区间是计划，不是预测。</div>
        <div class="chips">${reasons}${risks}${flags}${edgeChip}${learnChip}</div>
        <div class="pick-foot">
          <span class="confidence ${esc(conf)}">${esc(CONFIDENCE_LABELS[conf] || '')} · ${esc(plan.action || '')}</span>
          <button class="ghost-btn" data-action="detail" data-code="${esc(pick.code)}">${icon('chart')} 详情</button>
          <button class="ghost-btn" data-action="watch" data-code="${esc(pick.code)}">${icon('plus')} 自选</button>
        </div>
      </article>`;
  }).join('');

  const featureBars = (model.featureImportance || []).slice(0, 6).map((item) => {
    const max = Math.max(...(model.featureImportance || []).map((row) => Math.abs(row.weight) || 0), 0.0001);
    return `
      <div class="feature-row">
        <span>${esc(item.label)}</span>
        <span class="feature-track"><i class="feature-fill" style="width:${clamp(Math.abs(item.weight) / max * 100, 4, 100)}%"></i></span>
        <b>${Number(item.weight) >= 0 ? '+' : ''}${fmtNum(item.weight, 3)}</b>
      </div>`;
  }).join('');

  $('#viewRoot').innerHTML = `
    <section class="view picks-view">
      <div class="picks-toolbar">
        <div class="profile-tabs">
          ${profiles.map((profile) => `<button class="profile-tab ${profile.key === state.profile ? 'active' : ''}" data-profile="${esc(profile.key)}">${esc(profile.label)}</button>`).join('')}
        </div>
        <div class="model-health"><span class="health-dot"></span>日度截面 AUC <b>${dailyAuc ?? '—'}</b> · Rank IC <b>${rankIc ?? '—'}</b> · ${modelWeak ? '模型未通过门槛' : esc(modelConfidence)}</div>
      </div>
      ${staleNotice}
      ${usageGuide}
      ${learningStrip}
      ${paperStrip}
      ${notice}
      <div class="picks-grid ${picks.length === 1 ? 'single' : ''}">
        ${picks.length ? `${cards}${funnelCard}` : `
        <div class="pick-empty">
          ${icon('shield')}
          <h3>今日不凑数</h3>
          <p>${esc(data.noPickReason || '当前没有同时满足模型相对优势、趋势质量与风险约束的标的。空仓等待也是策略的一部分。')}</p>
          <button class="ghost-btn" data-route="overview">回到市场总览</button>
        </div>`}
      </div>

      <article class="card model-card">
        <div class="card-head">
          <div><div class="eyebrow">MODEL HEALTH</div><h2>模型体检与验证</h2></div>
          <span class="badge ${modelWeak ? 'cold' : 'gold'}">${modelWeak ? '未通过可用性门槛' : '可用信号'} · ${esc(model.method || '')}</span>
        </div>
        <div class="model-metrics">
          <div class="metric"><span>日度截面 AUC</span><b>${dailyAuc ?? '—'}</b><small>池化 AUC ${metrics.auc ?? '—'} · ${icDays ?? 0} 日</small></div>
          <div class="metric"><span>Rank IC</span><b>${rankIc ?? '—'}</b><small>截面选股相关性 · ICIR ${metrics.ic_ir ?? '—'}</small></div>
          <div class="metric"><span>Top 10% 命中率</span><b>${metrics.top_decile_hit ? (metrics.top_decile_hit * 100).toFixed(1) + '%' : '—'}</b><small>验证集未来10日上涨比例</small></div>
          <div class="metric"><span>Top 10% 超额</span><b class="${Number(metrics.edge) >= 0 ? 'up' : 'down'}">${metrics.edge !== undefined ? (metrics.edge * 100).toFixed(2) + 'pp' : '—'}</b><small>基准上涨率 ${metrics.base_rate ? (metrics.base_rate * 100).toFixed(1) + '%' : '—'}</small></div>
        </div>
        ${modelWeak ? `<div class="model-alert">${icon('alert')}<span>模型未通过门槛（日度 AUC ≥ 0.52 且 Rank IC ≥ 0.02）。候选排名已改为规则质量 + 风险，不再让弱模型分位参与。</span></div>` : ''}
        <div class="model-detail">
          <div>
            <div class="mini-title"><span class="dot"></span>标准化特征权重（正负方向）</div>
            <div class="feature-bars">${featureBars || '<p class="micro">模型不可用，仅展示规则评分。</p>'}</div>
            <p class="model-note" style="margin-top:10px">训练：${fmtCompact(metrics.train_samples)} 条 · 验证：${fmtCompact(metrics.validation_samples)} 条 · 净化 ${metrics.purge_days ?? 10} 日 · ${esc(metrics.validation_from || '—')} → ${esc(metrics.validation_to || '—')}</p>
          </div>
          <div class="model-note">
            <strong>怎么读这个模型</strong>
            <ul>
              <li><b>池化 AUC</b> 会把全市场同涨同跌日混在一起，不能单独代表选股能力；选股主要看 <b>日度截面 AUC / Rank IC</b>。</li>
              <li>当前模型即使未通过门槛，也会保留在页面里做透明诊断，但不再参与最终排序。</li>
              <li>概率仍是“未来10个交易日收盘上涨”的历史校准估计，不是保证；候选还会经过趋势、量能、波动与行业分散过滤。</li>
              <li>${esc(model.limitations || '模型存在选择偏差与样本重叠，验证结果可能偏乐观。')}</li>
            </ul>
          </div>
        </div>
      </article>

      <article class="card disclosure-card">
        ${icon('alert')}
        <p><b>交易计划不是收益承诺。</b> 止损价按 ATR 与结构位估算，实际成交还受 T+1、涨跌停、滑点、手续费与流动性影响。不要用生活必需资金或杠杆参与。</p>
      </article>
    </section>`;
}

/* ------------------------------------------------------------------ watch */
function watchAlertsHtml(item) {
  const alerts = (item.alerts || []).map((alert) => `<span class="alert-pill ${esc(alert.level || 'muted')}">${esc(alert.text)}</span>`).join('');
  return alerts || '<span class="micro">暂无新信号</span>';
}

function watchNextHtml(item) {
  const next = item.nextAction || {};
  return `
    <div class="next-action ${esc(next.tone || 'muted')}">
      <strong>${esc(next.label || '仅跟踪')}</strong>
      <span>${esc(next.detail || '先补关注区间和止损，再谈执行。')}</span>
    </div>`;
}

function watchStats(items) {
  const signalCount = items.reduce((sum, item) => sum + (item.alerts || []).filter((alert) => alert.level && alert.level !== 'muted').length, 0);
  const inBuyZone = items.filter((item) => (item.alerts || []).some((alert) => alert.text.includes('进入计划买区'))).length;
  const avgPct = items.length ? items.reduce((sum, item) => sum + Number(item.pct || 0), 0) / items.length : 0;
  const stopDistances = items.map((item) => item.distance_stop_pct).filter((value) => Number.isFinite(Number(value)));
  const avgStop = stopDistances.length ? stopDistances.reduce((sum, value) => sum + Number(value), 0) / stopDistances.length : null;
  return { signalCount, inBuyZone, avgPct, avgStop };
}

function watchFeedHtml(items) {
  const feed = items
    .flatMap((item) => (item.alerts || []).filter((alert) => alert.level && alert.level !== 'muted').map((alert) => ({ item, alert })))
    .slice(0, 4)
    .map(({ item, alert }) => `<div class="feed-row"><span class="alert-pill ${esc(alert.level)}">${esc(alert.text)}</span><span>${esc(item.name)}</span><b>${fmtNum(item.price)}</b></div>`)
    .join('');
  return feed || '<p class="micro">暂无触发信号，保持观察。</p>';
}

function updateWatchLive(items) {
  items.forEach((item) => {
    const row = document.querySelector(`tr[data-watch-code="${item.code}"]`);
    if (!row) return;
    const price = row.querySelector('.js-watch-price');
    const pct = row.querySelector('.js-watch-pct');
    if (price) price.textContent = fmtNum(item.price);
    if (pct) {
      pct.className = `micro js-watch-pct ${pctClass(item.pct)}`;
      pct.textContent = fmtPct(item.pct);
    }
    const next = row.querySelector('.next-action');
    if (next) next.outerHTML = watchNextHtml(item);
    const alerts = row.querySelector('.js-watch-alerts');
    if (alerts) alerts.innerHTML = watchAlertsHtml(item);
  });
  const stats = watchStats(items);
  const avgPct = document.getElementById('watchAvgPct');
  const signalCount = document.getElementById('watchSignalCount');
  const buyZone = document.getElementById('watchBuyZone');
  const avgStop = document.getElementById('watchAvgStop');
  if (avgPct) {
    avgPct.className = pctClass(stats.avgPct);
    avgPct.textContent = fmtPct(stats.avgPct);
  }
  if (signalCount) signalCount.textContent = String(stats.signalCount);
  if (buyZone) buyZone.textContent = String(stats.inBuyZone);
  if (avgStop) {
    avgStop.className = stats.avgStop !== null && stats.avgStop < 0 ? 'down' : '';
    avgStop.textContent = stats.avgStop !== null ? fmtPct(stats.avgStop) : '—';
  }
  const feed = document.getElementById('watchFeedList');
  if (feed) feed.innerHTML = watchFeedHtml(items);
}

function renderWatch() {
  const data = state.watch;
  if (!data) return;
  const items = data.items || [];
  const rows = items.map((item) => {
    const plan = item.plan || {};
    return `
      <tr data-watch-code="${esc(item.code)}">
        <td data-label="标的">
          <div class="watch-name" data-action="detail" data-code="${esc(item.code)}">
            <strong>${esc(item.name)}</strong>
            <small>${esc(item.code)} · ${esc(item.industry || '—')}</small>
          </div>
        </td>
        <td data-label="现价">
          <div class="watch-price js-watch-price">${fmtNum(item.price)}</div>
          <div class="micro js-watch-pct ${pctClass(item.pct)}">${fmtPct(item.pct)}</div>
        </td>
        <td data-label="交易计划">
          <div class="watch-plan">买 <b>${fmtNum(plan.entry_low)}–${fmtNum(plan.entry_high)}</b></div>
          <div class="watch-plan">止 <b class="down">${fmtNum(plan.stop)}</b> · 目标 <b class="up">${fmtNum(plan.target1)}</b></div>
          ${item.plan_source === 'auto' ? '<span class="plan-tag">加入时自动生成</span>' : ''}
        </td>
        <td data-label="下一步">
          ${watchNextHtml(item)}
          <div class="alert-list js-watch-alerts">${watchAlertsHtml(item)}</div>
        </td>
        <td data-label="操作">
          <div class="row-actions">
            <button class="ghost-btn" data-action="detail" data-code="${esc(item.code)}">详情</button>
            ${item.stop ? '' : `<button class="ghost-btn" data-action="fill-plan" data-code="${esc(item.code)}">补计划</button>`}
            <button class="ghost-btn danger-btn" data-action="remove-watch" data-code="${esc(item.code)}" aria-label="移除 ${esc(item.name)}">${icon('trash')}</button>
          </div>
        </td>
      </tr>`;
  }).join('');

  const { signalCount, inBuyZone, avgPct, avgStop } = watchStats(items);
  const feed = watchFeedHtml(items);

  $('#viewRoot').innerHTML = `
    <section class="view watch-view">
      <div class="usage-guide watch-guide ${state.guides.watchGuide ? 'collapsed' : ''}" id="watchGuide">
        <div class="usage-guide-head">
          <div><div class="eyebrow">HOW TO USE · NEXT ACTION</div><h2>每行先看“下一步”，再决定要不要动手</h2></div>
          <button class="text-btn" data-action="toggle-guide" data-target="watchGuide">${state.guides.watchGuide ? '展开说明' : '收起说明'}</button>
        </div>
        <div class="usage-steps compact">
          <div class="usage-step"><b>1</b><div><strong>等回踩</strong><p>现价高于买区：只加入自选，等价格回到区间，不追高。</p></div></div>
          <div class="usage-step"><b>2</b><div><strong>可分批</strong><p>价格进入买区：首笔减半、分 2 笔；没进区间就不动手。</p></div></div>
          <div class="usage-step"><b>3</b><div><strong>执行纪律</strong><p>跌破止损先退出；到目标先减仓。信号只是提醒，不是下单按钮。</p></div></div>
        </div>
        <div class="usage-foot">
          <span class="state-legend"><i class="state-dot wait"></i>等回踩 <i class="state-dot action"></i>可分批 <i class="state-dot warn"></i>不追高 <i class="state-dot danger"></i>止损 <i class="state-dot success"></i>止盈</span>
          <span>“下一步”是按当前价格与计划位自动生成的条件式提示。</span>
        </div>
      </div>
      <div class="watch-layout">
        <article class="card watch-card">
          <div class="watch-head">
            <div><div class="eyebrow">LIVE MONITOR</div><h2>自选监控 <span class="badge">${items.length} 只</span></h2></div>
            <div class="watch-add">
              <input id="watchCode" inputmode="numeric" maxlength="6" placeholder="6位代码" aria-label="输入股票代码">
              <button class="primary-btn" data-action="add-watch">${icon('plus')} 添加</button>
            </div>
          </div>
          ${items.length ? `
            <table class="watch-table">
              <thead><tr><th>标的</th><th>现价</th><th>交易计划</th><th>下一步 / 信号</th><th style="text-align:right">操作</th></tr></thead>
              <tbody>${rows}</tbody>
            </table>
            <div class="watch-summary">
              <div><span>自选平均涨跌</span><b id="watchAvgPct" class="${pctClass(avgPct)}">${fmtPct(avgPct)}</b></div>
              <div><span>当前触发信号</span><b id="watchSignalCount">${signalCount}</b></div>
              <div><span>处于买区内</span><b id="watchBuyZone">${inBuyZone}</b></div>
              <div><span>平均距止损</span><b id="watchAvgStop" class="${avgStop !== null && avgStop < 0 ? 'down' : ''}">${avgStop !== null ? fmtPct(avgStop) : '—'}</b></div>
            </div>
            <div class="watch-feed">
              <div class="mini-title"><span class="dot"></span>当前提醒</div>
              <div id="watchFeedList">${feed || '<p class="micro">暂无触发信号，保持观察。</p>'}</div>
            </div>` : `
            <div class="watch-empty">
              ${icon('eye')}
              <h3>自选还是空的</h3>
              <p>从今日候选中一键加入，或输入代码添加。加入后会按关注区间、止损与目标位实时提示。</p>
              <button class="primary-btn" data-route="picks">去今日候选</button>
            </div>`}
        </article>

        <aside class="card watch-help">
          <div class="card-head"><div><div class="eyebrow">ALERT RULES</div><h2>监控规则</h2></div></div>
          <div class="card-body">
            <div class="rule-list">
              <div class="rule-item">${icon('target')}<div><strong>进入计划买区</strong><span>现价落入加入自选时记录的建议区间。</span></div></div>
              <div class="rule-item">${icon('alert')}<div><strong>止损 / 目标触发</strong><span>触及止损或第一目标位时高亮提醒。</span></div></div>
              <div class="rule-item">${icon('chart')}<div><strong>量价异动</strong><span>量比 ≥ 2.5 且上涨，或放量下跌时提示。</span></div></div>
              <div class="rule-item">${icon('shield')}<div><strong>不自动下单</strong><span>只做提醒与记录，交易由你自己完成。</span></div></div>
            </div>
            <p class="micro" style="margin-top:12px">${esc(data.disclaimer || '')}</p>
          </div>
        </aside>
      </div>
    </section>`;
}

/* ------------------------------------------------------------------ learning */
function renderLearning() {
  const data = state.learning;
  if (!data) return;
  const stats = data.calibration || {};
  const previous = data.previous || [];
  const recent = data.recentDays || [];
  const factors = data.factors || [];
  const paper = data.paper || {};
  const paperLatest = paper.latest || null;
  const paperPending = paper.latestPending || null;
  const paperPositions = (paperLatest?.positions || paperPending?.positions || []);
  const paperRows = paperPositions.map((position) => `
    <tr>
      <td><div class="watch-name"><strong>${esc(position.name)}</strong><small>${esc(position.code)} · ${fmtNum(position.weightPct, 1)}%</small></div></td>
      <td><span class="learning-predicted">${fmtNum(position.allocation, 0)}</span></td>
      <td>${paperLatest ? `<span class="learning-date">${fmtNum(position.entryClose)} → ${fmtNum(position.exitClose)}</span>` : '<span class="micro">信号日收盘</span>'}</td>
      <td>${paperLatest ? `<span class="${pctClass(position.returnPct)}">${fmtPct(position.returnPct)}</span>` : '<span class="result-pill muted">待结算</span>'}</td>
      <td>${paperLatest ? `<span class="${Number(position.pnl) >= 0 ? 'up' : 'down'}">${fmtSigned(position.pnl)}</span>` : '—'}</td>
    </tr>`).join('');
  const paperCard = `
    <article class="card paper-card">
      <div class="card-head">
        <div><div class="eyebrow">PAPER PORTFOLIO · CNY 10,000</div><h2>1 万元模拟盘</h2></div>
        <span class="badge ${paperLatest ? 'gold' : ''}">${paperLatest ? '已结算' : paperPending ? '待次日结算' : paper.expiredTrades ? paper.expiredTrades + ' 条已失效' : '等待候选'}</span>
      </div>
      <div class="paper-stats">
        <div><span>模拟权益</span><b>${fmtNum(paper.equity || 10000, 2)}</b><small>初始 10,000</small></div>
        <div><span>累计盈亏</span><b class="${Number(paper.totalPnl || 0) >= 0 ? 'up' : 'down'}">${fmtSigned(paper.totalPnl || 0)}</b><small>${fmtPct(paper.totalReturnPct || 0)}</small></div>
        <div><span>已结算天数</span><b>${paper.resolvedTrades || 0}</b><small>每日固定 1 万</small></div>
        <div><span>模拟胜率</span><b>${paper.winRate !== null && paper.winRate !== undefined ? fmtNum(Number(paper.winRate) * 100, 1) + '%' : '—'}</b><small>按日盈亏</small></div>
      </div>
      ${paperPositions.length ? `
        <div class="paper-table-wrap">
          <div class="mini-title"><span class="dot"></span>${paperLatest ? `最近结算 ${esc(paperLatest.signalDate)} → ${esc(paperLatest.exitDate)}` : `已记录 ${esc(paperPending.signalDate)}，信号日收盘买入、下一交易日收盘结算（T+1）`}</div>
          <table class="learning-table">
            <thead><tr><th>标的</th><th>分配金额</th><th>买入 → 卖出</th><th>收益</th><th>盈亏</th></tr></thead>
            <tbody>${paperRows}</tbody>
          </table>
        </div>` : `<div class="learning-empty">${icon('chart')}<h3>${paper.expiredTrades ? '历史记录已过期' : '还没有模拟交易'}</h3><p>${paper.expiredTrades ? paper.expiredTrades + ' 条记录的信号日已超出可回溯的日K窗口，无法确认紧邻的次日 bar，已标记失效而不结算。' : '在“今日候选”生成候选后，系统会自动用 1 万元等权记录；信号日收盘买入、下一交易日收盘结算。'}</p><button class="ghost-btn" data-route="picks">去生成候选</button></div>`}
      ${paperPending && paperLatest ? `<p class="micro" style="padding:0 18px 4px">另有 ${esc(paperPending.signalDate)} 的候选已记录，等待下一交易日收盘结算。</p>` : ''}
      <p class="micro" style="padding:0 18px 4px"><b style="color:var(--text-2);font-weight:500">系统决策：</b>均衡档候选，等权分配，最多 5 只；信号日收盘买入，下一交易日收盘计值（T+1）。</p>
      <p class="micro" style="padding:0 18px 15px">${esc(paper.note || '每日固定 10,000 元等权模拟；信号日收盘买入、下一交易日收盘计值（T+1）；含 0.15% 估算成本。')}</p>
    </article>`;
  const hasSamples = Boolean(data.available && data.samples);
  const hitRate = data.hitRate !== null && data.hitRate !== undefined ? Number(data.hitRate) : null;
  const avgReturn = data.avgReturn !== null && data.avgReturn !== undefined ? Number(data.avgReturn) : null;
  const bias = Number(stats.b || 0);
  const adjustment = stats.adjustmentPpAt50 !== undefined ? Number(stats.adjustmentPpAt50) : 0;

  const previousRows = previous.map((item) => `
    <tr data-action="detail" data-code="${esc(item.code)}">
      <td><div class="watch-name"><strong>${esc(item.name)}</strong><small>${esc(item.code)} · ${esc(item.profile || '')}</small></div></td>
      <td><span class="learning-predicted">${fmtNum(item.predictedPct, 1)}%</span></td>
      <td><span class="${pctClass(item.actualPct)}">${fmtPct(item.actualPct)}</span></td>
      <td><span class="result-pill ${item.actualUp ? 'success' : 'danger'}">${item.actualUp ? '上涨' : '下跌'}</span></td>
      <td><span class="learning-date">${esc(item.snapshotDate)} → ${esc(item.evalDate)}</span></td>
    </tr>`).join('');

  const recentRows = recent.map((day) => {
    const rate = Number(day.hitRate || 0) * 100;
    return `
      <div class="learning-day">
        <div class="learning-day-head"><span>${esc(day.date)}</span><b class="${rate >= 50 ? 'up' : 'down'}">${fmtNum(rate, 0)}%</b><small>${day.count} 只</small></div>
        <div class="learning-bar"><i style="width:${clamp(rate, 2, 100)}%"></i></div>
        <div class="learning-day-foot"><span>次日均收益 ${fmtPct(Number(day.avgReturn || 0) * 100)}</span><span>${esc(day.evalDate || '')}</span></div>
      </div>`;
  }).join('');

  const factorRows = factors.map((factor) => `
    <div class="learning-factor">
      <span>${esc(factor.label)}</span>
      <div><b class="up">${fmtNum(factor.winAvg, 0)}</b><small>上涨组</small></div>
      <div><b class="down">${fmtNum(factor.lossAvg, 0)}</b><small>下跌组</small></div>
      <strong class="${Number(factor.diff) >= 0 ? 'up' : 'down'}">${Number(factor.diff) >= 0 ? '+' : ''}${fmtNum(factor.diff, 1)}</strong>
      <em>${factor.samples} 样本</em>
    </div>`).join('');

  $('#viewRoot').innerHTML = `
    <section class="view learning-view">
      <div class="learning-hero">
        <div>
          <div class="eyebrow">LEARNING LOOP · WALK FORWARD</div>
          <h2>用 <em>n-2 的候选</em>，看 <em>n-1 的结果</em>，校准 <em>n 的概率</em>。</h2>
          <p>每次生成候选后自动存档；下一交易日收盘后，用真实次日涨跌复盘，并把偏差温和地校准到新概率。不是用未来数据“假装预测”，而是让模型为自己的历史判断负责。</p>
          <div class="learning-flow">
            <span>n-2 候选</span><b>→</b><span>n-1 收盘结果</span><b>→</b><span>n 概率校准</span>
          </div>
        </div>
        <div class="learning-calibration">
          <span>概率校准</span>
          <strong>${bias >= 0 ? '+' : ''}${fmtNum(adjustment, 1)}<small>pp @50%</small></strong>
          <p>样本 ${data.samples || 0} · 收缩系数 ${fmtNum(data.samples ? data.samples / (data.samples + 30) : 0, 2)}</p>
          <p class="micro">${esc(data.method || '')}</p>
        </div>
      </div>

      <div class="learning-stats">
        <div><span>已复盘样本</span><b>${data.samples || 0}</b><small>候选 × 交易日</small></div>
        <div><span>实际命中率</span><b class="${hitRate !== null && hitRate >= 0.5 ? 'up' : 'down'}">${hitRate !== null ? fmtNum(hitRate * 100, 1) + '%' : '—'}</b><small>次日收盘上涨比例</small></div>
        <div><span>平均预测概率</span><b>${data.avgPredicted !== null && data.avgPredicted !== undefined ? fmtNum(Number(data.avgPredicted) * 100, 1) + '%' : '—'}</b><small>与命中率比较</small></div>
        <div><span>次日平均收益</span><b class="${avgReturn !== null && avgReturn >= 0 ? 'up' : 'down'}">${avgReturn !== null ? fmtPct(avgReturn * 100) : '—'}</b><small>未扣交易成本</small></div>
      </div>

      ${existsNotice(data)}
      ${paperCard}

      <div class="learning-grid">
        <article class="card">
          <div class="card-head">
            <div><div class="eyebrow">LAST REVIEW · ${esc(data.previousEvalDate || '—')}</div><h2>上一批候选，后来怎样了</h2></div>
            <span class="badge">${esc(data.previousDate || '—')} 记录</span>
          </div>
          ${previous.length ? `
            <table class="learning-table">
              <thead><tr><th>标的</th><th>预测</th><th>次日实际</th><th>结果</th><th>日期</th></tr></thead>
              <tbody>${previousRows}</tbody>
            </table>` : `<div class="learning-empty">${icon('loop')}<h3>还没有可复盘的候选</h3><p>先让工具在交易日记录一次候选；下一交易日收盘后会自动出现复盘结果。</p><button class="ghost-btn" data-route="picks">去生成今日候选</button></div>`}
        </article>
        <article class="card">
          <div class="card-head"><div><div class="eyebrow">RECENT HIT RATE</div><h2>最近学习曲线</h2></div><span class="badge">按候选日</span></div>
          <div class="learning-days">${recentRows || '<p class="micro" style="padding:16px">还没有足够样本。</p>'}</div>
        </article>
      </div>

      <div class="learning-grid">
        <article class="card">
          <div class="card-head"><div><div class="eyebrow">FACTOR EFFICACY</div><h2>近期因子表现</h2></div><span class="badge">描述性</span></div>
          <div class="learning-factors">
            ${factorRows || '<p class="micro" style="padding:16px">样本不足，暂不展示因子差异。</p>'}
          </div>
          <p class="micro" style="padding:0 18px 16px">上涨组与下跌组的平均因子分差异，只用于观察，不自动修改因子权重。</p>
        </article>
        <article class="card learning-note">
          <div class="card-head"><div><div class="eyebrow">HOW IT LEARNS</div><h2>它到底学到了什么</h2></div></div>
          <div class="learning-note-body">
            <div><b>01 只校准概率</b><p>用实际命中率与平均预测概率的偏差，做截距校准；不改写趋势、量能、MACD 等因子权重。</p></div>
            <div><b>02 强收缩</b><p>每天只有几只候选，样本很少。校准系数会被强烈拉回 0，避免一两笔盈利就把概率吹高。</p></div>
            <div><b>03 不碰未来</b><p>只有下一交易日已收盘的结果才进入复盘；盘中不完整数据不会用于校准。</p></div>
            <div><b>04 允许没学到</b><p>没有样本或结果不足时，校准保持 0，页面明确显示“还没有可复盘样本”。</p></div>
          </div>
          <div class="leader-risk" style="margin-top:0">${icon('alert')}<span>${esc(data.note || '')}</span></div>
        </article>
      </div>
    </section>`;
}

function existsNotice(data) {
  if (!data.available) {
    return `<div class="notice">${icon('loop')}<div>学习复盘需要至少一次“候选记录 + 下一交易日收盘”。当前还没有完整样本，校准暂不生效。</div></div>`;
  }
  return '';
}

/* ------------------------------------------------------------------ method */
function renderMethod() {
  const data = state.method;
  if (!data) return;
  const model = state.picks?.model || {};
  const metrics = model.metrics || {};
  const pipeline = (data.pipeline || []).map((step) => `
    <div class="pipe-step"><b>${esc(step.step)}</b><h3>${esc(step.title)}</h3><p>${esc(step.text)}</p></div>`).join('');
  const principles = (data.principles || []).slice(0, 4).map((text, index) => `
    <div class="principle"><strong>0${index + 1}</strong><span>${esc(text)}</span></div>`).join('');
  const factors = (data.factors || []).map((factor) => `
    <div class="factor-line">
      <span>${esc(factor.label)}<small>${esc(factor.desc)}</small></span>
      <span class="factor-bar"><i style="width:${clamp(Number(factor.weight) * 3.2, 8, 100)}%"></i></span>
      <b>${Number(factor.weight)}%</b>
    </div>`).join('');
  const limits = (data.limitations || []).map((text) => `<div class="limit-item">${icon('alert')}<span>${esc(text)}</span></div>`).join('');
  const sources = (data.sources || []).map((source) => `
    <a class="source-item" href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">
      <span><strong>${esc(source.name)}</strong><small>${esc(source.note || '')}</small></span>${icon('up-right')}
    </a>`).join('');

  $('#viewRoot').innerHTML = `
    <section class="view method-view">
      <div class="method-hero">
        <div>
          <div class="eyebrow">METHODOLOGY · NOT A BLACK BOX</div>
          <h2>不承诺预测，<br><em>只把概率、证据和风险</em>放在同一张桌上。</h2>
          <p>参考幻方量化的因子工业化与风险约束思路，但把规模缩小到个人可验证的范围：公开数据、可解释因子、时间切分验证、明确的失效条件。工具不接券商，也不自动下单。</p>
        </div>
        <div class="principle-grid">${principles}</div>
      </div>

      <article class="card" style="margin-bottom:12px">
        <div class="card-head"><div><div class="eyebrow">PIPELINE</div><h2>从行情到候选的五层流水线</h2></div><span class="badge">Research → Signal → Risk</span></div>
        <div class="pipeline">${pipeline}</div>
      </article>

      <div class="method-grid">
        <article class="card">
          <div class="card-head"><div><div class="eyebrow">FACTOR STACK</div><h2>八组可解释因子</h2></div><span class="badge">规则评分</span></div>
          <div class="factor-table">${factors}</div>
        </article>
        <article class="card">
          <div class="card-head"><div><div class="eyebrow">MODEL CARD</div><h2>模型体检</h2></div><span class="badge ${metrics.usable ? 'gold' : 'cold'}">${metrics.usable ? '可用信号' : '未通过门槛'}</span></div>
          <div class="model-metrics" style="grid-template-columns:1fr 1fr;padding-top:12px">
            <div class="metric"><span>日度截面 AUC</span><b>${metrics.daily_auc ?? '—'}</b><small>池化 ${metrics.auc ?? '—'} · ${metrics.ic_days ?? 0} 日</small></div>
            <div class="metric"><span>Rank IC</span><b>${metrics.rank_ic ?? '—'}</b><small>ICIR ${metrics.ic_ir ?? '—'}</small></div>
            <div class="metric"><span>Top 10% 命中率</span><b>${metrics.top_decile_hit ? (metrics.top_decile_hit * 100).toFixed(1) + '%' : '—'}</b><small>未来10日上涨</small></div>
            <div class="metric"><span>验证样本</span><b>${fmtCompact(metrics.validation_samples)}</b><small>净化 ${metrics.purge_days ?? 10} 日</small></div>
          </div>
          <div class="model-note" style="padding:12px 18px 18px">${esc(model.method || 'L2 逻辑回归 + 净化时间切分验证')}<br>${esc(model.limitations || '')}</div>
        </article>
      </div>

      <div class="method-grid">
        <article class="card">
          <div class="card-head"><div><div class="eyebrow">LIMITS</div><h2>明确不做什么</h2></div></div>
          <div class="limit-list">${limits}</div>
        </article>
        <article class="card">
          <div class="card-head"><div><div class="eyebrow">REFERENCES</div><h2>参考与数据来源</h2></div></div>
          <div class="source-list">${sources}</div>
        </article>
      </div>

      <div class="method-disclaimer">${esc(data.disclaimer || '')}<br><br>本工具不提供证券投资咨询，不代客理财，不承诺任何收益。所有候选、概率、价格区间均为公开数据上的研究输出，可能错误、延迟或失效。</div>
    </section>`;
}

/* ------------------------------------------------------------------ detail */
async function openDetail(code) {
  if (!code) return;
  state.lastDetailCode = code;
  const modal = $('#modal');
  modal.classList.remove('hidden');
  document.body.style.overflow = 'hidden';
  $('#modalContent').innerHTML = `
    <div style="padding:28px">
      <div class="skeleton sk-line w40"></div>
      <div class="skeleton sk-big"></div>
      <div class="skeleton" style="height:300px;margin-top:16px"></div>
    </div>`;
  try {
    const data = await api(`/api/stock/${encodeURIComponent(code)}?profile=${encodeURIComponent(state.profile)}`);
    renderDetail(data);
  } catch (error) {
    $('#modalContent').innerHTML = `<div style="padding:40px">${errorState('个股详情加载失败', error.message, 'detail')}</div>`;
  }
}

function closeModal() {
  $('#modal').classList.add('hidden');
  document.body.style.overflow = '';
  if (state.chart) {
    state.chart.dispose();
    state.chart = null;
  }
  $('#modalContent').innerHTML = '';
}

function renderDetail(data) {
  const quote = data.quote || {};
  const score = data.score || {};
  const plan = data.plan || {};
  const features = data.features || {};
  const modelP = data.probability;
  const modelUsable = Boolean(data.model_usable);
  const factors = (score.subscores || []).map((item) => `
    <div class="factor-mini-row">
      <span>${esc(item.label)}</span>
      <span class="track"><i style="width:${clamp(Number(item.score) || 0, 0, 100)}%"></i></span>
      <b>${fmtNum(item.score, 0)}</b>
    </div>`).join('');
  const reasons = (score.reasons || []).map((text) => `<li>${esc(text)}</li>`).join('');
  const risks = (score.risks || []).map((text) => `<li>${esc(text)}</li>`).join('');
  const flags = (data.flags || []).map((flag) => `<span class="chip bad">${esc(flag)}</span>`).join('');
  const displayScore = data.composite_score ?? score.rule_score;
  const modelTop = data.model_percentile ? Math.max(1, Math.round((1 - data.model_percentile) * 100)) : null;
  const execution = data.execution || {};
  const execSteps = (execution.steps || []).map((step) => `<li>${esc(step)}</li>`).join('');

  $('#modalContent').innerHTML = `
    <div class="detail-head">
      <div class="detail-title">
        <h2>${esc(quote.name)} <span>${esc(quote.code)}</span></h2>
        <p>${esc(quote.industry || '—')} · ${esc(data.profile?.label || '')}视角 · ${modelUsable ? `模型10日概率 ${modelP ? (modelP * 100).toFixed(1) + '%' : '—'}${data.base_rate ? `（基准 ${(data.base_rate * 100).toFixed(1)}%，相对 ${Number(data.relative_edge) >= 0 ? '+' : ''}${(Number(data.relative_edge) * 100).toFixed(1)}pp）` : ''}${modelTop ? ` · 分位 Top ${modelTop}%` : ''}` : `模型未通过门槛 · 规则分 ${fmtNum(score.rule_score, 1)} · 风险分 ${fmtNum(score.risk_score, 1)}`}</p>
      </div>
      <div class="detail-quote">
        <strong>${fmtNum(quote.price)}</strong>
        <span class="${pctClass(quote.pct)}">${fmtPct(quote.pct)} · ${fmtSigned(quote.change)}</span>
      </div>
    </div>
    <div class="detail-body">
      <div class="detail-chart-wrap"><div id="stockChart"></div></div>
      <aside class="detail-side">
        <div class="side-block">
          <div class="side-title"><span>综合评分</span><b>因子 ${fmtNum(score.rule_score, 1)} / 100</b></div>
          <div class="score-line">
            <div class="big-score" style="--score:${clamp(Number(displayScore) || 0, 0, 100)}"><strong>${fmtNum(displayScore, 0)}</strong></div>
            <div class="score-copy"><strong>${score.confidence === 'high' ? '信号一致性较高' : score.confidence === 'low' ? '信号一致性偏弱' : '信号中性'}</strong><span>因子 ${fmtNum(score.rule_score, 0)} · 趋势 ${fmtNum(score.trend_score, 0)} · 风险 ${fmtNum(score.risk_score, 0)}${modelTop ? ` · 模型 Top ${modelTop}%` : ''}</span></div>
          </div>
          <div class="factor-mini" style="margin-top:10px">${factors}</div>
          ${flags ? `<div class="chips">${flags}</div>` : ''}
        </div>
        <div class="side-block">
          <div class="side-title"><span>怎么执行</span><b>${esc(execution.label || '先观察')}</b></div>
          <div class="execution-box compact ${esc(execution.tone || 'observe')}">
            <div class="execution-head"><strong>${esc(execution.headline || '先加入自选，等价格进入区间再评估。')}</strong></div>
            <ol class="execution-steps">${execSteps}</ol>
            <div class="execution-foot"><span>${esc(execution.position || '')}</span><span class="execution-avoid">${esc(execution.avoid || '')}</span></div>
          </div>
        </div>
        <div class="side-block">
          <div class="side-title"><span>交易计划（研究参考）</span><b>${esc(plan.action || '')}</b></div>
          <div class="detail-plan">
            <div><span>关注区间</span><b>${fmtNum(plan.entry_low)}–${fmtNum(plan.entry_high)}</b></div>
            <div><span>止损</span><b class="down">${fmtNum(plan.stop)}</b></div>
            <div><span>目标1</span><b class="up">${fmtNum(plan.target1)}</b></div>
            <div><span>目标2</span><b class="up">${fmtNum(plan.target2)}</b></div>
            <div><span>盈亏比</span><b>${fmtNum(plan.risk_reward, 1)} : 1</b></div>
            <div><span>建议仓位</span><b>≤${fmtNum(plan.suggested_weight_pct, 1)}%</b></div>
          </div>
          <div class="detail-notes" style="margin-top:7px">
            <div>${esc(plan.action_note || '')}</div>
            <div>周期 ${esc(plan.horizon || '')}</div>
            <div>${esc(plan.t1_note || '')}</div>
          </div>
        </div>
        <div class="side-block">
          <div class="side-title"><span>关键证据</span></div>
          <div class="detail-notes"><ul>${reasons || '<li>暂无突出正向因子。</li>'}</ul></div>
        </div>
        <div class="side-block">
          <div class="side-title"><span>风险与失效条件</span></div>
          <div class="detail-notes"><ul>${risks || '<li>未触发硬性风险项。</li>'}<li>${esc(plan.invalid || '')}</li></ul></div>
        </div>
      </aside>
    </div>
    <div class="detail-foot">
      <span>数据：东方财富公开行情 · 生成于 ${esc((data.asOf || '').replace('T', ' '))}</span>
      <button class="ghost-btn" data-action="watch-detail" data-code="${esc(quote.code)}">${icon('plus')} 加入自选</button>
    </div>`;

  requestAnimationFrame(() => renderChart(data.chart || {}));
}

function renderChart(chart) {
  const el = $('#stockChart');
  if (!el) return;
  if (typeof echarts === 'undefined') {
    el.innerHTML = '<p class="micro">图表库未加载，可查看上方关键价位与因子。</p>';
    return;
  }
  if (state.chart) state.chart.dispose();
  const dates = chart.dates || [];
  const ohlc = chart.ohlc || [];
  const volumes = chart.volumes || [];
  const up = '#ff5d6d';
  const down = '#22c58b';
  const axis = 'rgba(145,170,205,0.13)';
  const label = '#7f91a9';
  state.chart = echarts.init(el, null, { renderer: 'canvas' });
  state.chart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', lineStyle: { color: 'rgba(244,201,93,0.4)' } },
      backgroundColor: 'rgba(13,21,32,0.96)',
      borderColor: 'rgba(145,170,205,0.3)',
      textStyle: { color: '#e9f0f9', fontSize: 11 },
      formatter(params) {
        const idx = params[0]?.dataIndex ?? 0;
        const row = ohlc[idx] || [];
        const volume = volumes[idx] || 0;
        const pct = row[0] ? ((row[1] / row[0] - 1) * 100).toFixed(2) : '—';
        return `<b>${dates[idx] || ''}</b><br>开 ${fmtNum(row[0])}　收 ${fmtNum(row[1])}<br>低 ${fmtNum(row[2])}　高 ${fmtNum(row[3])}<br>涨跌 ${pct}%　量 ${fmtCompact(volume)}`;
      },
    },
    legend: {
      data: ['K线', 'MA5', 'MA20', 'MA60'],
      top: 2,
      right: 8,
      itemWidth: 10,
      itemHeight: 2,
      textStyle: { color: label, fontSize: 9 },
    },
    grid: [
      { left: 45, right: 26, top: 34, height: '60%' },
      { left: 45, right: 26, top: '79%', height: '13%' },
    ],
    xAxis: [
      { type: 'category', data: dates, boundaryGap: true, axisLine: { lineStyle: { color: axis } }, axisLabel: { color: label, fontSize: 9, hideOverlap: true, formatter: (value) => String(value).slice(5) }, splitLine: { show: false } },
      { type: 'category', gridIndex: 1, data: dates, boundaryGap: true, axisLine: { lineStyle: { color: axis } }, axisLabel: { show: false }, axisTick: { show: false } },
    ],
    yAxis: [
      { scale: true, splitLine: { lineStyle: { color: axis } }, axisLabel: { color: label, fontSize: 9 }, axisLine: { show: false } },
      { gridIndex: 1, scale: true, splitLine: { show: false }, axisLabel: { show: false }, axisLine: { show: false } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 55, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1], bottom: 2, height: 15, borderColor: 'transparent', backgroundColor: 'rgba(255,255,255,0.03)', fillerColor: 'rgba(244,201,93,0.1)', handleStyle: { color: '#f4c95d' }, textStyle: { color: label, fontSize: 8 }, dataBackground: { lineStyle: { color: 'rgba(145,170,205,0.25)' }, areaStyle: { color: 'rgba(145,170,205,0.06)' } } },
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', data: ohlc,
        itemStyle: { color: up, color0: down, borderColor: up, borderColor0: down },
      },
      { name: 'MA5', type: 'line', data: chart.ma5 || [], smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#f4c95d' } },
      { name: 'MA20', type: 'line', data: chart.ma20 || [], smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#5cc8ff' } },
      { name: 'MA60', type: 'line', data: chart.ma60 || [], smooth: true, showSymbol: false, lineStyle: { width: 1, color: '#9a8cff' } },
      {
        name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volumes,
        itemStyle: {
          color(params) {
            const row = ohlc[params.dataIndex] || [];
            return row[1] >= row[0] ? 'rgba(255,93,109,0.5)' : 'rgba(34,197,139,0.5)';
          },
        },
      },
    ],
  });
}

function resizeChart() {
  if (state.chart) state.chart.resize();
}

/* ------------------------------------------------------------------ watch actions */
function findPick(code) {
  return (state.picks?.picks || []).find((item) => item.code === code);
}

async function addWatch(code) {
  const pick = findPick(code);
  const plan = pick?.plan || {};
  const payload = {
    code,
    profile: state.profile,
    entry_low: plan.entry_low ?? null,
    entry_high: plan.entry_high ?? null,
    stop: plan.stop ?? null,
    target1: plan.target1 ?? null,
  };
  try {
    await api('/api/watchlist', { method: 'POST', body: JSON.stringify(payload) });
    toast(`${pick?.name || code} 已加入自选`);
    await loadWatch(true);
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function addWatchByInput() {
  const input = $('#watchCode');
  const code = (input?.value || '').trim();
  if (!/^\d{6}$/.test(code)) {
    toast('请输入 6 位股票代码', 'error');
    return;
  }
  const btn = document.querySelector('[data-action="add-watch"]');
  if (btn) btn.disabled = true;
  try {
    await api('/api/watchlist', { method: 'POST', body: JSON.stringify({ code, profile: state.profile }) });
    toast('已加入自选');
    if (input) {
      input.value = '';
      input.blur();
    }
    await loadWatch(true);
  } catch (error) {
    toast(error.message, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function removeWatch(code) {
  try {
    await api(`/api/watchlist/${encodeURIComponent(code)}`, { method: 'DELETE' });
    toast('已移出自选');
    await loadWatch(true);
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function fillPlan(code) {
  try {
    const detail = await api(`/api/stock/${encodeURIComponent(code)}?profile=${encodeURIComponent(state.profile)}`);
    const plan = detail.plan || {};
    await api('/api/watchlist', {
      method: 'POST',
      body: JSON.stringify({
        code,
        profile: state.profile,
        entry_low: plan.entry_low,
        entry_high: plan.entry_high,
        stop: plan.stop,
        target1: plan.target1,
      }),
    });
    toast(`${detail.quote?.name || code} 的计划已补全`);
    await loadWatch(true);
  } catch (error) {
    toast(error.message, 'error');
  }
}

/* ------------------------------------------------------------------ search */
function hideSearch() {
  $('#searchResults')?.classList.add('hidden');
  const input = $('#globalSearch');
  if (input) input.value = '';
}

async function runSearch(text) {
  const results = $('#searchResults');
  if (!results) return;
  if (!text.trim()) {
    results.classList.add('hidden');
    return;
  }
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(text.trim())}`);
    const items = data.items || [];
    results.innerHTML = items.length
      ? items.map((item) => `
        <button class="search-item" data-action="detail" data-code="${esc(item.code)}">
          <span><strong>${esc(item.name)}</strong><small>${esc(item.code)} · ${esc(item.industry || '—')}</small></span>
          <span class="search-pct ${pctClass(item.pct)}">${fmtNum(item.price)} / ${fmtPct(item.pct)}</span>
        </button>`).join('')
      : '<div class="search-item"><span class="micro">没有匹配的股票</span></div>';
    results.classList.remove('hidden');
  } catch (error) {
    results.innerHTML = `<div class="search-item"><span class="micro">${esc(error.message)}</span></div>`;
    results.classList.remove('hidden');
  }
}

/* ------------------------------------------------------------------ skeleton */
function skeletonOverview() {
  return `<div class="overview-skeleton">
    ${Array.from({ length: 3 }, () => `<div class="skeleton-card"><div class="skeleton sk-line w40"></div><div class="skeleton sk-big"></div><div class="skeleton sk-line w60"></div></div>`).join('')}
    <div class="skeleton-card"><div class="skeleton sk-line w40"></div><div class="skeleton" style="height:160px;margin-top:12px"></div></div>
    <div class="skeleton-card"><div class="skeleton sk-line w40"></div><div class="skeleton sk-line" style="margin-top:14px"></div><div class="skeleton sk-line w80"></div><div class="skeleton sk-line w60"></div><div class="skeleton sk-line w80"></div></div>
    <div class="skeleton-card"><div class="skeleton sk-line w40"></div><div class="skeleton" style="height:120px;margin-top:12px"></div></div>
  </div>`;
}

function skeletonLeaders() {
  return `<div class="skeleton-card" style="height:220px;margin-bottom:12px"><div class="skeleton sk-line w40"></div><div class="skeleton sk-big" style="width:55%"></div><div class="skeleton sk-line w80"></div><div class="skeleton sk-line w60"></div></div>
  <div class="picks-skeleton">${Array.from({ length: 4 }, () => `<div class="skeleton-card" style="height:120px"><div class="skeleton sk-line w40"></div><div class="skeleton sk-big"></div></div>`).join('')}</div>`;
}

function skeletonPicks() {  return `<div class="picks-skeleton">
    ${Array.from({ length: 4 }, () => `<div class="skeleton-card"><div class="skeleton sk-line w40"></div><div class="skeleton sk-big"></div><div class="skeleton sk-line w80"></div><div class="skeleton sk-line w60"></div><div class="skeleton" style="height:100px;margin-top:16px"></div></div>`).join('')}
  </div>`;
}

function skeletonWatch() {
  return `<div class="watch-layout"><div class="skeleton-card" style="height:360px"><div class="skeleton sk-line w40"></div><div class="skeleton sk-line w80" style="margin-top:20px"></div><div class="skeleton sk-line w60"></div><div class="skeleton sk-line w80"></div><div class="skeleton sk-line w60"></div></div><div class="skeleton-card" style="height:260px"></div></div>`;
}

function skeletonLearning() {
  return `<div class="skeleton-card" style="height:220px;margin-bottom:12px"><div class="skeleton sk-line w40"></div><div class="skeleton sk-big" style="width:55%"></div><div class="skeleton sk-line w80"></div><div class="skeleton sk-line w60"></div></div>
  <div class="skeleton-card" style="height:300px"><div class="skeleton sk-line w40"></div><div class="skeleton sk-line w80" style="margin-top:20px"></div><div class="skeleton sk-line w60"></div><div class="skeleton sk-line w80"></div></div>`;
}

function skeletonMethod() {
  return `<div class="skeleton-card" style="height:200px;margin-bottom:12px"><div class="skeleton sk-line w40"></div><div class="skeleton sk-big" style="width:70%"></div><div class="skeleton sk-line w80"></div><div class="skeleton sk-line w60"></div></div><div class="skeleton-card" style="height:280px"><div class="skeleton sk-line w40"></div><div class="skeleton sk-line" style="margin-top:18px"></div><div class="skeleton sk-line w80"></div><div class="skeleton sk-line w60"></div></div>`;
}

/* ------------------------------------------------------------------ events */
function handleAction(element) {
  const action = element.dataset.action;
  const code = element.dataset.code;
  if (action === 'close-modal') return closeModal();
  if (action === 'toggle-guide') {
    const target = element.dataset.target || '';
    const guide = document.getElementById(target);
    if (!guide) return;
    const collapsed = !state.guides[target];
    state.guides[target] = collapsed;
    guide.classList.toggle('collapsed', collapsed);
    element.textContent = collapsed ? '展开说明' : '收起说明';
    return;
  }
  if (action === 'detail') return openDetail(code);
  if (action === 'watch') return addWatch(code);
  if (action === 'watch-detail') {
    closeModal();
    return addWatch(code);
  }
  if (action === 'remove-watch') return removeWatch(code);
  if (action === 'fill-plan') return fillPlan(code);
  if (action === 'add-watch') return addWatchByInput();
  if (action === 'retry') {
    const scope = element.dataset.scope;
    if (scope === 'overview') loadOverview(true);
    if (scope === 'leaders') loadLeaders(true);
    if (scope === 'picks') loadPicks(true);
    if (scope === 'learning') loadLearning(true);
    if (scope === 'watch') loadWatch();
    if (scope === 'method') loadMethod();
    if (scope === 'detail' && state.lastDetailCode) openDetail(state.lastDetailCode);
    return;
  }
  if (action === 'refresh') {
    if (state.route === 'leaders') loadLeaders(true);
    else if (state.route === 'picks') loadPicks(true);
    else if (state.route === 'watch') loadWatch();
    else if (state.route === 'learning') loadLearning(true);
    else if (state.route === 'method') loadMethod();
    else loadOverview(true);
    return;
  }
  if (action === 'open-picks') return navigate('picks');
}

function bindEvents() {
  document.addEventListener('click', (event) => {
    const routeEl = event.target.closest('[data-route]');
    if (routeEl) {
      event.preventDefault();
      navigate(routeEl.dataset.route);
      return;
    }
    const profileEl = event.target.closest('[data-profile]');
    if (profileEl) {
      const profile = profileEl.dataset.profile;
      if (PROFILE_LABELS[profile]) {
        state.profile = profile;
        localStorage.setItem('stocklens.profile', profile);
        state.picks = null;
        loadPicks(true);
        renderRoute();
      }
      return;
    }
    const actionEl = event.target.closest('[data-action]');
    if (actionEl) {
      event.preventDefault();
      handleAction(actionEl);
      return;
    }
    const searchItem = event.target.closest('.search-item[data-code], .preview-pick[data-code], .mover-row[data-code]');
    if (searchItem?.dataset.code) {
      hideSearch();
      openDetail(searchItem.dataset.code);
    }
  });

  $('#refreshBtn').addEventListener('click', () => {
    if (state.route === 'leaders') loadLeaders(true);
    else if (state.route === 'picks') loadPicks(true);
    else if (state.route === 'watch') loadWatch();
    else if (state.route === 'learning') loadLearning(true);
    else if (state.route === 'method') loadMethod();
    else loadOverview(true);
  });

  $('#liveToggle')?.addEventListener('click', () => {
    state.liveMode = !state.liveMode;
    localStorage.setItem('stocklens.liveMode', state.liveMode ? 'on' : 'off');
    if (!state.liveMode) {
      updateLiveBadge();
      toast('低延迟模式已暂停');
    } else {
      updateLiveBadge();
      loadLive(true);
      toast('低延迟模式已开启：1 秒刷新');
    }
  });

  const input = $('#globalSearch');
  input.addEventListener('input', () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => runSearch(input.value), 220);
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') hideSearch();
    if (event.key === 'Enter') {
      const first = $('#searchResults .search-item[data-code]');
      if (first) {
        const code = first.dataset.code;
        hideSearch();
        openDetail(code);
      }
    }
  });

  document.addEventListener('click', (event) => {
    if (!event.target.closest('#searchBox')) $('#searchResults')?.classList.add('hidden');
  });

  document.addEventListener('focusin', (event) => {
    if (event.target?.id === 'watchCode') state.watchEditing = true;
  });
  document.addEventListener('focusout', (event) => {
    if (event.target?.id === 'watchCode') state.watchEditing = false;
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      if (!$('#modal').classList.contains('hidden')) closeModal();
      hideSearch();
    }
    if (event.key === 'Enter' && event.target?.id === 'watchCode') addWatchByInput();
  });

  window.addEventListener('hashchange', () => {
    state.route = parseRoute();
    renderRoute();
  });

  window.addEventListener('resize', resizeChart, { passive: true });
}

/* ------------------------------------------------------------------ polling */
function startPolling() {
  setInterval(() => {
    if (document.hidden) return;
    state.pollTick += 1;
    if (state.route === 'overview' && state.pollTick % 4 === 0) loadOverview(false, true);
    if (state.route === 'leaders' && state.pollTick % 4 === 0) loadLeaders(true);
    if (state.route === 'overview' && state.pollTick % 12 === 0) loadPicks(false, true);
    if (state.route === 'watch' && (!state.liveMode || state.liveError) && !state.watchEditing && (state.watch?.items?.length || 0) > 0) loadWatch(true);
    if (state.route === 'picks' && state.pollTick % 18 === 0) loadPicks(false, true);
    if (state.route === 'learning' && state.pollTick % 12 === 0) loadLearning(true);
  }, 5000);
  setInterval(loadLive, 1000);
}

function startClock() {
  const tick = () => {
    const el = $('#clock');
    if (el) {
      el.textContent = new Date().toLocaleTimeString('zh-CN', {
        timeZone: 'Asia/Shanghai',
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
    }
  };
  tick();
  setInterval(tick, 1000);
}

/* ------------------------------------------------------------------ init */
async function init() {
  state.route = parseRoute();
  updateRouteChrome();
  bindEvents();
  startClock();
  startPolling();
  updateLiveBadge();
  await Promise.allSettled([loadOverview(), loadPicks(), loadWatch()]);
  if (state.liveMode) loadLive(true);
  if (state.route === 'leaders') loadLeaders();
  if (state.route === 'learning') loadLearning();
  if (state.route === 'method') loadMethod();
  if (!state.overview && !state.errors.overview) renderRoute();
}

window.addEventListener('error', (event) => {
  const message = event.error?.stack || event.message || '未知脚本错误';
  console.error(message);
  const el = document.querySelector('#globalNotice');
  if (el) {
    el.className = 'notice danger';
    el.innerHTML = `${icon('alert')}<div>页面渲染出错：${esc(String(message).split('\n')[0])}</div>`;
  }
});
window.addEventListener('unhandledrejection', (event) => {
  const message = event.reason?.stack || event.reason?.message || String(event.reason || '未知异步错误');
  console.error(message);
  const el = document.querySelector('#globalNotice');
  if (el) {
    el.className = 'notice danger';
    el.innerHTML = `${icon('alert')}<div>页面请求出错：${esc(String(message).split('\n')[0])}</div>`;
  }
});

document.addEventListener('DOMContentLoaded', init);

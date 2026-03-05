const API = {
  async scan(timeframe = '5m', providers = ['binance'], mode = 'union') {
    const url = `/api/scan?timeframe=${timeframe}&providers=${providers.join(',')}&mode=${mode}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Scan failed: ${res.status}`);
    return res.json();
  },
  async chart(symbol, tf = '5m') {
    const url = `/api/chart?symbol=${encodeURIComponent(symbol)}&tf=${tf}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Chart failed: ${res.status}`);
    return res.json();
  },
  async providers() {
    const res = await fetch('/api/providers');
    return res.json();
  }
};

// Global state
let currentData = [];
const cardViewState = new Map();
const cardTimeframeState = new Map();

const TIMEFRAMES = [
  { value: '1d', label: '1D' },
  { value: '4h', label: '4H' },
  { value: '1h', label: '1H' },
  { value: '30m', label: '30M' },
  { value: '15m', label: '15M' },
  { value: '5m', label: '5M' },
  { value: '1m', label: '1M' }
];

const UI = {
  statusEl: document.getElementById('status'),
  resultsEl: document.getElementById('results'),
  refreshBtn: document.getElementById('refreshBtn'),
  timeframe: document.getElementById('timeframe'),
  mode: document.getElementById('mode'),
  view: document.getElementById('view'),
  filterLong: document.getElementById('filterLong'),
  filterShort: document.getElementById('filterShort'),
  minConf: document.getElementById('minConf'),
  confVal: document.getElementById('confVal'),
  topN: document.getElementById('topN'),
  modal: document.getElementById('detailModal'),
  modalClose: document.querySelector('.close'),

  setStatus(msg, type = 'info') {
    this.statusEl.textContent = msg;
    this.statusEl.className = 'status ' + (type === 'loading' ? 'loading' : type === 'error' ? 'error' : type === 'success' ? 'success' : '');
  },

  formatNum(n, digits = 2) {
    if (n === undefined || n === null) return '-';
    return Number(n).toFixed(digits);
  },

  getScoreClass(val) {
    if (val >= 70) return 'high';
    if (val >= 50) return 'med';
    return 'low';
  },

  renderMetricBar(label, value, maxVal = 100, unit = '') {
    const pct = Math.min(100, Math.max(0, (value / maxVal) * 100));
    const colorClass = pct >= 70 ? 'high' : pct >= 40 ? 'med' : 'low';
    return `
      <div class="metric-bar">
        <div class="metric-bar-label">${label}</div>
        <div class="metric-bar-track">
          <div class="metric-bar-fill ${colorClass}" style="width: ${pct}%"></div>
        </div>
        <div class="metric-bar-value">${value.toFixed(1)}${unit}</div>
      </div>
    `;
  },

  renderBuySellBars(item) {
    const buyRatio = item.buySellRatio || item.takerRatio || 1;
    const buyStrength = Math.min(100, buyRatio * 50);
    const sellStrength = Math.min(100, (1 / buyRatio) * 50);
    return `
      <div class="metric-bar-group">
        <div class="metric-bar mini">
          <div class="metric-bar-label buy-label">BUY VOL</div>
          <div class="metric-bar-track">
            <div class="metric-bar-fill buy" style="width: ${buyStrength}%"></div>
          </div>
          <div class="metric-bar-value">${buyRatio.toFixed(2)}x</div>
        </div>
        <div class="metric-bar mini">
          <div class="metric-bar-label sell-label">SELL VOL</div>
          <div class="metric-bar-track">
            <div class="metric-bar-fill sell" style="width: ${sellStrength}%"></div>
          </div>
          <div class="metric-bar-value">${(1/buyRatio).toFixed(2)}x</div>
        </div>
      </div>
    `;
  },

  renderDiscoveryBars(item) {
    const metrics = [
      { label: 'VOLUME Δ', val: Math.min(100, item.volSpike * 20), raw: item.volSpike, unit: 'x' },
      { label: 'OI DELTA', val: Math.min(100, Math.abs(item.oiDeltaPct || 0) * 5), raw: item.oiDeltaPct, unit: '%' },
      { label: 'FUNDING', val: Math.min(100, Math.abs(item.funding || 0) * 5000), raw: (item.funding || 0) * 100, unit: '%' },
      { label: 'TAKER FLOW', val: Math.min(100, (item.takerRatio || 1) * 50), raw: item.takerRatio, unit: '' },
      { label: 'WHALE SCORE', val: Math.min(100, (item.whaleScore || 0) * 2), raw: item.whaleScore, unit: '' },
      { label: 'S/R PROX', val: item.srProximity || 0, raw: item.srProximity, unit: '%' }
    ];
    return metrics.map(m => this.renderMetricBar(m.label, m.val, 100, m.unit)).join('');
  },

  renderEntryBars(item) {
    let html = '';
    html += this.renderMetricBar('RSI', Math.min(100, Math.max(0, item.rsi14 || 50)), 100, '');
    html += this.renderMetricBar('VOL MoM', Math.min(100, (item.volSpike || 1) * 25), 100, 'x');
    html += this.renderMetricBar('OI/PRICE CORR', Math.min(100, Math.max(0, (item.oiPriceCorr || 0) * 50 + 50)), 100, '');
    html += this.renderBuySellBars(item);
    return html;
  },

  renderCard(item) {
    const symbol = item.symbol;
    const cardView = cardViewState.get(symbol) || 'discovery';
    const cardTf = cardTimeframeState.get(symbol) || this.timeframe.value || '5m';
    const hasEntry = item.entryLow && item.entryHigh;

    const tfOptions = TIMEFRAMES.map(tf =>
      `<option value="${tf.value}" ${tf.value === cardTf ? 'selected' : ''}>${tf.label}</option>`
    ).join('');

    return `
      <div class="card ${item.side}" data-symbol="${symbol}">
        <div class="card-header">
          <span class="symbol">${symbol}</span>
          <div class="card-controls">
            <select class="card-tf-select" data-symbol="${symbol}">${tfOptions}</select>
            <span class="side-badge ${item.side}">${item.side}</span>
          </div>
        </div>

        <div class="card-scores">
          <div class="score-box">
            <div class="label">Confidence</div>
            <div class="value ${this.getScoreClass(item.confidence)}">${this.formatNum(item.confidence, 1)}</div>
          </div>
          ${item.discoveryScore ? `
          <div class="score-box">
            <div class="label">Discovery</div>
            <div class="value ${this.getScoreClass(item.discoveryScore)}">${this.formatNum(item.discoveryScore, 1)}</div>
          </div>
          ` : ''}
          ${item.rr ? `
          <div class="score-box">
            <div class="label">R:R</div>
            <div class="value ${item.rr >= 2 ? 'high' : item.rr >= 1.5 ? 'med' : 'low'}">${this.formatNum(item.rr, 1)}</div>
          </div>
          ` : ''}
        </div>

        <div class="card-metrics compact">
          <div class="metric"><span>Price</span><span>$${this.formatNum(item.price, item.price < 1 ? 6 : 2)}</span></div>
          <div class="metric"><span>TF</span><span>${cardTf.toUpperCase()}</span></div>
        </div>

        <div class="view-toggle">
          <button class="view-btn ${cardView === 'discovery' ? 'active' : ''}" data-view="discovery" data-symbol="${symbol}">🔍 DISCOVERY</button>
          <button class="view-btn ${cardView === 'entry' ? 'active' : ''}" data-view="entry" data-symbol="${symbol}">🎯 ENTRY</button>
        </div>

        <div class="bars-container" data-bars="${cardView}">
          ${cardView === 'discovery' ? this.renderDiscoveryBars(item) : this.renderEntryBars(item)}
        </div>

        ${hasEntry ? `
        <div class="entry-summary">
          <div class="entry-row"><span class="label">Entry</span><span class="value">${this.formatNum(item.entryLow, 4)} - ${this.formatNum(item.entryHigh, 4)}</span></div>
          <div class="entry-row"><span class="label">SL / TP1</span><span class="value" style="color:#f85149">${this.formatNum(item.stop, 4)}</span> / <span style="color:#3fb950">${this.formatNum(item.tp1, 4)}</span></div>
        </div>
        ` : ''}

        ${item.why || item.discoveryWhy ? `
        <div class="card-why">
          ${item.discoveryWhy ? `<strong>Discovery:</strong> ${item.discoveryWhy}<br>` : ''}
          ${item.why ? `<strong>Setup:</strong> ${item.why}` : ''}
        </div>
        ` : ''}
      </div>
    `;
  },

  updateCardView(symbol, view) {
    cardViewState.set(symbol, view);
    const card = this.resultsEl.querySelector(`.card[data-symbol="${symbol}"]`);
    if (!card) return;

    // Find the item in current data
    const item = currentData.find(i => i.symbol === symbol);
    if (!item) return;

    // Update active states on buttons
    card.querySelectorAll('.view-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.view === view);
    });

    // Update bars content
    const barsContainer = card.querySelector('.bars-container');
    if (barsContainer) {
      barsContainer.innerHTML = view === 'discovery'
        ? this.renderDiscoveryBars(item)
        : this.renderEntryBars(item);
      barsContainer.dataset.bars = view;
    }
  },

  updateCardTimeframe(symbol, tf) {
    cardTimeframeState.set(symbol, tf);
    // Show status - actual data refresh requires API call
    this.setStatus(`${symbol}: Timeframe set to ${tf} — click Refresh to load data`, 'info');

    // Update the displayed TF text
    const card = this.resultsEl.querySelector(`.card[data-symbol="${symbol}"]`);
    if (card) {
      const tfDisplay = card.querySelector('.card-metrics.compact .metric:last-child span:last-child');
      if (tfDisplay) tfDisplay.textContent = tf.toUpperCase();
    }
  },

  renderEmpty(msg = 'No signals found') {
    return `<div class="empty-state"><h3>${msg}</h3><p>Try adjusting filters or refreshing</p></div>`;
  },

  filterData(data) {
    let filtered = data.data || [];
    const allowLong = this.filterLong.checked;
    const allowShort = this.filterShort.checked;
    filtered = filtered.filter(i => (i.side === 'LONG' && allowLong) || (i.side === 'SHORT' && allowShort));
    const minConf = parseInt(this.minConf.value);
    filtered = filtered.filter(i => i.confidence >= minConf);
    const topN = parseInt(this.topN.value);
    filtered = filtered.slice(0, topN);
    return filtered;
  },

  attachCardHandlers() {
    // View toggle handlers - use event delegation
    this.resultsEl.addEventListener('click', (e) => {
      const btn = e.target.closest('.view-btn');
      if (!btn) return;
      e.stopPropagation();
      const symbol = btn.dataset.symbol;
      const view = btn.dataset.view;
      this.updateCardView(symbol, view);
    });

    // Timeframe change handlers - use event delegation
    this.resultsEl.addEventListener('change', (e) => {
      const select = e.target.closest('.card-tf-select');
      if (!select) return;
      e.stopPropagation();
      const symbol = select.dataset.symbol;
      const tf = select.value;
      this.updateCardTimeframe(symbol, tf);
    });

    // Detail click handlers
    this.resultsEl.addEventListener('click', (e) => {
      const card = e.target.closest('.card');
      if (!card) return;
      if (e.target.closest('.view-toggle') || e.target.closest('.card-controls')) return;
      this.showDetail(card.dataset.symbol);
    });
  },

  render(data) {
    currentData = data.data || [];
    const filtered = this.filterData(data);
    if (!filtered.length) {
      this.resultsEl.innerHTML = this.renderEmpty();
      return;
    }
    this.resultsEl.innerHTML = filtered.map(item => this.renderCard(item)).join('');
  },

  async showDetail(symbol) {
    document.getElementById('detailTitle').textContent = symbol;
    this.modal.classList.remove('hidden');
    document.getElementById('detailData').innerHTML = '<p>Chart loading...</p>';
  },

  hideDetail() {
    this.modal.classList.add('hidden');
  }
};

// Event handlers
UI.refreshBtn.addEventListener('click', async () => {
  UI.setStatus('Scanning...', 'loading');
  UI.refreshBtn.disabled = true;

  try {
    const tf = UI.timeframe.value;
    const mode = UI.mode.value;
    const result = await API.scan(tf, ['binance'], mode);

    if (!result.ok) throw new Error(result.error);

    UI.render(result);
    const count = UI.filterData(result).length;
    UI.setStatus(`Found ${count} signals (${result.data?.length || 0} total) | TF: ${tf} | Mode: ${mode}`, 'success');
  } catch (err) {
    UI.setStatus(`Error: ${err.message}`, 'error');
    UI.resultsEl.innerHTML = UI.renderEmpty('Scan failed');
  } finally {
    UI.refreshBtn.disabled = false;
  }
});

UI.minConf.addEventListener('input', (e) => {
  UI.confVal.textContent = e.target.value;
});

[UI.filterLong, UI.filterShort, UI.minConf, UI.topN, UI.view].forEach(el => {
  el.addEventListener('change', () => UI.refreshBtn.click());
});

UI.modalClose.addEventListener('click', () => UI.hideDetail());
UI.modal.addEventListener('click', (e) => {
  if (e.target === UI.modal) UI.hideDetail();
});

// Attach card handlers once
UI.attachCardHandlers();

// Auto-refresh every 60s
setInterval(() => {
  if (!UI.refreshBtn.disabled) UI.refreshBtn.click();
}, 60000);

// Initial load
UI.refreshBtn.click();

// CoinAnk Adapter - Free Trial
// Remove this file or set COINANK_API_KEY empty to disable

const COINANK_BASE = process.env.COINANK_BASE_URL || 'https://api.coinank.com';

async function getJSON(path, apiKey = '') {
  if (!apiKey) throw new Error('Missing COINANK_API_KEY');
  const r = await fetch(`${COINANK_BASE}${path}`, {
    headers: {
      'accept': 'application/json',
      'X-API-KEY': apiKey,
    },
  });
  if (!r.ok) throw new Error(`CoinAnk HTTP ${r.status}`);
  return r.json();
}

function mapToBinanceUnified(symbolLike) {
  const raw = String(symbolLike || '').toUpperCase();
  const base = raw.replace(/[^A-Z0-9]/g, '').replace(/USDT$/, '');
  if (!base) return '';
  return `${base}/USDT:USDT`;
}

// Get top symbols by volume from CoinAnk
async function getCoinAnkTopSymbols(limit = 35, apiKey = '') {
  try {
    // CoinAnk endpoint for top tokens (adjust based on actual API)
    const data = await getJSON(`/api/v1/market/tickers?limit=${limit}`, apiKey);
    const items = data?.data || data?.tickers || data?.result || [];
    
    const out = [];
    for (const t of items) {
      const s = mapToBinanceUnified(t?.symbol || t?.pair || t?.name || '');
      if (s) out.push(s);
    }
    return [...new Set(out)].slice(0, limit);
  } catch (e) {
    console.error('CoinAnk top symbols error:', e.message);
    return [];
  }
}

// Get token analytics from CoinAnk
async function getCoinAnkAnalytics(symbol, apiKey = '') {
  try {
    const rawSymbol = symbol.replace('/USDT:USDT', '').replace('/USDT', '');
    const data = await getJSON(`/api/v1/analytics/${rawSymbol}`, apiKey);
    return { ok: true, data };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// Get funding rates from CoinAnk
async function getCoinAnkFunding(symbol, apiKey = '') {
  try {
    const rawSymbol = symbol.replace('/USDT:USDT', '').replace('/USDT', '');
    const data = await getJSON(`/api/v1/funding/${rawSymbol}`, apiKey);
    return { ok: true, data };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// Get whale activity from CoinAnk
async function getCoinAnkWhaleActivity(symbol, apiKey = '') {
  try {
    const rawSymbol = symbol.replace('/USDT:USDT', '').replace('/USDT', '');
    const data = await getJSON(`/api/v1/whale/activity?symbol=${rawSymbol}`, apiKey);
    return { ok: true, data };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

module.exports = {
  getCoinAnkTopSymbols,
  getCoinAnkAnalytics,
  getCoinAnkFunding,
  getCoinAnkWhaleActivity,
};

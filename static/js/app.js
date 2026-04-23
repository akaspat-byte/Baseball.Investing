'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const LS_KEY = 'bbAnalyzer_oddsApiKey';
let lastData = null;

// Date offset from today (0 = today, 1 = tomorrow, -1 = yesterday)
let dateOffset = 0;

function _offsetDate(offset) {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return d.toISOString().slice(0, 10); // YYYY-MM-DD
}

function _friendlyDate(iso) {
  const [y, m, day] = iso.split('-');
  const d = new Date(+y, +m - 1, +day);
  const today = new Date(); today.setHours(0,0,0,0);
  const target = new Date(+y, +m - 1, +day);
  const diff = Math.round((target - today) / 86400000);
  const label = diff === 0 ? ' (Today)' : diff === 1 ? ' (Tomorrow)' : diff === -1 ? ' (Yesterday)' : '';
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }) + label;
}

function shiftDate(delta) {
  dateOffset += delta;
  updateDateDisplay();
  analyzeGames();
}

function goToday() {
  dateOffset = 0;
  updateDateDisplay();
  analyzeGames();
}

function goTomorrow() {
  dateOffset = 1;
  updateDateDisplay();
  analyzeGames();
}

function updateDateDisplay() {
  const iso = _offsetDate(dateOffset);
  document.getElementById('date-display').textContent = _friendlyDate(iso);
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  const saved = localStorage.getItem(LS_KEY) || '';
  if (saved) document.getElementById('odds-api-key').value = saved;
  updateDateDisplay();
  analyzeGames();
});

// ---------------------------------------------------------------------------
// Main entry point
// ---------------------------------------------------------------------------
async function analyzeGames(forceFresh = false) {
  const keyInput = document.getElementById('odds-api-key');
  const key = keyInput.value.trim();
  if (key) localStorage.setItem(LS_KEY, key);

  setLoading(true);
  clearError();
  document.getElementById('results').style.display = 'none';

  const gameDate = _offsetDate(dateOffset);
  updateDateDisplay();

  const params = new URLSearchParams();
  if (key) params.set('key', key);
  params.set('date', gameDate);
  if (forceFresh) params.set('fresh', '1');

  try {
    const resp = await fetch(`/api/analyze?${params}`);
    const data = await resp.json();

    if (!data.success) throw new Error(data.error || 'Analysis failed');

    lastData = data;
    renderResults(data);
  } catch (err) {
    showError(err.message);
  } finally {
    setLoading(false);
  }
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
function renderResults(data) {
  renderHeaderMeta(data);
  renderSummaryBar(data);
  renderTopBets(data);
  renderGames(data);
  document.getElementById('results').style.display = 'block';
}

function renderHeaderMeta(data) {
  const el = document.getElementById('header-meta');
  const lines = [];
  if (data.cached) lines.push('Cached result');
  if (data.requests_remaining != null)
    lines.push(`DraftKings API: <strong>${data.requests_remaining}</strong> requests left`);
  el.innerHTML = lines.join('<br>');
}

function renderSummaryBar(data) {
  const bar = document.getElementById('summary-bar');
  const posEV = data.top_bets ? data.top_bets.length : 0;
  const items = [
    `<div class="summary-pill"><strong>${data.games_count}</strong> games today</div>`,
  ];
  if (data.odds_available) {
    const cls = posEV > 0 ? 'good' : '';
    items.push(`<div class="summary-pill ${cls}"><strong>${posEV}</strong> value bet${posEV !== 1 ? 's' : ''} found</div>`);
    items.push(`<div class="summary-pill"><strong>${data.odds_matched_count}</strong> games matched to odds</div>`);
  } else {
    items.push(`<div class="summary-pill warn">Add an Odds API key to see value bets</div>`);
  }
  if (data.odds_error) {
    items.push(`<div class="summary-pill" style="border-color:var(--yellow);color:var(--yellow)">⚠ ${escHtml(data.odds_error)}</div>`);
  }
  bar.innerHTML = items.join('');
}

function renderTopBets(data) {
  const container = document.getElementById('top-bets-container');

  if (!data.odds_available) {
    container.innerHTML = `
      <div class="no-odds-note">
        <p>Connect a live odds feed to see expected-value rankings.</p>
        <p><a href="https://the-odds-api.com/" target="_blank" rel="noopener">Get a free Odds API key (500 req/month) →</a></p>
      </div>`;
    return;
  }

  const bets = data.top_bets || [];
  if (bets.length === 0) {
    container.innerHTML = `<div class="no-odds-note"><p>No +EV bets found today — the market is sharp on today's slate.</p></div>`;
    return;
  }

  container.innerHTML = `<div class="top-bets-grid">${bets.map(renderTopBetCard).join('')}</div>`;
}

function renderTopBetCard(bet) {
  const oddsClass = oddsColorClass(bet.best_odds);
  const evSign = bet.ev_pct >= 0 ? '+' : '';
  const evCls = bet.ev_pct >= 0 ? 'pos' : 'neg';
  return `
  <div class="top-bet-card stars-${bet.stars}">
    <div class="top-bet-matchup">${escHtml(bet.matchup)}</div>
    <div class="top-bet-team">${escHtml(bet.team)}</div>
    <div class="top-bet-odds ${oddsClass}">${formatOdds(bet.best_odds)}</div>
    <div class="top-bet-book">${escHtml(bet.best_book || '')}</div>
    <div class="top-bet-metrics">
      <div class="top-bet-metric">
        <label>EV%</label>
        <div class="val ${evCls}">${evSign}${bet.ev_pct}%</div>
      </div>
      <div class="top-bet-metric">
        <label>Edge</label>
        <div class="val ${evCls}">${bet.edge != null ? (bet.edge >= 0 ? '+' : '') + pct(bet.edge) : '—'}</div>
      </div>
      <div class="top-bet-metric">
        <label>Model</label>
        <div class="val">${pct(bet.model_prob)}</div>
      </div>
      <div class="top-bet-metric">
        <label>Kelly</label>
        <div class="val">${bet.kelly_pct > 0 ? bet.kelly_pct + '%' : '—'}</div>
      </div>
    </div>
    <div style="margin-top:6px">${renderStars(bet.stars)}</div>
    <div class="top-bet-time">${formatGameTime(bet.game_time_utc)}</div>
  </div>`;
}

function renderGames(data) {
  const container = document.getElementById('games-container');
  const games = data.games || [];
  if (games.length === 0) {
    container.innerHTML = `<div class="no-odds-note"><p>No MLB games scheduled today.</p></div>`;
    return;
  }
  container.innerHTML = games.map(g => renderGameCard(g, data.odds_available)).join('');
}

function renderGameCard(game, oddsAvailable) {
  const home = game.home;
  const away = game.away;
  const model = game.model;
  const market = game.market;
  const homePct = model.home_win_prob;
  const awayPct = model.away_win_prob;

  return `
  <div class="game-card">
    <!-- Header -->
    <div class="game-header">
      <div class="game-teams">
        ${renderTeamBadge(away, 'away')}
        <span class="vs-sep">@</span>
        ${renderTeamBadge(home, 'home')}
      </div>
      <div class="game-meta">
        <span class="game-status-badge ${game.status === 'Live' ? 'live' : 'preview'}">
          ${game.status === 'Live' ? '🔴 Live' : formatGameTime(game.game_time_utc)}
        </span>
        ${game.venue ? `<br><span style="font-size:11px;color:var(--text-faint)">${escHtml(game.venue)}</span>` : ''}
      </div>
    </div>

    <div class="game-body">
      <!-- Pitcher matchup -->
      <div class="pitcher-row">
        ${renderPitcherBlock(away.pitcher, 'left')}
        <div class="vs-pitcher">SP MATCHUP</div>
        ${renderPitcherBlock(home.pitcher, 'right')}
      </div>

      <!-- Model probability -->
      <div class="prob-row">
        <div class="prob-block">
          <div class="prob-label">Away · ${escHtml(away.abbreviation)}</div>
          <div class="prob-value">${pct(awayPct)}</div>
          <div class="prob-fair-odds">Fair: ${formatOdds(model.away_fair_odds)}</div>
        </div>
        <div class="prob-sep">
          MODEL<br>PROB
          <div class="prob-bar-row" style="width:80px;margin-top:6px">
            <div class="prob-bar-away" style="width:${Math.round(awayPct*100)}%"></div>
            <div class="prob-bar-home" style="width:${Math.round(homePct*100)}%"></div>
          </div>
        </div>
        <div class="prob-block right">
          <div class="prob-label">Home · ${escHtml(home.abbreviation)}</div>
          <div class="prob-value">${pct(homePct)}</div>
          <div class="prob-fair-odds">Fair: ${formatOdds(model.home_fair_odds)}</div>
        </div>
      </div>

      ${market ? renderMarketRow(market, away, home) : ''}
      ${renderEvSection(game, oddsAvailable)}

      <!-- Team stats strip -->
      ${renderTeamStats(away, home)}
    </div>
  </div>`;
}

function renderTeamBadge(team, side) {
  const streak = team.streak ? `<span style="font-size:10px;color:var(--text-faint)">${escHtml(team.streak)}</span>` : '';
  return `
  <div class="team-badge">
    <div class="team-abbrev">${escHtml(team.abbreviation || team.name.slice(0, 3).toUpperCase())}</div>
    <div class="team-name-sm">${escHtml(team.name)}</div>
    <div class="team-record">${team.wins}-${team.losses} ${streak}</div>
  </div>`;
}

function renderPitcherBlock(pitcher, align) {
  const cls = align === 'right' ? 'right' : '';
  const era  = pitcher.era  != null ? pitcher.era.toFixed(2)  : '—';
  const whip = pitcher.whip != null ? pitcher.whip.toFixed(2) : '—';
  const k9   = pitcher.k9   != null ? pitcher.k9.toFixed(1)   : '—';
  const bb9  = pitcher.bb9  != null ? pitcher.bb9.toFixed(1)  : '—';
  const rec  = pitcher.id ? `${pitcher.wins}-${pitcher.losses}` : '';

  return `
  <div class="pitcher-block ${cls}">
    <div class="pitcher-label">Probable Starter</div>
    <div class="pitcher-name">${escHtml(pitcher.name)} ${rec ? `<small style="color:var(--text-muted)">${rec}</small>` : ''}</div>
    <div class="pitcher-stats">
      ${pStat('ERA', era)}
      ${pStat('WHIP', whip)}
      ${pStat('K/9', k9)}
      ${pStat('BB/9', bb9)}
    </div>
  </div>`;
}

function pStat(label, val) {
  return `<span class="pitcher-stat"><span class="ps-label">${label}</span><span class="ps-val">${val}</span></span>`;
}

function renderMarketRow(market, away, home) {
  const vig = market.vig_pct != null ? `Vig: ${market.vig_pct}%` : '';
  return `
  <div class="market-row">
    <div class="market-block">
      <div class="market-label">Best Market Odds · Away</div>
      <div class="market-best-odds ${oddsColorClass(market.away_best_odds)}">${formatOdds(market.away_best_odds)}</div>
      <div class="market-book">${escHtml(market.away_best_book || '')}</div>
      ${market.away_implied_prob != null ? `<div class="market-implied">Implied: ${pct(market.away_implied_prob)}</div>` : ''}
    </div>
    <div class="market-sep">LIVE<br>ODDS<br><div class="market-vig">${vig}</div></div>
    <div class="market-block right">
      <div class="market-label">Best Market Odds · Home</div>
      <div class="market-best-odds ${oddsColorClass(market.home_best_odds)}">${formatOdds(market.home_best_odds)}</div>
      <div class="market-book">${escHtml(market.home_best_book || '')}</div>
      ${market.home_implied_prob != null ? `<div class="market-implied">Implied: ${pct(market.home_implied_prob)}</div>` : ''}
    </div>
  </div>`;
}

function renderEvSection(game, oddsAvailable) {
  if (game.is_live) return `<p class="no-odds-game">⚡ Game in progress — pre-game model shown for reference only. Live odds excluded to avoid false EV signals.</p>`;
  if (!oddsAvailable) return `<p class="no-odds-game">Add Odds API key to see expected value analysis.</p>`;
  if (!game.odds_matched) return `<p class="no-odds-game">No odds found for this game.</p>`;

  const bets = game.bets || [];
  if (bets.length === 0) return '';

  const rows = bets.map(bet => {
    const isPos = bet.ev_pct >= 1;
    const sign  = bet.ev_pct >= 0 ? '+' : '';
    const edgeTxt = bet.edge != null ? `Edge: ${bet.edge >= 0 ? '+' : ''}${pct(bet.edge)}` : '';
    return `
    <div class="ev-row ${isPos ? 'positive' : 'negative'}">
      <div>
        <div class="ev-team">${escHtml(bet.team)} <span style="font-size:11px;color:var(--text-muted)">(${bet.side})</span></div>
        <div class="ev-odds">${formatOdds(bet.best_odds)} <span class="ev-book">${escHtml(bet.best_book || '')}</span></div>
      </div>
      <div class="ev-metrics">
        <span class="ev-badge ${isPos ? 'positive' : 'negative'}">EV ${sign}${bet.ev_pct}%</span>
        ${edgeTxt ? `<span class="ev-edge">${edgeTxt}</span>` : ''}
        ${bet.kelly_pct > 0 ? `<span class="ev-kelly">Kelly: ${bet.kelly_pct}%</span>` : ''}
        <span class="ev-stars">${renderStars(bet.stars)}</span>
      </div>
    </div>`;
  }).join('');

  return `<div class="ev-rows">${rows}</div>`;
}

function renderTeamStats(away, home) {
  return `
  <div class="team-stats-strip">
    ${statPill(away.abbreviation + ' RS/G', away.rs_per_game)}
    ${statPill(away.abbreviation + ' RA/G', away.ra_per_game)}
    ${statPill(away.abbreviation + ' L10', away.last_ten)}
    <span style="flex:1"></span>
    ${statPill(home.abbreviation + ' L10', home.last_ten)}
    ${statPill(home.abbreviation + ' RA/G', home.ra_per_game)}
    ${statPill(home.abbreviation + ' RS/G', home.rs_per_game)}
  </div>`;
}

function statPill(label, val) {
  return `<div class="stat-pill"><span class="sp-label">${escHtml(label)}</span><span class="sp-val">${val ?? '—'}</span></div>`;
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------
function formatOdds(american) {
  if (american == null) return 'N/A';
  return american > 0 ? `+${american}` : `${american}`;
}

function pct(prob) {
  if (prob == null) return '—';
  return (prob * 100).toFixed(1) + '%';
}

function oddsColorClass(american) {
  if (american == null) return '';
  if (american > 0)  return 'dog';
  if (american < -110) return 'fav';
  return 'even';
}

function renderStars(count) {
  const n = Math.max(0, Math.min(5, count || 0));
  return `<span class="stars">${'★'.repeat(n)}${'☆'.repeat(5 - n)}</span>`;
}

function formatGameTime(utcStr) {
  if (!utcStr) return 'TBD';
  try {
    return new Date(utcStr).toLocaleTimeString('en-US', {
      timeZone: 'America/New_York',
      hour: 'numeric',
      minute: '2-digit',
      timeZoneName: 'short',
    });
  } catch (_) { return utcStr; }
}

function formatDate(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-');
  const dt = new Date(+y, +m - 1, +d);
  return dt.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
}

function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---------------------------------------------------------------------------
// UI state helpers
// ---------------------------------------------------------------------------
function setLoading(on) {
  document.getElementById('loading').style.display = on ? 'block' : 'none';
  document.getElementById('analyze-btn').disabled = on;
}

function clearError() {
  const el = document.getElementById('error-panel');
  el.style.display = 'none';
  el.textContent = '';
}

function showError(msg) {
  const el = document.getElementById('error-panel');
  el.textContent = '⚠ ' + msg;
  el.style.display = 'block';
}

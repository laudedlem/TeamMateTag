// Teammate Tag client. Three modes share this file:
//   home -> mode picker
//   mp   -> Lineup Battle (multiplayer)
//   bp   -> Batting Practice (solo timed chain)
//   fr   -> Film Review (daily puzzle)

const els = {
  homeScreen: document.getElementById('home-screen'),
  profileNameInput: document.getElementById('profile-name-input'),
  profileSaveBtn: document.getElementById('profile-save-btn'),
  profileOpenBtn: document.getElementById('profile-open-btn'),
  profileStatus: document.getElementById('profile-status'),
  profileScreen: document.getElementById('profile-screen'),
  profileScreenName: document.getElementById('profile-screen-name'),
  profileBpBest: document.getElementById('profile-bp-best'),
  profileBpPlays: document.getElementById('profile-bp-plays'),
  profileFrRecord: document.getElementById('profile-fr-record'),
  profileDrElo: document.getElementById('profile-dr-elo'),
  profileDrRecord: document.getElementById('profile-dr-record'),
  startScreen: document.getElementById('start-screen'),
  gameScreen: document.getElementById('game-screen'),
  frScreen: document.getElementById('fr-screen'),

  brandSubtitle: document.getElementById('brand-subtitle'),
  exitBtn: document.getElementById('exit-btn'),
  headerToggles: document.getElementById('header-toggles'),
  toggleLineup: document.getElementById('toggle-lineup'),
  toggleOut: document.getElementById('toggle-out'),

  p1Input: document.getElementById('p1-input'),
  p2Input: document.getElementById('p2-input'),
  startBtn: document.getElementById('start-btn'),

  turnCard: document.getElementById('turn-card'),
  turnLabel: document.getElementById('turn-label'),
  timer: document.getElementById('timer'),
  currentPlayerName: document.getElementById('current-player-name'),
  guessForm: document.getElementById('guess-form'),
  guessInput: document.getElementById('guess-input'),
  guessBtn: document.getElementById('guess-btn'),
  autocompleteList: document.getElementById('autocomplete-list'),
  feedback: document.getElementById('last-move-feedback'),
  cardStack: document.getElementById('card-stack'),

  sidePanel: document.getElementById('side-panel'),
  lineupSection: document.getElementById('lineup-section'),
  outSection: document.getElementById('out-section'),
  lineup: document.getElementById('lineup'),
  outList: document.getElementById('out-list'),
  outEmpty: document.getElementById('out-empty'),

  gameOverBanner: document.getElementById('game-over-banner'),
  winnerText: document.getElementById('winner-text'),
  gameOverSummary: document.getElementById('game-over-summary'),
  playAgainBtn: document.getElementById('play-again-btn'),

  frTurnCard: document.getElementById('fr-turn-card'),
  frStats: document.getElementById('fr-stats'),
  frPairNames: document.getElementById('fr-pair-names'),
  frTeamInput: document.getElementById('fr-team-input'),
  frTeamAutocompleteList: document.getElementById('fr-team-autocomplete-list'),
  frYearInput: document.getElementById('fr-year-input'),
  frGuessForm: document.getElementById('fr-guess-form'),
  frFeedback: document.getElementById('fr-feedback'),
  frCardStack: document.getElementById('fr-card-stack'),
  frSummaryBanner: document.getElementById('fr-summary-banner'),
  frSummaryText: document.getElementById('fr-summary-text'),
  frSummaryDetail: document.getElementById('fr-summary-detail'),
  frAnswerReveal: document.getElementById('fr-answer-reveal'),
  frHomeBtn: document.getElementById('fr-home'),

  rulesBtn: document.getElementById('rules-btn'),
  rulesModal: document.getElementById('rules-modal'),
  rulesBackdrop: document.getElementById('rules-backdrop'),
  rulesClose: document.getElementById('rules-close'),
  rulesText: document.getElementById('rules-text'),
};

let currentMode = 'home';
let game = null;
let frGame = null;
let profile = null;
let timerInterval = null;
let countdownInterval = null;
let turnLocalStart = 0;
let lastChainLength = 0;

let acItems = [];
let acHighlight = -1;
let acFetchSeq = 0;
let userTypedQuery = '';

let teamAcItems = [];
let teamAcHighlight = -1;
let teamAcFetchSeq = 0;
let userTypedTeamQuery = '';

const GUEST_ID_KEY = 'tt_guest_id';

async function api(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

function storedGuestId() {
  return window.localStorage.getItem(GUEST_ID_KEY) || '';
}

function saveGuestId(guestId) {
  if (!guestId) return;
  window.localStorage.setItem(GUEST_ID_KEY, guestId);
}

function renderProfile() {
  if (!profile) {
    els.profileStatus.textContent = 'Loading guest profile...';
    els.profileScreenName.textContent = '';
    els.profileBpBest.textContent = '--';
    els.profileBpPlays.textContent = '--';
    els.profileFrRecord.textContent = '--';
    els.profileDrElo.textContent = '--';
    els.profileDrRecord.textContent = '--';
    return;
  }
  els.profileNameInput.value = profile.display_name || '';
  els.profileStatus.textContent = 'Guest profile saved on this browser.';
  const wins = profile.stats?.fr_wins ?? 0;
  const plays = profile.stats?.fr_plays ?? 0;
  const drWins = profile.stats?.dr_wins ?? 0;
  const drLosses = profile.stats?.dr_losses ?? 0;
  els.profileScreenName.textContent = profile.display_name || '';
  els.profileBpBest.textContent = String(profile.stats?.bp_best ?? 0);
  els.profileBpPlays.textContent = String(profile.stats?.bp_plays ?? 0);
  els.profileFrRecord.textContent = `${wins}-${Math.max(0, plays - wins)}`;
  els.profileDrElo.textContent = String(profile.stats?.dr_elo ?? 1200);
  els.profileDrRecord.textContent = `${drWins}-${drLosses}`;
}

async function bootstrapProfile() {
  profile = await api('/api/profile/bootstrap', { guest_id: storedGuestId() });
  if (profile?.guest_id) saveGuestId(profile.guest_id);
  renderProfile();
}

async function saveProfileName() {
  if (!profile?.guest_id) return;
  const display_name = els.profileNameInput.value.trim();
  if (!display_name) return;
  els.profileSaveBtn.disabled = true;
  const next = await api('/api/profile/name', {
    guest_id: profile.guest_id,
    display_name,
  });
  els.profileSaveBtn.disabled = false;
  if (next?.error) {
    els.profileStatus.textContent = next.error;
    return;
  }
  profile = next;
  renderProfile();
}

async function getAutocomplete(q) {
  const r = await fetch('/api/autocomplete?q=' + encodeURIComponent(q));
  return r.json();
}

async function getTeamAutocomplete(q) {
  const r = await fetch('/api/fr/team_autocomplete?q=' + encodeURIComponent(q));
  return r.json();
}

function showScreen(name) {
  els.homeScreen.hidden = name !== 'home';
  els.profileScreen.hidden = name !== 'profile';
  els.startScreen.hidden = name !== 'mp-setup';
  els.gameScreen.hidden = !(name === 'mp-game' || name === 'bp-game');
  els.frScreen.hidden = name !== 'fr-game';

  els.brandSubtitle.textContent = '';

  els.exitBtn.hidden = name === 'home';
  const togglesRelevant = name === 'mp-game' || name === 'bp-game';
  els.headerToggles.hidden = !togglesRelevant;
  els.lineupSection.hidden = !togglesRelevant || !els.toggleLineup.checked;
  els.outSection.hidden = !togglesRelevant || !els.toggleOut.checked;
}

function goHome() {
  currentMode = 'home';
  game = null;
  frGame = null;
  lastChainLength = 0;
  clearInterval(timerInterval);
  clearInterval(countdownInterval);
  hideGameOverBanner();
  hideFrSummaryBanner();
  closeAutocomplete();
  closeTeamAutocomplete();
  els.guessInput.value = '';
  els.frTeamInput.value = '';
  els.frYearInput.value = '';
  showScreen('home');
}

function openProfile() {
  renderProfile();
  showScreen('profile');
}

function pickMode(mode) {
  if (mode === 'mp') {
    showScreen('mp-setup');
    els.p1Input.focus();
    return;
  }
  if (mode === 'bp') {
    startBp();
    return;
  }
  if (mode === 'fr') {
    startFr();
  }
}

async function newMpGame(p1, p2) {
  currentMode = 'mp';
  lastChainLength = 0;
  hideGameOverBanner();
  showScreen('mp-game');
  renderLoadingGame('Division Rivalry', 'Starting matchup...');
  game = await api('/api/new_game', { p1, p2, guest_id: profile?.guest_id || storedGuestId() });
  if (game.error) {
    alert('error: ' + game.error);
    return false;
  }
  renderMpGame();
  runOpeningCountdown();
  return true;
}

async function startMpGame() {
  const p1 = els.p1Input.value.trim() || 'Player 1';
  const p2 = els.p2Input.value.trim() || 'Player 2';
  await newMpGame(p1, p2);
}

async function startBp() {
  currentMode = 'bp';
  lastChainLength = 0;
  hideGameOverBanner();
  showScreen('bp-game');
  renderLoadingGame('Batting Practice', 'Loading leadoff...');
  game = await api('/api/bp/new', { guest_id: profile?.guest_id || storedGuestId() });
  if (game.error) {
    alert('error: ' + game.error);
    return;
  }
  els.guessInput.value = '';
  renderBpGame();
  runOpeningCountdown();
}

async function startFr() {
  currentMode = 'fr';
  hideFrSummaryBanner();
  closeTeamAutocomplete();
  els.frTeamInput.value = '';
  els.frYearInput.value = '';
  showScreen('fr-game');
  renderLoadingFilmReview();
  frGame = await api('/api/fr/new', { guest_id: profile?.guest_id || storedGuestId() });
  if (frGame.error) {
    alert('error: ' + frGame.error);
    return;
  }
  renderFrGame(true);
  els.frTeamInput.focus();
}

function renderLoadingGame(label, prompt) {
  els.turnCard.hidden = false;
  els.turnLabel.textContent = label;
  els.timer.textContent = '...';
  els.currentPlayerName.textContent = prompt;
  els.feedback.innerHTML = '';
  els.cardStack.innerHTML = '';
  els.lineup.innerHTML = '';
  els.outList.innerHTML = '';
  els.outEmpty.hidden = false;
  setGuessDisabled(true);
}

function renderLoadingFilmReview() {
  els.frTurnCard.hidden = false;
  els.frStats.textContent = '--';
  els.frPairNames.textContent = 'Loading review...';
  els.frFeedback.innerHTML = '';
  els.frCardStack.innerHTML = '';
  els.frTeamInput.disabled = true;
  els.frYearInput.disabled = true;
}

async function rematch() {
  if (currentMode === 'mp') {
    await newMpGame(game.p1, game.p2);
    return;
  }
  if (currentMode === 'bp') {
    await startBp();
  }
}

function showGameOverBanner() {
  clearInterval(countdownInterval);
  els.timer.classList.remove('countdown');
  els.turnCard.hidden = true;
  els.gameOverBanner.hidden = false;

  if (currentMode === 'mp') {
    const teamsOut = game.strikes.filter((s) => s.count >= 3).length;
    els.winnerText.textContent = game.winner ? `${game.winner} wins!` : 'Game over.';
    els.gameOverSummary.textContent =
      `Lineup of ${game.chain.length}. ${teamsOut} team${teamsOut === 1 ? '' : 's'} struck out.`;
    els.playAgainBtn.textContent = "Let's play two.";
  } else if (currentMode === 'bp') {
    els.winnerText.textContent = `Lineup of ${game.longest_chain - 1}.`;
    els.gameOverSummary.textContent = 'Time expired. Try to beat your longest lineup.';
    els.playAgainBtn.textContent = 'Take more cuts';
  }

  ensureHomeFromBanner();
}

function hideGameOverBanner() {
  els.turnCard.hidden = false;
  els.gameOverBanner.hidden = true;
  removeHomeFromBanner();
}

function ensureHomeFromBanner() {
  if (els.gameOverBanner.querySelector('.home-from-banner')) return;
  const homeBtn = document.createElement('button');
  homeBtn.textContent = 'Home';
  homeBtn.className = 'secondary home-from-banner';
  homeBtn.style.marginLeft = '0.5rem';
  homeBtn.addEventListener('click', goHome);
  els.playAgainBtn.parentNode.appendChild(homeBtn);
}

function removeHomeFromBanner() {
  const homeBtn = els.gameOverBanner.querySelector('.home-from-banner');
  if (homeBtn) homeBtn.remove();
}

function resetTurnTimer() {
  clearInterval(timerInterval);
  if (!(currentMode === 'mp' || currentMode === 'bp')) {
    els.timer.textContent = '--';
    return;
  }
  if (!game || game.finished) {
    els.timer.textContent = '--';
    return;
  }
  turnLocalStart = performance.now() / 1000;
  const startRemaining = game.remaining_seconds;
  els.timer.title = 'seconds left';
  els.timer.textContent = startRemaining.toFixed(1) + 's';
  timerInterval = setInterval(() => {
    const elapsed = performance.now() / 1000 - turnLocalStart;
    const remaining = Math.max(0, startRemaining - elapsed);
    els.timer.textContent = remaining.toFixed(1) + 's';
    els.timer.style.color = remaining < 5 ? '#ff5b3a' : '';
    if (remaining <= 0) {
      clearInterval(timerInterval);
      if (currentMode === 'bp') onBpTimeout();
      else onMpTimeout();
    }
  }, 100);
}

function setGuessDisabled(disabled) {
  els.guessInput.disabled = disabled;
  els.guessBtn.disabled = disabled;
}

function runOpeningCountdown() {
  clearInterval(countdownInterval);
  const remaining = Number(game?.countdown_seconds_remaining || 0);
  if (!(currentMode === 'mp' || currentMode === 'bp') || remaining <= 0) {
    els.timer.classList.remove('countdown');
    setGuessDisabled(false);
    resetTurnTimer();
    els.guessInput.focus();
    return;
  }

  setGuessDisabled(true);
  els.timer.classList.add('countdown');

  const countdownStart = performance.now() / 1000;
  const update = () => {
    const elapsed = performance.now() / 1000 - countdownStart;
    const left = remaining - elapsed;
    if (left <= 0) {
      clearInterval(countdownInterval);
      els.timer.classList.remove('countdown');
      els.timer.style.color = '';
      setGuessDisabled(false);
      resetTurnTimer();
      els.guessInput.focus();
      return;
    }
    els.timer.textContent = String(Math.ceil(left));
  };

  update();
  countdownInterval = setInterval(update, 100);
}

async function onMpTimeout() {
  game = await api('/api/timeout', { game_id: game.game_id });
  if (game.finished) {
    renderMpGame();
    showGameOverBanner();
    bootstrapProfile();
  } else {
    resetTurnTimer();
    renderMpGame();
  }
}

async function onBpTimeout() {
  game = await api('/api/bp/timeout', { game_id: game.game_id });
  if (game.finished) {
    renderBpGame();
    showGameOverBanner();
    bootstrapProfile();
  } else {
    resetTurnTimer();
    renderBpGame();
  }
}

async function submitMove({ raw, player_id }) {
  if (!game || game.finished) return;
  closeAutocomplete();
  els.guessInput.value = '';
  const path = currentMode === 'bp' ? '/api/bp/move' : '/api/move';
  const prevTurnIndex = game.turn_index;
  game = await api(path, { game_id: game.game_id, raw, player_id });
  if (currentMode === 'mp') {
    if (game.last_move?.outcome === 'valid' || game.turn_index !== prevTurnIndex) {
      resetTurnTimer();
    }
    renderMpGame();
    if (game.finished) {
      showGameOverBanner();
      bootstrapProfile();
    }
  } else {
    if (game.last_move?.outcome === 'valid') resetTurnTimer();
    renderBpGame();
    if (game.finished) {
      showGameOverBanner();
      bootstrapProfile();
    }
  }
  if (!game.finished) els.guessInput.focus();
}

function onGuessSubmit(e) {
  e.preventDefault();
  if (acHighlight >= 0 && acHighlight < acItems.length) {
    submitMove({ player_id: acItems[acHighlight].player_id });
    return;
  }
  const raw = els.guessInput.value.trim();
  if (!raw) return;
  submitMove({ raw });
}

async function onGuessInput() {
  userTypedQuery = els.guessInput.value;
  const q = userTypedQuery.trim();
  if (!q) {
    closeAutocomplete();
    return;
  }
  const seq = ++acFetchSeq;
  const items = await getAutocomplete(q);
  if (seq !== acFetchSeq) return;
  if (!items || items.length === 0) {
    closeAutocomplete();
    return;
  }
  acItems = items;
  acHighlight = -1;
  renderAutocomplete();
}

function applyHighlightToInput() {
  if (acHighlight >= 0 && acHighlight < acItems.length) {
    els.guessInput.value = acItems[acHighlight].display_name;
  } else {
    els.guessInput.value = userTypedQuery;
  }
  const end = els.guessInput.value.length;
  els.guessInput.setSelectionRange(end, end);
}

function onGuessKeydown(e) {
  if (els.autocompleteList.hidden) return;
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    acHighlight += 1;
    if (acHighlight >= acItems.length) acHighlight = -1;
    applyHighlightToInput();
    renderAutocomplete();
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    acHighlight -= 1;
    if (acHighlight < -1) acHighlight = acItems.length - 1;
    applyHighlightToInput();
    renderAutocomplete();
  } else if (e.key === 'Escape') {
    closeAutocomplete();
    els.guessInput.value = userTypedQuery;
  }
}

function renderAutocomplete() {
  els.autocompleteList.innerHTML = acItems.map((it, i) => {
    const yrs = formatYears(it.debut_year, it.final_year);
    return `<li data-i="${i}" class="${i === acHighlight ? 'active' : ''}">
              <span class="ac-name">${escapeHtml(it.display_name)}</span>
              <span class="ac-years">${escapeHtml(yrs)}</span>
            </li>`;
  }).join('');
  els.autocompleteList.hidden = false;
  els.autocompleteList.querySelectorAll('li').forEach((li) => {
    li.addEventListener('click', () => {
      const i = parseInt(li.dataset.i, 10);
      submitMove({ player_id: acItems[i].player_id });
    });
  });
}

function closeAutocomplete() {
  els.autocompleteList.hidden = true;
  els.autocompleteList.innerHTML = '';
  acItems = [];
  acHighlight = -1;
  userTypedQuery = '';
}

async function onTeamInput() {
  userTypedTeamQuery = els.frTeamInput.value;
  const q = userTypedTeamQuery.trim();
  if (!q) {
    closeTeamAutocomplete();
    return;
  }
  const seq = ++teamAcFetchSeq;
  const items = await getTeamAutocomplete(q);
  if (seq !== teamAcFetchSeq) return;
  if (!items || items.length === 0) {
    closeTeamAutocomplete();
    return;
  }
  teamAcItems = items;
  teamAcHighlight = -1;
  renderTeamAutocomplete();
}

function applyTeamHighlightToInput() {
  if (teamAcHighlight >= 0 && teamAcHighlight < teamAcItems.length) {
    els.frTeamInput.value = teamAcItems[teamAcHighlight];
  } else {
    els.frTeamInput.value = userTypedTeamQuery;
  }
  const end = els.frTeamInput.value.length;
  els.frTeamInput.setSelectionRange(end, end);
}

function onTeamKeydown(e) {
  if (e.key === 'Enter') {
    e.preventDefault();
    if (teamAcHighlight >= 0 && teamAcHighlight < teamAcItems.length) {
      els.frTeamInput.value = teamAcItems[teamAcHighlight];
      closeTeamAutocomplete({ keepValue: true });
    }
    els.frYearInput.focus();
    return;
  }

  if (els.frTeamAutocompleteList.hidden) return;
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    teamAcHighlight += 1;
    if (teamAcHighlight >= teamAcItems.length) teamAcHighlight = -1;
    applyTeamHighlightToInput();
    renderTeamAutocomplete();
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    teamAcHighlight -= 1;
    if (teamAcHighlight < -1) teamAcHighlight = teamAcItems.length - 1;
    applyTeamHighlightToInput();
    renderTeamAutocomplete();
  } else if (e.key === 'Escape') {
    closeTeamAutocomplete();
    els.frTeamInput.value = userTypedTeamQuery;
  }
}

function renderTeamAutocomplete() {
  els.frTeamAutocompleteList.innerHTML = teamAcItems.map((name, i) => {
    return `<li data-i="${i}" class="${i === teamAcHighlight ? 'active' : ''}">
              <span class="ac-name">${escapeHtml(name)}</span>
            </li>`;
  }).join('');
  els.frTeamAutocompleteList.hidden = false;
  els.frTeamAutocompleteList.querySelectorAll('li').forEach((li) => {
    li.addEventListener('click', () => {
      const i = parseInt(li.dataset.i, 10);
      els.frTeamInput.value = teamAcItems[i];
      closeTeamAutocomplete({ keepValue: true });
      els.frYearInput.focus();
    });
  });
}

function closeTeamAutocomplete(opts = {}) {
  els.frTeamAutocompleteList.hidden = true;
  els.frTeamAutocompleteList.innerHTML = '';
  teamAcItems = [];
  teamAcHighlight = -1;
  if (!opts.keepValue) userTypedTeamQuery = '';
}

function renderMpGame() {
  els.turnLabel.textContent = `${game.current_label}'s turn`;
  els.currentPlayerName.textContent = game.current_player.name;
  setGuessDisabled(game.finished || (game.countdown_seconds_remaining || 0) > 0);
  els.guessInput.placeholder = 'Type a name (first or last)...';

  els.feedback.innerHTML = renderMoveFeedback(game.last_move, game);
  renderCardStack(game.chain, game.strikes, true);
  renderLineup(game.chain);
  renderOut(game.strikes);

  els.lineupSection.hidden = !els.toggleLineup.checked;
  els.outSection.hidden = !els.toggleOut.checked;
  lastChainLength = game.chain.length;
}

function renderBpGame() {
  els.turnLabel.textContent = 'Batting Practice';
  els.currentPlayerName.textContent = game.current_player.name;
  els.timer.title = 'seconds left';
  setGuessDisabled(game.finished || (game.countdown_seconds_remaining || 0) > 0);
  els.guessInput.placeholder = 'Type a name (first or last)...';

  els.feedback.innerHTML = renderMoveFeedback(game.last_move, game);
  renderCardStack(game.chain, game.strikes, true);
  renderLineup(game.chain);
  renderOut(game.strikes);

  els.lineupSection.hidden = !els.toggleLineup.checked;
  els.outSection.hidden = !els.toggleOut.checked;
  lastChainLength = game.chain.length;
}

function renderCardStack(chain, allStrikes, showStrikes) {
  const newPlayerAdded = chain.length > lastChainLength;
  const reversed = chain.slice().reverse();
  els.cardStack.innerHTML = '';
  reversed.forEach((player, i) => {
    const isSeed = i === reversed.length - 1;
    const playerCard = makePlayerCard(player, isSeed);
    if (newPlayerAdded && i === 0) playerCard.classList.add('slide-in');
    els.cardStack.appendChild(playerCard);
    if (i < reversed.length - 1) {
      const bar = makeConnectionBar(player.shared_with_prev, allStrikes, showStrikes);
      if (newPlayerAdded && i === 0) bar.classList.add('slide-in');
      els.cardStack.appendChild(bar);
    }
  });
}

function makePlayerCard(player, isSeed, options = {}) {
  const showTeams = options.showTeams !== false;
  const playerCard = document.createElement('div');
  playerCard.className = 'player-card' + (isSeed ? ' seed' : '');

  const headshot = document.createElement('div');
  headshot.className = 'headshot';
  const initials = ((player.name || '?').split(/\s+/).map((p) => p[0] || '').join('').slice(0, 2)).toUpperCase();
  headshot.innerHTML = `<span class="initials">${escapeHtml(initials)}</span>`;
  if (player.headshot_url) {
    const img = document.createElement('img');
    img.src = player.headshot_url;
    img.alt = player.name;
    img.loading = 'lazy';
    img.onload = () => headshot.querySelector('.initials')?.remove();
    img.onerror = () => img.remove();
    headshot.appendChild(img);
  }
  playerCard.appendChild(headshot);

  const info = document.createElement('div');
  info.className = 'player-info';
  const yrs = formatYears(player.debut_year, player.final_year);
  const seedBadge = isSeed ? '<span class="seed-badge">leadoff</span>' : '';
  info.innerHTML = `
    <h3 class="name">${escapeHtml(player.name)}${seedBadge}</h3>
    <div class="years">${escapeHtml(yrs)}</div>
    ${showTeams ? `<div class="teams-label">Teams</div><ul class="teams">${(player.teams || []).map((t) => `<li>${escapeHtml(t)}</li>`).join('')}</ul>` : ''}`;
  playerCard.appendChild(info);
  return playerCard;
}

function makeConnectionBar(sharedSeasons, allStrikes, showStrikes) {
  const bar = document.createElement('div');
  bar.className = 'connection-bar';
  const seasons = document.createElement('div');
  seasons.className = 'connection-seasons';
  (sharedSeasons || []).forEach((s) => {
    const strikeRow = (allStrikes || []).find(
      (x) => x.team_id === s.team_id && x.season === s.season
    );
    const count = strikeRow ? strikeRow.count : 0;
    const burned = count >= 3;
    const pill = document.createElement('span');
    pill.className = 'season-pill' + (burned ? ' burned' : '');
    if (showStrikes) {
      pill.innerHTML = `
        ${escapeHtml(s.team_name)} ${s.season}
        <span class="x-marks">
          <span class="x-mark ${count >= 1 ? 's' + Math.min(count, 3) : ''}"></span>
          <span class="x-mark ${count >= 2 ? 's' + Math.min(count, 3) : ''}"></span>
          <span class="x-mark ${count >= 3 ? 's3' : ''}"></span>
        </span>`;
    } else {
      pill.innerHTML = `${escapeHtml(s.team_name)} ${s.season}`;
    }
    seasons.appendChild(pill);
  });
  bar.appendChild(seasons);
  return bar;
}

function renderLineup(chain) {
  els.lineup.innerHTML = chain
    .map((p, i) => `<li class="${i === chain.length - 1 ? 'last' : ''}">${escapeHtml(p.name)}</li>`)
    .join('');
}

function renderOut(strikes) {
  const burned = strikes.filter((s) => s.count >= 3);
  if (burned.length === 0) {
    els.outEmpty.hidden = false;
    els.outList.innerHTML = '';
  } else {
    els.outEmpty.hidden = true;
    els.outList.innerHTML = burned.map((s) => `<li>${escapeHtml(s.team_name)} ${s.season}</li>`).join('');
  }
}

function renderMoveFeedback(m, g) {
  if (!m) return '';
  if (m.outcome === 'timeout') return '<span class="bad">Time expired.</span>';

  const name = m.display_name
    ? `${escapeHtml(m.display_name)}${m.disambiguation ? ` <span class="muted-inline">(${escapeHtml(m.disambiguation)})</span>` : ''}`
    : '';
  const ambig = m.ambiguous_count > 1
    ? ` <span class="muted-inline">(auto-picked from ${m.ambiguous_count} matches. Try the dropdown to be specific.)</span>`
    : '';

  switch (m.outcome) {
    case 'valid': {
      const teams = m.shared_seasons.map((s) => `${s.team_name} ${s.season}`).join(', ');
      const newOut = m.shared_seasons
        .filter((s) => {
          const row = g.strikes.find((x) => x.team_id === s.team_id && x.season === s.season);
          return row && row.count >= 3;
        })
        .map((s) => `${s.team_name} ${s.season}`).join(', ');
      return `<span class="ok">✓ ${name}${ambig}. Teammates on ${escapeHtml(teams)}.</span>` +
        (newOut ? `<br><span class="burn">STRUCK OUT this move: ${escapeHtml(newOut)}</span>` : '');
    }
    case 'unknown_player':
      return '<span class="bad">✗ unknown player.</span>';
    case 'already_used':
      return `<span class="bad">✗ ${name} already used in this lineup.</span>`;
    case 'not_teammate': {
      const prev = g.chain[g.chain.length - 1].name;
      return `<span class="bad">✗ ${name}${ambig} was never a teammate of ${escapeHtml(prev)}.</span>`;
    }
    case 'blocked_by_burned': {
      const prev = g.chain[g.chain.length - 1].name;
      const allShared = m.shared_seasons.map((s) => `${s.team_name} ${s.season}`).join(', ');
      const out = m.burned_seasons.map((s) => `${s.team_name} ${s.season}`).join(', ');
      const verb = m.burned_seasons.length === 1 ? 'is' : 'are';
      return `<span class="bad">✗ ${name}${ambig} and ${escapeHtml(prev)} were teammates on ${escapeHtml(allShared)},<br>` +
        `but ${escapeHtml(out)} ${verb} already struck out. Pick someone else.</span>`;
    }
    default:
      return '';
  }
}

async function frSubmit(e) {
  e.preventDefault();
  if (!frGame || frGame.finished) return;
  if (teamAcHighlight >= 0 && teamAcHighlight < teamAcItems.length) {
    els.frTeamInput.value = teamAcItems[teamAcHighlight];
  }
  const team = els.frTeamInput.value.trim();
  const year = els.frYearInput.value.trim();
  if (!team || !year) return;
  closeTeamAutocomplete({ keepValue: true });
  frGame = await api('/api/fr/guess', {
    game_id: frGame.game_id,
    team,
    year,
  });
  els.frTeamInput.value = '';
  els.frYearInput.value = '';
  closeTeamAutocomplete();
  renderFrGame(false);
  if (frGame.finished) {
    showFrSummaryBanner();
    bootstrapProfile();
  } else {
    els.frTeamInput.focus();
  }
}

function renderFrGame(initialRender) {
  const s = frGame.stats;
  els.frStats.innerHTML =
    `<span class="stat-hit">${s.hits}H</span> <span class="stat-sep">|</span> ` +
    `<span class="stat-foul">${s.fouls}F</span> <span class="stat-sep">|</span> ` +
    `<span class="stat-strike">${s.strikes}/${s.max_strikes}K</span>`;

  if (frGame.pair_names[0] && frGame.pair_names[1]) {
    els.frPairNames.innerHTML =
      `${escapeHtml(frGame.pair_names[0])}` +
      `<span class="arrow">to</span>` +
      `${escapeHtml(frGame.pair_names[1])}`;
  } else {
    els.frPairNames.innerHTML = '';
  }

  els.frFeedback.innerHTML = renderFrFeedback(frGame.last_guess);

  const reversed = frGame.revealed_cards.slice().reverse();
  const solvedLinks = frGame.solved_links || [];
  els.frCardStack.innerHTML = '';
  reversed.forEach((player, i) => {
    const isSeed = i === reversed.length - 1;
    const playerCard = makePlayerCard(player, isSeed, { showTeams: false });
    if (!initialRender && i === 0 && reversed.length > 2) {
      playerCard.classList.add('slide-in');
    }
    els.frCardStack.appendChild(playerCard);
    if (i < reversed.length - 1) {
      const solvedIndex = solvedLinks.length - 1 - i;
      const solved = solvedLinks[solvedIndex];
      if (solved) {
        const bar = makeConnectionBar([solved], [], false);
        if (!initialRender && i === 0 && reversed.length > 2) {
          bar.classList.add('slide-in');
        }
        els.frCardStack.appendChild(bar);
      } else {
        const spacer = document.createElement('div');
        spacer.className = 'fr-link-spacer';
        els.frCardStack.appendChild(spacer);
      }
    }
  });

  els.frTeamInput.disabled = frGame.finished;
  els.frYearInput.disabled = frGame.finished;
}

function renderFrFeedback(g) {
  if (!g) return '';
  if (g.outcome === 'invalid') {
    return '<span class="bad">Enter both a team and a 4-digit year.</span>';
  }
  if (g.outcome === 'hit') {
    const m = g.matched && g.matched[0];
    const detail = m ? ` (${escapeHtml(m.team_name)} ${m.season})` : '';
    return `<span class="ok">✓ HIT${escapeHtml(detail)}.</span>`;
  }
  if (g.outcome === 'foul') {
    return '<span class="burn">FOUL. One of team or year is right. Try again.</span>';
  }
  if (g.outcome === 'strike') {
    if (g.converted_from_foul) {
      return '<span class="bad">STRIKE. Another foul in the same streak counts as a strike.</span>';
    }
    return '<span class="bad">STRIKE. Neither team nor year is right.</span>';
  }
  return '';
}

function showFrSummaryBanner() {
  els.frTurnCard.hidden = true;
  els.frSummaryBanner.hidden = false;
  if (frGame.won) {
    els.frSummaryText.textContent = "That's the lineup!";
    els.frSummaryDetail.textContent =
      `${frGame.stats.hits} hits, ${frGame.stats.fouls} fouls, ${frGame.stats.strikes} strikes.`;
  } else {
    els.frSummaryText.textContent = 'Game called.';
    els.frSummaryDetail.textContent =
      `${frGame.stats.hits} hits before 3 strikes.`;
    loadFrAnswers();
  }
}

function hideFrSummaryBanner() {
  els.frTurnCard.hidden = false;
  els.frSummaryBanner.hidden = true;
  els.frAnswerReveal.innerHTML = '';
}

async function loadFrAnswers() {
  if (!frGame?.game_id || frGame?.won) return;
  const res = await api('/api/fr/reveal_answer', { game_id: frGame.game_id });
  if (!res.answers) return;
  if (res.full_cards && res.canonical_links) {
    frGame.revealed_cards = res.full_cards;
    frGame.solved_links = res.canonical_links;
    renderFrGame(true);
  }
  els.frAnswerReveal.innerHTML = '';
}

function formatYears(debut, final) {
  if (debut == null) return '';
  if (final == null) return `${debut}-`;
  return debut === final ? `${debut}` : `${debut}-${final}`;
}

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function applyToggles() {
  if (!(currentMode === 'mp' || currentMode === 'bp')) return;
  els.lineupSection.hidden = !els.toggleLineup.checked;
  els.outSection.hidden = !els.toggleOut.checked;
}

function rulesForMode() {
  if (currentMode === 'bp') {
    return 'Build the longest lineup you can. You have 30 seconds to name a teammate of the last player, and a correct guess resets the clock. Each team shared by two linked players gets a strike. Once a team is Struck Out, that team cannot be used to link players again. Your run ends when the clock hits zero.';
  }
  if (currentMode === 'fr') {
    return 'Review the revealed players and guess the team and year that links each pair. A correct team and year is a hit and reveals the next player. One correct field is a foul. The first foul in a streak is safe, then every foul after that in the same streak counts as a strike. Three strikes ends the review.';
  }
  if (currentMode === 'mp') {
    return 'Two players alternate turns building one lineup. The round starts with a 3 second countdown, then the 30 second clock begins. On your turn, name a teammate of the last player before time runs out. Correct guesses pass the turn. Teams collect strikes when used, and Struck Out teams cannot link players again. You win when your opponent runs out of time.';
  }
  return 'Pick a mode, then build or review a lineup by connecting baseball players through their shared teams.';
}

function openRules() {
  els.rulesText.textContent = rulesForMode();
  els.rulesModal.hidden = false;
}

function closeRules() {
  els.rulesModal.hidden = true;
}

document.querySelectorAll('.mode-tile').forEach((tile) => {
  tile.addEventListener('click', () => pickMode(tile.dataset.mode));
});

els.exitBtn.addEventListener('click', goHome);

document.querySelectorAll('[data-back="home"]').forEach((btn) => {
  btn.addEventListener('click', goHome);
});

els.startBtn.addEventListener('click', startMpGame);
[els.p1Input, els.p2Input].forEach((inp) => {
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') startMpGame();
  });
});

els.guessForm.addEventListener('submit', onGuessSubmit);
els.guessInput.addEventListener('input', onGuessInput);
els.guessInput.addEventListener('keydown', onGuessKeydown);

els.playAgainBtn.addEventListener('click', rematch);
els.toggleLineup.addEventListener('change', applyToggles);
els.toggleOut.addEventListener('change', applyToggles);

els.frGuessForm.addEventListener('submit', frSubmit);
els.frTeamInput.addEventListener('input', onTeamInput);
els.frTeamInput.addEventListener('keydown', onTeamKeydown);
els.frHomeBtn.addEventListener('click', goHome);

document.addEventListener('click', (e) => {
  if (els.guessForm.contains(e.target) || els.autocompleteList.contains(e.target)) {
    return;
  }
  if (els.frGuessForm.contains(e.target) || els.frTeamAutocompleteList.contains(e.target)) {
    return;
  }
  closeAutocomplete();
  closeTeamAutocomplete();
});

els.rulesBtn.addEventListener('click', openRules);
els.rulesClose.addEventListener('click', closeRules);
els.rulesBackdrop.addEventListener('click', closeRules);
els.profileSaveBtn.addEventListener('click', saveProfileName);
els.profileOpenBtn.addEventListener('click', openProfile);
els.profileNameInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    saveProfileName();
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !els.rulesModal.hidden) closeRules();
});

showScreen('home');
renderProfile();
bootstrapProfile();

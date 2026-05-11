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
  friendsOpenBtn: document.getElementById('friends-open-btn'),
  profileStatus: document.getElementById('profile-status'),
  accountLoggedOut: document.getElementById('account-logged-out'),
  accountLoggedIn: document.getElementById('account-logged-in'),
  accountUsernameInput: document.getElementById('account-username-input'),
  accountPasswordInput: document.getElementById('account-password-input'),
  accountRegisterBtn: document.getElementById('account-register-btn'),
  accountLoginBtn: document.getElementById('account-login-btn'),
  accountLogoutBtn: document.getElementById('account-logout-btn'),
  accountSummary: document.getElementById('account-summary'),
  accountStatus: document.getElementById('account-status'),
  profileScreen: document.getElementById('profile-screen'),
  friendsScreen: document.getElementById('friends-screen'),
  profileScreenName: document.getElementById('profile-screen-name'),
  profileBpBest: document.getElementById('profile-bp-best'),
  profileBpPlays: document.getElementById('profile-bp-plays'),
  profileFrRecord: document.getElementById('profile-fr-record'),
  profileDrElo: document.getElementById('profile-dr-elo'),
  profileDrRecord: document.getElementById('profile-dr-record'),
  profileTopStruck: document.getElementById('profile-top-struck'),
  bpLeaderboard: document.getElementById('bp-leaderboard'),
  friendsStatus: document.getElementById('friends-status'),
  friendTargetInput: document.getElementById('friend-target-input'),
  friendRequestBtn: document.getElementById('friend-request-btn'),
  incomingRequestsList: document.getElementById('incoming-requests-list'),
  outgoingRequestsList: document.getElementById('outgoing-requests-list'),
  incomingChallengesList: document.getElementById('incoming-challenges-list'),
  outgoingChallengesList: document.getElementById('outgoing-challenges-list'),
  challengeHistoryList: document.getElementById('challenge-history-list'),
  friendsList: document.getElementById('friends-list'),
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
  cancelMatchBtn: document.getElementById('cancel-match-btn'),
  mpStatusText: document.getElementById('mp-status-text'),
  createCodeBtn: document.getElementById('create-code-btn'),
  joinCodeInput: document.getElementById('join-code-input'),
  joinCodeBtn: document.getElementById('join-code-btn'),
  challengeStatusText: document.getElementById('challenge-status-text'),

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
  mpRematchStatus: document.getElementById('mp-rematch-status'),
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
let mpPollInterval = null;
let mpQueuePollInterval = null;
let mpRematchPollInterval = null;
let friendsPollInterval = null;
let turnLocalStart = 0;
let lastChainLength = 0;
let activeCountdownKey = '';
let activeTimerKey = '';

let acItems = [];
let acHighlight = -1;
let acFetchSeq = 0;
let userTypedQuery = '';

let teamAcItems = [];
let teamAcHighlight = -1;
let teamAcFetchSeq = 0;
let userTypedTeamQuery = '';
let friendsData = null;

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
    els.profileTopStruck.textContent = '--';
    els.accountLoggedOut.hidden = false;
    els.accountLoggedIn.hidden = true;
    els.accountStatus.textContent = '';
    return;
  }
  els.profileNameInput.value = profile.display_name || '';
  els.profileStatus.textContent = profile.account
    ? 'Signed in. Your profile follows you across devices.'
    : 'Guest profile saved on this browser.';
  const wins = profile.stats?.fr_wins ?? 0;
  const plays = profile.stats?.fr_plays ?? 0;
  const drWins = profile.stats?.dr_wins ?? 0;
  const drLosses = profile.stats?.dr_losses ?? 0;
  els.profileScreenName.textContent = profile.account?.username || profile.display_name || '';
  els.profileBpBest.textContent = String(profile.stats?.bp_best ?? 0);
  els.profileBpPlays.textContent = String(profile.stats?.bp_plays ?? 0);
  els.profileFrRecord.textContent = `${wins}-${Math.max(0, plays - wins)}`;
  els.profileDrElo.textContent = String(profile.stats?.dr_elo ?? 1200);
  els.profileDrRecord.textContent = `${drWins}-${drLosses}`;
  const topStruck = profile.stats?.top_struck_teams || [];
  els.profileTopStruck.textContent = topStruck.length
    ? topStruck.map((t) => `${t.team_name} ${t.season} (${t.count})`).join(', ')
    : 'None yet';
  if (profile.account) {
    els.accountLoggedOut.hidden = true;
    els.accountLoggedIn.hidden = false;
    els.accountSummary.textContent = `${profile.account.username} · ${profile.account.email}`;
    els.accountStatus.textContent = 'Account connected.';
  } else {
    els.accountLoggedOut.hidden = false;
    els.accountLoggedIn.hidden = true;
    els.accountSummary.textContent = '';
    els.accountStatus.textContent = 'Create an account or log in to carry your profile across browsers and devices.';
  }
}

async function bootstrapProfile() {
  profile = await api('/api/profile/bootstrap', { guest_id: storedGuestId() });
  if (profile?.guest_id) saveGuestId(profile.guest_id);
  renderProfile();
  refreshBpLeaderboard();
  startFriendsPolling();
  refreshFriends();
}

async function refreshBpLeaderboard() {
  const rows = await fetch('/api/bp/leaderboard').then((r) => r.json());
  els.bpLeaderboard.innerHTML = rows.length
    ? rows.map((row) => `<li>${escapeHtml(row.display_name)} - ${row.chain_length}</li>`).join('')
    : '<li>No runs yet today.</li>';
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

async function registerAccount() {
  if (!profile?.guest_id) return;
  const username = els.accountUsernameInput.value.trim();
  const password = els.accountPasswordInput.value;
  const display_name = els.profileNameInput.value.trim() || profile.display_name || username;
  els.accountRegisterBtn.disabled = true;
  const next = await api('/api/account/register', {
    guest_id: profile.guest_id,
    username,
    password,
    display_name,
  });
  els.accountRegisterBtn.disabled = false;
  if (next?.error) {
    els.accountStatus.textContent = next.error;
    return;
  }
  profile = next;
  saveGuestId(profile.guest_id);
  els.accountPasswordInput.value = '';
  renderProfile();
  startFriendsPolling();
  refreshFriends();
}

async function loginAccount() {
  const identifier = els.accountUsernameInput.value.trim();
  const password = els.accountPasswordInput.value;
  els.accountLoginBtn.disabled = true;
  const next = await api('/api/account/login', { identifier, password });
  els.accountLoginBtn.disabled = false;
  if (next?.error) {
    els.accountStatus.textContent = next.error;
    return;
  }
  profile = next;
  saveGuestId(profile.guest_id);
  els.accountPasswordInput.value = '';
  renderProfile();
  refreshBpLeaderboard();
  startFriendsPolling();
  refreshFriends();
}

async function logoutAccount() {
  clearInterval(friendsPollInterval);
  window.localStorage.removeItem(GUEST_ID_KEY);
  profile = null;
  friendsData = null;
  renderProfile();
  await bootstrapProfile();
}

function currentHandle() {
  return profile?.account?.username || profile?.display_name || 'your profile';
}

function renderSimpleList(el, rows, emptyText, actionBuilder) {
  if (!rows || rows.length === 0) {
    el.innerHTML = `<div class="friend-empty">${escapeHtml(emptyText)}</div>`;
    return;
  }
  el.innerHTML = rows.map((row) => {
    const actions = actionBuilder ? actionBuilder(row) : '';
    const sub = row.display_name && row.display_name !== row.username
      ? `<div class="friend-sub">${escapeHtml(row.display_name)}</div>`
      : '';
    const label = row.username || row.name || row.display_name || '';
    return `<div class="friend-row">
      <div class="friend-meta">
        <div class="friend-name">${escapeHtml(label)}</div>
        ${sub}
      </div>
      <div class="friend-actions">${actions}</div>
    </div>`;
  }).join('');
}

function wireFriendsActions() {
  document.querySelectorAll('[data-friend-accept]').forEach((btn) => {
    btn.addEventListener('click', () => respondFriendRequest(btn.dataset.friendAccept, true));
  });
  document.querySelectorAll('[data-friend-decline]').forEach((btn) => {
    btn.addEventListener('click', () => respondFriendRequest(btn.dataset.friendDecline, false));
  });
  document.querySelectorAll('[data-friend-cancel]').forEach((btn) => {
    btn.addEventListener('click', () => cancelFriendRequest(btn.dataset.friendCancel));
  });
  document.querySelectorAll('[data-friend-challenge]').forEach((btn) => {
    btn.addEventListener('click', () => sendFriendChallenge(btn.dataset.friendChallenge));
  });
  document.querySelectorAll('[data-challenge-accept]').forEach((btn) => {
    btn.addEventListener('click', () => respondFriendChallenge(btn.dataset.challengeAccept, true));
  });
  document.querySelectorAll('[data-challenge-decline]').forEach((btn) => {
    btn.addEventListener('click', () => respondFriendChallenge(btn.dataset.challengeDecline, false));
  });
  document.querySelectorAll('[data-challenge-cancel]').forEach((btn) => {
    btn.addEventListener('click', () => cancelFriendChallenge(btn.dataset.challengeCancel));
  });
}

function renderFriends() {
  if (!profile?.account) {
    els.friendsOpenBtn.textContent = 'Friends';
    els.friendsStatus.textContent = 'Create an account or log in to use friends.';
    renderSimpleList(els.incomingRequestsList, [], 'Account required.', null);
    renderSimpleList(els.outgoingRequestsList, [], 'Account required.', null);
    renderSimpleList(els.incomingChallengesList, [], 'Account required.', null);
    renderSimpleList(els.outgoingChallengesList, [], 'Account required.', null);
    renderSimpleList(els.challengeHistoryList, [], 'Account required.', null);
    renderSimpleList(els.friendsList, [], 'Account required.', null);
    return;
  }
  if (!friendsData) {
    els.friendsOpenBtn.textContent = 'Friends';
    els.friendsStatus.textContent = 'Loading friends...';
    return;
  }
  const pendingCount = (friendsData.incoming_requests?.length || 0) + (friendsData.incoming_challenges?.length || 0);
  els.friendsOpenBtn.textContent = pendingCount > 0 ? `Friends (${pendingCount})` : 'Friends';
  els.friendsStatus.textContent = 'Add friends by username or email. Friend challenges start Division Rivalry right away.';
  renderSimpleList(
    els.incomingRequestsList,
    friendsData.incoming_requests,
    'No incoming requests.',
    (row) => `
      <button class="secondary" type="button" data-friend-accept="${row.request_id}">Accept</button>
      <button class="secondary" type="button" data-friend-decline="${row.request_id}">Decline</button>
    `,
  );
  renderSimpleList(
    els.outgoingRequestsList,
    friendsData.outgoing_requests,
    'No outgoing requests.',
    (row) => `<button class="secondary" type="button" data-friend-cancel="${row.request_id}">Cancel</button>`,
  );
  renderSimpleList(
    els.incomingChallengesList,
    friendsData.incoming_challenges,
    'No game requests.',
    (row) => `
      <button class="secondary" type="button" data-challenge-accept="${row.challenge_id}">Accept</button>
      <button class="secondary" type="button" data-challenge-decline="${row.challenge_id}">Decline</button>
    `,
  );
  renderSimpleList(
    els.outgoingChallengesList,
    friendsData.outgoing_challenges,
    'No sent game requests.',
    (row) => `<button class="secondary" type="button" data-challenge-cancel="${row.challenge_id}">Cancel</button>`,
  );
  renderSimpleList(
    els.friendsList,
    friendsData.friends,
    'No friends yet.',
    (row) => `<button class="secondary" type="button" data-friend-challenge="${row.user_id}">Challenge</button>`,
  );
  renderSimpleList(
    els.challengeHistoryList,
    friendsData.challenge_history,
    'No challenge history yet.',
    (row) => '',
  );
  els.challengeHistoryList.querySelectorAll('.friend-row').forEach((rowEl, idx) => {
    const row = friendsData.challenge_history[idx];
    if (!row) return;
    rowEl.innerHTML = `
      <div class="friend-meta">
        <div class="friend-name">${escapeHtml(row.won ? 'Win' : 'Loss')} vs ${escapeHtml(row.opponent_label || 'Friend')}</div>
        <div class="friend-sub">Lineup ${escapeHtml(String(row.chain_length || 0))}</div>
      </div>
    `;
  });
  wireFriendsActions();
}

async function refreshFriends() {
  if (!profile?.account) {
    friendsData = null;
    renderFriends();
    return;
  }
  const next = await api('/api/friends/list', { guest_id: profile.guest_id });
  if (next?.error) {
    els.friendsStatus.textContent = next.error;
    return;
  }
  friendsData = next;
  renderFriends();
  if (friendsData.matched_game && currentMode !== 'mp') {
    await enterMatchedGame(friendsData.matched_game);
  }
}

function startFriendsPolling() {
  clearInterval(friendsPollInterval);
  if (!profile?.account) return;
  friendsPollInterval = setInterval(refreshFriends, 3000);
}

async function openFriends() {
  showScreen('friends');
  await refreshFriends();
  startFriendsPolling();
}

async function sendFriendRequest() {
  if (!profile?.account) {
    els.friendsStatus.textContent = 'You need an account to add friends.';
    return;
  }
  const target = els.friendTargetInput.value.trim();
  if (!target) return;
  const next = await api('/api/friends/request', { guest_id: profile.guest_id, target });
  if (next?.error) {
    els.friendsStatus.textContent = next.error;
    return;
  }
  els.friendTargetInput.value = '';
  friendsData = next;
  renderFriends();
}

async function respondFriendRequest(requestId, accept) {
  const next = await api('/api/friends/respond', {
    guest_id: profile.guest_id,
    request_id: requestId,
    accept,
  });
  if (next?.error) {
    els.friendsStatus.textContent = next.error;
    return;
  }
  friendsData = next;
  renderFriends();
}

async function cancelFriendRequest(requestId) {
  const next = await api('/api/friends/request_cancel', {
    guest_id: profile.guest_id,
    request_id: requestId,
  });
  if (next?.error) {
    els.friendsStatus.textContent = next.error;
    return;
  }
  friendsData = next;
  renderFriends();
}

async function sendFriendChallenge(friendUserId) {
  const next = await api('/api/friends/challenge', {
    guest_id: profile.guest_id,
    friend_user_id: friendUserId,
  });
  if (next?.error) {
    els.friendsStatus.textContent = next.error;
    return;
  }
  friendsData = next;
  els.friendsStatus.textContent = 'Game request sent.';
  renderFriends();
}

async function respondFriendChallenge(challengeId, accept) {
  const next = await api('/api/friends/challenge_respond', {
    guest_id: profile.guest_id,
    challenge_id: challengeId,
    accept,
  });
  if (next?.error) {
    els.friendsStatus.textContent = next.error;
    return;
  }
  if (next.status === 'matched' && next.game) {
    await enterMatchedGame(next.game);
    return;
  }
  await refreshFriends();
}

async function cancelFriendChallenge(challengeId) {
  const next = await api('/api/friends/challenge_cancel', {
    guest_id: profile.guest_id,
    challenge_id: challengeId,
  });
  if (next?.error) {
    els.friendsStatus.textContent = next.error;
    return;
  }
  friendsData = next;
  renderFriends();
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
  els.friendsScreen.hidden = name !== 'friends';
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

async function goHome() {
  const wasWaiting = !els.cancelMatchBtn.hidden;
  const activeMpGameId = currentMode === 'mp' && game?.game_id ? game.game_id : '';
  const finishedMpGameId = currentMode === 'mp' && game?.finished ? game.game_id : '';
  currentMode = 'home';
  game = null;
  frGame = null;
  lastChainLength = 0;
  clearInterval(timerInterval);
  clearInterval(countdownInterval);
  clearInterval(mpPollInterval);
  clearInterval(mpQueuePollInterval);
  clearInterval(mpRematchPollInterval);
  clearInterval(friendsPollInterval);
  activeCountdownKey = '';
  activeTimerKey = '';
  hideGameOverBanner();
  hideFrSummaryBanner();
  closeAutocomplete();
  closeTeamAutocomplete();
  els.guessInput.value = '';
  els.frTeamInput.value = '';
  els.frYearInput.value = '';
  els.startBtn.hidden = false;
  els.cancelMatchBtn.hidden = true;
  els.challengeStatusText.textContent = '';
  if (wasWaiting) {
    await api('/api/dr/cancel_queue', { guest_id: profile?.guest_id || storedGuestId() });
    await api('/api/dr/cancel_challenge', { guest_id: profile?.guest_id || storedGuestId() });
  } else if (activeMpGameId) {
    if (finishedMpGameId) {
      const payload = JSON.stringify({
        guest_id: profile?.guest_id || storedGuestId(),
        game_id: finishedMpGameId,
      });
      await fetch('/api/dr/postgame_leave', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
        keepalive: true,
      });
    } else {
      await api('/api/dr/leave_game', {
        guest_id: profile?.guest_id || storedGuestId(),
        game_id: activeMpGameId,
      });
    }
  }
  showScreen('home');
}

function openProfile() {
  clearInterval(friendsPollInterval);
  renderProfile();
  showScreen('profile');
}

function pickMode(mode) {
  clearInterval(friendsPollInterval);
  if (mode === 'mp') {
    showScreen('mp-setup');
    els.mpStatusText.textContent = `Queue as ${currentHandle()}.`;
    els.startBtn.hidden = false;
    els.cancelMatchBtn.hidden = true;
    els.challengeStatusText.textContent = '';
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

function startMpPolling() {
  clearInterval(mpPollInterval);
  if (currentMode !== 'mp' || !game || game.finished) return;
  mpPollInterval = setInterval(async () => {
    if (!game?.game_id) return;
    const previousGame = game;
    const next = await api('/api/dr/game', {
      guest_id: profile?.guest_id || storedGuestId(),
      game_id: game.game_id,
    });
    if (!next.error) {
      const prevChain = previousGame?.chain?.length || 0;
      game = next;
      lastChainLength = prevChain;
      renderMpGame();
      syncMpClock(previousGame, next);
      if (game.finished) {
        clearInterval(mpPollInterval);
        showGameOverBanner();
        bootstrapProfile();
      }
    }
  }, 1000);
}

async function enterMatchedGame(nextGame) {
  currentMode = 'mp';
  lastChainLength = 0;
  clearInterval(mpRematchPollInterval);
  hideGameOverBanner();
  showScreen('mp-game');
  clearInterval(mpQueuePollInterval);
  game = nextGame;
  renderMpGame();
  syncMpClock(null, game, { force: true });
  startMpPolling();
}

async function pollMatchmaking() {
  const status = await api('/api/dr/status', { guest_id: profile?.guest_id || storedGuestId() });
  if (status.status === 'matched' && status.game) {
    await enterMatchedGame(status.game);
    return;
  }
  if (status.status === 'waiting') {
    els.mpStatusText.textContent = 'Searching for an opponent...';
    return;
  }
  if (status.status === 'idle' && els.challengeStatusText.textContent) return;
  els.mpStatusText.textContent = `Queue as ${currentHandle()}.`;
}

async function startMpGame(opts = {}) {
  showScreen('mp-setup');
  els.mpStatusText.textContent = 'Searching for an opponent...';
  els.startBtn.hidden = true;
  els.cancelMatchBtn.hidden = false;
  clearInterval(mpQueuePollInterval);
  const queued = await api('/api/dr/queue', {
    guest_id: profile?.guest_id || storedGuestId(),
    avoid_guest_id: opts.avoidGuestId || '',
  });
  if (queued.error) {
    alert('error: ' + queued.error);
    els.startBtn.hidden = false;
    els.cancelMatchBtn.hidden = true;
    return;
  }
  if (queued.status === 'matched' && queued.game) {
    await enterMatchedGame(queued.game);
    return;
  }
  mpQueuePollInterval = setInterval(pollMatchmaking, 1000);
}

async function cancelMatchmaking() {
  clearInterval(mpQueuePollInterval);
  await api('/api/dr/cancel_queue', { guest_id: profile?.guest_id || storedGuestId() });
  await api('/api/dr/cancel_challenge', { guest_id: profile?.guest_id || storedGuestId() });
  els.startBtn.hidden = false;
  els.cancelMatchBtn.hidden = true;
  els.mpStatusText.textContent = `Queue as ${currentHandle()}.`;
  els.challengeStatusText.textContent = '';
}

async function createChallengeCode() {
  const res = await api('/api/dr/create_challenge', { guest_id: profile?.guest_id || storedGuestId() });
  if (res.error) {
    els.challengeStatusText.textContent = res.error;
    return;
  }
  els.challengeStatusText.textContent = `Challenge code: ${res.code}`;
  els.startBtn.hidden = true;
  els.cancelMatchBtn.hidden = false;
  els.mpStatusText.textContent = 'Waiting for someone to join your challenge...';
  clearInterval(mpQueuePollInterval);
  mpQueuePollInterval = setInterval(pollMatchmaking, 1000);
}

async function joinChallengeCode() {
  const code = els.joinCodeInput.value.trim().toUpperCase();
  if (!code) return;
  els.challengeStatusText.textContent = 'Joining challenge...';
  const res = await api('/api/dr/join_challenge', {
    guest_id: profile?.guest_id || storedGuestId(),
    code,
  });
  if (res.error) {
    els.challengeStatusText.textContent = res.error;
    return;
  }
  els.joinCodeInput.value = '';
  els.challengeStatusText.textContent = '';
  if (res.status === 'matched' && res.game) {
    await enterMatchedGame(res.game);
  }
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
    if (!game) return;
    if (game.last_move?.outcome === 'forfeit') {
      goHome();
      pickMode('mp');
      await startMpGame();
      return;
    }
    const res = await api('/api/dr/rematch_request', {
      guest_id: profile?.guest_id || storedGuestId(),
      game_id: game.game_id,
    });
    if (res.error) {
      els.mpRematchStatus.hidden = false;
      els.mpRematchStatus.textContent = res.error;
      return;
    }
    if (res.status === 'matched' && res.game) {
      els.mpRematchStatus.hidden = true;
      await enterMatchedGame(res.game);
      return;
    }
    els.mpRematchStatus.hidden = false;
    els.mpRematchStatus.textContent = "Let's play two? Waiting on your opponent.";
    startRematchPolling();
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
  clearInterval(mpRematchPollInterval);
  els.mpRematchStatus.hidden = true;
  els.mpRematchStatus.textContent = '';

  if (currentMode === 'mp') {
    const teamsOut = game.strikes.filter((s) => s.count >= 3).length;
    els.winnerText.textContent = game.winner ? `${game.winner} wins!` : 'Game over.';
    els.gameOverSummary.textContent =
      `Lineup of ${game.chain.length}. ${teamsOut} team${teamsOut === 1 ? '' : 's'} struck out.`;
    if (game.last_move?.outcome === 'forfeit') {
      els.playAgainBtn.textContent = 'Find New Match';
    } else {
      els.playAgainBtn.textContent = "Let's play two.";
      startRematchPolling();
    }
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
  clearInterval(mpRematchPollInterval);
  els.mpRematchStatus.hidden = true;
  els.mpRematchStatus.textContent = '';
  removeHomeFromBanner();
}

async function requeueAfterRematchAbandoned() {
  clearInterval(mpRematchPollInterval);
  const avoidGuestId = game?.p1_guest_id === (profile?.guest_id || storedGuestId())
    ? game?.p2_guest_id
    : game?.p1_guest_id;
  hideGameOverBanner();
  game = null;
  currentMode = 'mp';
  showScreen('mp-setup');
  els.challengeStatusText.textContent = '';
  els.mpStatusText.textContent = 'Opponent left. Searching for a new opponent...';
  await startMpGame({ avoidGuestId });
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
    activeTimerKey = '';
    return;
  }
  if (!game || game.finished) {
    els.timer.textContent = '--';
    activeTimerKey = '';
    return;
  }
  activeTimerKey = timerKey(game);
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
    activeCountdownKey = '';
    setGuessDisabled(false);
    resetTurnTimer();
    els.guessInput.focus();
    return;
  }

  setGuessDisabled(true);
  els.timer.classList.add('countdown');
  activeCountdownKey = countdownKey(game);

  const countdownStart = performance.now() / 1000;
  const update = () => {
    const elapsed = performance.now() / 1000 - countdownStart;
    const left = remaining - elapsed;
    if (left <= 0) {
      clearInterval(countdownInterval);
      els.timer.classList.remove('countdown');
      els.timer.style.color = '';
      activeCountdownKey = '';
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
  game = await api('/api/timeout', {
    game_id: game.game_id,
    guest_id: profile?.guest_id || storedGuestId(),
  });
  if (game.finished) {
    renderMpGame();
    showGameOverBanner();
    bootstrapProfile();
  } else {
    renderMpGame();
    syncMpClock(null, game, { force: true });
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
  game = await api(path, {
    game_id: game.game_id,
    raw,
    player_id,
    guest_id: currentMode === 'mp' ? (profile?.guest_id || storedGuestId()) : undefined,
  });
  if (currentMode === 'mp') {
    renderMpGame();
    syncMpClock(null, game, { force: true });
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
  els.turnLabel.textContent = game.your_turn ? 'Your turn' : `${game.current_label}'s turn`;
  els.currentPlayerName.textContent = game.current_player.name;
  els.turnCard.classList.toggle('your-turn', !!game.your_turn);
  els.turnCard.classList.toggle('opponent-turn', !game.your_turn);
  els.timer.classList.toggle('your-turn', !!game.your_turn);
  els.timer.classList.toggle('opponent-turn', !game.your_turn);
  setGuessDisabled(game.finished || (game.countdown_seconds_remaining || 0) > 0 || !game.your_turn);
  els.guessInput.placeholder = game.your_turn ? 'Type a name (first or last)...' : '';

  els.feedback.innerHTML = renderMoveFeedback(game.last_move, game);
  renderCardStack(game.chain, game.strikes, true);
  renderLineup(game.chain);
  renderOut(game.strikes);

  els.lineupSection.hidden = !els.toggleLineup.checked;
  els.outSection.hidden = !els.toggleOut.checked;
  lastChainLength = game.chain.length;
}

function countdownKey(state) {
  if (!state) return '';
  return [
    state.game_id,
    state.turn_index,
    state.current_player?.id || '',
    Math.ceil(Number(state.countdown_seconds_remaining || 0)),
  ].join('|');
}

function timerKey(state) {
  if (!state) return '';
  return [
    state.game_id,
    state.turn_index,
    state.current_player?.id || '',
    Number(state.remaining_seconds || 0).toFixed(1),
  ].join('|');
}

function syncMpClock(previousState, nextState, opts = {}) {
  if (currentMode !== 'mp' || !nextState) return;
  if (nextState.finished) {
    clearInterval(countdownInterval);
    clearInterval(timerInterval);
    activeCountdownKey = '';
    activeTimerKey = '';
    els.timer.textContent = '0.0s';
    return;
  }
  const nextCountdown = Number(nextState.countdown_seconds_remaining || 0);
  if (nextCountdown > 0) {
    const key = countdownKey(nextState);
    if (opts.force || key !== activeCountdownKey) {
      runOpeningCountdown();
    }
    return;
  }

  clearInterval(countdownInterval);
  els.timer.classList.remove('countdown');
  activeCountdownKey = '';

  const nextTimer = Number(nextState.remaining_seconds || 0);
  const prevTimer = Number(previousState?.remaining_seconds || 0);
  const changedTurn =
    !previousState ||
    previousState.turn_index !== nextState.turn_index ||
    previousState.current_player?.id !== nextState.current_player?.id;
  const drifted = Math.abs(nextTimer - prevTimer) > 0.65;
  if (opts.force || changedTurn || drifted || timerKey(nextState) !== activeTimerKey) {
    resetTurnTimer();
  }
}

function startRematchPolling() {
  clearInterval(mpRematchPollInterval);
  mpRematchPollInterval = setInterval(async () => {
    if (currentMode !== 'mp' || !game?.game_id) return;
    const res = await api('/api/dr/rematch_status', {
      guest_id: profile?.guest_id || storedGuestId(),
      game_id: game.game_id,
    });
    if (res.error) {
      els.mpRematchStatus.hidden = false;
      els.mpRematchStatus.textContent = res.error;
      return;
    }
    if (res.status === 'matched' && res.game) {
      clearInterval(mpRematchPollInterval);
      els.mpRematchStatus.hidden = true;
      await enterMatchedGame(res.game);
      return;
    }
    if (res.status === 'abandoned') {
      els.mpRematchStatus.hidden = false;
      els.mpRematchStatus.textContent = 'Opponent left. Finding a new match...';
      await requeueAfterRematchAbandoned();
      return;
    }
    if (!res.rematch_available) {
      clearInterval(mpRematchPollInterval);
      els.mpRematchStatus.hidden = false;
      els.mpRematchStatus.textContent = 'Rematch is unavailable after a player leaves.';
      return;
    }
    if (res.opponent_requested && !res.you_requested) {
      els.mpRematchStatus.hidden = false;
      els.mpRematchStatus.textContent = 'Your opponent wants a rematch.';
    } else if (res.you_requested) {
      els.mpRematchStatus.hidden = false;
      els.mpRematchStatus.textContent = "Let's play two? Waiting on your opponent.";
    }
  }, 1000);
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
    return 'Build the longest lineup you can. You have 20 seconds to name a teammate of the last player, and a correct guess resets the clock. Each team shared by two linked players gets a strike. Once a team is Struck Out, that team cannot be used to link players again. Your run ends when the clock hits zero.';
  }
  if (currentMode === 'fr') {
    return 'Review the revealed players and guess the team and year that links each pair. A correct team and year is a hit and reveals the next player. One correct field is a foul. The first foul in a streak is safe, then every foul after that in the same streak counts as a strike. Three strikes ends the review.';
  }
  if (currentMode === 'mp') {
    return 'Queue into an online match and take turns building one lineup. After the 3 second countdown, the 20 second clock begins. On your turn, name a teammate of the last player before time runs out. Correct guesses pass the turn and reset the clock. Teams collect strikes when used, and Struck Out teams cannot link players again. You win when your opponent runs out of time.';
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
els.cancelMatchBtn.addEventListener('click', cancelMatchmaking);
els.createCodeBtn.addEventListener('click', createChallengeCode);
els.joinCodeBtn.addEventListener('click', joinChallengeCode);
els.joinCodeInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    joinChallengeCode();
  }
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
els.friendsOpenBtn.addEventListener('click', openFriends);
els.accountRegisterBtn.addEventListener('click', registerAccount);
els.accountLoginBtn.addEventListener('click', loginAccount);
els.accountLogoutBtn.addEventListener('click', logoutAccount);
els.friendRequestBtn.addEventListener('click', sendFriendRequest);
els.profileNameInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    saveProfileName();
  }
});
els.accountPasswordInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    if (els.accountUsernameInput.value.trim()) {
      loginAccount();
    }
  }
});
els.friendTargetInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    sendFriendRequest();
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !els.rulesModal.hidden) closeRules();
});

showScreen('home');
renderProfile();
bootstrapProfile();

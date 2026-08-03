// Teammate Tag client. Three modes share this file:
//   home -> mode picker
//   mp   -> Division Rivalry (multiplayer)
//   po   -> Playoffs (multiplayer with powerups)
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
  accountEmailInput: document.getElementById('account-email-input'),
  accountPasswordInput: document.getElementById('account-password-input'),
  accountRegisterBtn: document.getElementById('account-register-btn'),
  accountLoginBtn: document.getElementById('account-login-btn'),
  accountResetBtn: document.getElementById('account-reset-btn'),
  accountLogoutBtn: document.getElementById('account-logout-btn'),
  accountSummary: document.getElementById('account-summary'),
  accountStatus: document.getElementById('account-status'),
  profileScreen: document.getElementById('profile-screen'),
  friendsScreen: document.getElementById('friends-screen'),
  profileScreenName: document.getElementById('profile-screen-name'),
  profileSportSelect: document.getElementById('profile-sport-select'),
  profileBpBestLabel: document.getElementById('profile-bp-best-label'),
  profileBpPlaysLabel: document.getElementById('profile-bp-plays-label'),
  profileFrLabel: document.getElementById('profile-fr-label'),
  profileDrEloLabel: document.getElementById('profile-dr-elo-label'),
  profileDrRecordLabel: document.getElementById('profile-dr-record-label'),
  profileTopStruckLabel: document.getElementById('profile-top-struck-label'),
  profileLeaderboardCard: document.getElementById('profile-leaderboard-card'),
  profileBpBest: document.getElementById('profile-bp-best'),
  profileBpPlays: document.getElementById('profile-bp-plays'),
  profileFrRecord: document.getElementById('profile-fr-record'),
  profileFrStreak: document.getElementById('profile-fr-streak'),
  profileDrElo: document.getElementById('profile-dr-elo'),
  profileDrRecord: document.getElementById('profile-dr-record'),
  profileTopStruck: document.getElementById('profile-top-struck'),
  bpLeaderboard: document.getElementById('bp-leaderboard'),
  deleteAccountCard: document.getElementById('delete-account-card'),
  deleteAccountPasswordInput: document.getElementById('delete-account-password-input'),
  deleteAccountBtn: document.getElementById('delete-account-btn'),
  deleteAccountStatus: document.getElementById('delete-account-status'),
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
  frSetupScreen: document.getElementById('fr-setup-screen'),

  brandSubtitle: document.getElementById('brand-subtitle'),
  exitBtn: document.getElementById('exit-btn'),
  headerToggles: document.getElementById('header-toggles'),
  toggleLineup: document.getElementById('toggle-lineup'),
  toggleOut: document.getElementById('toggle-out'),
  referenceBtn: document.getElementById('reference-btn'),

  p1Input: document.getElementById('p1-input'),
  p2Input: document.getElementById('p2-input'),
  matchModeTitle: document.getElementById('match-mode-title'),
  startBtn: document.getElementById('start-btn'),
  cancelMatchBtn: document.getElementById('cancel-match-btn'),
  mpStatusText: document.getElementById('mp-status-text'),
  createCodeBtn: document.getElementById('create-code-btn'),
  joinCodeInput: document.getElementById('join-code-input'),
  joinCodeBtn: document.getElementById('join-code-btn'),
  challengeStatusText: document.getElementById('challenge-status-text'),
  challengeBox: document.querySelector('.challenge-box'),
  playoffConditionPicker: document.getElementById('playoff-condition-picker'),
  playoffConditionSelect: document.getElementById('playoff-condition-select'),
  playoffRandomBtn: document.getElementById('playoff-random-btn'),

  turnCard: document.getElementById('turn-card'),
  turnLabel: document.getElementById('turn-label'),
  timer: document.getElementById('timer'),
  currentPlayerName: document.getElementById('current-player-name'),
  winPanel: document.getElementById('win-panel'),
  yourWinName: document.getElementById('your-win-name'),
  yourWinDesc: document.getElementById('your-win-desc'),
  yourWinPips: document.getElementById('your-win-pips'),
  oppWinName: document.getElementById('opp-win-name'),
  oppWinDesc: document.getElementById('opp-win-desc'),
  oppWinPips: document.getElementById('opp-win-pips'),
  powerupPanel: document.getElementById('powerup-panel'),
  yourPowerupName: document.getElementById('your-powerup-name'),
  yourPowerupDesc: document.getElementById('your-powerup-desc'),
  oppPowerupName: document.getElementById('opp-powerup-name'),
  oppPowerupDesc: document.getElementById('opp-powerup-desc'),
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
  requeueBtn: document.getElementById('requeue-btn'),
  homeFromBannerBtn: document.getElementById('home-from-banner-btn'),

  frTurnCard: document.getElementById('fr-turn-card'),
  frTitle: document.getElementById('fr-title'),
  frArchive: document.getElementById('fr-archive'),
  frArchiveList: document.getElementById('fr-archive-list'),
  frStats: document.getElementById('fr-stats'),
  frPairNames: document.getElementById('fr-pair-names'),
  frTeamInput: document.getElementById('fr-team-input'),
  frTeamAutocompleteList: document.getElementById('fr-team-autocomplete-list'),
  frYearInput: document.getElementById('fr-year-input'),
  frGuessForm: document.getElementById('fr-guess-form'),
  frFeedback: document.getElementById('fr-feedback'),
  frCardStack: document.getElementById('fr-card-stack'),
  frLineupBoard: document.getElementById('fr-lineup-board'),
  frOffenseBtn: document.getElementById('fr-offense-btn'),
  frDefenseBtn: document.getElementById('fr-defense-btn'),
  frSummaryBanner: document.getElementById('fr-summary-banner'),
  frSummaryText: document.getElementById('fr-summary-text'),
  frSummaryDetail: document.getElementById('fr-summary-detail'),
  frAnswerReveal: document.getElementById('fr-answer-reveal'),
  frHomeBtn: document.getElementById('fr-home'),

  rulesBtn: document.getElementById('rules-btn'),
  rulesModal: document.getElementById('rules-modal'),
  rulesBackdrop: document.getElementById('rules-backdrop'),
  rulesClose: document.getElementById('rules-close'),
  rulesTitle: document.getElementById('rules-title'),
  rulesText: document.getElementById('rules-text'),
};

function on(el, eventName, handler, options) {
  if (el) el.addEventListener(eventName, handler, options);
}

function hasProfileUi() {
  return !!els.profileStatus;
}

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
let mpRequeueRelaxTimeout = null;
let mpPollInFlight = false;
let moveSubmissionInFlight = false;
let turnLocalStart = 0;
let lastChainLength = 0;
let activeCountdownKey = '';
let activeTimerKey = '';
let animateNewestCard = false;
let referenceSport = 'baseball';

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
const CURRENT_SPORT = document.body.dataset.sport || '';
const LOCAL_SOLO_SPORTS = new Set(['football', 'basketball', 'hockey']);
const CROSS_SPORTS_ONLINE = document.body.dataset.crossSportsOnline === 'true';
const USE_LOCAL_CROSS_SPORTS = !CROSS_SPORTS_ONLINE && LOCAL_SOLO_SPORTS.has(CURRENT_SPORT);

function localSoloPath(suffix) {
  return (USE_LOCAL_CROSS_SPORTS ? '/api/local/' : '/api/sports/') + CURRENT_SPORT + '/bp/' + suffix;
}

function usesLocalFilmReview() {
  return USE_LOCAL_CROSS_SPORTS;
}

function usesLocalPlayoffs() {
  return USE_LOCAL_CROSS_SPORTS;
}

function localFilmReviewPath(suffix) {
  return (USE_LOCAL_CROSS_SPORTS ? '/api/local/' : '/api/sports/') + CURRENT_SPORT + '/fr/' + suffix;
}

function filmReviewPath(suffix) {
  return CURRENT_SPORT === 'baseball' ? '/api/fr/' + suffix : localFilmReviewPath(suffix);
}

function isCrossSport() {
  return LOCAL_SOLO_SPORTS.has(CURRENT_SPORT);
}

const POWERUP_UI = {
  bubblegum: { icon: 'BG', className: 'bubblegum' },
  pine_tar: { icon: 'PT', className: 'pine-tar' },
  bat_donut: { icon: 'BD', className: 'bat-donut' },
  sunglasses: { icon: 'SG', className: 'sunglasses' },
  backup_mitt: { icon: 'BM', className: 'backup-mitt' },
  abs: { icon: 'ABS', className: 'abs' },
  quick_pitch: { icon: 'QP', className: 'quick-pitch' },
  heat_check: { icon: 'HC', className: 'bubblegum' },
  sixth_man: { icon: '6M', className: 'pine-tar' },
  switch: { icon: 'SW', className: 'bat-donut' },
  mvp_badge: { icon: 'MV', className: 'sunglasses' },
  all_star_callup: { icon: 'AS', className: 'backup-mitt' },
  timeout: { icon: 'TO', className: 'abs' },
  full_court_press: { icon: 'FP', className: 'quick-pitch' },
  trick_play: { icon: 'TP', className: 'bubblegum' },
  iron_man: { icon: 'IM', className: 'pine-tar' },
  package_change: { icon: 'PC', className: 'bat-donut' },
  pro_bowl_callup: { icon: 'PB', className: 'backup-mitt' },
  blitz: { icon: 'BZ', className: 'quick-pitch' },
  breakaway: { icon: 'BA', className: 'bubblegum' },
  veteran_presence: { icon: 'VP', className: 'pine-tar' },
  line_change: { icon: 'LC', className: 'bat-donut' },
  hart_honor: { icon: 'HT', className: 'sunglasses' },
  forecheck: { icon: 'FC', className: 'quick-pitch' },
};

function isOnlineMode(mode = currentMode) {
  return mode === 'mp' || mode === 'po';
}

function onlineApiBase(mode = currentMode) {
  if (CURRENT_SPORT === 'baseball') {
    return '/api/sports/baseball/' + (mode === 'po' ? 'po' : 'dr');
  }
  if (isCrossSport() && CROSS_SPORTS_ONLINE) {
    return '/api/sports/' + CURRENT_SPORT + '/' + (mode === 'po' ? 'po' : 'dr');
  }
  if (mode === 'po' && usesLocalPlayoffs()) {
    return '/api/local/' + CURRENT_SPORT + '/po';
  }
  if (mode === 'mp' && LOCAL_SOLO_SPORTS.has(CURRENT_SPORT)) {
    return '/api/local/' + CURRENT_SPORT + '/dr';
  }
  return mode === 'po' ? '/api/po' : '/api/dr';
}

const LOCAL_PLAYOFF_OPTIONS = {
  basketball: [
    ['random', 'Random'], ['bucket_getter', 'Bucket Getter: 2 players with 25,000 career points'],
    ['season_scorer', 'Scoring Run: 2 players with a 2,000-point season'],
    ['playmaker', 'Table Setter: 2 players with 7,000 career assists'],
    ['three_point_club', 'Deep Range: 2 players with 2,000 career three-pointers'],
    ['ironhorse', 'Ironhorse: 2 players with 1,000 career games'],
    ['one_team', 'Home Court: 2 players with 8 seasons for one franchise'],
    ['journeyman', 'Frequent Flyer: 2 players who played for 5 teams'], ['mvp_circle', 'MVP Circle: 2 MVP winners'],
    ['all_star_marathon', 'All-Star Marathon: 12 combined selections'], ['ring_chaser', 'Ring Chaser: 6 combined championships'],
    ['young_guns', 'Young Guns: 2 Rookie of the Year winners'],
  ],
  football: [
    ['random', 'Random'], ['touchdown_club', 'End Zone: 2 players with 100 career touchdowns'],
    ['season_scorer', 'Season Scorer: 2 players with a 15-touchdown season'],
    ['air_raid', 'Air Raid: 2 players with 300 career passing touchdowns'],
    ['single_season_passer', 'Sunday Slingers: 2 players with a 35-passing-touchdown season'],
    ['sack_master', 'Sack Master: 2 players with 100 career sacks'], ['ballhawk', 'Ballhawk: 2 players with 30 career interceptions'],
    ['one_team', 'One Club: 2 players with 10 seasons for one franchise'],
    ['journeyman', 'Journeyman: 2 players who played for 5 teams'], ['mvp_circle', 'MVP Circle: 2 MVP winners'],
    ['pro_bowl_marathon', 'Pro Bowl Marathon: 12 combined selections'], ['ring_chaser', 'Ring Chaser: 5 combined championships'],
    ['young_guns', 'Fresh Faces: 2 Rookie of the Year winners'],
  ],
  hockey: [
    ['random', 'Random'], ['sniper', 'Sniper: 2 players with 500 career goals'],
    ['single_season_sniper', 'Rocket Season: 1 player with a 60-goal season'],
    ['playmaker', 'Playmaker: 2 players with 1,000 career assists'],
    ['point_streak', 'Point Machine: 1 player with a 120-point season'],
    ['one_team', 'Lifer: 2 players with 10 seasons for one franchise'],
    ['journeyman', 'Journeyman: 2 players who played for 5 teams'], ['mvp_circle', 'Hart Club: 2 Hart Trophy winners'],
    ['all_star_marathon', 'All-Star Marathon: 12 combined selections'], ['ironhorse', 'Ironhorse: 2 players with 1,200 career games'],
    ['ring_chaser', 'Cup Chasers: 7 combined Stanley Cup credits'], ['young_guns', 'Fresh Ice: 2 Calder Trophy winners'],
  ],
};

function configureLocalPlayoffPicker() {
  const options = LOCAL_PLAYOFF_OPTIONS[CURRENT_SPORT];
  if (!options) return;
  const storageKey = 'tt_local_playoff_condition_' + CURRENT_SPORT;
  const saved = window.localStorage.getItem(storageKey) || 'random';
  els.playoffConditionSelect.innerHTML = options.map(([value, label]) =>
    `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join('');
  els.playoffConditionSelect.value = options.some(([value]) => value === saved) ? saved : 'random';
}

function onlineModeName(mode = currentMode) {
  return mode === 'po' ? 'Playoffs' : 'Division Rivalry';
}

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
  if (!hasProfileUi()) return;
  if (!profile) {
    els.profileStatus.textContent = 'Loading guest profile...';
    els.profileScreenName.textContent = '';
    els.profileBpBest.textContent = '--';
    els.profileBpPlays.textContent = '--';
    els.profileFrRecord.textContent = '--';
    els.profileFrStreak.textContent = '--';
    els.profileDrElo.textContent = '--';
    els.profileDrRecord.textContent = '--';
    els.profileTopStruck.textContent = '--';
    els.deleteAccountCard.hidden = true;
    els.deleteAccountStatus.textContent = '';
    els.accountLoggedOut.hidden = false;
    els.accountLoggedIn.hidden = true;
    els.accountStatus.textContent = '';
    return;
  }
  els.profileNameInput.value = profile.display_name || '';
  els.profileStatus.textContent = profile.authenticated
    ? 'Signed in. Your profile follows you across devices.'
    : profile.account
      ? 'Account found. Log in to use it on this device.'
      : 'Guest profile saved on this browser.';
  const selectedSport = els.profileSportSelect?.value || 'baseball';
  const sportTerms = {
    baseball: { reps: 'Manager Mode', out: 'Struck Out' },
    basketball: { reps: 'Manager Mode', out: 'Fouled Out' },
    football: { reps: 'Manager Mode', out: 'Punted' },
    hockey: { reps: 'Manager Mode', out: 'Game Misconduct' },
  }[selectedSport];
  const stats = profile.stats?.sports?.[selectedSport] || profile.stats || {};
  const wins = stats.fr_wins ?? 0;
  const plays = stats.fr_plays ?? 0;
  const drWins = stats.dr_wins ?? 0;
  const drLosses = stats.dr_losses ?? 0;
  els.profileScreenName.textContent = profile.account?.username || profile.display_name || '';
  els.profileBpBest.textContent = String(stats.bp_best ?? 0);
  els.profileBpPlays.textContent = String(stats.bp_plays ?? 0);
  if (selectedSport === 'football') {
    const offenseWins = stats.fr_offense_wins ?? 0;
    const offensePlays = stats.fr_offense_plays ?? 0;
    const defenseWins = stats.fr_defense_wins ?? 0;
    const defensePlays = stats.fr_defense_plays ?? 0;
    els.profileFrRecord.textContent = `O ${offenseWins}-${Math.max(0, offensePlays - offenseWins)} | D ${defenseWins}-${Math.max(0, defensePlays - defenseWins)}`;
  } else {
    els.profileFrRecord.textContent = `${wins}-${Math.max(0, plays - wins)}`;
  }
  els.profileFrStreak.textContent = `${stats.fr_daily_streak ?? 0} day${(stats.fr_daily_streak ?? 0) === 1 ? '' : 's'}`;
  els.profileDrElo.textContent = String(stats.dr_elo ?? 1200);
  els.profileDrRecord.textContent = `${drWins}-${drLosses}`;
  const topStruck = stats.top_struck_teams || [];
  els.profileTopStruck.textContent = topStruck.length
    ? topStruck.map((t) => `${t.team_name} ${t.season} (${t.count})`).join(', ')
    : 'None yet';
  els.profileBpBestLabel.textContent = `${sportTerms.reps} Longest Lineup`;
  els.profileBpPlaysLabel.textContent = `${sportTerms.reps} Plays`;
  els.profileFrLabel.textContent = 'Film Review Win-Loss';
  els.profileDrEloLabel.textContent = 'Division Rivalry ELO';
  els.profileDrRecordLabel.textContent = 'Division Rivalry Record';
  els.profileTopStruckLabel.textContent = `Teams Most ${sportTerms.out}`;
  els.profileLeaderboardCard.hidden = selectedSport !== 'baseball';
  if (profile.authenticated) {
    els.accountLoggedOut.hidden = true;
    els.accountLoggedIn.hidden = false;
    els.accountSummary.textContent = profile.account?.email
      ? `${profile.account.username} - ${profile.account.email}`
      : `${profile.account?.username || ""}`;
    els.accountStatus.textContent = 'Account connected.';
  } else {
    els.accountLoggedOut.hidden = false;
    els.accountLoggedIn.hidden = true;
    els.accountSummary.textContent = '';
    els.accountStatus.textContent = 'Create an account or log in to carry your profile across browsers and devices.';
  }
  els.deleteAccountCard.hidden = !profile.authenticated;
  if (!profile.authenticated) {
    els.deleteAccountStatus.textContent = '';
    els.deleteAccountPasswordInput.value = '';
  }
}

async function bootstrapProfile() {
  profile = await api('/api/profile/bootstrap', { guest_id: storedGuestId() });
  if (profile?.guest_id) saveGuestId(profile.guest_id);
  renderProfile();
  refreshBpLeaderboard();
  startFriendsPolling();
  refreshFriends();
  if (currentMode === 'fr' && frGame) loadFrArchive();
}

async function refreshBpLeaderboard() {
  if (!els.bpLeaderboard) return;
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
  const promptedEmail = window.prompt('Email for password recovery (optional for now, but recommended):', '') || '';
  const email = promptedEmail.trim();
  const password = els.accountPasswordInput.value;
  const display_name = els.profileNameInput.value.trim() || profile.display_name || username;
  els.accountRegisterBtn.disabled = true;
  const next = await api('/api/account/register', {
    guest_id: profile.guest_id,
    username,
    email,
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
  els.accountStatus.textContent = 'Account created and signed in.';
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
  await api('/api/account/logout');
  clearInterval(friendsPollInterval);
  window.localStorage.removeItem(GUEST_ID_KEY);
  profile = null;
  friendsData = null;
  renderProfile();
  await bootstrapProfile();
}

async function resetPassword() {
  const identifier = els.accountUsernameInput.value.trim();
  if (!identifier) {
    els.accountStatus.textContent = 'Enter your username or email first.';
    return;
  }
  els.accountResetBtn.disabled = true;
  const res = await api('/api/account/reset_password', { identifier });
  els.accountResetBtn.disabled = false;
  els.accountStatus.textContent = res?.error || 'Password reset email sent if the account exists.';
}

async function deleteAccount() {
  if (!profile?.authenticated || !profile?.guest_id) return;
  const password = els.deleteAccountPasswordInput.value;
  if (!password) {
    els.deleteAccountStatus.textContent = 'Enter your password to delete the account.';
    return;
  }
  const confirmed = window.confirm('Delete this account and all linked profile data? This cannot be undone.');
  if (!confirmed) return;
  els.deleteAccountBtn.disabled = true;
  const res = await api('/api/account/delete', {
    password,
  });
  els.deleteAccountBtn.disabled = false;
  if (res?.error) {
    els.deleteAccountStatus.textContent = res.error;
    return;
  }
  els.deleteAccountStatus.textContent = '';
  els.deleteAccountPasswordInput.value = '';
  await logoutAccount();
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
  if (!els.friendsStatus) return;
  if (!profile?.authenticated) {
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
  if (!els.friendsStatus) return;
  if (!profile?.authenticated) {
    friendsData = null;
    renderFriends();
    return;
  }
  const next = await api('/api/friends/list');
  if (next?.error) {
    els.friendsStatus.textContent = next.error;
    return;
  }
  friendsData = next;
  renderFriends();
  if (friendsData.matched_game && !isOnlineMode()) {
    await enterMatchedGame(friendsData.matched_game);
  }
}

function startFriendsPolling() {
  clearInterval(friendsPollInterval);
  if (!profile?.authenticated) return;
  friendsPollInterval = setInterval(refreshFriends, 3000);
}

async function openFriends() {
  showScreen('friends');
  await refreshFriends();
  startFriendsPolling();
}

async function sendFriendRequest() {
  if (!profile?.authenticated) {
    els.friendsStatus.textContent = 'You need an account to add friends.';
    return;
  }
  const target = els.friendTargetInput.value.trim();
  if (!target) return;
  const next = await api('/api/friends/request', { target });
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
  const endpoint = LOCAL_SOLO_SPORTS.has(CURRENT_SPORT)
    ? (USE_LOCAL_CROSS_SPORTS ? '/api/local/' : '/api/sports/') + CURRENT_SPORT + '/autocomplete'
    : '/api/autocomplete';
  const r = await fetch(endpoint + '?q=' + encodeURIComponent(q));
  return r.json();
}

async function getTeamAutocomplete(q) {
  const endpoint = filmReviewPath('team_autocomplete');
  const r = await fetch(endpoint + '?q=' + encodeURIComponent(q));
  return r.json();
}

function showScreen(name) {
  els.homeScreen.hidden = name !== 'home';
  els.profileScreen.hidden = name !== 'profile';
  els.friendsScreen.hidden = name !== 'friends';
  els.frSetupScreen.hidden = name !== 'fr-setup';
  els.startScreen.hidden = name !== 'mp-setup';
  els.gameScreen.hidden = !(name === 'mp-game' || name === 'bp-game' || name === 'po-game');
  els.frScreen.hidden = name !== 'fr-game';

  els.brandSubtitle.textContent = '';

  els.exitBtn.hidden = name === 'home' && !CURRENT_SPORT;
  els.referenceBtn.hidden = currentMode !== 'po' || !(name === 'mp-setup' || name === 'po-game') ||
    (usesLocalPlayoffs() && name === 'mp-setup');
  const togglesRelevant = name === 'mp-game' || name === 'bp-game' || name === 'po-game';
  els.headerToggles.hidden = !togglesRelevant;
  els.lineupSection.hidden = !togglesRelevant || !els.toggleLineup.checked;
  els.outSection.hidden = !togglesRelevant || !els.toggleOut.checked;
}

function clearModePanels() {
  els.winPanel.hidden = true;
  els.powerupPanel.hidden = true;
  els.winPanel.style.display = 'none';
  els.powerupPanel.style.display = 'none';
}

function exitToHome() {
  if (currentMode === 'home' && CURRENT_SPORT) {
    window.location.assign('/');
    return;
  }
  goHome();
}

function clearRequeueRelaxTimeout() {
  if (mpRequeueRelaxTimeout) {
    clearTimeout(mpRequeueRelaxTimeout);
    mpRequeueRelaxTimeout = null;
  }
}

async function goHome() {
  const wasWaiting = !els.startScreen.hidden && !els.cancelMatchBtn.hidden;
  const activeMpGameId = isOnlineMode() && game?.game_id ? game.game_id : '';
  const finishedMpGameId = isOnlineMode() && game?.finished ? game.game_id : '';
  clearRequeueRelaxTimeout();
  if (wasWaiting) {
    await api(onlineApiBase() + '/cancel_queue', { guest_id: profile?.guest_id || storedGuestId() });
    await api(onlineApiBase() + '/cancel_challenge', { guest_id: profile?.guest_id || storedGuestId() });
  } else if (activeMpGameId) {
    if (finishedMpGameId) {
      await api(onlineApiBase() + '/postgame_leave', {
        guest_id: profile?.guest_id || storedGuestId(),
        game_id: finishedMpGameId,
      });
    } else {
      await api(onlineApiBase() + '/leave_game', {
        guest_id: profile?.guest_id || storedGuestId(),
        game_id: activeMpGameId,
      });
    }
  }
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
  showScreen('home');
}

function openProfile() {
  clearInterval(friendsPollInterval);
  renderProfile();
  showScreen('profile');
}

function pickMode(mode) {
  clearInterval(friendsPollInterval);
  if (mode === 'mp' || mode === 'po') {
    currentMode = mode;
    showScreen('mp-setup');
    els.matchModeTitle.textContent = onlineModeName(mode);
    els.mpStatusText.textContent = `Queue as ${currentHandle()}.`;
    els.startBtn.hidden = false;
    els.cancelMatchBtn.hidden = true;
    els.challengeStatusText.textContent = '';
    // Cross-sport random matchmaking is persistent. Friend/code challenges
    // remain on the established baseball contract until their sport field is
    // added to the invitations and history tables.
    els.challengeBox.hidden = (mode === 'mp' || mode === 'po') && isCrossSport();
    els.playoffConditionPicker.hidden = mode !== 'po';
    if (mode === 'po') {
      if (isCrossSport()) configureLocalPlayoffPicker();
      else els.playoffConditionSelect.value = profile?.playoff_win_condition_preference || 'random';
    }
    return;
  }
  if (mode === 'bp') {
    startBp();
    return;
  }
  if (mode === 'fr') {
    if (CURRENT_SPORT === 'football') {
      currentMode = 'fr';
      showScreen('fr-setup');
      return;
    }
    startFr();
  }
}

function startMpPolling() {
  clearInterval(mpPollInterval);
  if (!isOnlineMode() || !game || game.finished) return;
  mpPollInterval = setInterval(async () => {
    if (!game?.game_id || mpPollInFlight || moveSubmissionInFlight) return;
    mpPollInFlight = true;
    const next = await api(onlineApiBase() + '/game', {
      guest_id: profile?.guest_id || storedGuestId(),
      game_id: game.game_id,
    });
    mpPollInFlight = false;
    if (!next.error && game?.game_id === next.game_id) {
      // A move response can arrive while this request is in flight. Never let
      // the older poll remove a just-played card or rewind a turn.
      const previousGame = game;
      const prevChain = previousGame?.chain?.length || 0;
      const nextChain = next.chain?.length || 0;
      if (nextChain < prevChain) return;
      const needsRender = nextChain !== prevChain ||
        next.turn_index !== previousGame.turn_index ||
        next.finished !== previousGame.finished ||
        next.last_move?.player_id !== previousGame.last_move?.player_id ||
        next.last_move?.outcome !== previousGame.last_move?.outcome;
      game = next;
      lastChainLength = prevChain;
      animateNewestCard = nextChain > prevChain;
      if (needsRender) renderMpGame();
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
  clearRequeueRelaxTimeout();
  currentMode = nextGame.mode || currentMode || 'mp';
  lastChainLength = 0;
  clearInterval(mpRematchPollInterval);
  hideGameOverBanner();
  showScreen(currentMode === 'po' ? 'po-game' : 'mp-game');
  clearInterval(mpQueuePollInterval);
  els.startBtn.hidden = false;
  els.cancelMatchBtn.hidden = true;
  els.challengeStatusText.textContent = '';
  game = nextGame;
  animateNewestCard = false;
  renderMpGame();
  syncMpClock(null, game, { force: true });
  startMpPolling();
}

async function pollMatchmaking() {
  const status = await api(onlineApiBase() + '/status', { guest_id: profile?.guest_id || storedGuestId() });
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
  clearRequeueRelaxTimeout();
  showScreen('mp-setup');
  els.mpStatusText.textContent = 'Searching for an opponent...';
  els.startBtn.hidden = true;
  els.cancelMatchBtn.hidden = false;
  clearInterval(mpQueuePollInterval);
  const queued = await api(onlineApiBase() + '/queue', {
    guest_id: profile?.guest_id || storedGuestId(),
    display_name: currentHandle(),
    avoid_guest_id: opts.avoidGuestId || '',
    win_condition_preference: currentMode === 'po' ? els.playoffConditionSelect.value : '',
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
  if (opts.allowSameOpponentAfterMs && opts.avoidGuestId) {
    mpRequeueRelaxTimeout = setTimeout(async () => {
      if (!isOnlineMode() || game) return;
      const status = await api(onlineApiBase() + '/status', { guest_id: profile?.guest_id || storedGuestId() });
      if (status.status !== 'waiting') return;
      clearInterval(mpQueuePollInterval);
      await api(onlineApiBase() + '/cancel_queue', { guest_id: profile?.guest_id || storedGuestId() });
      await startMpGame();
    }, opts.allowSameOpponentAfterMs);
  }
}

async function cancelMatchmaking() {
  clearRequeueRelaxTimeout();
  clearInterval(mpQueuePollInterval);
  await api(onlineApiBase() + '/cancel_queue', { guest_id: profile?.guest_id || storedGuestId() });
  await api(onlineApiBase() + '/cancel_challenge', { guest_id: profile?.guest_id || storedGuestId() });
  els.startBtn.hidden = false;
  els.cancelMatchBtn.hidden = true;
  els.mpStatusText.textContent = `Queue as ${currentHandle()}.`;
  els.challengeStatusText.textContent = '';
}

async function createChallengeCode() {
  const res = await api(onlineApiBase() + '/create_challenge', {
    guest_id: profile?.guest_id || storedGuestId(),
    win_condition_preference: currentMode === 'po' ? els.playoffConditionSelect.value : '',
  });
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
  const res = await api(onlineApiBase() + '/join_challenge', {
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
  clearModePanels();
  lastChainLength = 0;
  hideGameOverBanner();
  showScreen('bp-game');
  const localModeName = 'Manager Mode';
  renderLoadingGame(localModeName, 'Loading ' + ({ football: 'snapper', basketball: 'tipoff', hockey: 'faceoff' })[CURRENT_SPORT] + '...');
  game = await api(LOCAL_SOLO_SPORTS.has(CURRENT_SPORT) ? localSoloPath('new') : '/api/bp/new',
    { guest_id: profile?.guest_id || storedGuestId() });
  if (game.error) {
    alert('error: ' + game.error);
    return;
  }
  els.guessInput.value = '';
  animateNewestCard = false;
  renderBpGame();
  runOpeningCountdown();
}

async function startFr(unit = null, options = {}) {
  currentMode = 'fr';
  hideFrSummaryBanner();
  closeTeamAutocomplete();
  els.frTeamInput.value = '';
  els.frYearInput.value = '';
  showScreen('fr-game');
  renderLoadingFilmReview();
  frGame = await api(filmReviewPath('new'),
    { guest_id: profile?.guest_id || storedGuestId(), unit, ...options });
  if (frGame.error) {
    alert('error: ' + frGame.error);
    return;
  }
  renderFrGame(true);
  loadFrArchive();
  els.frTeamInput.focus();
}

function renderLoadingGame(label, prompt) {
  clearModePanels();
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
  els.frLineupBoard.innerHTML = '';
  els.frTeamInput.disabled = true;
  els.frYearInput.disabled = true;
}

async function rematch() {
  if (isOnlineMode()) {
    if (!game) return;
    if (game.last_move?.outcome === 'forfeit') {
      goHome();
      pickMode(currentMode);
      await startMpGame();
      return;
    }
    const res = await api(onlineApiBase() + '/rematch_request', {
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
    els.requeueBtn.hidden = false;
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
  els.requeueBtn.hidden = true;

  if (isOnlineMode()) {
    const teamsOut = game.strikes.filter((s) => s.count >= 3).length;
    const outSummary = ({ baseball: 'struck out', basketball: 'fouled out', football: 'punted', hockey: 'with game misconducts' })[CURRENT_SPORT] || 'out';
    els.winnerText.textContent = game.winner ? `${game.winner} wins!` : 'Game over.';
    if (currentMode === 'po' && game.last_move?.win_condition_completed) {
      els.gameOverSummary.textContent =
        `${game.last_move.win_condition_label} completed. Lineup of ${game.chain.length}. ${teamsOut} team${teamsOut === 1 ? '' : 's'} ${outSummary}.`;
    } else {
      els.gameOverSummary.textContent =
        `Lineup of ${game.chain.length}. ${teamsOut} team${teamsOut === 1 ? '' : 's'} ${outSummary}.`;
    }
    if (game.last_move?.outcome === 'forfeit') {
      els.playAgainBtn.hidden = true;
      els.requeueBtn.hidden = false;
    } else {
      els.playAgainBtn.hidden = false;
      els.playAgainBtn.textContent = ({
        football: 'Kick off again.',
        basketball: 'Run it back.',
        hockey: 'Drop the puck again.',
      })[CURRENT_SPORT] || "Let's play two.";
      els.requeueBtn.hidden = false;
      startRematchPolling();
    }
  } else if (currentMode === 'bp') {
    els.playAgainBtn.hidden = false;
    els.requeueBtn.hidden = true;
    els.winnerText.textContent = `Lineup of ${game.longest_chain - 1}.`;
    els.gameOverSummary.textContent = 'Time expired. Try to beat your longest lineup.';
    els.playAgainBtn.textContent = ({ football: 'Run it back', basketball: 'Shoot again', hockey: 'Skate again' })[CURRENT_SPORT] || 'Take more cuts';
  }
}

function hideGameOverBanner() {
  els.turnCard.hidden = false;
  els.gameOverBanner.hidden = true;
  clearInterval(mpRematchPollInterval);
  els.mpRematchStatus.hidden = true;
  els.mpRematchStatus.textContent = '';
  els.playAgainBtn.hidden = false;
  els.requeueBtn.hidden = true;
}

async function requeueForNewMatch(message, options = {}) {
  clearInterval(mpRematchPollInterval);
  clearRequeueRelaxTimeout();
  const onlineMode = currentMode;
  const finishedGameId = game?.finished ? game.game_id : '';
  const shouldAvoidLastOpponent = !!options.avoidLastOpponent;
  const avoidGuestId = shouldAvoidLastOpponent
    ? (game?.p1_guest_id === (profile?.guest_id || storedGuestId())
      ? game?.p2_guest_id
      : game?.p1_guest_id)
    : '';
  if (finishedGameId) {
    await api(onlineApiBase(onlineMode) + '/postgame_leave', {
      guest_id: profile?.guest_id || storedGuestId(),
      game_id: finishedGameId,
    });
  }
  hideGameOverBanner();
  game = null;
  currentMode = onlineMode;
  showScreen('mp-setup');
  els.matchModeTitle.textContent = onlineModeName(onlineMode);
  els.challengeStatusText.textContent = '';
  els.mpStatusText.textContent = message || 'Searching for a new opponent...';
  await startMpGame({
    avoidGuestId,
    allowSameOpponentAfterMs: shouldAvoidLastOpponent ? 5000 : 0,
  });
}

function resetTurnTimer() {
  clearInterval(timerInterval);
  if (!(isOnlineMode() || currentMode === 'bp')) {
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
  if (!(isOnlineMode() || currentMode === 'bp') || remaining <= 0) {
    els.timer.classList.remove('countdown');
    activeCountdownKey = '';
    setGuessDisabled(isOnlineMode() && !game?.your_turn);
    resetTurnTimer();
    if (!isOnlineMode() || game?.your_turn) els.guessInput.focus();
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
      setGuessDisabled(isOnlineMode() && !game?.your_turn);
      resetTurnTimer();
      if (!isOnlineMode() || game?.your_turn) els.guessInput.focus();
      return;
    }
    els.timer.textContent = String(Math.ceil(left));
  };

  update();
  countdownInterval = setInterval(update, 100);
}

async function onMpTimeout() {
  const timeoutPath = currentMode === 'po'
    ? (usesLocalPlayoffs() ? onlineApiBase() + '/game' : onlineApiBase() + '/timeout')
    : (USE_LOCAL_CROSS_SPORTS ? onlineApiBase() + '/game' : onlineApiBase() + '/timeout');
  game = await api(timeoutPath, {
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
  game = await api(LOCAL_SOLO_SPORTS.has(CURRENT_SPORT) ? localSoloPath(USE_LOCAL_CROSS_SPORTS ? 'move' : 'timeout') : '/api/bp/timeout',
    { game_id: game.game_id });
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
  if (!game || game.finished || moveSubmissionInFlight) return;
  moveSubmissionInFlight = true;
  closeAutocomplete();
  els.guessInput.value = '';
  const path = currentMode === 'bp'
    ? (LOCAL_SOLO_SPORTS.has(CURRENT_SPORT) ? localSoloPath('move') : '/api/bp/move')
    : onlineApiBase() + '/move';
  const previousChainLength = game.chain?.length || 0;
  const nextGame = await api(path, {
    game_id: game.game_id,
    raw,
    player_id,
    guest_id: isOnlineMode() ? (profile?.guest_id || storedGuestId()) : undefined,
  });
  moveSubmissionInFlight = false;
  if (nextGame?.error) {
    els.feedback.innerHTML = `<span class="bad">${escapeHtml(nextGame.error)}</span>`;
    return;
  }
  game = nextGame;
  if (isOnlineMode()) {
    animateNewestCard = (game.chain?.length || 0) > previousChainLength;
    renderMpGame();
    syncMpClock(null, game, { force: true });
    if (game.finished) {
      showGameOverBanner();
      bootstrapProfile();
    }
  } else {
    if (game.last_move?.outcome === 'valid') resetTurnTimer();
    animateNewestCard = (game.chain?.length || 0) > previousChainLength;
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

function powerupClass(key) {
  return POWERUP_UI[key]?.className || 'generic';
}

function powerupIcon(key) {
  return POWERUP_UI[key]?.icon || 'P';
}

function powerupButtonHtml(powerup, disabled) {
  const classes = ['powerup-chip', 'powerup-' + powerupClass(powerup.key)];
  if (powerup.used) classes.push('used');
  return `<button
    type="button"
    class="${classes.join(' ')}"
    data-powerup-key="${escapeHtml(powerup.key)}"
    ${disabled ? 'disabled' : ''}>
      <span class="powerup-icon">${escapeHtml(powerupIcon(powerup.key))}</span>
      <span class="powerup-label">${escapeHtml(powerup.label || powerup.key)}</span>
      <span class="powerup-state">${powerup.used ? 'Used' : 'Ready'}</span>
    </button>`;
}

function powerupPillHtml(powerup) {
  const classes = ['powerup-chip', 'powerup-chip-static', 'powerup-' + powerupClass(powerup.key)];
  if (powerup.used) classes.push('used');
  return `<div class="${classes.join(' ')}">
    <span class="powerup-icon">${escapeHtml(powerupIcon(powerup.key))}</span>
    <span class="powerup-label">${escapeHtml(powerup.label || powerup.key)}</span>
    <span class="powerup-state">${powerup.used ? 'Used' : 'Ready'}</span>
  </div>`;
}

function renderWinPips(progress, target) {
  const pips = [];
  for (let i = 0; i < target; i += 1) {
    pips.push(`<span class="win-pip ${i < progress ? 'filled' : ''}"></span>`);
  }
  return pips.join('');
}

function renderWinConditions() {
  const isPo = currentMode === 'po' && game?.win_conditions;
  els.winPanel.hidden = !isPo;
  els.winPanel.style.display = isPo ? '' : 'none';
  if (!isPo) return;
  const your = game.win_conditions.your_condition;
  const opp = game.win_conditions.opponent_condition;
  els.yourWinName.textContent = your?.label || '--';
  els.yourWinDesc.innerHTML = your
    ? `${your.progress}/${your.target} <em>${escapeHtml(your.description || '')}</em>`
    : '';
  els.yourWinPips.innerHTML = your ? renderWinPips(your.progress, your.target) : '';
  els.oppWinName.textContent = opp?.label || '--';
  els.oppWinDesc.innerHTML = opp
    ? `${opp.progress}/${opp.target} <em>${escapeHtml(opp.description || '')}</em>`
    : '';
  els.oppWinPips.innerHTML = opp ? renderWinPips(opp.progress, opp.target) : '';
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
  const prompt = currentMode === 'po' && game.powerups?.active_turn_powerup
    ? 'Name a player linked to'
    : 'Name a teammate of';
  const promptLabel = document.querySelector('#turn-card .turn-prompt .muted');
  if (promptLabel) promptLabel.textContent = prompt;
  renderWinConditions();
  renderPowerups();

  els.feedback.innerHTML = renderMoveFeedback(game.last_move, game);
  renderCardStack(game.chain, game.strikes, true, animateNewestCard);
  renderLineup(game.chain);
  renderOut(game.strikes);

  els.lineupSection.hidden = !els.toggleLineup.checked;
  els.outSection.hidden = !els.toggleOut.checked;
  lastChainLength = game.chain.length;
  animateNewestCard = false;
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
  ].join('|');
}

function syncMpClock(previousState, nextState, opts = {}) {
  if (!isOnlineMode() || !nextState) return;
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
  // A normal poll arrives about one second after the previous response. Do
  // not restart the visual clock for that expected network drift.
  const drifted = Math.abs(nextTimer - prevTimer) > 2.0;
  if (opts.force || changedTurn || drifted || timerKey(nextState) !== activeTimerKey) {
    resetTurnTimer();
  }
}

function startRematchPolling() {
  clearInterval(mpRematchPollInterval);
  mpRematchPollInterval = setInterval(async () => {
    if (!isOnlineMode() || !game?.game_id) return;
    const res = await api(onlineApiBase() + '/rematch_status', {
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
    if (res.status === 'requeued') {
      if (res.you_requested) {
        els.mpRematchStatus.hidden = false;
        els.mpRematchStatus.textContent = 'Opponent left. Finding a new match...';
        await requeueForNewMatch('Opponent left. Searching for a new opponent...', {
          avoidLastOpponent: true,
        });
      } else {
        els.mpRematchStatus.hidden = false;
        els.mpRematchStatus.textContent = 'Opponent left the game.';
      }
      return;
    }
    if (res.status === 'abandoned') {
      if (res.you_requested) {
        els.mpRematchStatus.hidden = false;
        els.mpRematchStatus.textContent = 'Opponent left. Finding a new match...';
        await requeueForNewMatch('Opponent left. Searching for a new opponent...', {
          avoidLastOpponent: true,
        });
      } else {
        els.mpRematchStatus.hidden = false;
        els.mpRematchStatus.textContent = 'Opponent left the game.';
      }
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
      els.mpRematchStatus.textContent = res.opponent_present
        ? "Let's play two? Waiting on your opponent."
        : 'Opponent left. Finding a new match...';
      if (!res.opponent_present) {
        await requeueForNewMatch('Opponent left. Searching for a new opponent...', {
          avoidLastOpponent: true,
        });
      }
    }
  }, 1000);
}

function renderBpGame() {
  clearModePanels();
  els.turnLabel.textContent = game.mode_name || 'Manager Mode';
  els.currentPlayerName.textContent = game.current_player.name;
  els.timer.title = 'seconds left';
  setGuessDisabled(game.finished || (game.countdown_seconds_remaining || 0) > 0);
  els.guessInput.placeholder = 'Type a name (first or last)...';

  els.feedback.innerHTML = renderMoveFeedback(game.last_move, game);
  renderCardStack(game.chain, game.strikes, true, animateNewestCard);
  renderLineup(game.chain);
  renderOut(game.strikes);

  els.lineupSection.hidden = !els.toggleLineup.checked;
  els.outSection.hidden = !els.toggleOut.checked;
  lastChainLength = game.chain.length;
  animateNewestCard = false;
}

function renderPowerups() {
  const isPo = currentMode === 'po' && game?.powerups;
  els.powerupPanel.hidden = !isPo;
  els.powerupPanel.style.display = isPo ? '' : 'none';
  if (isPo && !els.powerupPanel.dataset.initialized) {
    els.powerupPanel.open = false;
    els.powerupPanel.dataset.initialized = 'true';
  }
  if (!isPo) return;
  const your = game.powerups.your_powerups || [];
  const opp = game.powerups.opponent_powerups || [];
  const buttonsDisabled = !game.your_turn || game.finished || !!game.powerups.turn_powerup_used || (game.countdown_seconds_remaining || 0) > 0;
  els.yourPowerupName.textContent = game.your_turn
    ? (game.powerups.turn_powerup_used ? 'Powerup used this turn' : 'One use each this game')
    : 'Available on your turn';
  els.oppPowerupName.textContent = 'Track what is still live';
  els.yourPowerupDesc.innerHTML = your.length
    ? your.map((powerup) => powerupButtonHtml(powerup, buttonsDisabled || powerup.used)).join('')
    : '<div class="muted small">No powerups assigned.</div>';
  els.oppPowerupDesc.innerHTML = opp.length
    ? opp.map((powerup) => powerupPillHtml(powerup)).join('')
    : '<div class="muted small">No powerups assigned.</div>';
  els.yourPowerupDesc.querySelectorAll('[data-powerup-key]').forEach((btn) => {
    btn.addEventListener('click', () => usePowerup(btn.dataset.powerupKey));
  });
}

async function usePowerup(powerupKey) {
  if (currentMode !== 'po' || !game?.game_id || !powerupKey) return;
  const next = await api(onlineApiBase('po') + '/powerup', {
    guest_id: profile?.guest_id || storedGuestId(),
    game_id: game.game_id,
    powerup_key: powerupKey,
  });
  if (next?.error) {
    els.feedback.innerHTML = `<span class="bad">${escapeHtml(next.error)}</span>`;
    return;
  }
  game = next;
  renderMpGame();
  syncMpClock(null, game, { force: true });
}

function renderCardStack(chain, allStrikes, showStrikes, animateNewest = false) {
  // Do not erase the visible lineup for a transient polling response.
  const reversed = Array.isArray(chain) ? chain.slice().reverse() : [];
  els.cardStack.innerHTML = '';
  reversed.forEach((player, i) => {
    const isSeed = i === reversed.length - 1;
    const playerCard = makePlayerCard(player, isSeed);
    if (animateNewest && i === 0) playerCard.classList.add('slide-in');
    els.cardStack.appendChild(playerCard);
    if (i < reversed.length - 1) {
      const bar = makeConnectionBar(player.shared_with_prev, allStrikes, showStrikes, player.link_meta_with_prev);
      if (animateNewest && i === 0) bar.classList.add('slide-in');
      els.cardStack.appendChild(bar);
    }
  });
}

function makePlayerCard(player, isSeed, options = {}) {
  const showTeams = options.showTeams !== false;
  const playerCard = document.createElement('div');
  playerCard.className = 'player-card' + (isSeed ? ' seed' : '') + (player.win_condition_hit ? ' win-hit' : '');

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
  const position = player.primary_pos ? `<div class="years">${escapeHtml(player.primary_pos)}</div>` : '';
  const seedBadge = isSeed ? `<span class="seed-badge">${({ football: 'snapper', basketball: 'tipoff', hockey: 'faceoff' })[CURRENT_SPORT] || 'leadoff'}</span>` : '';
  info.innerHTML = `
    <h3 class="name">${escapeHtml(player.name)}${seedBadge}</h3>
    <div class="years">${escapeHtml(yrs)}</div>
    ${position}
    ${showTeams ? `<div class="teams-label">Teams</div><ul class="teams">${(player.teams || []).map((t) => `<li>${escapeHtml(t)}</li>`).join('')}</ul>` : ''}`;
  playerCard.appendChild(info);
  return playerCard;
}

function makeConnectionBar(sharedSeasons, allStrikes, showStrikes, linkMeta) {
  const bar = document.createElement('div');
  bar.className = 'connection-bar';
  const seasons = document.createElement('div');
  seasons.className = 'connection-seasons';
  if (linkMeta?.type === 'powerup' && linkMeta?.powerup_label) {
    const badge = document.createElement('span');
    badge.className = `mode-tile-tag powerup-badge powerup-${powerupClass(linkMeta.powerup_key)}`;
    badge.textContent = `${powerupIcon(linkMeta.powerup_key)} ${linkMeta.powerup_label}`;
    seasons.appendChild(badge);
  }
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
  if (m.outcome === 'powerup_activated') {
    return `<span class="ok">${escapeHtml(m.message || `${m.powerup_label} activated.`)}</span>`;
  }

  const name = m.display_name
    ? `${escapeHtml(m.display_name)}${m.disambiguation ? ` <span class="muted-inline">(${escapeHtml(m.disambiguation)})</span>` : ''}`
    : '';
  const ambig = m.ambiguous_count > 1
    ? ` <span class="muted-inline">(auto-picked from ${m.ambiguous_count} matches. Try the dropdown to be specific.)</span>`
    : '';
  const outTerm = ({ baseball: 'STRUCK OUT', basketball: 'FOULED OUT', hockey: 'GAME MISCONDUCT', football: 'PUNTED' })[CURRENT_SPORT] || 'OUT';

  switch (m.outcome) {
    case 'valid': {
      const teams = m.shared_seasons.map((s) => `${s.team_name} ${s.season}`).join(', ');
      const newOut = m.shared_seasons
        .filter((s) => {
          const row = g.strikes.find((x) => x.team_id === s.team_id && x.season === s.season);
          return row && row.count >= 3;
        })
        .map((s) => `${s.team_name} ${s.season}`).join(', ');
      const lead = m.move_via_powerup
        ? `${escapeHtml(m.powerup_label || 'Powerup')}: ${name}${ambig}. Linked through ${escapeHtml(teams)}.`
        : `${name}${ambig}. Teammates on ${escapeHtml(teams)}.`;
      const winNote = m.win_condition_hit
        ? `<br><span class="ok">${escapeHtml(m.win_condition_label)}: ${m.win_condition_progress}/${m.win_condition_target}</span>`
        : '';
      const winFinish = m.win_condition_completed
        ? `<br><span class="burn">${escapeHtml(m.win_condition_label)} completed.</span>`
        : '';
      return `<span class="ok">${lead}</span>` +
        (newOut ? `<br><span class="burn">${outTerm} this move: ${escapeHtml(newOut)}</span>` : '') +
        winNote +
        winFinish;
    }
    case 'unknown_player':
      return '<span class="bad">Unknown player.</span>';
    case 'already_used':
      return `<span class="bad">${name} already used in this lineup.</span>`;
    case 'not_teammate': {
      const prev = g.chain[g.chain.length - 1].name;
      return `<span class="bad">${name}${ambig} was never a teammate of ${escapeHtml(prev)}.</span>`;
    }
    case 'blocked_by_burned': {
      const prev = g.chain[g.chain.length - 1].name;
      const allShared = m.shared_seasons.map((s) => `${s.team_name} ${s.season}`).join(', ');
      const out = m.burned_seasons.map((s) => `${s.team_name} ${s.season}`).join(', ');
      const verb = m.burned_seasons.length === 1 ? 'is' : 'are';
      return `<span class="bad">${name}${ambig} and ${escapeHtml(prev)} were linked on ${escapeHtml(allShared)},<br>` +
        `but ${escapeHtml(out)} ${verb} already ${outTerm.toLowerCase()}. Pick someone else.</span>`;
    }
    case 'powerup_not_eligible':
      return `<span class="bad">${name}${ambig} does not qualify for ${escapeHtml(m.powerup_label || 'that powerup')}. ${escapeHtml(m.reason || '')}</span>`;
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
  frGame = await api(filmReviewPath('guess'), {
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
  const terms = frTerms();
  if (frGame.puzzle_number) {
    const dateLabel = frGame.puzzle_date
      ? new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
        .format(new Date(`${frGame.puzzle_date}T12:00:00`))
      : '';
    els.frTitle.textContent = `Film Review #${frGame.puzzle_number}${dateLabel ? ` · ${dateLabel}` : ''}`;
  } else {
    els.frTitle.textContent = 'Film Review';
  }
  els.frStats.innerHTML =
    `<span class="stat-hit">${s.hits}${terms.hitShort}</span> <span class="stat-sep">|</span> ` +
    `<span class="stat-foul">${s.fouls}${terms.foulShort}</span> <span class="stat-sep">|</span> ` +
    `<span class="stat-strike">${s.strikes}/${s.max_strikes}${terms.strikeShort}</span>`;

  if (frGame.pair_names[0] && frGame.pair_names[1]) {
    els.frPairNames.innerHTML =
      `${escapeHtml(frGame.pair_names[0])}` +
      `<span class="arrow">to</span>` +
      `${escapeHtml(frGame.pair_names[1])}`;
  } else {
    els.frPairNames.innerHTML = '';
  }

  els.frFeedback.innerHTML = renderFrFeedback(frGame.last_guess);
  renderFrLineupBoard();

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
    return `<span class="ok">${frTerms().hit}${escapeHtml(detail)}.</span>`;
  }
  if (g.outcome === 'foul') {
    return `<span class="burn">${frTerms().foul}. One of team or year is right. Try again.</span>`;
  }
  if (g.outcome === 'strike') {
    if (g.converted_from_foul) {
      return `<span class="bad">${frTerms().strike}. Another ${frTerms().foul.toLowerCase()} in the same streak counts as a ${frTerms().strike.toLowerCase()}.</span>`;
    }
    return `<span class="bad">${frTerms().strike}. Neither team nor year is right.</span>`;
  }
  return '';
}

function showFrSummaryBanner() {
  els.frTurnCard.hidden = true;
  els.frSummaryBanner.hidden = false;
  const terms = frTerms();
  if (frGame.won) {
    els.frSummaryText.textContent = "That's the lineup!";
    els.frSummaryDetail.textContent =
      `${frGame.stats.hits} ${terms.hit.toLowerCase()}, ${frGame.stats.fouls} ${terms.foul.toLowerCase()}, ${frGame.stats.strikes} ${terms.strike.toLowerCase()}.`;
  } else {
    els.frSummaryText.textContent = 'Game called.';
    els.frSummaryDetail.textContent =
      `${frGame.stats.hits} ${terms.hit.toLowerCase()} before 3 ${terms.strikePlural}.`;
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
  const res = await api(filmReviewPath('reveal_answer'), { game_id: frGame.game_id });
  if (!res.full_cards || !res.canonical_links) return;
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
  if (!(isOnlineMode() || currentMode === 'bp')) return;
  els.lineupSection.hidden = !els.toggleLineup.checked;
  els.outSection.hidden = !els.toggleOut.checked;
}

function rulesForMode() {
  const outTerm = ({ baseball: 'Struck Out', basketball: 'Fouled Out', hockey: 'Game Misconduct', football: 'Punted' })[CURRENT_SPORT] || 'Out';
  const sportName = ({ baseball: 'baseball', basketball: 'basketball', hockey: 'hockey', football: 'football' })[CURRENT_SPORT] || 'sports';
  if (currentMode === 'bp') {
    return `Build the longest lineup you can. You have 20 seconds to name a teammate of the last player, and a correct guess resets the clock. Each team shared by two linked players gets a strike. Once a team is ${outTerm}, that team cannot be used to link players again. Your run ends when the clock hits zero.`;
  }
  if (currentMode === 'fr') {
    const terms = frTerms();
    return `Review the revealed players and guess the team and year that links each pair. A correct team and year is a ${terms.hit.toLowerCase()} and reveals the next player. One correct field is a ${terms.foul.toLowerCase()}. The first ${terms.foul.toLowerCase()} in a streak is safe, then every ${terms.foul.toLowerCase()} after that in the same streak counts as a ${terms.strike.toLowerCase()}. Three ${terms.strikePlural} end the review.`;
  }
  if (currentMode === 'mp') {
    return `Queue into an online match and take turns building one lineup. After the 3 second countdown, the 20 second clock begins. On your turn, name a teammate of the last player before time runs out. Correct guesses pass the turn and reset the clock. Teams collect strikes when used, and ${outTerm} teams cannot link players again. You win when your opponent runs out of time.`;
  }
  if (currentMode === 'po') {
    return 'Playoffs uses the Division Rivalry rules, but both players also get one use of every powerup during the game and a secret win condition. You can use at most one powerup on a turn. Finish your win condition first, or win on time. Open Reference for the powerups.';
  }
  return `Pick a mode, then build or review a lineup by connecting ${sportName} players through their shared teams.`;
}

async function loadFrArchive() {
  const guestId = profile?.guest_id || storedGuestId();
  if (!guestId) return;
  const res = await api(filmReviewPath('archive'), { guest_id: guestId });
  if (!res.days) return;
  els.frArchiveList.innerHTML = res.days.map((day) => {
    const state = escapeHtml(day.status);
    const date = escapeHtml(day.date);
    const label = day.is_today ? 'Today' : `#${day.number}`;
    const action = day.is_today ? 'daily' : (day.status === 'unseen' ? 'archive' : 'review');
    const text = day.is_today ? (day.status === 'unseen' ? 'Play today' : 'Resume today')
      : (day.status === 'unseen' ? 'Play' : 'Review');
    const retry = !day.is_today && day.status !== 'unseen'
      ? `<button class="fr-archive-action" data-date="${date}" data-action="retry">Retry</button>` : '';
    return `<div class="fr-archive-day ${state}">
      <span class="fr-archive-number">${label}</span>
      <span class="fr-archive-status">${state === 'won' ? 'Completed' : state === 'lost' ? 'Failed' : state === 'in_progress' ? 'In progress' : 'Unseen'}</span>
      <button class="fr-archive-action" data-date="${date}" data-game-id="${escapeHtml(day.game_id || '')}" data-action="${action}">${text}</button>
      ${retry}
    </div>`;
  }).join('');
  els.frArchiveList.querySelectorAll('[data-action]').forEach((button) => {
    button.addEventListener('click', async () => {
      const action = button.dataset.action;
      if (action === 'daily') {
        await startFr();
      } else if (action === 'archive' || action === 'retry') {
        await startFr(null, { puzzle_date: button.dataset.date, archive: true });
      } else if (action === 'review') {
        const result = await api(filmReviewPath('daily_game'), {
          guest_id: profile?.guest_id || storedGuestId(), game_id: button.dataset.gameId,
        });
        if (result.error) {
          alert('error: ' + result.error);
          return;
        }
        frGame = result;
        hideFrSummaryBanner();
        renderFrGame(true);
        showFrSummaryBanner();
      }
    });
  });
}

function frTeamPlaceholder() {
  return ({
    baseball: 'Team (e.g., Chicago Cubs)',
    basketball: 'Team (e.g., Chicago Bulls)',
    hockey: 'Team (e.g., Chicago Blackhawks)',
    football: 'Team (e.g., Chicago Bears)',
  })[CURRENT_SPORT] || 'Team';
}

function frTerms() {
  return ({
    baseball: { hit: 'HIT', foul: 'FOUL', strike: 'STRIKE', strikePlural: 'strikes', hitShort: 'H', foulShort: 'F', strikeShort: 'K' },
    basketball: { hit: 'BUCKET', foul: 'RIM OUT', strike: 'TURNOVER', strikePlural: 'turnovers', hitShort: 'B', foulShort: 'R', strikeShort: 'T' },
    hockey: { hit: 'GOAL', foul: 'OFFSIDE', strike: 'PENALTY', strikePlural: 'penalties', hitShort: 'G', foulShort: 'O', strikeShort: 'P' },
    football: { hit: 'COMPLETE', foul: 'INCOMPLETE', strike: 'TURNOVER', strikePlural: 'turnovers', hitShort: 'C', foulShort: 'I', strikeShort: 'T' },
  })[frGame?.sport || CURRENT_SPORT || 'baseball'];
}

function frBoardRole(sport, unit, slot, index) {
  if (sport === 'hockey' && slot === 'D') return index < 8 ? 'LD' : 'RD';
  if (sport === 'football' && unit === 'offense') {
    return ({ 6: 'OT', 7: 'OG', 8: 'C', 9: 'OG', 10: 'OT' })[index] || slot;
  }
  if (sport === 'football' && unit === 'defense') {
    return ({ 0: 'EDGE', 1: 'DT', 2: 'DT', 3: 'EDGE', 4: 'OLB', 5: 'MIKE', 6: 'OLB' })[index] || slot;
  }
  return slot;
}

function renderFrLineupBoard() {
  const slots = frGame?.slots || [];
  if (!slots.length) {
    els.frLineupBoard.innerHTML = '';
    return;
  }
  const sport = frGame.sport || CURRENT_SPORT || 'baseball';
  const unit = frGame.unit || 'full';
  const filledCount = Math.max(1, (frGame.revealed_count || 0) - 1);
  const revealed = (frGame.revealed_cards || []).slice(0, filledCount);
  els.frLineupBoard.className = `fr-lineup-board sport-${sport} unit-${unit}`;
  els.frLineupBoard.innerHTML = `
    <div class="fr-board-title">${sport === 'football' ? (unit === 'defense' ? 'Defensive Formation' : 'Offensive Formation') : 'Lineup Board'}</div>
    <div class="fr-board-grid">
      ${slots.map((slot, index) => {
        const player = revealed[index];
        const role = frBoardRole(sport, unit, slot, index);
        return `<div class="fr-board-slot slot-${slot.toLowerCase()} slot-index-${index} ${player ? 'filled' : ''}">
          <span class="fr-board-role">${escapeHtml(role)}</span>
          <span class="fr-board-player">${player ? escapeHtml(player.name) : ''}</span>
        </div>`;
      }).join('')}
    </div>`;
  els.frTeamInput.placeholder = frTeamPlaceholder();
}

function renderPowerupReferenceHtml() {
  if (isCrossSport() && game?.powerups?.your_powerups) {
    const rows = game.powerups.your_powerups;
    return `
      <p class="muted">Each Playoffs game gives both players one use of every powerup. You can activate one powerup on a turn.</p>
      <div class="reference-key">
        ${rows.map((powerup) => `
          <div class="reference-row powerup-${powerupClass(powerup.key)}">
            <div class="reference-chip powerup-chip powerup-chip-static powerup-${powerupClass(powerup.key)}">
              <span class="powerup-icon">${escapeHtml(powerupIcon(powerup.key))}</span>
              <span class="powerup-label">${escapeHtml(powerup.label || powerup.key)}</span>
            </div>
            <div class="reference-copy">
              <div class="reference-name">${escapeHtml(powerup.label || powerup.key)}</div>
              <div class="muted small">${escapeHtml(powerup.description || '')}</div>
            </div>
          </div>
        `).join('')}
      </div>`;
  }
  const names = {
    bubblegum: 'Bubblegum',
    pine_tar: 'Pine Tar',
    bat_donut: 'Bat Donut',
    sunglasses: 'Sunglasses',
    backup_mitt: 'Backup Mitt',
    abs: 'ABS',
    quick_pitch: 'Quick Pitch',
  };
  const baseballRows = [
    ['bubblegum', 'Any batter from the same franchise with a 40+ home run season. Adds 5 seconds.'],
    ['pine_tar', 'Any pitcher from the same franchise with a 200+ strikeout season. Adds 5 seconds.'],
    ['bat_donut', 'Any player from the same franchise with a Silver Slugger. Adds 5 seconds.'],
    ['sunglasses', 'Any player from the same franchise with an All-Star selection. Adds 5 seconds.'],
    ['backup_mitt', 'Any player from the same franchise with a Gold Glove. Adds 5 seconds.'],
    ['abs', 'Adds 15 seconds to your current turn.'],
    ['quick_pitch', 'Cuts your opponent down to 10 seconds on their next turn.'],
  ];
  const sportRows = {
    basketball: [['heat_check', 'A 2,000-point season scorer from the same franchise. Adds 5 seconds.'], ['sixth_man', 'A 7,000-assist player from the same franchise. Adds 5 seconds.'], ['switch', 'A same-position-group player from the same franchise. Adds 5 seconds.'], ['mvp_badge', 'An MVP winner from the same franchise. Adds 5 seconds.'], ['all_star_callup', 'An All-Star from the same franchise. Adds 5 seconds.'], ['timeout', 'Adds 15 seconds to your turn.'], ['full_court_press', 'Your opponent gets 10 seconds next turn.']],
    football: [['trick_play', 'A 20-touchdown scorer from the same franchise. Adds 5 seconds.'], ['iron_man', 'A 100-game veteran from the same franchise. Adds 5 seconds.'], ['package_change', 'A same-unit player from the same franchise. Adds 5 seconds.'], ['mvp_badge', 'An MVP winner from the same franchise. Adds 5 seconds.'], ['pro_bowl_callup', 'A Pro Bowl player from the same franchise. Adds 5 seconds.'], ['timeout', 'Adds 15 seconds to your turn.'], ['blitz', 'Your opponent gets 10 seconds next turn.']],
    hockey: [['breakaway', 'A 250-goal scorer from the same franchise. Adds 5 seconds.'], ['veteran_presence', 'A 500-point scorer from the same franchise. Adds 5 seconds.'], ['line_change', 'A same-position-group player from the same franchise. Adds 5 seconds.'], ['hart_honor', 'A Hart Trophy winner from the same franchise. Adds 5 seconds.'], ['all_star_callup', 'An All-Star from the same franchise. Adds 5 seconds.'], ['timeout', 'Adds 15 seconds to your turn.'], ['forecheck', 'Your opponent gets 10 seconds next turn.']],
  };
  const labels = { ...names, heat_check: 'Heat Check', sixth_man: 'Sixth Man', switch: 'Switch', mvp_badge: 'MVP Badge', all_star_callup: 'All-Star Call-Up', timeout: 'Timeout', full_court_press: 'Full-Court Press', trick_play: 'Trick Play', iron_man: 'Iron Man', package_change: 'Package Change', pro_bowl_callup: 'Pro Bowl Call-Up', blitz: 'Blitz', breakaway: 'Breakaway', veteran_presence: 'Veteran Presence', line_change: 'Line Change', hart_honor: 'Hart Honor', forecheck: 'Forecheck' };
  const sport = CURRENT_SPORT || referenceSport;
  const rows = sportRows[sport] || baseballRows;
  const picker = CURRENT_SPORT ? '' : `<label class="muted small" for="reference-sport-select">Sport</label><select id="reference-sport-select"><option value="baseball">Baseball</option><option value="basketball">Basketball</option><option value="football">Football</option><option value="hockey">Hockey</option></select>`;
  return `
    <p class="muted">Each Playoffs game gives both players one use of every powerup. You can activate one powerup on a turn.</p>
    ${picker}
    <div class="reference-key">
      ${rows.map(([key, desc]) => `
        <div class="reference-row powerup-${powerupClass(key)}">
          <div class="reference-chip powerup-chip powerup-chip-static powerup-${powerupClass(key)}">
            <span class="powerup-icon">${escapeHtml(powerupIcon(key))}</span>
            <span class="powerup-label">${escapeHtml(labels[key])}</span>
          </div>
          <div class="reference-copy">
            <div class="reference-name">${escapeHtml(labels[key])}</div>
            <div class="muted small">${escapeHtml(desc)}</div>
          </div>
        </div>
      `).join('')}
    </div>`;
}

function openRules() {
  els.rulesTitle.textContent = 'How to Play';
  els.rulesText.innerHTML = `<p>${escapeHtml(rulesForMode())}</p>`;
  els.rulesModal.hidden = false;
}

function openReference() {
  els.rulesTitle.textContent = 'Powerup Reference';
  els.rulesText.innerHTML = renderPowerupReferenceHtml();
  const selector = document.getElementById('reference-sport-select');
  if (selector) {
    selector.value = referenceSport;
    selector.addEventListener('change', () => { referenceSport = selector.value; openReference(); });
  }
  els.rulesModal.hidden = false;
}

function closeRules() {
  els.rulesModal.hidden = true;
}

document.querySelectorAll('.mode-tile').forEach((tile) => {
  tile.addEventListener('click', () => pickMode(tile.dataset.mode));
});

on(els.exitBtn, 'click', exitToHome);

document.querySelectorAll('[data-back="home"]').forEach((btn) => {
  btn.addEventListener('click', goHome);
});

on(els.startBtn, 'click', startMpGame);
on(els.playoffRandomBtn, 'click', () => {
  els.playoffConditionSelect.value = 'random';
  if (isCrossSport()) {
    window.localStorage.setItem('tt_local_playoff_condition_' + CURRENT_SPORT, 'random');
  }
});
on(els.playoffConditionSelect, 'change', () => {
  if (isCrossSport()) {
    window.localStorage.setItem('tt_local_playoff_condition_' + CURRENT_SPORT, els.playoffConditionSelect.value);
  }
});
on(els.cancelMatchBtn, 'click', cancelMatchmaking);
on(els.createCodeBtn, 'click', createChallengeCode);
on(els.joinCodeBtn, 'click', joinChallengeCode);
on(els.joinCodeInput, 'keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    joinChallengeCode();
  }
});

on(els.guessForm, 'submit', onGuessSubmit);
on(els.guessInput, 'input', onGuessInput);
on(els.guessInput, 'keydown', onGuessKeydown);

on(els.playAgainBtn, 'click', rematch);
on(els.requeueBtn, 'click', () => requeueForNewMatch('Searching for a new opponent...', {
  avoidLastOpponent: true,
}));
on(els.homeFromBannerBtn, 'click', goHome);
on(els.toggleLineup, 'change', applyToggles);
on(els.toggleOut, 'change', applyToggles);

on(els.frGuessForm, 'submit', frSubmit);
on(els.frTeamInput, 'input', onTeamInput);
on(els.frTeamInput, 'keydown', onTeamKeydown);
on(els.frHomeBtn, 'click', goHome);
on(els.frOffenseBtn, 'click', () => startFr('offense'));
on(els.frDefenseBtn, 'click', () => startFr('defense'));

document.addEventListener('click', (e) => {
  if ((els.guessForm?.contains(e.target) || els.autocompleteList?.contains(e.target))) {
    return;
  }
  if ((els.frGuessForm?.contains(e.target) || els.frTeamAutocompleteList?.contains(e.target))) {
    return;
  }
  closeAutocomplete();
  closeTeamAutocomplete();
});

on(els.rulesBtn, 'click', openRules);
on(els.referenceBtn, 'click', openReference);
on(els.rulesClose, 'click', closeRules);
on(els.rulesBackdrop, 'click', closeRules);
on(els.profileSaveBtn, 'click', saveProfileName);
on(els.profileOpenBtn, 'click', openProfile);
on(els.profileSportSelect, 'change', renderProfile);
on(els.friendsOpenBtn, 'click', openFriends);
on(els.accountRegisterBtn, 'click', registerAccount);
on(els.accountLoginBtn, 'click', loginAccount);
on(els.accountResetBtn, 'click', resetPassword);
on(els.accountLogoutBtn, 'click', logoutAccount);
on(els.deleteAccountBtn, 'click', deleteAccount);
on(els.friendRequestBtn, 'click', sendFriendRequest);
on(els.profileNameInput, 'keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    saveProfileName();
  }
});
on(els.accountPasswordInput, 'keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    if (els.accountUsernameInput?.value.trim()) {
      loginAccount();
    }
  }
});
on(els.deleteAccountPasswordInput, 'keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    deleteAccount();
  }
});
on(els.friendTargetInput, 'keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    sendFriendRequest();
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && els.rulesModal && !els.rulesModal.hidden) closeRules();
});

const launchParams = new URLSearchParams(window.location.search);
const launchMode = launchParams.get('mode');
const launchDate = launchParams.get('date');
const launchArchive = launchParams.get('archive') === '1';
const launchGameId = launchParams.get('game_id');
showScreen('home');
renderProfile();
async function handleQueryLaunch() {
  if (launchMode && ['bp', 'fr', 'mp', 'po'].includes(launchMode)) {
    window.history.replaceState({}, '', window.location.pathname);
    if ((launchMode === 'mp' || launchMode === 'po') && launchGameId) {
      currentMode = launchMode;
      try {
        const next = await api(onlineApiBase(launchMode) + '/game', {
          guest_id: profile?.guest_id || storedGuestId(),
          game_id: launchGameId,
        });
        if (next.error) throw new Error(next.error);
        await enterMatchedGame(next);
      } catch (err) {
        alert('Could not open match: ' + err.message);
        pickMode(launchMode);
      }
    } else if (launchMode === 'bp') {
      await startBp();
    } else if (launchMode === 'fr') {
      await startFr(null, launchDate ? { puzzle_date: launchDate, archive: launchArchive } : {});
    } else {
      pickMode(launchMode);
    }
  }
}

bootstrapProfile()
  .catch((err) => console.warn('profile bootstrap failed before launch', err))
  .finally(handleQueryLaunch);

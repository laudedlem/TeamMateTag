const guestKey = 'tt_guest_id';
const hub = document.body.dataset.modeHub;
let hubProfile = null;
let queuePoll = null;
let activeQueueSports = [];

const PLAYOFF_OPTIONS = {
  baseball: [
    ['random', 'Random'], ['sunset_kingdom', 'Sunset Kingdom'], ['havana_heat', 'Havana Heat'],
    ['maple_corridor', 'Maple Corridor'], ['mvp_circle', 'MVP Circle'], ['young_buck', 'Young Buck'],
    ['gonna_be_golden', 'Gonna Be Golden'], ['secretariat', 'Secretariat'], ['hound_dog', 'Hound-dog'],
    ['great_bambinos', 'Great Bambinos'], ['ring_chaser', 'Ring Chaser'], ['journeyman', 'Journeyman'],
  ],
  basketball: [
    ['random', 'Random'], ['bucket_getter', 'Bucket Getter'], ['season_scorer', 'Scoring Run'],
    ['playmaker', 'Table Setter'], ['three_point_club', 'Deep Range'], ['ironhorse', 'Ironhorse'],
    ['one_team', 'Home Court'], ['journeyman', 'Frequent Flyer'], ['mvp_circle', 'MVP Circle'],
    ['all_star_marathon', 'All-Star Marathon'], ['ring_chaser', 'Ring Chaser'], ['young_guns', 'Young Guns'],
  ],
  football: [
    ['random', 'Random'], ['touchdown_club', 'End Zone'], ['season_scorer', 'Season Scorer'],
    ['air_raid', 'Air Raid'], ['single_season_passer', 'Sunday Slingers'], ['sack_master', 'Sack Master'],
    ['ballhawk', 'Ballhawk'], ['one_team', 'One Club'], ['journeyman', 'Journeyman'],
    ['mvp_circle', 'MVP Circle'], ['pro_bowl_marathon', 'Pro Bowl Marathon'], ['ring_chaser', 'Ring Chaser'],
    ['young_guns', 'Fresh Faces'],
  ],
  hockey: [
    ['random', 'Random'], ['sniper', 'Sniper'], ['single_season_sniper', 'Rocket Season'],
    ['playmaker', 'Playmaker'], ['point_streak', 'Point Machine'], ['one_team', 'Lifer'],
    ['journeyman', 'Journeyman'], ['mvp_circle', 'Hart Club'], ['all_star_marathon', 'All-Star Marathon'],
    ['ironhorse', 'Ironhorse'], ['ring_chaser', 'Cup Chasers'], ['young_guns', 'Fresh Ice'],
  ],
};

const MODE_RULES = {
  manager: `<h3>Manager Mode</h3>
    <p>Manager Mode is the solo endless lineup mode. You start with the daily starter already on the board. After a short countdown, the 20-second clock begins.</p>
    <p><strong>Your action:</strong> type a player name, use autocomplete when it helps, then submit a player who was teammates with the current top player. A teammate link means both players appeared for the same franchise in the same season.</p>
    <p><strong>Team marks:</strong> every team-season used as a link receives a mark. At three marks, that team is maxed out and cannot be used as a link again. If your guessed player shares any maxed-out team with the top player, the guess is blocked, even if they also share another open team.</p>
    <p><strong>Ending:</strong> invalid guesses do not end the run, but the clock keeps moving. Each valid teammate adds to the lineup and resets the clock. When time expires, your score is the full lineup length, including the starter.</p>`,
  film: `<h3>Film Review</h3>
    <p>Film Review is the daily puzzle mode. You are given a fixed lineup and must identify the team and year connecting each pair of players. Each daily tape is preserved in the archive forever, so Film Review #1 will always be the same puzzle.</p>
    <p><strong>Your action:</strong> enter the team and the season for the visible pair. The next player is only added to the lineup board after the connection is correct. Football has separate Offense and Defense tapes.</p>
    <p><strong>Feedback:</strong> a fully correct team and year advances the tape. If only one field is correct, that is a partial answer. The first partial answer in a streak is safe, then every additional partial answer in that same streak counts against you. A completely wrong answer also counts against you.</p>
    <p><strong>Ending:</strong> three misses benches the review. Solving every connection is Fully Scouted. Today's first attempt controls your streak; archived attempts let you continue, review, or retry old tapes without changing the daily streak.</p>`,
  division: `<h3>Division Rivalry</h3>
    <p>Division Rivalry is the basic online head-to-head lineup battle. Queue for one sport, or use the multi-sport queue to enter whichever selected sport finds an opponent first.</p>
    <p><strong>Game flow:</strong> one player is randomly chosen to go first. After the countdown, players alternate turns. On your turn, you have 20 seconds to name a teammate of the current top player. A valid answer adds that player to the shared lineup, passes the turn, and resets the clock.</p>
    <p><strong>Team marks:</strong> the same maxed-team rule from Manager Mode applies. Baseball teams get Struck Out, Basketball teams Fouled Out, Football teams Punted, and Hockey teams receive Game Misconducts. Once a team is maxed out, it cannot be used to link future players.</p>
    <p><strong>Winning:</strong> win by making your opponent run out of time, or when your opponent exits an active match. After a completed game, both players can request a rematch or find a new match.</p>`,
  playoffs: `<h3>Playoffs</h3>
    <p>Playoffs is the advanced online head-to-head mode. It uses the same teammate-link, turn timer, and maxed-team rules as Division Rivalry, then adds powerups and personal win conditions.</p>
    <p><strong>Before queueing:</strong> choose a preferred win condition for each sport, or choose Random. Your selected condition becomes your default for the next queue.</p>
    <p><strong>Powerups:</strong> each player gets one use of every powerup. Some powerups let you play a same-franchise player who is not a direct teammate, as long as that player qualifies for the powerup. Team marks still apply to powerup links. Other powerups add time to your turn or reduce the opponent's next turn.</p>
    <p><strong>Winning:</strong> win on the clock like Division Rivalry, or immediately win by completing your win condition first. Progress pips show how close each player is, and qualifying players are highlighted on the lineup.</p>`,
};

const POWERUP_REFERENCE = {
  baseball: [['Bubblegum', 'Use a same-franchise 40+ home run season batter. +5 seconds.'], ['Pine Tar', 'Use a same-franchise 200+ strikeout season pitcher. +5 seconds.'], ['Bat Donut', 'Use a same-franchise Silver Slugger winner. +5 seconds.'], ['Sunglasses', 'Use a same-franchise All-Star. +5 seconds.'], ['Backup Mitt', 'Use a same-franchise Gold Glove winner. +5 seconds.'], ['ABS', 'Add 15 seconds to your turn.'], ['Quick Pitch', 'Opponent gets 10 seconds next turn.']],
  basketball: [['Heat Check', 'Use a same-franchise 2,000-point season scorer. +5 seconds.'], ['Sixth Man', 'Use a same-franchise 7,000-assist player. +5 seconds.'], ['Switch', 'Use a same-position-group player from the same franchise. +5 seconds.'], ['MVP Badge', 'Use a same-franchise MVP winner. +5 seconds.'], ['All-Star Call-Up', 'Use a same-franchise All-Star. +5 seconds.'], ['Timeout', 'Add 15 seconds to your turn.'], ['Full-Court Press', 'Opponent gets 10 seconds next turn.']],
  football: [['Trick Play', 'Use a same-franchise 20-touchdown scorer. +5 seconds.'], ['Iron Man', 'Use a same-franchise 100-game veteran. +5 seconds.'], ['Package Change', 'Use a same-unit player from the same franchise. +5 seconds.'], ['MVP Badge', 'Use a same-franchise MVP winner. +5 seconds.'], ['Pro Bowl Call-Up', 'Use a same-franchise Pro Bowl player. +5 seconds.'], ['Timeout', 'Add 15 seconds to your turn.'], ['Blitz', 'Opponent gets 10 seconds next turn.']],
  hockey: [['Breakaway', 'Use a same-franchise 250-goal scorer. +5 seconds.'], ['Veteran Presence', 'Use a same-franchise 500-point scorer. +5 seconds.'], ['Line Change', 'Use a same-position-group player from the same franchise. +5 seconds.'], ['Hart Honor', 'Use a same-franchise Hart Trophy winner. +5 seconds.'], ['All-Star Call-Up', 'Use a same-franchise All-Star. +5 seconds.'], ['Timeout', 'Add 15 seconds to your turn.'], ['Forecheck', 'Opponent gets 10 seconds next turn.']],
};

const CONDITION_REQUIREMENTS = {
  random: 'Randomly choose from available conditions.',
  sunset_kingdom: 'Japanese players.', havana_heat: 'Cuban players.', maple_corridor: 'Canadian players.',
  mvp_circle: 'MVP winners.', young_buck: 'Rookie of the Year winners.', gonna_be_golden: 'Gold Glove winners.',
  secretariat: 'Triple Crown winner.', hound_dog: 'One-franchise lifers.', great_bambinos: '500 career home run player.',
  ring_chaser: 'Combined championships.', journeyman: 'Players with many franchises.',
  bucket_getter: 'Career scoring greats.', season_scorer: 'Peak single-season scorers.', playmaker: 'Career assist/playmaking greats.',
  three_point_club: 'Elite three-point shooters.', ironhorse: 'Durable long-career players.', one_team: 'One-franchise players.',
  all_star_marathon: 'Combined All-Star selections.', young_guns: 'Rookie of the Year winners.', touchdown_club: 'Career touchdown scorers.',
  air_raid: 'Career passing leaders.', single_season_passer: 'Peak passing seasons.', sack_master: 'Career sack leaders.',
  ballhawk: 'Career interception leaders.', pro_bowl_marathon: 'Combined Pro Bowl selections.', sniper: 'Career goal scorers.',
  single_season_sniper: 'Peak goal seasons.', point_streak: 'Career point producers.',
};

async function post(url, body) {
  const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return response.json();
}

async function ensureHubGuestId() {
  const stored = localStorage.getItem(guestKey) || '';
  if (stored) {
    hubProfile = { guest_id: stored };
    post('/api/profile/bootstrap', { guest_id: stored }).then((profile) => {
      if (profile?.guest_id) {
        hubProfile = profile;
        localStorage.setItem(guestKey, profile.guest_id);
      }
    }).catch(() => {});
    return stored;
  }
  hubProfile = await post('/api/profile/bootstrap', { guest_id: '' });
  if (!hubProfile?.guest_id) return '';
  localStorage.setItem(guestKey, hubProfile.guest_id);
  return hubProfile.guest_id;
}

async function initHub() {
  const guestId = await ensureHubGuestId();
  if (!guestId) return;
  if (hub === 'manager') {
    const tiles = await post('/api/manager/tiles', { guest_id: guestId });
    renderManagerTiles(tiles);
    post('/api/manager/summary', { guest_id: guestId }).then(renderManagerSummary).catch(() => {});
  }
  if (hub === 'film') {
    const summary = await post('/api/film/archive_summary', { guest_id: guestId });
    Object.entries(summary.sports || {}).forEach(([sport, data]) => renderFilmReviewHubSport(sport, data || {}));
  }
  configureSharedQueue();
}
initHub();

function displayStatus(status) {
  const normalized = String(status || 'unseen');
  if (normalized === 'won') return 'Fully Scouted';
  if (normalized === 'lost') return 'Benched';
  if (normalized === 'new') return 'New';
  if (normalized === 'in_progress') return 'In Progress';
  return 'Unseen';
}

function formatArchiveLabel(day) {
  const dateText = day.date ? new Date(day.date + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: '2-digit' }).replace(', 20', ", '") : '';
  const status = displayStatus(day.is_today && day.status === 'unseen' ? 'new' : day.status);
  const prefix = day.is_today ? 'Today' : `#${day.number}`;
  const pct = Number(day.success_rate?.percent || 0);
  return `${prefix}${dateText ? ' - ' + dateText : ''} - ${status} - ${pct}%`;
}

function filmReviewUrl(sport, day) {
  const routeSport = String(sport || '').split(':')[0];
  const unit = day.unit ? `unit=${encodeURIComponent(day.unit)}` : '';
  const archive = day.is_today ? '' : `date=${encodeURIComponent(day.date)}&archive=1`;
  const query = [unit, archive].filter(Boolean).join('&');
  return `/film/${routeSport}${query ? '?' + query : ''}`;
}

function unitLabel(unit) {
  if (unit === 'offense') return 'Offense';
  if (unit === 'defense') return 'Defense';
  return '';
}

function renderFilmReviewArchiveSelect(sport, days, unitData = null) {
  const select = document.querySelector(`[data-fr-archive-select="${sport}"]`);
  const status = document.querySelector(`[data-fr-today-status="${sport}"]`);
  if (!select) return;
  const current = days.find((day) => day.is_today) || days[0];
  if (status && current) {
    const streak = Number(unitData?.streak || 0);
    const rate = unitData?.success_rate || { percent: 0 };
    const display = current.is_today && current.status === 'unseen' ? 'new' : current.status || 'unseen';
    status.innerHTML = `Streak ${streak}<br>${Number(rate.percent || 0)}% Fully Scouted<br>${displayStatus(display)}`;
    status.className = `fr-today-status ${display}`;
  }
  const sorted = [...days].sort((a, b) => {
    if (a.is_today) return -1;
    if (b.is_today) return 1;
    return Number(b.number || 0) - Number(a.number || 0);
  });
  select.innerHTML = '<option value="">Select Archived Tape...</option>' + sorted.map((day) => {
    const optionStatus = day.is_today && day.status === 'unseen' ? 'new' : day.status || 'unseen';
    return `<option value="${filmReviewUrl(sport, day)}" class="fr-option-${optionStatus}">${unitLabel(day.unit)}${day.unit ? ' - ' : ''}${formatArchiveLabel(day)}</option>`;
  }).join('');
  select.value = '';
  select.addEventListener('change', () => {
    if (select.value) window.location.href = select.value;
  });
}

function renderFilmPreview(sport, data) {
  const tile = document.querySelector(`[data-film-unit-tile="${sport}"]`) ||
    document.querySelector(`[data-sport="${sport}"]`);
  if (!tile) return;
  tile.querySelector('.film-hub-meta')?.remove();
  const preview = data.preview || [];
  const compact = preview.some((player) => String(player.name || '').length > 16);
  const meta = document.createElement('span');
  meta.className = `film-hub-meta ${compact ? 'compact-names' : ''}`;
  meta.innerHTML = `<span class="film-preview-pair">${preview.map((player) => `
      <span class="film-preview-player">
        <span class="film-preview-photo">${player.headshot_url ? `<img src="${escapeHtml(player.headshot_url)}" alt="">` : ''}</span>
        <small>${escapeHtml(player.name || 'Unknown')}</small>
      </span>`).join('')}</span>`;
  tile.appendChild(meta);
}

function renderFilmReviewHubSport(sport, data) {
  if (sport === 'football') {
    ['offense', 'defense'].forEach((unit) => {
      const unitData = data.today?.[unit] || {};
      const unitDays = (data.days || []).filter((day) => day.unit === unit);
      renderFilmReviewArchiveSelect(`football:${unit}`, unitDays, unitData);
      renderFilmPreview(`football:${unit}`, unitData);
    });
    return;
  }
  const unitData = data.today?.default || {};
  renderFilmReviewArchiveSelect(sport, data.days || [], unitData);
  renderFilmPreview(sport, unitData);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}

function managerScore(run, emptyText = 'No lineup yet') {
  if (!run) return `<strong>--</strong><span>${emptyText}</span>`;
  const starter = run.starter?.name ? `Starter: ${escapeHtml(run.starter.name)}` : '';
  return `<strong>${run.chain_length}</strong><span>${starter}</span>`;
}

function renderManagerCard(title, run, emptyText) {
  return `<div class="manager-board-card">
    <span>${escapeHtml(title)}</span>
    ${managerScore(run, emptyText)}
    ${run?.display_name ? `<small>${escapeHtml(run.display_name)}</small>` : ''}
  </div>`;
}

function renderManagerLeaderboard(summary, sport) {
  const data = summary?.sports?.[sport] || {};
  const grid = document.getElementById('manager-leaderboard-grid');
  const records = document.getElementById('manager-records-select');
  if (!grid || !records) return;
  grid.innerHTML = [
    renderManagerCard('Your All-Time Best', data.own_all_time, 'No finished run'),
    renderManagerCard('Your Best Today', data.own_today, 'No run today'),
    renderManagerCard('Global All-Time Best', data.global_all_time, 'No global run'),
    renderManagerCard('Global Best Today', data.global_today, 'No global run today'),
  ].join('');
  const rows = data.records || [];
  records.innerHTML = rows.length
    ? rows.map((row) => {
      const starter = row.starter?.name ? ` - ${row.starter.name}` : '';
      return `<option>${escapeHtml(row.date || '')} - ${escapeHtml(row.display_name || 'Guest')} - ${row.chain_length}${escapeHtml(starter)}</option>`;
    }).join('')
    : '<option>No daily records yet</option>';
}

function renderManagerSummary(summary) {
  const sports = summary?.sports || {};
  renderManagerTiles(summary);
  const picker = document.getElementById('manager-leaderboard-sport');
  if (picker) {
    const saved = localStorage.getItem('tt_manager_board_sport') || 'baseball';
    picker.value = sports[saved] ? saved : 'baseball';
    renderManagerLeaderboard(summary, picker.value);
    if (!picker.dataset.bound) {
      picker.dataset.bound = 'true';
      picker.addEventListener('change', () => {
        localStorage.setItem('tt_manager_board_sport', picker.value);
        renderManagerLeaderboard(summary, picker.value);
      });
    }
  }
}

function renderManagerTiles(summary) {
  const sports = summary?.sports || {};
  Object.entries(sports).forEach(([sport, data]) => {
    const bestTarget = document.querySelector(`[data-manager-best="${sport}"]`);
    if (bestTarget) bestTarget.textContent = `Longest lineup: ${data.own_best ?? data.own_all_time?.chain_length ?? 0}`;
    const starterTarget = document.querySelector(`[data-manager-starter="${sport}"]`);
    if (starterTarget) {
      const starter = data.starter || {};
      const photo = starter.headshot_url
        ? `<img src="${escapeHtml(starter.headshot_url)}" alt="">`
        : '';
      starterTarget.innerHTML = `<span class="manager-starter-photo ${photo ? '' : 'placeholder'}">${photo}</span>
        <span><small>Today's starter</small><strong>${escapeHtml(starter.name || 'Unknown')}</strong></span>`;
    }
  });
}

function selectedSports() {
  return [...document.querySelectorAll('.shared-sport-option input[type="checkbox"]:checked')]
    .map((input) => input.value);
}

function playoffPreferences() {
  const prefs = {};
  document.querySelectorAll('[data-condition-sport]').forEach((select) => {
    prefs[select.dataset.conditionSport] = select.value || 'random';
    localStorage.setItem('tt_hub_playoff_condition_' + select.dataset.conditionSport, prefs[select.dataset.conditionSport]);
  });
  return prefs;
}

function playoffPreferenceForSport(sport) {
  const select = document.querySelector(`[data-condition-sport="${sport}"]`);
  const value = select?.value || 'random';
  localStorage.setItem('tt_hub_playoff_condition_' + sport, value);
  return { [sport]: value };
}

function setQueueUi(searching, text) {
  const start = document.getElementById('shared-queue-btn');
  const cancel = document.getElementById('shared-cancel-btn');
  const status = document.getElementById('shared-queue-status');
  if (!start || !cancel || !status) return;
  start.disabled = searching;
  cancel.hidden = !searching;
  status.textContent = text || '';
}

function queueEndpoint(suffix) {
  return `/api/modes/${hub}/${suffix}`;
}

async function handleQueueResponse(response) {
  if (response.error) {
    setQueueUi(false, response.error);
    return;
  }
  if (response.status === 'matched' && response.redirect) {
    window.location.href = response.redirect;
    return;
  }
  setQueueUi(true, 'Searching selected sports...');
}

async function queueForSports(sports, preferences = {}) {
  clearInterval(queuePoll);
  activeQueueSports = [...sports];
  const response = await post(queueEndpoint('queue'), {
    guest_id: hubProfile?.guest_id || localStorage.getItem(guestKey) || '',
    sports,
    preferences,
  });
  await handleQueueResponse(response);
  if (response.status !== 'matched' && !response.error) {
    queuePoll = setInterval(pollSharedQueue, 1000);
  }
}

async function pollSharedQueue() {
  const response = await post(queueEndpoint('status'), {
    guest_id: hubProfile?.guest_id || localStorage.getItem(guestKey) || '',
    sports: activeQueueSports.length ? activeQueueSports : selectedSports(),
  });
  if (response.status === 'matched' && response.redirect) {
    window.location.href = response.redirect;
    return;
  }
  if (response.status === 'idle') {
    clearInterval(queuePoll);
    queuePoll = null;
    activeQueueSports = [];
    setQueueUi(false, 'Queue stopped.');
  }
}

function configureSharedQueue() {
  const panel = document.getElementById('shared-queue-panel');
  if (!panel) return;
  document.querySelectorAll('[data-condition-sport]').forEach((select) => {
    const sport = select.dataset.conditionSport;
    const options = PLAYOFF_OPTIONS[sport] || [['random', 'Random']];
    select.innerHTML = options.map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
    select.value = localStorage.getItem('tt_hub_playoff_condition_' + sport) || 'random';
  });
  document.getElementById('shared-queue-btn')?.addEventListener('click', async () => {
    const sports = selectedSports();
    if (!sports.length) {
      setQueueUi(false, 'Choose at least one sport.');
      return;
    }
    await queueForSports(sports, hub === 'playoffs' ? playoffPreferences() : {});
  });
  document.getElementById('shared-cancel-btn')?.addEventListener('click', async () => {
    clearInterval(queuePoll);
    queuePoll = null;
    activeQueueSports = [];
    await post(queueEndpoint('cancel_queue'), {
      guest_id: hubProfile?.guest_id || localStorage.getItem(guestKey) || '',
    });
    setQueueUi(false, 'Queue canceled.');
  });
}

function hubModal(title, html) {
  const modal = document.getElementById('hub-modal');
  const titleEl = document.getElementById('hub-modal-title');
  const textEl = document.getElementById('hub-modal-text');
  if (!modal || !titleEl || !textEl) return;
  titleEl.textContent = title;
  textEl.innerHTML = html;
  modal.hidden = false;
}

function closeHubModal() {
  const modal = document.getElementById('hub-modal');
  if (modal) modal.hidden = true;
}

function allModeRulesHtml() {
  if (hub && MODE_RULES[hub]) return MODE_RULES[hub];
  return ['manager', 'film', 'division', 'playoffs'].map((key) => MODE_RULES[key]).join('');
}

function conditionsHtml(sport = null) {
  const sports = sport ? [sport] : ['baseball', 'basketball', 'football', 'hockey'];
  return sports.map((sportKey) => {
    const rows = PLAYOFF_OPTIONS[sportKey] || [];
    return `<h3>${escapeHtml(sportKey[0].toUpperCase() + sportKey.slice(1))}</h3><div class="reference-key">${rows.map(([key, label]) =>
      `<div class="reference-row"><div class="reference-name">${escapeHtml(label)}</div><div class="muted small">${escapeHtml(CONDITION_REQUIREMENTS[key] || 'Complete the listed stat goal before your opponent.')}</div></div>`
    ).join('')}</div>`;
  }).join('');
}

function powerupsHtml(sport = null) {
  const sports = sport ? [sport] : ['baseball', 'basketball', 'football', 'hockey'];
  return sports.map((sportKey) => {
    const rows = POWERUP_REFERENCE[sportKey] || [];
    return `<h3>${escapeHtml(sportKey[0].toUpperCase() + sportKey.slice(1))}</h3><div class="reference-key">${rows.map(([label, desc]) =>
      `<div class="reference-row"><div class="reference-name">${escapeHtml(label)}</div><div class="muted small">${escapeHtml(desc)}</div></div>`
    ).join('')}</div>`;
  }).join('');
}

document.getElementById('hub-exit-btn')?.addEventListener('click', () => {
  window.location.href = '/';
});
document.getElementById('hub-rules-btn')?.addEventListener('click', () => {
  hubModal('How to Play', allModeRulesHtml());
});
document.getElementById('hub-conditions-btn')?.addEventListener('click', () => {
  hubModal('Win Conditions', conditionsHtml());
});
document.getElementById('hub-powerups-btn')?.addEventListener('click', () => {
  hubModal('Powerups', powerupsHtml());
});
document.getElementById('hub-modal-close')?.addEventListener('click', closeHubModal);
document.getElementById('hub-modal-backdrop')?.addEventListener('click', closeHubModal);

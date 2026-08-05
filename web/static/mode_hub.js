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
  if (normalized === 'won') return 'completed';
  if (normalized === 'lost') return 'failed';
  return normalized.replace(/_/g, ' ');
}

function formatArchiveLabel(day) {
  const dateText = day.date ? new Date(day.date + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: '2-digit' }).replace(', 20', ", '") : '';
  const status = displayStatus(day.status);
  const prefix = day.is_today ? 'Today' : `#${day.number}`;
  return `${prefix}${dateText ? ' - ' + dateText : ''} - ${status}`;
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
    status.innerHTML = `Streak ${streak}<br>${Number(rate.percent || 0)}% solved today`;
    status.className = `fr-today-status ${current.status || 'unseen'}`;
  }
  const sorted = [...days].sort((a, b) => {
    if (a.is_today) return -1;
    if (b.is_today) return 1;
    return Number(b.number || 0) - Number(a.number || 0);
  });
  select.innerHTML = '<option value="">Select Archived Tape...</option>' + sorted.map((day) =>
    `<option value="${filmReviewUrl(sport, day)}" class="fr-option-${day.is_today ? 'today' : day.status || 'unseen'}">${unitLabel(day.unit)}${day.unit ? ' - ' : ''}${formatArchiveLabel(day)}</option>`
  ).join('');
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
  document.querySelectorAll('[data-direct-queue-sport]').forEach((tile) => {
    tile.addEventListener('click', async (event) => {
      event.preventDefault();
      const sport = tile.dataset.directQueueSport;
      setQueueUi(true, `Searching ${sport}...`);
      await queueForSports([sport], hub === 'playoffs' ? playoffPreferenceForSport(sport) : {});
    });
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

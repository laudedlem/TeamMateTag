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

async function initHub() {
  hubProfile = await post('/api/profile/bootstrap', { guest_id: localStorage.getItem(guestKey) || '' });
  if (!hubProfile?.guest_id) return;
  localStorage.setItem(guestKey, hubProfile.guest_id);
  if (hub === 'manager') {
    const summary = await post('/api/manager/summary', { guest_id: hubProfile.guest_id });
    renderManagerSummary(summary);
  }
  if (hub === 'film') {
    const sports = ['baseball', 'basketball', 'hockey', 'football'];
    const results = await Promise.all(sports.map(async (sport) => {
      const path = sport === 'baseball' ? '/api/fr/archive' : `/api/sports/${sport}/fr/archive`;
      return [sport, await post(path, { guest_id: hubProfile.guest_id })];
    }));
    results.forEach(([sport, data]) => renderFilmReviewArchiveSelect(sport, data.days || []));
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
  const dateText = day.date ? new Date(day.date + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '';
  const status = displayStatus(day.status);
  const prefix = day.is_today ? 'Today' : `#${day.number}`;
  return `${prefix}${dateText ? ' - ' + dateText : ''} - ${status}`;
}

function filmReviewUrl(sport, day) {
  if (day.is_today) return `/film/${sport}`;
  return `/film/${sport}?date=${encodeURIComponent(day.date)}&archive=1`;
}

function renderFilmReviewArchiveSelect(sport, days) {
  const select = document.querySelector(`[data-fr-archive-select="${sport}"]`);
  const status = document.querySelector(`[data-fr-today-status="${sport}"]`);
  if (!select) return;
  const current = days.find((day) => day.is_today) || days[0];
  if (status && current) {
    status.textContent = `Today: ${displayStatus(current.status)}`;
    status.className = `fr-today-status ${current.status || 'unseen'}`;
  }
  const sorted = [...days].sort((a, b) => {
    if (a.is_today) return -1;
    if (b.is_today) return 1;
    return Number(b.number || 0) - Number(a.number || 0);
  });
  select.innerHTML = '<option value="">Choose puzzle...</option>' + sorted.map((day) =>
    `<option value="${filmReviewUrl(sport, day)}" class="fr-option-${day.is_today ? 'today' : day.status || 'unseen'}">${formatArchiveLabel(day)}</option>`
  ).join('');
  select.value = '';
  select.addEventListener('change', () => {
    if (select.value) window.location.href = select.value;
  });
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
  Object.entries(sports).forEach(([sport, data]) => {
    const bestTarget = document.querySelector(`[data-manager-best="${sport}"]`);
    if (bestTarget) bestTarget.textContent = `Longest lineup: ${data.own_all_time?.chain_length || 0}`;
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
  const picker = document.getElementById('manager-leaderboard-sport');
  if (picker) {
    const saved = localStorage.getItem('tt_manager_board_sport') || 'baseball';
    picker.value = sports[saved] ? saved : 'baseball';
    renderManagerLeaderboard(summary, picker.value);
    picker.addEventListener('change', () => {
      localStorage.setItem('tt_manager_board_sport', picker.value);
      renderManagerLeaderboard(summary, picker.value);
    });
  }
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

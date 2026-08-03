const guestKey = 'tt_guest_id';
const hub = document.body.dataset.modeHub;
let hubProfile = null;
let queuePoll = null;

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
  if (hub === 'manager-mode') {
    Object.entries(hubProfile.stats?.sports || {}).forEach(([sport, stats]) => {
      const target = document.querySelector(`[data-manager-best="${sport}"]`);
      if (target) target.textContent = `Longest lineup: ${stats.bp_best || 0}`;
    });
  }
  if (hub === 'film-review') {
    const archive = document.getElementById('mode-hub-archive');
    const sports = ['baseball', 'basketball', 'hockey', 'football'];
    const results = await Promise.all(sports.map(async (sport) => {
      const path = sport === 'baseball' ? '/api/fr/archive' : `/api/sports/${sport}/fr/archive`;
      return [sport, await post(path, { guest_id: hubProfile.guest_id })];
    }));
    archive.innerHTML = results.map(([sport, data]) => {
      const days = data.days || [];
      const current = days.find((day) => day.is_today);
      const past = days.filter((day) => !day.is_today);
      const name = sport[0].toUpperCase() + sport.slice(1);
      const links = past.map((day) => `<a class="fr-archive-action ${day.status}" href="/${sport}?mode=fr&date=${day.date}&archive=1">#${day.number} ${day.status}</a>`).join(' ');
      return `<div class="hub-archive-row"><strong>${name}</strong><a class="today-link ${current?.status || 'unseen'}" href="/${sport}?mode=fr">Today: ${current?.status || 'unseen'}</a><span>${links}</span></div>`;
    }).join('');
  }
  configureSharedQueue();
}
initHub();

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

async function pollSharedQueue() {
  const response = await post(queueEndpoint('status'), {
    guest_id: hubProfile?.guest_id || localStorage.getItem(guestKey) || '',
    sports: selectedSports(),
  });
  if (response.status === 'matched' && response.redirect) {
    window.location.href = response.redirect;
    return;
  }
  if (response.status === 'idle') {
    clearInterval(queuePoll);
    queuePoll = null;
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
    clearInterval(queuePoll);
    const response = await post(queueEndpoint('queue'), {
      guest_id: hubProfile?.guest_id || localStorage.getItem(guestKey) || '',
      sports,
      preferences: hub === 'playoffs' ? playoffPreferences() : {},
    });
    await handleQueueResponse(response);
    if (response.status !== 'matched' && !response.error) {
      queuePoll = setInterval(pollSharedQueue, 1000);
    }
  });
  document.getElementById('shared-cancel-btn')?.addEventListener('click', async () => {
    clearInterval(queuePoll);
    queuePoll = null;
    await post(queueEndpoint('cancel_queue'), {
      guest_id: hubProfile?.guest_id || localStorage.getItem(guestKey) || '',
    });
    setQueueUi(false, 'Queue canceled.');
  });
}

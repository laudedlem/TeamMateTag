const guestKey = 'tt_guest_id';
const hub = document.body.dataset.modeHub;

async function post(url, body) {
  const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return response.json();
}

async function initHub() {
  const profile = await post('/api/profile/bootstrap', { guest_id: localStorage.getItem(guestKey) || '' });
  if (!profile?.guest_id) return;
  localStorage.setItem(guestKey, profile.guest_id);
  if (hub === 'manager-mode') {
    Object.entries(profile.stats?.sports || {}).forEach(([sport, stats]) => {
      const target = document.querySelector(`[data-manager-best="${sport}"]`);
      if (target) target.textContent = `Longest lineup: ${stats.bp_best || 0}`;
    });
  }
  if (hub === 'film-review') {
    const archive = document.getElementById('mode-hub-archive');
    const sports = ['baseball', 'basketball', 'hockey', 'football'];
    const results = await Promise.all(sports.map(async (sport) => {
      const path = sport === 'baseball' ? '/api/fr/archive' : `/api/sports/${sport}/fr/archive`;
      return [sport, await post(path, { guest_id: profile.guest_id })];
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
}
initHub();

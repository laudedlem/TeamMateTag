(() => {
  const query = new URLSearchParams(window.location.search);
  const token = query.get('token') || '';
  const headers = token ? { 'X-Headshot-Audit-Token': token } : {};
  const grid = document.getElementById('audit-grid');
  const more = document.getElementById('audit-more');
  const sport = document.getElementById('audit-sport');
  const status = document.getElementById('audit-status');
  let offset = 0;

  const esc = (value) => String(value || '').replace(/[&<>'"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[c]);
  const api = async (path, options = {}) => {
    const response = await fetch(path, { ...options, headers: { ...headers, ...(options.headers || {}) } });
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    return response.json();
  };

  async function loadSummary() {
    const rows = await api(`/api/headshot-audit/summary${token ? `?token=${encodeURIComponent(token)}` : ''}`);
    const totals = {};
    rows.forEach((row) => { totals[row.status] = (totals[row.status] || 0) + row.count; });
    document.getElementById('audit-summary').innerHTML = Object.entries(totals)
      .map(([key, value]) => `<span class="audit-count status-${esc(key)}">${esc(key.replace('_', ' '))}: ${value}</span>`).join('');
  }

  function card(item) {
    const el = document.createElement('article');
    el.className = 'audit-card';
    const image = item.source_url ? `<img src="${esc(item.source_url)}" alt="${esc(item.name)}">` : '<span class="audit-no-image">No source</span>';
    el.innerHTML = `
      <div class="audit-photo">${image}</div>
      <div class="audit-copy"><strong>${esc(item.name)}</strong><span>${esc(item.sport)} · ${esc(item.debut_year)}-${esc(item.final_year)}</span><span class="audit-status-pill">${esc(item.status.replace('_', ' '))}</span></div>
      <details><summary>Review</summary>
        <label>Replacement URL <input class="audit-url" type="url" placeholder="https://..."></label>
        <label>Note <input class="audit-note" type="text" maxlength="1000" value="${esc(item.review_note)}"></label>
        <div class="audit-actions">
          <button data-status="verified" type="button">Correct</button>
          <button data-status="placeholder" type="button">Placeholder</button>
          <button data-status="wrong_player" type="button">Wrong Player</button>
          <button data-status="bad_crop" type="button">Bad Crop</button>
        </div>
      </details>`;
    el.querySelectorAll('[data-status]').forEach((button) => button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        await api(`/api/headshot-audit/review${token ? `?token=${encodeURIComponent(token)}` : ''}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sport: item.sport, player_id: item.player_id, status: button.dataset.status,
            replacement_url: el.querySelector('.audit-url').value, review_note: el.querySelector('.audit-note').value }),
        });
        el.remove(); loadSummary();
      } catch (error) { button.disabled = false; window.alert(error.message); }
    }));
    return el;
  }

  async function load(reset = false) {
    if (reset) { offset = 0; grid.innerHTML = ''; }
    const params = new URLSearchParams({ sport: sport.value, status: status.value, offset: String(offset) });
    if (token) params.set('token', token);
    const data = await api(`/api/headshot-audit/items?${params}`);
    data.items.forEach((item) => grid.appendChild(card(item)));
    offset = data.next_offset || 0;
    more.hidden = !data.next_offset;
  }
  document.getElementById('audit-refresh').addEventListener('click', () => load(true));
  more.addEventListener('click', () => load(false));
  loadSummary().then(() => load(true)).catch((error) => { grid.textContent = error.message; });
})();

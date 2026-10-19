// The Places page: where each of us has been, following the dashboard's energy-date-selection (the same energy
// collection the energy-* cards use, hass.connection["_energy_<dashboard url>"]; see energy-cost-card.js).
//
//   custom:presence-card      one person: a donut of hours per place over the picked range, drawn as SVG (HA's
//                             charts only include a pie for its own energy card). Reads the long-term statistics
//                             of the "<Name> at <Place>" sensors (100 while there, else 0), so it goes back forever.
//                             (The timeline is HA's own history graph on the page.)
//   custom:presence-map-card  every recorded position of the given trackers in the picked range, drawn as paths on
//                             HA's own map (ha-map). Reads raw history (the recorder keeps it for 10 years).
//
// The map is HA's ha-map, a frontend internal: if an update breaks it, the card shows the error.
// Hourly up to a little over a week, daily to ~3 months, weekly to ~a year, monthly beyond (fewer rows to sum).
const PERIOD = days => days > 400 ? 'month' : days > 95 ? 'week' : days > 8 ? 'day' : 'hour';
class CollectionCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
    if (!this.shadowRoot) {
      this.attachShadow({mode: 'open'});
      this.shadowRoot.innerHTML = `<style>
        ha-card { padding: 16px; }
        .title { font-size: 20px; line-height: 28px; margin: 0 0 8px; }
        .msg { color: var(--secondary-text-color); padding: 32px 0; text-align: center; }
        .donut { display: flex; flex-direction: column; align-items: center; gap: 8px; }
        .donut svg { width: 150px; height: 150px; }
        .legend { display: grid; gap: 4px; width: 100%; max-width: 260px; font-size: 13px; }
        .item { display: grid; grid-template-columns: 10px 1fr auto; gap: 8px; align-items: center; }
        .dot { width: 10px; height: 10px; border-radius: 50%; }
        .val { color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      </style><ha-card><div class="title"></div><div class="body"><div class="msg">Loading…</div></div></ha-card>`;
    }
    this.shadowRoot.querySelector('.title').textContent = config.title || '';
  }

  set hass(hass) {
    this._hass = hass;
    this._elements?.forEach(e => { e.hass = hass; });
    if (!this._started) { this._started = true; this._connect(); }
  }

  disconnectedCallback() { this._unsub?.(); this._unsub = undefined; this._started = false; }
  connectedCallback() { if (this._hass && !this._started) { this._started = true; this._connect(); } }

  _message(text) {
    this._elements = undefined;
    this.shadowRoot.querySelector('.body').innerHTML = '<div class="msg"></div>';
    this.shadowRoot.querySelector('.msg').textContent = text;
  }

  async _whenDefined(tag, stub) {
    const helpers = await window.loadCardHelpers();
    helpers.createCardElement(stub);
    await Promise.race([customElements.whenDefined(tag),
      new Promise((_, rej) => setTimeout(() => rej(new Error(`${tag} never loaded`)), 15000))]);
  }

  async _connect() {
    try {
      await this._load();
      const key = this.config.collection_key ? `_${this.config.collection_key}` : `_energy_${this._hass.panelUrl}`;
      let collection;
      for (let i = 0; i < 60 && !(collection = this._hass.connection[key]); i++) await new Promise(r => setTimeout(r, 250));
      if (!collection) throw new Error(`no date picker on this dashboard (${key})`);
      this._unsub?.();
      this._unsub = collection.subscribe(({start, end}) =>
        this._update(start, end || new Date()).catch(e => this._message(`${this.config.title}: ${e.message}`)));
    } catch (e) {
      this._started = false;
      this._message(`${this.config.title}: ${e.message}`);
    }
  }
}

class PresenceCard extends CollectionCard {
  getCardSize() { return 4; }
  getGridOptions() { return {columns: 'full', rows: 4, min_rows: 3}; }

  async _load() {}

  async _update(start, end) {
    const places = this.config.places;
    const stats = await this._hass.callWS({
      type: 'recorder/statistics_during_period', start_time: start.toISOString(), end_time: end.toISOString(),
      statistic_ids: places.map(p => p.entity), period: PERIOD((end - start) / 86400000), types: ['mean'],
    });
    const now = Date.now();
    // Hours there: each bucket's time-weighted mean (% of it there) times its length so far.
    const totals = places.map(p => (stats[p.entity] || []).reduce((h, st) =>
      h + (st.mean || 0) / 100 * (Math.min(st.end, now) - st.start) / 3600000, 0));
    const sum = totals.reduce((a, b) => a + b, 0);
    if (!sum) { this._message('Nothing recorded for this range yet.'); return; }

    const lang = this._hass.locale?.language;
    const fmtH = h => h >= 48 ? `${new Intl.NumberFormat(lang, {maximumFractionDigits: 1}).format(h / 24)} days`
      : `${new Intl.NumberFormat(lang, {maximumFractionDigits: h < 10 ? 1 : 0}).format(h)} h`;
    // One arc per place, sized by its share of the hours, and a legend of hours and percentages.
    let angle = -Math.PI / 2;
    const arcs = places.map((p, i) => {
      const share = totals[i] / sum;
      if (share <= 0) return '';
      const title = `<title>${p.name}: ${fmtH(totals[i])} (${Math.round(share * 100)}%)</title>`;
      if (share > 0.9999) return `<circle cx="60" cy="60" r="45" fill="none" stroke="${p.color}" stroke-width="22">${title}</circle>`;
      const a0 = angle, a1 = angle + share * 2 * Math.PI; angle = a1;
      const pt = a => `${60 + 45 * Math.cos(a)} ${60 + 45 * Math.sin(a)}`;
      return `<path d="M ${pt(a0)} A 45 45 0 ${share > 0.5 ? 1 : 0} 1 ${pt(a1)}" fill="none" stroke="${p.color}" stroke-width="22">${title}</path>`;
    }).join('');
    const legend = places.map((p, i) => totals[i] > 0 ? `<div class="item"><span class="dot" style="background:${p.color}"></span>
      <span class="name">${p.name}</span><span class="val">${fmtH(totals[i])} · ${Math.round(100 * totals[i] / sum)}%</span></div>` : '').join('');
    this._elements = [];
    this.shadowRoot.querySelector('.body').innerHTML =
      `<div class="donut"><svg viewBox="0 0 120 120">${arcs}</svg><div class="legend">${legend}</div></div>`;
  }
}

class PresenceMapCard extends CollectionCard {
  getCardSize() { return 8; }
  getGridOptions() { return {columns: 'full', rows: 8, min_rows: 6}; }

  async _load() { await this._whenDefined('ha-map', {type: 'map', entities: ['zone.home']}); }

  async _update(start, end) {
    const trackers = this.config.entities;
    const history = await this._hass.callWS({
      type: 'history/history_during_period', start_time: start.toISOString(), end_time: end.toISOString(),
      entity_ids: trackers.map(t => t.entity), minimal_response: false, no_attributes: false,
      significant_changes_only: false,
    });
    const max = this.config.max_points || 5000;
    const paths = trackers.map(t => {
      // Attributes only come with the rows where they changed; carry the last seen position forward.
      let pos = null;
      const points = [];
      for (const row of history[t.entity] || []) {
        if (row.a?.latitude !== undefined) pos = [row.a.latitude, row.a.longitude];
        if (pos) points.push({point: pos, timestamp: new Date(1000 * (row.lu ?? row.lc))});
      }
      // Thin long ranges so the browser copes; the first and last points always stay.
      const step = Math.ceil(points.length / max);
      const thinned = step > 1 ? points.filter((_, i) => i % step === 0 || i === points.length - 1) : points;
      return {points: thinned, name: t.name, color: t.color, fullDatetime: (end - start) > 6 * 86400000};
    }).filter(p => p.points.length);

    if (!this._elements) {
      const body = this.shadowRoot.querySelector('.body');
      body.innerHTML = '';
      const map = document.createElement('ha-map');
      map.style.display = 'block';
      map.style.height = this.config.height || '480px';
      body.appendChild(map);
      this._elements = [map];
    }
    const map = this._elements[0];
    map.hass = this._hass;
    map.entities = [...trackers.map(t => ({entity_id: t.entity, color: t.color})),
      ...(this.config.zones || []).map(z => ({entity_id: z}))];
    map.paths = paths;
    map.autoFit = true;
    map.fitZones = false;
  }
}

customElements.define('presence-card', PresenceCard);
customElements.define('presence-map-card', PresenceMapCard);
window.customCards = window.customCards || [];
window.customCards.push(
  {type: 'presence-card', name: 'Presence', description: 'Hours per place: pie and timeline, follows the date picker.'},
  {type: 'presence-map-card', name: 'Presence map', description: 'Every recorded position in the picked range.'});

// The TV page: what's been on, for the range the dashboard's date picker shows (its energy collection, like
// presence-card.js and energy-cost-card.js). Everything comes from recorded history, so it goes back as far as
// the recorder does (10 years):
//   remote.tv               on/off and the app in front (current_activity, an Android package name)
//   media_player.tv_cast    playing/paused and, while playing, the title, channel/artist and app
//
//   custom:tv-timeline-card  the History page's timeline (state-history-charts): the app, and what's playing
//   custom:tv-apps-card      a donut of hours per app
//   custom:tv-titles-card    what was watched, longest first: title, channel, app, time
//   custom:tv-daily-card     hours of TV per hour/day/week/month, stacked by app (ha-chart-base)
// Uses HA frontend internals (state-history-charts, ha-chart-base); if an update breaks one, the card says so.
const APPS = {
  'com.google.android.youtube.tv': 'YouTube',
  'com.google.android.youtube.tvunplugged': 'YouTube TV',
  'com.netflix.ninja': 'Netflix',
  'com.amazon.amazonvideo.livingroom': 'Prime Video',
  'com.disney.disneyplus': 'Disney+',
  'com.wbd.stream': 'Max',
  'com.plexapp.android': 'Plex',
  'com.apple.atve.androidtv.appletv': 'Apple TV',
  'com.stremio.one': 'Stremio',
  'com.spocky.projengmenu': 'Home screen',
  'com.android.systemui': 'System',
  'com.android.tv.settings': 'Settings',
  'com.android.vending': 'Play Store',
  'com.google.android.gms': 'Google',
};
// Not watching anything: the launcher and system screens.
const NOT_WATCHING = new Set(['Home screen', 'System', 'Settings', 'Play Store', 'Google', '']);
const COLORS = ['#ff0000', '#4269d0', '#efb118', '#6cc5b0', '#a463f2', '#ff8ab7', '#3ca951', '#97bbf5'];
const PERIOD = days => days > 400 ? 'month' : days > 95 ? 'week' : days > 2 ? 'day' : 'hour';
const appName = id => APPS[id] ?? (id || '');

// One history fetch per range, shared by every card on the page.
const cache = new Map();
function tvHistory(hass, start, end) {
  const key = `${start.getTime()}-${end.getTime()}`;
  if (!cache.has(key)) {
    cache.clear();
    cache.set(key, hass.callWS({
      type: 'history/history_during_period', start_time: start.toISOString(), end_time: end.toISOString(),
      entity_ids: ['remote.tv', 'media_player.tv_cast'], minimal_response: false, no_attributes: false,
      significant_changes_only: false,
    }).then(h => segments(h, start, end)));
  }
  return cache.get(key);
}

// Turns the two entities' rows into labelled intervals: apps (what's in front while the TV is on) and plays
// (what the Cast player says is playing). Attributes ride along on every row; "unavailable" is the TV's standby
// network blip, so it ends whatever was going on.
function segments(history, start, end) {
  const stop = Math.min(end.getTime(), Date.now());
  const walk = (rows, label) => {
    const out = [];
    rows = rows || [];
    rows.forEach((r, i) => {
      const from = Math.max(1000 * (r.lu ?? r.lc), start.getTime());
      const to = Math.min(i + 1 < rows.length ? 1000 * (rows[i + 1].lu ?? rows[i + 1].lc) : stop, stop);
      const l = label(r);
      if (to > from && l) out.push({...l, start: from, end: to});
    });
    return out;
  };
  const apps = walk(history['remote.tv'], r => r.s === 'on' ? {label: appName(r.a?.current_activity)} : null);
  const plays = walk(history['media_player.tv_cast'], r => r.s === 'playing' ? {
    label: r.a?.media_title || r.a?.app_name || 'Playing', title: r.a?.media_title || '',
    channel: r.a?.media_artist || r.a?.media_series_title || '', app: r.a?.app_name || '',
  } : null);
  return {apps, plays, stop};
}

class TvCard extends HTMLElement {
  getGridOptions() { return {columns: this.config?.columns ?? 'full'}; }

  setConfig(config) {
    this.config = config;
    if (!this.shadowRoot) {
      this.attachShadow({mode: 'open'});
      this.shadowRoot.innerHTML = `<style>
        ha-card { padding: 16px; height: 100%; box-sizing: border-box; }
        .title { font-size: 20px; line-height: 28px; margin: 0 0 8px; }
        .msg { color: var(--secondary-text-color); padding: 32px 0; text-align: center; }
        .donut { display: flex; flex-direction: column; align-items: center; gap: 8px; }
        .donut svg { width: 150px; height: 150px; }
        .legend { display: grid; gap: 4px; width: 100%; max-width: 300px; font-size: 13px; }
        .item { display: grid; grid-template-columns: 10px 1fr auto; gap: 8px; align-items: center; }
        .dot { width: 10px; height: 10px; border-radius: 50%; }
        .val, .meta { color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
        .rows { display: grid; gap: 2px; }
        .row { display: grid; grid-template-columns: 1fr auto; gap: 12px; padding: 6px 0; align-items: center;
               border-bottom: 1px solid var(--divider-color); }
        .row:last-child { border-bottom: none; }
        .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .meta { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .bar { height: 4px; border-radius: 2px; background: var(--primary-color); opacity: .6; margin-top: 4px; }
      </style><ha-card><div class="title"></div><div class="body"><div class="msg">Loading…</div></div></ha-card>`;
    }
    this.shadowRoot.querySelector('.title').textContent = config.title || '';
  }

  set hass(hass) {
    this._hass = hass;
    if (this._chart) this._chart.hass = hass;
    if (!this._started) { this._started = true; this._connect(); }
  }

  disconnectedCallback() { this._unsub?.(); this._unsub = undefined; this._started = false; }
  connectedCallback() { if (this._hass && !this._started) { this._started = true; this._connect(); } }

  _message(text) {
    this._chart = undefined;
    this.shadowRoot.querySelector('.body').innerHTML = '<div class="msg"></div>';
    this.shadowRoot.querySelector('.msg').textContent = text;
  }

  async _needs(tag, stub) {
    const helpers = await window.loadCardHelpers();
    helpers.createCardElement(stub);
    await Promise.race([customElements.whenDefined(tag),
      new Promise((_, rej) => setTimeout(() => rej(new Error(`${tag} never loaded`)), 15000))]);
  }

  async _connect() {
    try {
      await this._load?.();
      const key = this.config.collection_key ? `_${this.config.collection_key}` : `_energy_${this._hass.panelUrl}`;
      let collection;
      for (let i = 0; i < 60 && !(collection = this._hass.connection[key]); i++) await new Promise(r => setTimeout(r, 250));
      if (!collection) throw new Error(`no date picker on this dashboard (${key})`);
      this._unsub?.();
      this._unsub = collection.subscribe(({start, end}) => {
        end = end || new Date();
        tvHistory(this._hass, start, end).then(d => this._render(d, start, end))
          .catch(e => this._message(`${this.config.title || 'TV'}: ${e.message}`));
      });
    } catch (e) {
      this._started = false;
      this._message(`${this.config.title || 'TV'}: ${e.message}`);
    }
  }

  _fmt(ms) {
    const m = ms / 60000;
    if (m < 60) return `${Math.max(1, Math.round(m))} min`;
    const h = m / 60;
    return h < 48 ? `${new Intl.NumberFormat(this._hass.locale?.language, {maximumFractionDigits: h < 10 ? 1 : 0}).format(h)} h`
      : `${new Intl.NumberFormat(this._hass.locale?.language, {maximumFractionDigits: 1}).format(h / 24)} days`;
  }

  _totals(list) {
    const t = new Map();
    for (const s of list) t.set(s.label, (t.get(s.label) || 0) + (s.end - s.start));
    return [...t.entries()].sort((a, b) => b[1] - a[1]);
  }
}

class TvTimelineCard extends TvCard {
  async _load() { await this._needs('state-history-charts', {type: 'history-graph', entities: ['zone.home']}); }

  _render({apps, plays, stop}, start) {
    const row = (id, name, list) => {
      // The timeline wants a state per change; gaps between intervals are "Off" / nothing playing.
      const data = [];
      let t = start.getTime();
      for (const s of list) {
        if (s.start > t) data.push({state: 'off', state_localize: id === 'remote.tv' ? 'Off' : ' ', last_changed: t, attributes: {}});
        data.push({state: s.label, state_localize: s.label, last_changed: s.start, attributes: {}});
        t = s.end;
      }
      if (t < stop) data.push({state: 'off', state_localize: id === 'remote.tv' ? 'Off' : ' ', last_changed: t, attributes: {}});
      return {name, entity_id: id, data};
    };
    if (!this._chart) {
      const body = this.shadowRoot.querySelector('.body');
      body.innerHTML = '';
      this._chart = document.createElement('state-history-charts');
      body.appendChild(this._chart);
    }
    this._chart.hass = this._hass;
    this._chart.startTime = start;
    this._chart.endTime = new Date(stop);
    this._chart.isLoadingData = false;
    // Real entity ids: the timeline colours bars by the entity's domain, and drew nothing for made-up ones.
    this._chart.historyData = {line: [], timeline: [row('remote.tv', 'App', apps), row('media_player.tv_cast', 'Playing', plays)]};
  }
}

class TvAppsCard extends TvCard {
  _render({apps}) {
    const totals = this._totals(apps.filter(s => !NOT_WATCHING.has(s.label)));
    const sum = totals.reduce((a, [, v]) => a + v, 0);
    if (!sum) { this._message('Nothing watched in this range.'); return; }
    let angle = -Math.PI / 2;
    const arcs = totals.map(([name, v], i) => {
      const share = v / sum, color = COLORS[i % COLORS.length];
      const title = `<title>${name}: ${this._fmt(v)} (${Math.round(share * 100)}%)</title>`;
      if (share > 0.9999) return `<circle cx="60" cy="60" r="45" fill="none" stroke="${color}" stroke-width="22">${title}</circle>`;
      const a0 = angle, a1 = angle + share * 2 * Math.PI; angle = a1;
      const pt = a => `${60 + 45 * Math.cos(a)} ${60 + 45 * Math.sin(a)}`;
      return `<path d="M ${pt(a0)} A 45 45 0 ${share > 0.5 ? 1 : 0} 1 ${pt(a1)}" fill="none" stroke="${color}" stroke-width="22">${title}</path>`;
    }).join('');
    const legend = totals.map(([name, v], i) => `<div class="item"><span class="dot" style="background:${COLORS[i % COLORS.length]}"></span>
      <span class="name">${name}</span><span class="val">${this._fmt(v)} · ${Math.round(100 * v / sum)}%</span></div>`).join('');
    this.shadowRoot.querySelector('.body').innerHTML =
      `<div class="donut"><svg viewBox="0 0 120 120">${arcs}</svg><div class="legend">${legend}</div></div>`;
  }
}

// "Episode 2", "S1 E3", "Season 1 Episode 3": an episode, so it's grouped under its show.
const EPISODE = /^(episode\s*\d+|s\d+\s*:?\s*e\d+|season\s*\d+.*episode\s*\d+)/i;

class TvTitlesCard extends TvCard {
  _render({plays}) {
    const by = new Map();
    for (const s of plays) {
      const episode = EPISODE.test(s.channel) ? s.channel : '';
      const k = episode ? `${s.label}\u0000show` : `${s.label}\u0000${s.channel}`;
      const e = by.get(k) || {label: s.label, channel: episode ? '' : s.channel, app: s.app, ms: 0, last: 0, episodes: new Set()};
      e.ms += s.end - s.start; e.last = Math.max(e.last, s.end);
      if (episode) e.episodes.add(episode);
      by.set(k, e);
    }
    for (const e of by.values()) {
      if (e.episodes.size) e.channel = [...e.episodes].join(', ');
    }
    const list = [...by.values()].filter(e => e.ms >= 60000).sort((a, b) => b.ms - a.ms).slice(0, this.config.max || 25);
    if (!list.length) { this._message('Nothing played in this range.'); return; }
    const esc = t => String(t).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
    const lang = this._hass.locale?.language;
    const top = list[0].ms;
    this.shadowRoot.querySelector('.body').innerHTML = `<div class="rows">${list.map(e => `<div class="row">
      <div style="min-width:0"><div class="name">${esc(e.label)}</div>
        <div class="meta">${esc([e.channel, e.app, new Date(e.last).toLocaleDateString(lang, {weekday: 'short', day: 'numeric', month: 'short'})].filter(Boolean).join(' · '))}</div>
        <div class="bar" style="width:${Math.max(2, 100 * e.ms / top)}%"></div></div>
      <div class="val">${this._fmt(e.ms)}</div></div>`).join('')}</div>`;
  }
}

class TvDailyCard extends TvCard {
  async _load() { await this._needs('ha-chart-base', {type: 'statistics-graph', entities: []}); }

  _render({apps, stop}, start, end) {
    const period = PERIOD((end - start) / 86400000);
    const buckets = [];
    for (const t = new Date(start); t < end && buckets.length < 5000;) {
      buckets.push(t.getTime());
      if (period === 'hour') t.setHours(t.getHours() + 1);
      else if (period === 'day') t.setDate(t.getDate() + 1);
      else if (period === 'week') t.setDate(t.getDate() + 7);
      else t.setMonth(t.getMonth() + 1);
    }
    const edges = [...buckets, end.getTime()];
    const watching = apps.filter(s => !NOT_WATCHING.has(s.label));
    const names = this._totals(watching).map(([n]) => n);
    const hours = new Map(names.map(n => [n, buckets.map(() => 0)]));
    for (const s of watching) {
      // Split each interval across the buckets it spans.
      for (let i = 0; i < buckets.length; i++) {
        const lo = Math.max(s.start, edges[i]), hi = Math.min(s.end, edges[i + 1]);
        if (hi > lo) hours.get(s.label)[i] += (hi - lo) / 3600000;
      }
    }
    const lang = this._hass.locale?.language;
    const when = t => new Date(t).toLocaleString(lang, period === 'hour' ? {weekday: 'short', hour: 'numeric'}
      : period === 'month' ? {month: 'long', year: 'numeric'} : {weekday: 'short', day: 'numeric', month: 'short'});
    const node = html => { const el = document.createElement('div'); el.innerHTML = html; return el; };
    const data = names.map((n, i) => ({
      type: 'bar', name: n, stack: 'tv', barMaxWidth: 40, itemStyle: {color: COLORS[i % COLORS.length]},
      data: buckets.map((t, j) => [t, +hours.get(n)[j].toFixed(2)]),
    }));
    const options = {
      xAxis: {type: 'time', min: start.getTime(), max: end.getTime()},
      yAxis: {type: 'value', name: 'h'},
      grid: {top: 30, left: 8, right: 8, bottom: 36, containLabel: true},
      legend: {show: true, bottom: 0, left: 'center'}, dataZoom: [], animation: false,
      tooltip: {trigger: 'axis', formatter: items => node([`<b>${when(items[0].value[0])}</b>`,
        ...items.filter(i => i.value[1] > 0).map(i => `${i.marker} ${i.seriesName}: ${this._fmt(i.value[1] * 3600000)}`)].join('<br>'))},
    };
    if (!names.length) { this._message('Nothing watched in this range.'); return; }
    if (!this._chart) {
      const body = this.shadowRoot.querySelector('.body');
      body.innerHTML = '';
      this._chart = document.createElement('ha-chart-base');
      this._chart.height = '260px';
      body.appendChild(this._chart);
    }
    this._chart.hass = this._hass;
    this._chart.options = options;
    this._chart.data = data;
  }
}

customElements.define('tv-timeline-card', TvTimelineCard);
customElements.define('tv-apps-card', TvAppsCard);
customElements.define('tv-titles-card', TvTitlesCard);
customElements.define('tv-daily-card', TvDailyCard);
window.customCards = window.customCards || [];
window.customCards.push(
  {type: 'tv-timeline-card', name: 'TV timeline', description: 'App and what was playing, for the picked range.'},
  {type: 'tv-apps-card', name: 'TV apps', description: 'Hours per app.'},
  {type: 'tv-titles-card', name: 'TV watched', description: 'What was watched and for how long.'},
  {type: 'tv-daily-card', name: 'TV per day', description: 'Hours of TV per day, by app.'});

// One chart for the Energy dashboard: each plug's kWh stacked with the rest of the meter as "Untracked" (left
// axis), and PG&E's cost as a line (right axis, $). The built-in cards can't do two axes.
//
// It follows the dashboard's energy-date-selection: it subscribes to the same energy collection the energy-*
// cards use (hass.connection["_energy_<dashboard url>"], or "_<collection_key>") for the range and the Energy
// settings, then fetches the meter, plug and cost statistics itself, all at one period: hourly up to 2 days,
// daily up to 5 weeks, weekly up to ~3 months, monthly beyond (HA's own cards jump from daily to monthly). Drawn with HA's own ha-chart-base, so it looks like the other charts.
// Both are frontend internals: if a HA update breaks this card, it shows the error instead of a chart.
const PALETTE = ['#4269d0', '#efb118', '#ff725c', '#6cc5b0', '#3ca951', '#ff8ab7', '#a463f2', '#97bbf5'];

class EnergyCostCard extends HTMLElement {
  setConfig(config) {
    this.config = {title: 'Electricity', ...config};
    if (!this.shadowRoot) {
      this.attachShadow({mode: 'open'});
      this.shadowRoot.innerHTML = `<style>
        ha-card { padding: 16px; }
        .title { font-size: 20px; line-height: 28px; margin: 0 0 8px; }
        .msg { color: var(--secondary-text-color); padding: 32px 0; text-align: center; }
      </style><ha-card><div class="title"></div><div class="body"><div class="msg">Loading…</div></div></ha-card>`;
    }
    this.shadowRoot.querySelector('.title').textContent = this.config.title;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._chart) this._chart.hass = hass;
    if (!this._started) { this._started = true; this._connect(); }
  }

  getCardSize() { return 6; }
  getGridOptions() { return {columns: 'full', rows: 6, min_rows: 4}; }

  disconnectedCallback() { this._unsub?.(); this._unsub = undefined; this._started = false; }
  connectedCallback() { if (this._hass && !this._started) { this._started = true; this._connect(); } }

  _message(text) {
    this._chart = undefined;
    this.shadowRoot.querySelector('.body').innerHTML = `<div class="msg"></div>`;
    this.shadowRoot.querySelector('.msg').textContent = text;
  }

  async _connect() {
    try {
      // Loading a statistics graph pulls in ha-chart-base.
      const helpers = await window.loadCardHelpers();
      helpers.createCardElement({type: 'statistics-graph', entities: []});
      await Promise.race([customElements.whenDefined('ha-chart-base'),
        new Promise((_, rej) => setTimeout(() => rej(new Error('ha-chart-base never loaded')), 15000))]);
      const key = this.config.collection_key ? `_${this.config.collection_key}` : `_energy_${this._hass.panelUrl}`;
      // The date picker creates the collection; wait for it.
      let collection;
      for (let i = 0; i < 60 && !(collection = this._hass.connection[key]); i++) await new Promise(r => setTimeout(r, 250));
      if (!collection) throw new Error(`no energy date picker on this dashboard (${key})`);
      this._unsub?.();
      this._unsub = collection.subscribe(data => this._update(data).catch(e => this._message(`Energy chart: ${e.message}`)));
    } catch (e) {
      this._started = false;
      this._message(`Energy chart: ${e.message}`);
    }
  }

  async _update(data) {
    const {start, end, prefs} = data;
    const grid = prefs?.energy_sources?.find(s => s.type === 'grid');
    if (!grid?.stat_energy_from) { this._message('No grid source in the Energy settings.'); return; }
    const days = ((end || new Date()) - start) / 86400000;
    const period = days > 95 ? 'month' : days > 35 ? 'week' : days > 2 ? 'day' : 'hour';
    const ids = [grid.stat_energy_from, ...prefs.device_consumption.map(d => d.stat_consumption), grid.stat_cost].filter(Boolean);
    const stats = await this._hass.callWS({
      type: 'recorder/statistics_during_period', start_time: start.toISOString(), end_time: end?.toISOString(),
      statistic_ids: ids, period, types: ['change'], units: {energy: 'kWh'},
    });
    const cost = grid.stat_cost ? stats[grid.stat_cost] || [] : [];

    const points = list => new Map((list || []).map(p => [p.start, Math.max(0, p.change || 0)]));
    const meter = points(stats[grid.stat_energy_from]);
    const devices = prefs.device_consumption.map(d => ({name: d.name || d.stat_consumption, values: points(stats[d.stat_consumption])}));
    const times = [...new Set([...meter.keys(), ...devices.flatMap(d => [...d.values.keys()])])].sort((a, b) => a - b);

    const style = getComputedStyle(this);
    const color = i => style.getPropertyValue(`--graph-color-${i + 1}`).trim() || PALETTE[i % PALETTE.length];
    const bar = (name, values, c) => ({
      type: 'bar', name, stack: 'kwh', yAxisIndex: 0, barMaxWidth: 40, itemStyle: {color: c},
      data: times.map(t => [t, values(t)]),
    });
    const series = [
      ...devices.map((d, i) => bar(d.name, t => d.values.get(t) ?? 0, color(i))),
      // The meter is about a day behind the plugs: no meter reading means no untracked, not negative.
      bar('Untracked', t => meter.has(t)
        ? Math.max(0, meter.get(t) - devices.reduce((sum, d) => sum + (d.values.get(t) ?? 0), 0)) : 0,
        style.getPropertyValue('--disabled-color').trim() || '#9e9e9e'),
      {
        type: 'line', name: 'Cost', yAxisIndex: 1, step: false, symbol: 'circle', symbolSize: 5,
        lineStyle: {width: 2}, itemStyle: {color: style.getPropertyValue('--primary-text-color').trim() || '#222'},
        data: cost.map(p => [p.start, Math.max(0, p.change || 0)]),
      },
    ];

    const lang = this._hass.locale?.language;
    const fmt = (v, d) => new Intl.NumberFormat(lang, {maximumFractionDigits: d, minimumFractionDigits: d}).format(v);
    const when = t => (period === 'week' ? 'Week of ' : '') + new Date(t).toLocaleString(lang, period === 'hour'
      ? {weekday: 'short', hour: 'numeric'} : period === 'month' ? {month: 'long', year: 'numeric'}
      : {weekday: 'short', day: 'numeric', month: 'short'});
    const options = {
      xAxis: {type: 'time', min: start.getTime(), max: (end || new Date()).getTime()},
      yAxis: [
        {type: 'value', name: 'kWh', position: 'left', splitLine: {show: true}},
        {type: 'value', name: '$', position: 'right', splitLine: {show: false},
          axisLabel: {formatter: v => `$${fmt(v, v < 10 ? 2 : 0)}`}},
      ],
      grid: {top: 30, left: 8, right: 8, bottom: 36, containLabel: true},
      legend: {show: true, bottom: 0, left: 'center'},
      tooltip: {
        trigger: 'axis',
        formatter: items => {
          const rows = items.filter(i => i.value[1] > 0 || i.seriesName === 'Cost').map(i =>
            `${i.marker} ${i.seriesName}: ${i.seriesName === 'Cost' ? `$${fmt(i.value[1], 2)}` : `${fmt(i.value[1], 2)} kWh`}`);
          const kwh = items.filter(i => i.seriesName !== 'Cost').reduce((s, i) => s + i.value[1], 0);
          // ha-chart-base renders what the formatter returns with lit, so a string would show as text (tags and
          // all); a DOM node is rendered as is.
          const el = document.createElement('div');
          el.innerHTML = [`<b>${when(items[0].value[0])}</b>`, ...rows, `<b>Total: ${fmt(kwh, 2)} kWh</b>`].join('<br>');
          return el;
        },
      },
    };

    if (!this._chart) {
      const body = this.shadowRoot.querySelector('.body');
      body.innerHTML = '';
      this._chart = document.createElement('ha-chart-base');
      this._chart.height = '320px';
      body.appendChild(this._chart);
    }
    this._chart.hass = this._hass;
    this._chart.options = options;
    this._chart.data = series;
  }
}

customElements.define('energy-cost-card', EnergyCostCard);
window.customCards = window.customCards || [];
window.customCards.push({type: 'energy-cost-card', name: 'Energy and cost', description: 'Device kWh stacked, cost on a second axis.'});

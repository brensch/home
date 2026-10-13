// Display conversion only: both units retain the source entity's history.
class TemperatureBadge extends HTMLElement {
  setConfig(config) {
    if (!config.entity || !['C', 'F', 'both'].includes(config.unit)) throw new Error('Temperature badge requires entity and unit C, F, or both');
    this.config = config;
    if (!this.shadowRoot) {
      this.attachShadow({mode: 'open'});
      this.shadowRoot.innerHTML = `<style>
        :host { display: inline-block; }
        button { display:flex;align-items:center;gap:8px;height:36px;padding:0 12px;
          border:1px solid var(--ha-card-border-color,var(--divider-color));border-radius:18px;
          background:var(--ha-card-background,var(--card-background-color));color:var(--primary-text-color);
          font:inherit;font-size:14px;cursor:pointer; }
        button:focus-visible {outline:2px solid var(--primary-color);outline-offset:2px;}
        ha-icon {color:var(--state-sensor-temperature-color,var(--primary-color));--mdc-icon-size:20px;}
      </style><button type="button"><ha-icon></ha-icon><span></span></button>`;
      this.shadowRoot.querySelector('button').addEventListener('click', () => {
        this.dispatchEvent(new CustomEvent('hass-more-info', {detail:{entityId:this.config.entity}, bubbles:true, composed:true}));
      });
    }
    this.render();
  }
  set hass(hass) { this._hass = hass; this.render(); }
  render() {
    if (!this.config || !this._hass) return;
    const state = this._hass.states[this.config.entity];
    const value = Number(state?.state);
    const sourceUnit = state?.attributes.unit_of_measurement;
    const valid = state && !['unknown','unavailable',''].includes(state.state) && Number.isFinite(value) && ['°C','°F'].includes(sourceUnit);
    let converted = value;
    if (valid && sourceUnit !== `°${this.config.unit}`) converted = this.config.unit === 'C' ? (value-32)*5/9 : value*9/5+32;
    const format = new Intl.NumberFormat(this._hass.locale?.language || undefined,{maximumFractionDigits:1});
    const celsius = sourceUnit === '°F' ? (value-32)*5/9 : value;
    const fahrenheit = sourceUnit === '°C' ? value*9/5+32 : value;
    const label = !valid ? 'Unavailable' : this.config.unit === 'both'
      ? `${format.format(celsius)} °C · ${format.format(fahrenheit)} °F`
      : `${format.format(converted)} °${this.config.unit}`;
    this.shadowRoot.querySelector('span').textContent = label;
    this.shadowRoot.querySelector('ha-icon').setAttribute('icon','mdi:thermometer');
    const button = this.shadowRoot.querySelector('button');
    button.title = this.config.name || state?.attributes.friendly_name || 'Outside temperature';
    button.setAttribute('aria-label',`${button.title}: ${label}`);
  }
}
if (!customElements.get('temperature-badge')) customElements.define('temperature-badge', TemperatureBadge);

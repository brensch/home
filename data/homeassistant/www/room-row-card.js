// room-row-card: one room on one line, everything inside the card:
//
//   [ Living room        (bulb) ======slider======        ⋮ ]
//
// Tap the bulb to switch the room on/off (it takes the light's colour when on),
// drag the slider to dim (dragging an off room turns it on), tap ⋮ or the name
// for the full options dialog. No dependencies; served from /local/.
//
//   - type: custom:room-row-card
//     entity: light.living_room_all
//     name: Living room          # optional
//     icon: mdi:sofa             # optional, defaults to the entity's icon

class RoomRowCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity || !config.entity.startsWith("light.")) {
      throw new Error("room-row-card needs a light entity");
    }
    this._config = config;
    if (this._root) this._update();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._root) this._build();
    this._update();
  }

  _build() {
    this._root = this.attachShadow({ mode: "open" });
    this._root.innerHTML = `
      <style>
        ha-card { height: 100%; }
        .row { display: flex; align-items: center; gap: 10px; height: 100%; min-height: 52px;
               padding: 0 6px 0 14px; box-sizing: border-box; }
        .name { flex: 0 1 34%; min-width: 0; font-size: 15px; font-weight: 500; white-space: nowrap;
                overflow: hidden; text-overflow: ellipsis; cursor: pointer; color: var(--primary-text-color); }
        button { border: none; background: none; padding: 0; margin: 0; cursor: pointer; color: inherit;
                 display: flex; align-items: center; justify-content: center; -webkit-tap-highlight-color: transparent; }
        .icon { flex: none; width: 36px; height: 36px; border-radius: 50%; transition: background .2s, color .2s;
                background: var(--secondary-background-color); color: var(--disabled-text-color, #9e9e9e); }
        .icon.on { color: var(--bulb, #ffb300); background: color-mix(in srgb, var(--bulb, #ffb300) 20%, transparent); }
        .slider { flex: 1 1 auto; min-width: 60px; height: 30px; margin: 0; border-radius: 15px;
                  -webkit-appearance: none; appearance: none; cursor: pointer; outline: none;
                  background: linear-gradient(to right, var(--fill) var(--pct), var(--secondary-background-color) var(--pct)); }
        .slider::-webkit-slider-thumb { -webkit-appearance: none; width: 6px; height: 18px; border-radius: 3px;
                                         background: var(--card-background-color, #fff); box-shadow: 0 0 2px rgba(0,0,0,.4); }
        .slider::-moz-range-thumb { width: 6px; height: 18px; border: none; border-radius: 3px;
                                    background: var(--card-background-color, #fff); box-shadow: 0 0 2px rgba(0,0,0,.4); }
        .more { flex: none; width: 32px; height: 40px; color: var(--secondary-text-color); }
        .unavailable { opacity: .45; }
      </style>
      <ha-card>
        <div class="row">
          <div class="name"></div>
          <button class="icon" title="On / off"><ha-icon></ha-icon></button>
          <input class="slider" type="range" min="1" max="100" step="1" aria-label="Brightness">
          <button class="more" title="Options"><ha-icon icon="mdi:dots-vertical"></ha-icon></button>
        </div>
      </ha-card>`;
    const $ = (s) => this._root.querySelector(s);
    this._els = { row: $(".row"), name: $(".name"), icon: $(".icon"), bulb: $(".icon ha-icon"), slider: $(".slider") };

    this._els.icon.addEventListener("click", () => this._call("toggle", {}));
    this._els.slider.addEventListener("input", () => {
      this._dragging = true;
      this._paint(Number(this._els.slider.value), true);
    });
    this._els.slider.addEventListener("change", () => {
      this._dragging = false;
      this._call("turn_on", { brightness_pct: Number(this._els.slider.value) });
    });
    const moreInfo = () => this.dispatchEvent(new CustomEvent("hass-more-info", {
      detail: { entityId: this._config.entity }, bubbles: true, composed: true }));
    $(".more").addEventListener("click", moreInfo);
    this._els.name.addEventListener("click", moreInfo);
  }

  _call(service, data) {
    this._hass.callService("light", service, { entity_id: this._config.entity, ...data });
  }

  _paint(pct, on) {
    this._els.slider.style.setProperty("--pct", `${pct}%`);
    this._els.slider.style.setProperty("--fill", on ? "var(--bulb, #ffb300)" : "var(--disabled-text-color, #9e9e9e)");
  }

  _update() {
    if (!this._els || !this._hass) return;
    const st = this._hass.states[this._config.entity];
    const { row, name, icon, bulb, slider } = this._els;
    name.textContent = this._config.name || (st && st.attributes.friendly_name) || this._config.entity;
    bulb.setAttribute("icon", this._config.icon || (st && st.attributes.icon) || "mdi:lightbulb");
    const available = st && st.state !== "unavailable";
    row.classList.toggle("unavailable", !available);
    slider.disabled = icon.disabled = !available;
    if (!available) { this._paint(0, false); return; }
    const on = st.state === "on";
    const rgb = st.attributes.rgb_color;
    // The light's own colour, but never so dark the icon disappears.
    const colour = on && rgb && Math.max(...rgb) > 60 ? `rgb(${rgb.join(",")})` : "#ffb300";
    this.style.setProperty("--bulb", colour);
    icon.classList.toggle("on", on);
    if (!this._dragging) {
      const pct = on && st.attributes.brightness ? Math.max(1, Math.round(st.attributes.brightness / 2.55)) : 0;
      slider.value = String(pct || 1);
      this._paint(pct, on);
    }
  }

  getCardSize() { return 1; }

  getGridOptions() { return { columns: 12, rows: 1, min_columns: 6, min_rows: 1 }; }
}

customElements.define("room-row-card", RoomRowCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "room-row-card", name: "Room row", description: "Name, on/off bulb, slider and options in one line." });

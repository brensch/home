// The Plants dashboard, built from Home Assistant's own cards and charts.
//
// A dashboard strategy: Home Assistant asks it for the dashboard when the page loads, and it answers with ordinary
// native cards for whatever plants exist right now (any sensor.plant_<id>_status), so the dashboard follows the
// agent adding and retiring plants with nothing to edit. Reload the page to pick up a new plant.
//
//   - Status: every plant's status on one timeline (Home Assistant's history graph).
//   - Soil moisture: every plant with a probe on one chart.
//   - Then one section per plant: photo, summary, numbers, buttons.
//
// Installed as /local/plants-strategy.js; used as `strategy: {type: custom:plants}` at the top of the dashboard.

const STATUS_RE = /^sensor\.plant_(.+)_status$/;

function plants(hass) {
  return Object.keys(hass.states)
    .map((e) => e.match(STATUS_RE))
    .filter(Boolean)
    .map((m) => {
      const id = m[1];
      const st = hass.states[m[0]];
      return { id, name: st.attributes.name || st.attributes.friendly_name || id, attention: hass.states[`binary_sensor.plant_${id}_attention`]?.state === "on" };
    })
    .sort((a, b) => (b.attention - a.attention) || a.name.localeCompare(b.name));
}

const has = (hass, e) => e in hass.states;
const press = (entity) => ({ action: "perform-action", perform_action: "button.press", target: { entity_id: entity } });

class PlantsStrategy extends HTMLElement {
  static async generate(config, hass) {
    const hours = config.hours_to_show || 168;
    const ps = plants(hass);
    const withProbe = ps.filter((p) => {
      const s = hass.states[`sensor.plant_${p.id}_moisture`]?.state;
      return s !== undefined && s !== "unknown" && s !== "unavailable";
    });

    const overview = {
      type: "grid",
      column_span: 4,
      cards: [
        {
          type: "heading",
          heading: "Balcony",
          badges: [
            { type: "entity", entity: "sensor.plants_needing_attention", show_state: true, show_icon: true },
            { type: "entity", entity: "sensor.plants_last_round", show_state: true, show_icon: true },
          ],
        },
        {
          type: "history-graph",
          title: "Status",
          hours_to_show: hours,
          entities: ps.map((p) => ({ entity: `sensor.plant_${p.id}_status`, name: p.name })),
          grid_options: { columns: "full" },
        },
        {
          type: "history-graph",
          title: "Soil moisture",
          hours_to_show: hours,
          entities: (withProbe.length ? withProbe : ps).map((p) => ({ entity: `sensor.plant_${p.id}_moisture`, name: p.name })),
          grid_options: { columns: "full" },
        },
        {
          type: "tile",
          entity: "button.plants_round",
          name: "Do a round now",
          icon: "mdi:robot",
          tap_action: press("button.plants_round"),
          grid_options: { columns: 6 },
        },
      ],
    };

    const sections = ps.map((p) => {
      const e = (kind) => `sensor.plant_${p.id}_${kind}`;
      const cards = [
        {
          type: "heading",
          heading: p.name,
          tap_action: { action: "more-info" },
          badges: [{ type: "entity", entity: e("status"), show_state: true, show_icon: false, color: p.attention ? "red" : "green" }],
        },
      ];
      if (has(hass, `image.plant_${p.id}_photo`)) {
        cards.push({ type: "picture-entity", entity: `image.plant_${p.id}_photo`, show_name: false, show_state: false });
      }
      if (has(hass, e("summary"))) {
        cards.push({ type: "markdown", content: `{{ states('${e("summary")}') }}`, text_only: true });
      }
      const probe = !["unknown", "unavailable", undefined].includes(hass.states[e("moisture")]?.state);
      for (const [kind, name] of [["moisture", "Soil"], ["last_watered", "Watered"], ["height", "Height"], ["health", "Health"]]) {
        if (kind === "moisture" && !probe) continue;
        if (has(hass, e(kind))) cards.push({ type: "tile", entity: e(kind), name, grid_options: { columns: 6 } });
      }
      for (const [kind, name, icon] of [["watered", "Watered", "mdi:watering-can"], ["fertilised", "Fed", "mdi:sprout"], ["check", "Look now", "mdi:magnify"]]) {
        const b = `button.plant_${p.id}_${kind}`;
        if (has(hass, b)) cards.push({ type: "tile", entity: b, name, icon, hide_state: true, tap_action: press(b), grid_options: { columns: 4 } });
      }
      return { type: "grid", cards };
    });

    return {
      title: "Plants",
      views: [
        {
          title: "Plants",
          path: "plants",
          icon: "mdi:sprout",
          type: "sections",
          max_columns: 4,
          sections: [overview, ...sections],
        },
      ],
    };
  }
}

customElements.define("ll-strategy-dashboard-plants", PlantsStrategy);

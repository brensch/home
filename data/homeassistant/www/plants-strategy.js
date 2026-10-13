// The Plants dashboard, built from Home Assistant's own cards and charts.
//
// A dashboard strategy: Home Assistant asks it for the dashboard when the page loads, and it answers with ordinary
// native cards for whatever plants exist right now (any sensor.plant_<id>_status), so the dashboard follows the
// agent adding and retiring plants with nothing to edit. Reload the page to pick up a new plant.
//
// Made to show the whole balcony on a phone at once:
//   - the AI: "Inspect plants with AI", or what it's doing while it works, and a history button for its steps;
//   - the plants' photos two to a row, each with its name, status, soil, height and health along the bottom (and a
//     warning sign when it needs attention). Tap a number for its graph; tap the photo for the plant's own page:
//     the camera's live stream zoomed to just that plant beside its time-lapse, its judgement, and its graphs;
//   - then every plant's status over the week as bars, the balcony's time-lapse, and soil moisture. (Every film
//     from the first day is in Media > My media > plants.)
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

// The AI, from sensor.plants_ai (idle / queued / inspecting): while idle, a button to start an inspection, showing
// when the last one finished; otherwise the same spot shows what the agent says it is doing and since when, and
// can't be pressed. Beside it, a history button for every step it took. Conditional cards swap them live, without reloading the page.
function aiCards(hass) {
  const full = { columns: 9 }; // the rest of the row is the history button
  // What the AI did, step by step (its commands, the photos it looked at, what it said): the log sensor's popup,
  // whose activity list is exactly that.
  const history = {
    type: "button", entity: "sensor.plants_ai_log", icon: "mdi:history", name: "History", show_name: false,
    show_state: false, tap_action: { action: "more-info" }, hold_action: { action: "none" }, grid_options: { columns: 3, rows: 1 },
  };
  if (!has(hass, "sensor.plants_ai")) {
    return [{ type: "tile", entity: "button.plants_round", name: "Inspect plants with AI", icon: "mdi:robot", hide_state: true, tap_action: press("button.plants_round"), grid_options: full }, history];
  }
  return [
    {
      type: "conditional",
      conditions: [{ condition: "state", entity: "sensor.plants_ai", state: "idle" }],
      card: {
        type: "tile",
        entity: "sensor.plants_ai",
        name: "Inspect plants with AI",
        icon: "mdi:robot",
        color: "primary",
        state_content: ["last_changed"],
        tap_action: press("button.plants_round"),
        icon_tap_action: press("button.plants_round"),
      },
      grid_options: full,
    },
    {
      type: "conditional",
      conditions: [{ condition: "state", entity: "sensor.plants_ai", state_not: "idle" }],
      card: {
        type: "tile",
        entity: "sensor.plants_ai",
        name: "AI is inspecting the plants",
        icon: "mdi:robot-happy",
        color: "amber",
        state_content: ["activity", "last_changed"],
        tap_action: { action: "more-info" },
        icon_tap_action: { action: "none" },
      },
      grid_options: full,
    },
    history,
  ];
}

// A plant's photo with its numbers laid over the bottom: a dark bar with its name and status, and under that its
// soil, height and health. A warning sign in the corner when it needs attention. Tap a number for its graph (the
// sensor's own popup), anywhere else for the plant's page.
function plantCard(p, probed, page) {
  const s = (k) => `sensor.plant_${p.id}_${k}`;
  const open = { action: "navigate", navigation_path: page };
  // Elements are absolutely placed by their top-left corner (no default centring), as blocks so sizes apply.
  const base = { transform: "none", display: "block", color: "white", "text-shadow": "0 1px 3px rgba(0,0,0,.9)", "white-space": "nowrap", "box-sizing": "border-box" };
  // Padded so a finger can hit it; the padding is taken back off its position.
  const stat = (k, prefix, left) => ({
    type: "state-label", entity: s(k), prefix, tap_action: { action: "more-info" },
    style: { ...base, left: `calc(${left} - 8px)`, top: "auto", bottom: "0", "font-size": "12px", "line-height": "16px", padding: "4px 8px 6px" },
  });
  const elements = [
    // The photo, as an element: a picture-elements card only reacts to taps on its elements, never on its own
    // background, so the photo is laid over the card as an image element of the plant's photo entity.
    { type: "image", image_entity: `image.plant_${p.id}_photo`, tap_action: open, aspect_ratio: "1:1", style: { transform: "none", left: "0", top: "0", width: "100%" } },
    // The dark band along the bottom (a label for an entity with nothing in it but its background).
    { type: "state-label", entity: s("status"), tap_action: open, style: { ...base, left: "0", top: "auto", bottom: "0", width: "100%", height: "52px", color: "transparent", "text-shadow": "none", background: "linear-gradient(rgba(0,0,0,0), rgba(0,0,0,.85))", overflow: "hidden" } },
    // First line: name and status.
    {
      type: "state-label", entity: s("status"), prefix: `${p.name} · `, tap_action: open,
      style: { ...base, left: "10px", right: "10px", top: "auto", bottom: "24px", "font-size": "14px", "font-weight": "600", "line-height": "18px", padding: "0", overflow: "hidden", "text-overflow": "ellipsis" },
    },
    // Second line: soil, height, health, each opening its graph.
    ...(probed ? [stat("moisture", "💧", "10px")] : []),
    stat("height", "↕", probed ? "36%" : "10px"),
    stat("health", "♥", probed ? "68%" : "40%"),
    {
      type: "conditional",
      conditions: [{ condition: "state", entity: `binary_sensor.plant_${p.id}_attention`, state: "on" }],
      elements: [{ type: "icon", icon: "mdi:alert-circle", tap_action: open, style: { transform: "none", top: "8px", left: "auto", right: "8px", color: "#ff5252", "--mdc-icon-size": "24px" } }],
    },
  ];
  return { type: "picture-elements", image_entity: `image.plant_${p.id}_photo`, aspect_ratio: "1:1", elements, tap_action: open, grid_options: { columns: 6 } };
}

// The shape of a plant's photo, as an aspect ratio for a card: its "view" (left, top, right, bottom, from 0 to 1)
// of the camera's 16:9 frame.
const shape = ([x0, y0, x1, y1]) => `${((x1 - x0) * 16).toFixed(3)}:${((y1 - y0) * 9).toFixed(3)}`;

// The camera's live stream, zoomed to the part of the frame a plant's photo shows. The card has the photo's shape
// and the photo underneath (it shows until the stream starts); the stream is laid over it scaled up and shifted so
// only the plant shows, and the card clips the rest. Tap it for the whole frame (a camera's popup can't crop).
function liveCard(p, view, camera) {
  const [x0, y0, x1, y1] = view;
  const w = x1 - x0, h = y1 - y0;
  const pct = (v) => `${(v * 100).toFixed(3)}%`;
  return {
    type: "picture-elements",
    image_entity: `image.plant_${p.id}_photo`,
    aspect_ratio: shape(view),
    elements: [{
      type: "image", camera_image: camera, camera_view: "live", tap_action: { action: "more-info", entity: camera },
      style: { transform: "none", left: pct(-x0 / w), top: pct(-y0 / h), width: pct(1 / w) },
    }],
    grid_options: { columns: 6 },
  };
}

const graph = (title, entities, hours) => ({ type: "history-graph", title, hours_to_show: hours, entities, grid_options: { columns: "full" } });

// A plant's own page (a subview: a back arrow, no tab): the plant live beside its time-lapse, what the AI last
// said, and its graphs.
function plantView(hass, p, probed, back, camera, hours) {
  const s = (k) => `sensor.plant_${p.id}_${k}`;
  const view = hass.states[s("status")]?.attributes?.view;
  const seen = Array.isArray(view) && view.length === 4;
  const now = {
    type: "grid",
    cards: [
      { type: "heading", heading: p.name, icon: "mdi:sprout" },
      ...(seen ? [
        liveCard(p, view, camera),
        // The last 30 days, a photo an hour, on a loop. The films are letterboxed to one size for every plant;
        // filling the plant's own shape crops the bars back off.
        {
          type: "picture-entity", entity: `image.plant_${p.id}_timelapse`, show_name: false, show_state: false,
          aspect_ratio: shape(view), fit_mode: "cover", tap_action: { action: "more-info" }, grid_options: { columns: 6 },
        },
      ] : []),
      // Its status, and the AI's latest judgement in its popup.
      { type: "tile", entity: s("status"), name: "Status", state_content: ["state", "last_changed"], grid_options: { columns: "full" } },
    ],
  };
  const history = {
    type: "grid",
    cards: [
      { type: "heading", heading: "History", icon: "mdi:chart-line" },
      graph("Status", [{ entity: s("status"), name: p.name }], hours),
      ...(probed ? [graph("Soil moisture", [{ entity: s("moisture"), name: "Soil" }], hours)] : []),
      graph("Height", [{ entity: s("height"), name: "Height" }], 24 * 30),
      graph("Health", [{ entity: s("health"), name: "Health" }], 24 * 30),
    ],
  };
  return { title: p.name, path: `plant_${p.id}`, subview: true, back_path: back, type: "sections", max_columns: 2, sections: [now, history] };
}

class PlantsStrategy extends HTMLElement {
  static async generate(config, hass) {
    const hours = config.hours_to_show || 168;
    // The high-resolution stream: the live view is zoomed a long way in, and only plays on a plant's page.
    const camera = config.camera || "camera.balcony_4k";
    // This dashboard's address, for the plants' pages (/plants-dash/plant_basil).
    const dash = `/${location.pathname.split("/")[1]}`;
    const page = (p) => `${dash}/plant_${p.id}`;
    const ps = plants(hass);
    const probed = ps.filter((p) => hass.states[`sensor.plant_${p.id}_moisture`]?.state?.match(/^[0-9.]+$/));
    const photo = (p) => !["unknown", "unavailable", undefined].includes(hass.states[`image.plant_${p.id}_photo`]?.state);

    // The AI gets a section of its own, so on a wide screen its row isn't stretched to the height of the photos.
    const ai = {
      type: "grid",
      cards: [
        {
          type: "heading",
          heading: "Balcony",
          badges: [{ type: "entity", entity: "sensor.plants_needing_attention", show_state: true, show_icon: true }],
        },
        ...aiCards(hass),
      ],
    };

    const top = {
      type: "grid",
      column_span: 2,
      cards: [
        // Photos two to a row, each with its plant's numbers on it.
        ...ps.filter(photo).map((p) => plantCard(p, probed.includes(p), page(p))),
        // Plants off camera (probe only) have no photo: a tile each, opening the same page.
        ...ps.filter((p) => !photo(p)).map((p) => ({
          type: "tile", entity: `sensor.plant_${p.id}_status`, name: p.name, icon: "mdi:sprout",
          state_content: ["state"], grid_options: { columns: 6 }, tap_action: { action: "navigate", navigation_path: page(p) },
        })),
      ],
    };

    const more = {
      type: "grid",
      column_span: 2,
      cards: [
        { type: "heading", heading: "History", heading_style: "subtitle", icon: "mdi:chart-line" },
        // Every plant's status over the week, a row of coloured bars each.
        graph("Status", ps.map((p) => ({ entity: `sensor.plant_${p.id}_status`, name: p.name })), hours),
        // A heading can open something (it gets a chevron): the balcony's last 30 days, in a popup.
        { type: "heading", heading: "Balcony time-lapse", heading_style: "subtitle", icon: "mdi:filmstrip", tap_action: { action: "more-info", entity: "image.plants_timelapse" } },
        graph("Soil moisture", (probed.length ? probed : ps).map((p) => ({ entity: `sensor.plant_${p.id}_moisture`, name: p.name })), hours),
      ],
    };

    return {
      title: "Plants",
      views: [
        { title: "Plants", path: "plants", icon: "mdi:sprout", type: "sections", max_columns: 2, sections: [ai, top, more] },
        ...ps.map((p) => plantView(hass, p, probed.includes(p), `${dash}/plants`, camera, hours)),
      ],
    };
  }
}

customElements.define("ll-strategy-dashboard-plants", PlantsStrategy);

"""Native HA sensors for both Rengstorff sites and all their connectors."""
from datetime import timedelta
import json
import logging
import os
from pathlib import Path

import voluptuous as vol
from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator, UpdateFailed

from .api import ReadOnlyClient, Stopped

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({vol.Required("session_file"): cv.string})
WATCH_ENTITY = "input_boolean.opf_watch"


def save_session(path, tokens):
    temporary = str(path) + ".tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(tokens, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def load_files(path):
    tokens = json.loads(Path(path).read_text())
    if not isinstance(tokens.get("refresh_token"), str) or not tokens["refresh_token"]:
        raise ValueError("Missing OPF refresh token")
    sites = json.loads(Path(__file__).with_name("sites.json").read_text())
    return tokens, sites


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    path = config["session_file"]
    tokens, sites = await hass.async_add_executor_job(load_files, path)

    async def save(tokens):
        await hass.async_add_executor_job(save_session, path, tokens)

    client = ReadOnlyClient(async_get_clientsession(hass), tokens, sites, save)

    def set_interval():
        seconds = 30 if hass.states.is_state(WATCH_ENTITY, "on") else 120
        client.set_poll_interval(seconds)
        coordinator.update_interval = timedelta(seconds=seconds)

    async def update():
        try:
            if not client.stopped:
                set_interval()
            return await client.poll()
        except Stopped as error:
            coordinator.update_interval = None
            client.tokens["stopped"] = True
            await save(client.tokens)
            raise UpdateFailed(str(error)) from None
        except ValueError:
            raise UpdateFailed("OPF snapshot unavailable") from None

    coordinator = DataUpdateCoordinator(hass, logging.getLogger(__name__), name="opf_status",
                                        update_method=update, update_interval=timedelta(seconds=120))
    await coordinator.async_refresh()

    @callback
    def watch_changed(event):
        if client.stopped:
            return
        set_interval()
        hass.async_create_task(coordinator.async_request_refresh())

    unsubscribe = async_track_state_change_event(hass, [WATCH_ENTITY], watch_changed)
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda event: unsubscribe())
    if not client.stopped:
        set_interval()
    entities = [CountSensor(coordinator, site) for site in sites] + [CountSensor(coordinator, None)]
    entities += [LastCheckedSensor(coordinator)]
    entities += [ChargerSensor(coordinator, site, charger) for site in sites for charger in site["chargers"]]
    async_add_entities(entities)


class ChargerSensor(CoordinatorEntity, SensorEntity):
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator, site, charger):
        super().__init__(coordinator)
        self.site, self.charger = site, charger
        self._attr_name = f"OPF {charger['alias']}"
        self._attr_unique_id = f"opf_{charger['identifier']}_{charger['connector']}"

    @property
    def native_value(self):
        data = (self.coordinator.data or {}).get(self.site["building_id"], {})
        return data.get("states", {}).get((self.charger["identifier"], str(self.charger["connector"])))

    @property
    def extra_state_attributes(self):
        return {"site": self.site["name"], "charger_identifier": self.charger["identifier"],
                "connector": self.charger["connector"]}


class CountSensor(CoordinatorEntity, SensorEntity):
    _attr_icon = "mdi:ev-station"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "chargers"
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator, site):
        super().__init__(coordinator)
        self.site = site
        key = str(site["building_id"]) if site else "all"
        name = site["name"] if site else "Rengstorff"
        self._attr_name = f"OPF {name} free"
        self._attr_unique_id = f"opf_{key}_free"

    def snapshots(self):
        data = self.coordinator.data or {}
        return [data.get(self.site["building_id"], {})] if self.site else list(data.values())

    @property
    def native_value(self):
        data = self.snapshots()
        if not data or any(d.get("free") is None for d in data):
            return None
        return sum(d["free"] for d in data)

    @property
    def extra_state_attributes(self):
        data = self.snapshots()
        return {"total": sum(d.get("total", 0) for d in data),
                "available_chargers": sorted(a for d in data for a in d.get("available_aliases", []))}


class LastCheckedSensor(CoordinatorEntity, SensorEntity):
    _attr_name = "OPF last checked"
    _attr_unique_id = "opf_last_checked"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        from datetime import datetime
        data = self.coordinator.data or {}
        checked = min((d["checked_at"] for d in data.values()), default=None)
        return datetime.fromisoformat(checked) if checked else None

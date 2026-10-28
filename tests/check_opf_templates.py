"""Run isolated template checks with: docker exec -i homeassistant python < tests/check_opf_templates.py"""
import asyncio
from datetime import datetime, timedelta, timezone
import homeassistant
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from homeassistant.util.unit_system import METRIC_SYSTEM
import yaml

async def main():
    hass=HomeAssistant('/tmp/opf-template-validation')
    hass.config.units=METRIC_SYSTEM
    automations=yaml.safe_load(open('/config/automations.yaml'))
    alert=next(a for a in automations if a.get('id')=='opf_notify_approaching_home')
    condition=Template(alert['triggers'][0]['value_template'],hass)
    message=Template(alert['actions'][0]['data']['message'],hass)
    now=datetime.now(timezone.utc)
    hass.states.async_set('zone.home','0',{'latitude':37.4,'longitude':-122.1})
    hass.states.async_set('person.brend','not_home',{'latitude':37.41,'longitude':-122.1})
    hass.states.async_set('input_boolean.opf_commute_pending','on')
    hass.states.async_set('input_datetime.opf_left_work',now.isoformat())
    assert condition.async_render() is True
    hass.states.async_set('person.brend','not_home',{'latitude':37.45,'longitude':-122.1})
    assert condition.async_render() is False
    hass.states.async_set('person.brend','not_home',{'latitude':37.41,'longitude':-122.1})
    hass.states.async_set('input_datetime.opf_left_work',(now-timedelta(hours=5)).isoformat())
    assert condition.async_render() is False
    hass.states.async_set('input_datetime.opf_left_work',now.isoformat())
    hass.states.async_set('input_boolean.opf_commute_pending','off')
    assert condition.async_render() is False
    hass.states.async_set('input_boolean.opf_commute_pending','on')
    hass.states.async_set('person.brend','unknown')
    assert condition.async_render() is False
    hass.states.async_set('sensor.opf_community_center_free','2',{'total':9,'available_chargers':['099102','099109']})
    hass.states.async_set('sensor.opf_aquatics_center_free','1',{'total':4,'available_chargers':['990804']})
    hass.states.async_set('sensor.opf_last_checked',now.isoformat())
    text=' '.join(str(message.async_render()).split())
    assert 'Community Center: 2 of 9 free' in text and 'Aquatics Center: 1 of 4 free' in text
    print('Fresh message:',text)
    hass.states.async_set('sensor.opf_last_checked',(now-timedelta(minutes=6)).isoformat())
    assert str(message.async_render()).count('availability unknown')==2
    hass.states.async_set('sensor.opf_last_checked',now.isoformat())
    hass.states.async_set('sensor.opf_community_center_free','unavailable')
    assert str(message.async_render()).count('availability unknown')==1
    print('8 checks passed: near/far/expired/disarmed/no GPS/fresh/stale/unavailable')

asyncio.run(main())

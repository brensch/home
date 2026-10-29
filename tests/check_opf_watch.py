"""Isolated HA watch tests. Captures notifications locally; sends no phone pushes.

Run: docker exec -i homeassistant python < tests/check_opf_watch.py
"""
import asyncio
import homeassistant
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.template import Template
from homeassistant.helpers.script import Script
import yaml


async def main():
    hass = HomeAssistant('/tmp/opf-watch-validation')
    automation = next(row for row in yaml.safe_load(open('/config/automations.yaml'))
                      if row.get('id') == 'opf_watch_new_availability')
    calls = []

    async def capture(call):
        calls.append((call.service, dict(call.data)))

    hass.services.async_register('notify', 'mobile_app_schlixel', capture)
    hass.services.async_register('notify', 'send_message', capture)
    hass.states.async_set('notify.alyssas_iphone', 'unknown')
    check = Template(automation['conditions'][1]['value_template'], hass)

    def variables(before, after, old_aliases, new_aliases, site='community'):
        entity = f'sensor.opf_{"community_center" if site == "community" else "aquatics_center"}_free'
        trigger = {'entity_id': entity,
                   'from_state': State(entity, before, {'available_chargers': old_aliases}),
                   'to_state': State(entity, after, {'available_chargers': new_aliases, 'total': 8 if site == 'community' else 4})}
        values = {'trigger': trigger}
        for name, text in automation['variables'].items():
            values[name] = Template(text, hass).async_render(values)
        return values

    # New opening from a full site, another opening, and a swap with the same count.
    for values, expected in [
        (variables('0', '1', [], ['099102']), ['099102']),
        (variables('1', '2', ['099101'], ['099101', '099102']), ['099102']),
        (variables('1', '1', ['099101'], ['099102']), ['099102']),
        (variables('1', '1', ['099102'], ['099102']), []),
        (variables('1', '0', ['099102'], []), []),
    ]:
        assert values['newly_free'] == expected, values
        assert check.async_render(values) is bool(expected)
    assert check.async_render(variables('unavailable', '1', [], ['099102'])) is False
    assert check.async_render(variables('0', 'unknown', [], [])) is False
    for site in ('community', 'aquatics'):
        values = variables('0', '1', [], ['099102' if site == 'community' else '990804'], site)
        calls.clear()
        actions = cv.SCRIPT_SCHEMA(automation['actions'])
        await Script(hass, actions, 'OPF isolated test', 'automation').async_run(values, context=Context())
        assert len(calls) == 2, calls
        assert calls[0][0] == 'mobile_app_schlixel'
        assert calls[1][0] == 'send_message'
        assert calls[1][1]['entity_id'] == ['notify.alyssas_iphone'], calls
        assert values['site_name'] in calls[0][1]['message']
        assert calls[0][1]['message'] == calls[1][1]['message']
        print('Captured both recipients:', ' '.join(calls[0][1]['message'].split()))
    assert automation['conditions'][0] == {'condition': 'state', 'entity_id': 'input_boolean.opf_watch', 'state': 'on'}
    print('Watch transition, recovery, recipient and action checks passed. No notifications sent.')


asyncio.run(main())

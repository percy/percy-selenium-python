import os
import platform
import functools
import json
import requests

from selenium.webdriver import __version__ as SELENIUM_VERSION
from percy.version import __version__ as SDK_VERSION

# Collect client and environment information
CLIENT_INFO = 'percy-selenium-python/' + SDK_VERSION
ENV_INFO = ['selenium/' + SELENIUM_VERSION, 'python/' + platform.python_version()]

# Maybe get the CLI API address from the environment
PERCY_CLI_API = os.environ.get('PERCY_CLI_API') or 'http://localhost:5338'
PERCY_DEBUG = os.environ.get('PERCY_LOGLEVEL') == 'debug'

# for logging
LABEL = '[\u001b[35m' + ('percy:python' if PERCY_DEBUG else 'percy') + '\u001b[39m]'

# Check if Percy is enabled, caching the result so it is only checked once
@functools.cache
def is_percy_enabled():
    try:
        response = requests.get(f'{PERCY_CLI_API}/percy/healthcheck')
        response.raise_for_status()
        data = response.json()

        if not data['success']: raise Exception(data['error'])
        return True
    except Exception as e:
        print(f'{LABEL} Percy is not running, disabling snapshots')
        if PERCY_DEBUG: print(f'{LABEL} {e}')
        return False

# Fetch the @percy/dom script, caching the result so it is only fetched once
@functools.cache
def fetch_percy_dom():
    response = requests.get(f'{PERCY_CLI_API}/percy/dom.js')
    response.raise_for_status()
    return response.text

# Take a DOM snapshot and post it to the snapshot endpoint
def percy_snapshot(driver, name, **kwargs):
    if not is_percy_enabled(): return

    try:
        # Inject the DOM serialization script
        driver.execute_script(fetch_percy_dom())

        # Serialize and capture the DOM
        dom_snapshot = driver.execute_script(f'return PercyDOM.serialize({json.dumps(kwargs)})')

        # Post the DOM to the snapshot endpoint with snapshot options and other info
        response = requests.post(f'{PERCY_CLI_API}/percy/snapshot', json=dict(**kwargs, **{
            'domSnapshot': dom_snapshot,
            'clientInfo': CLIENT_INFO,
            'environmentInfo': ENV_INFO,
            'url': driver.current_url,
            'name': name
        }))

        # Handle errors
        response.raise_for_status()
        data = response.json()

        if not data['success']: raise Exception(data['error'])
    except Exception as e:
        print(f'{LABEL} Could not take DOM snapshot "{name}"')
        print(f'{LABEL} {e}')

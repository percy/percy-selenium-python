import os
import platform
import requests

from selenium.webdriver import __version__ as SELENIUM_VERSION
from percy.version import __version__ as SDK_VERSION

# for logging
LABEL = '[\u001b[35mpercy\u001b[39m] '

# Collect client and environment information
CLIENT_INFO = 'percy-selenium-python/' + SDK_VERSION
ENV_INFO = ['selenium/' + SELENIUM_VERSION, 'python/' + platform.python_version()]

# Maybe get the CLI API address from the environment
PERCY_CLI_API = os.environ.get('PERCY_CLI_API') or 'http://localhost:5338/percy'
PERCY_LOGLEVEL = os.environ.get('PERCY_LOGLEVEL') or 'info'

# Cache @percy/dom script to avoid extraneous requests
PERCY_DOM_SCRIPT = None

# Check if Percy is enabled while caching the @percy/dom script
def isPercyEnabled():
    global PERCY_DOM_SCRIPT

    if PERCY_DOM_SCRIPT is None:
        try:
            r = requests.get(PERCY_CLI_API + '/dom.js')
            r.raise_for_status()
            PERCY_DOM_SCRIPT = r.text
        except Exception as e:
            if PERCY_LOGLEVEL == 'debug': print(e)
            PERCY_DOM_SCRIPT = ''

        if not PERCY_DOM_SCRIPT:
            print(LABEL + 'Percy is not running, disabling snapshots')

    return bool(PERCY_DOM_SCRIPT)

# Take a DOM snapshot and post it to the snapshot endpoint
def percySnapshot(driver, name, **kwargs):
    if not isPercyEnabled(): return

    try:
        # Inject the DOM serialization script
        driver.execute_script(PERCY_DOM_SCRIPT)

        # Post the DOM to the snapshot endpoint with snapshot options and other info
        r = requests.post(PERCY_CLI_API + '/snapshot', json=dict(**kwargs, **{
            'domSnapshot': driver.execute_script('return PercyDOM.serialize()'),
            'clientInfo': CLIENT_INFO,
            'environmentInfo': ENV_INFO,
            'url': driver.current_url,
            'name': name
        })).json()

        # Handle errors
        if not r['success']: raise Exception(r['error'])
    except Exception as e:
        print(LABEL + 'Could not take DOM snapshot "' + name + '"')
        if PERCY_LOGLEVEL == 'debug': print(e)
        return

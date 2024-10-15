import os
import platform
import json
from functools import lru_cache
from time import sleep
import requests

from selenium.webdriver import __version__ as SELENIUM_VERSION
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from percy.version import __version__ as SDK_VERSION
from percy.driver_metadata import DriverMetaData

# Collect client and environment information
CLIENT_INFO = 'percy-selenium-python/' + SDK_VERSION
ENV_INFO = ['selenium/' + SELENIUM_VERSION, 'python/' + platform.python_version()]

# Maybe get the CLI API address from the environment
PERCY_CLI_API = os.environ.get('PERCY_CLI_API') or 'http://localhost:5338'
PERCY_DEBUG = os.environ.get('PERCY_LOGLEVEL') == 'debug'
RESONSIVE_CAPTURE_SLEEP_TIME = os.environ.get('RESONSIVE_CAPTURE_SLEEP_TIME')

# for logging
LABEL = '[\u001b[35m' + ('percy:python' if PERCY_DEBUG else 'percy') + '\u001b[39m]'
CDP_SUPPORT_SELENIUM = (str(SELENIUM_VERSION)[0].isdigit() and int(
    str(SELENIUM_VERSION)[0]) >= 4) if SELENIUM_VERSION else False

def log(message, lvl = 'info'):
    message = f'{LABEL} {message}'
    try:
        requests.post(f'{PERCY_CLI_API}/percy/log',
                    json={'message': message, 'level': lvl}, timeout=1)
    except Exception as e:
        if PERCY_DEBUG: print(f'Sending log to CLI Failed {e}')
    finally:
        # Only log if lvl is 'debug' and PERCY_DEBUG is True
        if lvl != 'debug' or PERCY_DEBUG:
            print(message)

# Check if Percy is enabled, caching the result so it is only checked once
@lru_cache(maxsize=None)
def is_percy_enabled():
    try:
        response = requests.get(f'{PERCY_CLI_API}/percy/healthcheck', timeout=30)
        response.raise_for_status()
        data = response.json()
        session_type =  data.get('type', None)
        widths = data.get('widths', {})
        config = data.get('config', {})

        if not data['success']: raise Exception(data['error'])
        version = response.headers.get('x-percy-core-version')

        if not version:
            print(f'{LABEL} You may be using @percy/agent '
                  'which is no longer supported by this SDK. '
                  'Please uninstall @percy/agent and install @percy/cli instead. '
                  'https://www.browserstack.com/docs/percy/migration/migrate-to-cli')
            return False

        if version.split('.')[0] != '1':
            print(f'{LABEL} Unsupported Percy CLI version, {version}')
            return False

        return {
            'session_type': session_type,
            'config': config,
            'widths': widths
        }
    except Exception as e:
        print(f'{LABEL} Percy is not running, disabling snapshots')
        if PERCY_DEBUG: print(f'{LABEL} {e}')
        return False

# Fetch the @percy/dom script, caching the result so it is only fetched once
@lru_cache(maxsize=None)
def fetch_percy_dom():
    response = requests.get(f'{PERCY_CLI_API}/percy/dom.js', timeout=30)
    response.raise_for_status()
    return response.text

def get_serialized_dom(driver, cookies, **kwargs):
    dom_snapshot = driver.execute_script(f'return PercyDOM.serialize({json.dumps(kwargs)})')
    dom_snapshot['cookies'] = cookies
    return dom_snapshot

def get_widths_for_multi_dom(eligible_widths, **kwargs):
    user_passed_widths = kwargs.get('widths', [])
    width = kwargs.get('width')
    if width: user_passed_widths = [width]

    # Deep copy mobile widths otherwise it will get overridden
    allWidths = eligible_widths.get('mobile', [])[:]
    if len(user_passed_widths) != 0:
        allWidths.extend(user_passed_widths)
    else:
        allWidths.extend(eligible_widths.get('config', []))
    return list(set(allWidths))

def change_window_dimension_and_wait(driver, width, height, resizeCount):
    try:
        if CDP_SUPPORT_SELENIUM and driver.capabilities['browserName'] == 'chrome':
            driver.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', { 'height': height,
                                'width': width, 'deviceScaleFactor': 1, 'mobile': False })
        else:
            driver.set_window_size(width, height)
    except Exception as e:
        log(f'Resizing using cdp failed falling back driver for width {width} {e}', 'debug')
        driver.set_window_size(width, height)

    try:
        WebDriverWait(driver, 1).until(
            lambda driver: driver.execute_script("return window.resizeCount") == resizeCount
        )
    except TimeoutException:
        log(f"Timed out waiting for window resize event for width {width}", 'debug')


def capture_responsive_dom(driver, eligible_widths, cookies, **kwargs):
    widths = get_widths_for_multi_dom(eligible_widths, **kwargs)
    dom_snapshots = []
    window_size = driver.get_window_size()
    current_width, current_height = window_size['width'], window_size['height']
    last_window_width = current_width
    resize_count = 0
    driver.execute_script("PercyDOM.waitForResize()")

    for width in widths:
        if last_window_width != width:
            resize_count += 1
            change_window_dimension_and_wait(driver, width, current_height, resize_count)
            last_window_width = width

        if RESONSIVE_CAPTURE_SLEEP_TIME: sleep(int(RESONSIVE_CAPTURE_SLEEP_TIME))
        dom_snapshot = get_serialized_dom(driver, cookies, **kwargs)
        dom_snapshot['width'] = width
        dom_snapshots.append(dom_snapshot)

    change_window_dimension_and_wait(driver, current_width, current_height, resize_count + 1)
    return dom_snapshots

def is_responsive_snapshot_capture(config, **kwargs):
    # Don't run resposive snapshot capture when defer uploads is enabled
    if 'percy' in config and config['percy'].get('deferUploads', False): return False

    return kwargs.get('responsive_snapshot_capture', False) or kwargs.get(
            'responsiveSnapshotCapture', False) or (
                'snapshot' in config and config['snapshot'].get('responsiveSnapshotCapture'))

# Take a DOM snapshot and post it to the snapshot endpoint
def percy_snapshot(driver, name, **kwargs):
    data = is_percy_enabled()
    if not data: return None

    if data['session_type'] == "automate": raise Exception("Invalid function call - "\
      "percy_snapshot(). Please use percy_screenshot() function while using Percy with Automate. "\
      "For more information on usage of PercyScreenshot, "\
      "refer https://www.browserstack.com/docs/percy/integrate/functional-and-visual")

    try:
        # Inject the DOM serialization script
        driver.execute_script(fetch_percy_dom())
        cookies = driver.get_cookies()

        # Serialize and capture the DOM
        if is_responsive_snapshot_capture(data['config'], **kwargs):
            dom_snapshot = capture_responsive_dom(driver, data['widths'], cookies, **kwargs)
        else:
            dom_snapshot = get_serialized_dom(driver, cookies, **kwargs)

        # Post the DOM to the snapshot endpoint with snapshot options and other info
        response = requests.post(f'{PERCY_CLI_API}/percy/snapshot', json={**kwargs, **{
            'client_info': CLIENT_INFO,
            'environment_info': ENV_INFO,
            'dom_snapshot': dom_snapshot,
            'url': driver.current_url,
            'name': name
        }}, timeout=600)

        # Handle errors
        response.raise_for_status()
        data = response.json()

        if not data['success']: raise Exception(data['error'])
        return data.get("data", None)
    except Exception as e:
        log(f'Could not take DOM snapshot "{name}"')
        log(f'{e}')
        return None

# Take screenshot on driver
def percy_automate_screenshot(driver, name, options = None, **kwargs):
    data = is_percy_enabled()
    if not data: return None

    if data['session_type'] != "automate": raise Exception("Invalid function call - "\
      "percy_screenshot(). Please use percy_snapshot() function for taking screenshot. "\
      "percy_screenshot() should be used only while using Percy with Automate. "\
      "For more information on usage of percy_snapshot(), "\
      "refer doc for your language https://www.browserstack.com/docs/percy/integrate/overview")

    if options is None:
        options = {}

    try:
        metadata = DriverMetaData(driver)
        if 'ignoreRegionSeleniumElements' in options:
            options['ignore_region_selenium_elements'] = options['ignoreRegionSeleniumElements']
            options.pop('ignoreRegionSeleniumElements')

        if 'considerRegionSeleniumElements' in options:
            options['consider_region_selenium_elements'] = options['considerRegionSeleniumElements']
            options.pop('considerRegionSeleniumElements')

        ignore_region_elements = get_element_ids(
            options.get("ignore_region_selenium_elements", [])
        )
        consider_region_elements = get_element_ids(
            options.get("consider_region_selenium_elements", [])
        )
        options.pop("ignore_region_selenium_elements", None)
        options.pop("consider_region_selenium_elements", None)
        options["ignore_region_elements"] = ignore_region_elements
        options["consider_region_elements"] = consider_region_elements

        # Post to automateScreenshot endpoint with driver options and other info
        response = requests.post(f'{PERCY_CLI_API}/percy/automateScreenshot', json={**kwargs, **{
            'client_info': CLIENT_INFO,
            'environment_info': ENV_INFO,
            'sessionId': metadata.session_id,
            'commandExecutorUrl': metadata.command_executor_url,
            'capabilities': metadata.capabilities,
            'snapshotName': name,
            'options': options
        }}, timeout=600)

        # Handle errors
        response.raise_for_status()
        data = response.json()

        if not data['success']: raise Exception(data['error'])
        return data.get("data", None)
    except Exception as e:
        log(f'Could not take Screenshot "{name}"')
        log(f'{e}')
        return None

def get_element_ids(elements):
    return [element.id for element in elements]

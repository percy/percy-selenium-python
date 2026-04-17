import os
import platform
import json
from contextlib import contextmanager
from functools import lru_cache
from time import sleep
from urllib.parse import urlparse, urljoin
import requests

from selenium.webdriver import __version__ as SELENIUM_VERSION
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from percy.version import __version__ as SDK_VERSION
from percy.driver_metadata import DriverMetaData

# Collect client and environment information
CLIENT_INFO = 'percy-selenium-python/' + SDK_VERSION
ENV_INFO = ['selenium/' + SELENIUM_VERSION, 'python/' + platform.python_version()]

def _get_bool_env(key):
    return os.environ.get(key, "").lower() == "true"

# Maybe get the CLI API address from the environment
PERCY_CLI_API = os.environ.get('PERCY_CLI_API') or 'http://localhost:5338'
PERCY_DEBUG = os.environ.get('PERCY_LOGLEVEL') == 'debug'
RESPONSIVE_CAPTURE_SLEEP_TIME = (
    os.environ.get('RESPONSIVE_CAPTURE_SLEEP_TIME') or
    os.environ.get('RESONSIVE_CAPTURE_SLEEP_TIME')
)
PERCY_RESPONSIVE_CAPTURE_MIN_HEIGHT = _get_bool_env("PERCY_RESPONSIVE_CAPTURE_MIN_HEIGHT")
PERCY_RESPONSIVE_CAPTURE_RELOAD_PAGE = _get_bool_env("PERCY_RESPONSIVE_CAPTURE_RELOAD_PAGE")
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

# pylint: disable=too-many-arguments, too-many-branches, too-many-locals
def create_region(
    boundingBox=None,
    elementXpath=None,
    elementCSS=None,
    padding=None,
    algorithm="ignore",
    diffSensitivity=None,
    imageIgnoreThreshold=None,
    carouselsEnabled=None,
    bannersEnabled=None,
    adsEnabled=None,
    diffIgnoreThreshold=None
    ):

    element_selector = {}
    if boundingBox:
        element_selector["boundingBox"] = boundingBox
    if elementXpath:
        element_selector["elementXpath"] = elementXpath
    if elementCSS:
        element_selector["elementCSS"] = elementCSS

    region = {
        "algorithm": algorithm,
        "elementSelector": element_selector
    }

    if padding:
        region["padding"] = padding

    if algorithm in ["standard", "intelliignore"]:
        config_values = {
            "diffSensitivity": diffSensitivity,
            "imageIgnoreThreshold": imageIgnoreThreshold,
            "carouselsEnabled": carouselsEnabled,
            "bannersEnabled": bannersEnabled,
            "adsEnabled": adsEnabled,
        }
        configuration = {k: v for k, v in config_values.items() if v is not None}
        if configuration:
            region["configuration"] = configuration

    assertion = {}
    if diffIgnoreThreshold is not None:
        assertion["diffIgnoreThreshold"] = diffIgnoreThreshold

    if assertion:
        region["assertion"] = assertion

    return region

@contextmanager
def iframe_context(driver, frame_element):
    """Safely switches to an iframe and always switches back to the parent."""
    driver.switch_to.frame(frame_element)
    try:
        yield
    finally:
        driver.switch_to.parent_frame()

def process_frame(driver, frame_element, options, percy_dom_script):
    """Processes a single cross-origin frame to capture its snapshot."""
    frame_url = frame_element.get_attribute('src') or "unknown-src"
    with iframe_context(driver, frame_element):
        try:
            # Inject Percy DOM into the cross-origin frame context
            driver.execute_script(percy_dom_script)
            # Serialize inside the frame.
            # enableJavaScript=True is required to handle CORS iframes manually.
            iframe_options = {**options, 'enableJavaScript': True}
            iframe_snapshot = driver.execute_script(
                f"return PercyDOM.serialize({json.dumps(iframe_options)})"
            )
        except Exception as e:
            log(f"Failed to process cross-origin frame {frame_url}: {e}", "debug")
            return None
    # Back in parent context: find the percyElementId created by the main page serialization
    percy_element_id = frame_element.get_attribute('data-percy-element-id')
    if not percy_element_id:
        log(f"Skipping frame {frame_url}: no matching percyElementId found", "debug")
        return None
    return {
        "iframeData": {"percyElementId": percy_element_id},
        "iframeSnapshot": iframe_snapshot,
        "frameUrl": frame_url
    }


def _is_unsupported_iframe_src(frame_src):
    return (
        not frame_src or
        frame_src == "about:blank" or
        frame_src.startswith("javascript:") or
        frame_src.startswith("data:") or
        frame_src.startswith("vbscript:")
    )


def _get_origin(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"

def _wait_for_ready(driver, kwargs):
    """Run readiness checks before serialize. PER-7348.

    Sends PercyDOM.waitForReady via execute_async_script. The script checks
    typeof PercyDOM.waitForReady in-browser so older CLI versions without the
    method are a graceful no-op. Any failure is caught and logged at debug;
    serialize still runs.

    Readiness config precedence: kwargs['readiness'] > cached
    percy.config.snapshot.readiness > {} (CLI falls back to balanced default).
    If preset is 'disabled', skip the async script call entirely.
    """
    readiness_config = kwargs.get('readiness')
    if readiness_config is None:
        data = is_percy_enabled()
        if isinstance(data, dict):
            readiness_config = (data.get('config') or {}).get('snapshot', {}).get('readiness', {}) or {}
        else:
            readiness_config = {}
    if isinstance(readiness_config, dict) and readiness_config.get('preset') == 'disabled':
        return
    try:
        driver.execute_async_script(
            'var config = ' + json.dumps(readiness_config) + ';'
            'var done = arguments[arguments.length - 1];'
            'try {'
            "  if (typeof PercyDOM !== 'undefined' && typeof PercyDOM.waitForReady === 'function') {"
            '    PercyDOM.waitForReady(config).then(function(r){ done(r); }).catch(function(){ done(); });'
            '  } else { done(); }'
            '} catch(e) { done(); }'
        )
    except Exception as e:
        log(f'waitForReady failed, proceeding to serialize: {e}', 'debug')


def get_serialized_dom(driver, cookies, percy_dom_script=None, **kwargs):
    # 0. Readiness gate before serialize (PER-7348). Graceful on old CLI.
    _wait_for_ready(driver, kwargs)
    # 1. Serialize the main page first (this adds the data-percy-element-ids)
    dom_snapshot = driver.execute_script(f'return PercyDOM.serialize({json.dumps(kwargs)})')
    # 2. Process CORS IFrames
    try:
        page_origin = _get_origin(driver.current_url)
        iframes = driver.find_elements("tag name", "iframe")
        if iframes and percy_dom_script:
            processed_frames = []
            for frame in iframes:
                frame_src = frame.get_attribute('src')
                if _is_unsupported_iframe_src(frame_src):
                    continue

                try:
                    frame_origin = _get_origin(urljoin(driver.current_url, frame_src))
                except Exception as e:
                    log(f"Skipping iframe \"{frame_src}\": {e}", "debug")
                    continue

                if frame_origin == page_origin:
                    continue

                result = process_frame(driver, frame, kwargs, percy_dom_script)
                if result:
                    processed_frames.append(result)

            if processed_frames:
                dom_snapshot['corsIframes'] = processed_frames
    except Exception as e:
        log(f"Failed to process cross-origin iframes: {e}", "debug")

    dom_snapshot['cookies'] = cookies
    return dom_snapshot

def get_responsive_widths(widths=None):
    if widths is None:
        widths = []
    try:
        widths_list = widths if isinstance(widths, list) else []
        query_param = f"?widths={','.join(map(str, widths_list))}" if widths_list else ""
        response = requests.get(
            f"{PERCY_CLI_API}/percy/widths-config{query_param}",
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        widths_data = data.get("widths")
        if not isinstance(widths_data, list):
            msg = "Update Percy CLI to the latest version to use responsiveSnapshotCapture"
            raise Exception(msg)
        return widths_data
    except Exception as e:
        log(f"Failed to get responsive widths: {e}.", "debug")
        msg = "Update Percy CLI to the latest version to use responsiveSnapshotCapture"
        raise Exception(msg) from e

def _setup_resize_listener(driver):
    """Initializes the resize counter and attaches a named listener to avoid duplicates."""
    driver.execute_script("""
        const handler = window._percyResizeHandler;
         if (handler) {
             window.removeEventListener('resize', handler);
         }
        window._percyResizeHandler = () => { window.resizeCount++; };
        window.resizeCount = 0;
        window.addEventListener('resize', window._percyResizeHandler);
    """)

def change_window_dimension_and_wait(driver, width, height, resizeCount):
    try:
        if CDP_SUPPORT_SELENIUM and driver.capabilities['browserName'] == 'chrome':
            print(f'Attempting to resize using CDP for width {width} and height {height}')
            driver.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', { 'height': height,
                                'width': width, 'deviceScaleFactor': 1, 'mobile': False })
        else:
            #driver.execute_script(f"window.resizeTo({width}, {height});")
            driver.set_window_size(width, height)
    except Exception as e:
        log(f'Resizing using cdp failed falling back driver for width {width} {e}', 'debug')
        print(
            f'Error during CDP resize: {e}, '
            f'falling back to driver resize for width {width} and height {height}'
        )
        #driver.execute_script(f"window.resizeTo({width}, {height});")
        driver.set_window_size(width, height)
    print(f'Resized to {width}x{height}, waiting for resize event...')

    try:
        WebDriverWait(driver, 1).until(
            lambda driver: driver.execute_script("return window.resizeCount") == resizeCount
        )
    except TimeoutException:
        log(f"Timed out waiting for window resize event for width {width}", 'debug')

def _responsive_sleep():
    if not RESPONSIVE_CAPTURE_SLEEP_TIME:
        return
    try:
        secs = int(RESPONSIVE_CAPTURE_SLEEP_TIME)
        if secs > 0:
            sleep(secs)
    except (TypeError, ValueError):
        pass

def capture_responsive_dom(driver, cookies, config, percy_dom_script=None, **kwargs):
    widths = get_responsive_widths(kwargs.get('widths'))
    log(widths, 'debug')
    dom_snapshots = []
    window_size = driver.get_window_size()
    current_width, current_height = window_size['width'], window_size['height']
    log(f'Before window size: {current_width}x{current_height}', 'debug')
    last_window_width = current_width
    resize_count = 0
    # Initialize resize listener once before the loop
    driver.execute_script("PercyDOM.waitForResize()")
    target_height = current_height

    if PERCY_RESPONSIVE_CAPTURE_MIN_HEIGHT:
        target_height = kwargs.get('minHeight') or config.get('snapshot', {}).get('minHeight')
        if target_height:
            try:
                target_height = int(target_height)
            except (TypeError, ValueError):
                log(
                     f'Invalid minHeight value {target_height!r}; expected integer, '
                     'using current window height instead.',
                     'debug',
                 )
    for width_dict in widths:
        width = width_dict['width']
        height = width_dict.get('height', target_height)
        print(f'Capturing responsive snapshot for width: {width} and height: {height}')
        if last_window_width != width:
            resize_count += 1
            change_window_dimension_and_wait(driver, width, height, resize_count)
            last_window_width = width

        if PERCY_RESPONSIVE_CAPTURE_RELOAD_PAGE:
            log(f'Reloading page for width: {width}', 'debug')
            driver.refresh()
            driver.execute_script(percy_dom_script)
            _setup_resize_listener(driver)
            driver.execute_script("PercyDOM.waitForResize();")
            resize_count = 0 # Reset count because the listener just started fresh
        print(f'{width}x{height} ready, taking snapshot...')
        _responsive_sleep()
        dom_snapshot = get_serialized_dom(
            driver, cookies, percy_dom_script=percy_dom_script, **kwargs)
        dom_snapshot['width'] = width
        print(f'Taken snapshot for width: {width}, height: {height}')
        dom_snapshots.append(dom_snapshot)
    with open("output_file.json", "w", encoding="utf-8") as file_handle:
        json.dump(dom_snapshots, file_handle, indent=4)
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
        percy_dom_script = fetch_percy_dom()
        driver.execute_script(percy_dom_script)
        cookies = driver.get_cookies()

        # Serialize and capture the DOM
        if is_responsive_snapshot_capture(data['config'], **kwargs):
            dom_snapshot = capture_responsive_dom(
                driver=driver,
                cookies=cookies,
                config=data['config'],
                percy_dom_script=percy_dom_script,
                **kwargs,
            )
        else:
            dom_snapshot = get_serialized_dom(
                driver, cookies, percy_dom_script=percy_dom_script, **kwargs)

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

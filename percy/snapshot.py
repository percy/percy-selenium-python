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

def _deep_merge(base, override):
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        merged[key] = _deep_merge(existing, value) \
            if isinstance(existing, dict) and isinstance(value, dict) else value
    return merged

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

def _resolve_readiness_config(percy_config, kwargs):
    """Shallow-merge global (.percy.yml) readiness config with per-snapshot
    override. Per-snapshot keys win; unspecified keys are inherited.

    Defensive against `config.snapshot` being None or non-dict — the CLI is
    free to evolve its healthcheck payload and `None` should degrade to `{}`,
    not raise AttributeError mid-snapshot."""
    config = percy_config or {}
    global_readiness = ((config.get('snapshot') or {}).get('readiness')) or {}
    per_snapshot = kwargs.get('readiness') or {}
    if not isinstance(global_readiness, dict):
        global_readiness = {}
    if not isinstance(per_snapshot, dict):
        per_snapshot = {}
    return {**global_readiness, **per_snapshot}


def _wait_for_ready(driver, percy_config, kwargs):
    """Run readiness checks before serialize.

    Sends PercyDOM.waitForReady via execute_async_script. The script checks
    typeof PercyDOM.waitForReady in-browser so older CLI versions without the
    method are a graceful no-op. Any failure is caught and logged at debug;
    serialize still runs.

    Returns readiness diagnostics (or None) so callers can attach it
    to the domSnapshot for CLI-side logging.

    Config precedence: per-snapshot `kwargs['readiness']` shallow-merged
    over global `percy_config.snapshot.readiness`; per-snapshot keys win,
    unspecified keys (e.g. a global `preset: disabled` kill switch) are
    inherited. Skips entirely when the merged preset is 'disabled'.

    The caller must pass `percy_config` explicitly (from the `is_percy_enabled()`
    payload they already have in scope) — we don't re-call the cached lookup
    here, both for clarity and to avoid surprise dependencies on the cache.
    """
    has_explicit_kwarg = 'readiness' in kwargs
    has_global_config = bool(
        (percy_config or {}).get('snapshot', {}).get('readiness')
        if isinstance(percy_config, dict) else False)
    if not has_explicit_kwarg and not has_global_config:
        return None
    readiness_config = _resolve_readiness_config(percy_config, kwargs)
    if readiness_config.get('preset') == 'disabled':
        return None
    # Match readiness.timeoutMs to the driver's async-script timeout so a
    # higher user-configured readiness timeout isn't silently capped by
    # WebDriver's default (~30s on Selenium 4, lower on some remotes).
    timeout_ms = readiness_config.get('timeoutMs')
    previous_timeout = None
    if isinstance(timeout_ms, (int, float)) and timeout_ms > 0:
        try:
            # Selenium 4 exposes driver.timeouts.script (float seconds).
            previous_timeout = getattr(driver.timeouts, 'script', None)
            driver.set_script_timeout(timeout_ms / 1000 + 2)  # +2s buffer
        except Exception:
            previous_timeout = None  # older Selenium / unsupported — best effort
    # JS-side hard timeout: geckodriver does not reliably honor selenium's
    # script_timeout for async scripts whose pending work lives in microtasks
    # (Promise.then chains), so tests can hang indefinitely. Wrap done() in
    # a once-only guard and arm a setTimeout that calls it after the
    # readiness deadline + 2s buffer, regardless of what waitForReady does.
    deadline_ms = int((timeout_ms if isinstance(timeout_ms, (int, float)) and timeout_ms > 0
                       else 10000) + 2000)
    try:
        # done() must be called ASYNCHRONOUSLY for execute_async_script to
        # unblock — calling it synchronously within the script's body has
        # historically hung geckodriver in CI for hours. fireDone() wraps
        # done() in setTimeout(_, 0) so every code path defers the callback
        # to the next event-loop tick.
        diagnostics = driver.execute_async_script(
            'var config = ' + json.dumps(readiness_config) + ';'
            'var done = arguments[arguments.length - 1];'
            'var doneFired = false;'
            'function fireDone(v) {'
            '  if (doneFired) return;'
            '  doneFired = true;'
            '  setTimeout(function() { done(v); }, 0);'
            '}'
            'setTimeout(function() { fireDone(); }, ' + str(deadline_ms) + ');'
            'try {'
            "  if (typeof PercyDOM !== 'undefined'"
            "      && typeof PercyDOM.waitForReady === 'function') {"
            '    PercyDOM.waitForReady(config)'
            '      .then(function(r){ fireDone(r); })'
            '      .catch(function(){ fireDone(); });'
            '  } else { fireDone(); }'
            '} catch(e) { fireDone(); }'
        )
        return diagnostics
    except Exception as e:
        log(f'waitForReady failed, proceeding to serialize: {e}', 'debug')
        return None
    finally:
        if previous_timeout is not None:
            try:
                driver.set_script_timeout(previous_timeout)
            except Exception:
                pass


def get_serialized_dom(driver, cookies, percy_config=None, percy_dom_script=None,
                       skip_readiness=False, readiness_diagnostics=None, **kwargs):
    # 0. Readiness gate before serialize. Graceful on old CLI.
    #    `skip_readiness` lets responsive capture run readiness once before the
    #    width loop and pass diagnostics through, instead of paying the cost
    #    per width.
    if not skip_readiness:
        readiness_diagnostics = _wait_for_ready(driver, percy_config, kwargs)
    # Strip `readiness` from kwargs before forwarding — it's an SDK-local
    # concern; the CLI already has it from healthcheck and a top-level
    # `readiness` in the POST body is brittle against future validators.
    kwargs.pop('readiness', None)
    # 1. Serialize the main page first (this adds the data-percy-element-ids)
    dom_snapshot = driver.execute_script(f'return PercyDOM.serialize({json.dumps(kwargs)})')
    # Attach readiness diagnostics so the CLI can log timing and pass/fail.
    # `is not None` preserves legitimate falsy returns (e.g. `{}` meaning
    # "gate ran, no notable diagnostics").
    if readiness_diagnostics is not None and isinstance(dom_snapshot, dict):
        dom_snapshot['readiness_diagnostics'] = readiness_diagnostics
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
    # Run readiness ONCE before the per-width loop. With N widths and a
    # `timeoutMs` of e.g. 10s, running readiness per width can cost up to
    # N*timeout seconds of sequential waits — almost always undesirable.
    # Per-width DOM mutations after viewport changes are handled by the
    # `waitForResize` instrumentation above, not by re-running readiness.
    responsive_readiness_diagnostics = _wait_for_ready(driver, config, kwargs)
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
            driver, cookies, percy_config=config,
            percy_dom_script=percy_dom_script,
            skip_readiness=True,
            readiness_diagnostics=responsive_readiness_diagnostics,
            **kwargs)
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

        # Merge .percy.yml config options with snapshot options (snapshot options take priority)
        config_options = data['config'].get('snapshot') or {}
        merged_kwargs = _deep_merge(config_options, kwargs)

        # Serialize and capture the DOM
        if is_responsive_snapshot_capture(data['config'], **merged_kwargs):
            dom_snapshot = capture_responsive_dom(
                driver=driver,
                cookies=cookies,
                config=data['config'],
                percy_dom_script=percy_dom_script,
                **merged_kwargs,
            )
        else:
            dom_snapshot = get_serialized_dom(
                driver, cookies, percy_config=data.get('config'),
                percy_dom_script=percy_dom_script, **merged_kwargs)

        # Strip SDK-local `readiness` from the snapshot POST body. The CLI
        # already has it via healthcheck; sending it again here risks future
        # CLI-side validators rejecting unknown top-level fields.
        post_kwargs = {k: v for k, v in kwargs.items() if k != 'readiness'}
        # Post the DOM to the snapshot endpoint with snapshot options and other info
        response = requests.post(f'{PERCY_CLI_API}/percy/snapshot', json={**post_kwargs, **{
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

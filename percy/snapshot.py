import os
import platform
import json
from contextlib import contextmanager
from functools import lru_cache
from time import sleep
from urllib.parse import urlparse
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
    """Processes a single cross-origin frame to capture its snapshot.

    Kept for backwards compatibility with existing tests/callers. New code paths
    (nested CORS-iframe capture) go through ``process_frame_tree``.
    """
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


# In-browser script that walks document.querySelectorAll('iframe') and returns
# metadata for each. Mirrors percy-nightwatch's enumerateIframesScript so the
# wire shape stays in sync. Selectors is a list[str] of CSS selectors that
# users want to opt out of CORS iframe capture for.
def enumerate_iframes_script(selectors):
    selectors_json = json.dumps(list(selectors or []))
    return (
        "var __percySelectors = " + selectors_json + ";"
        "var __percyIframes = document.querySelectorAll('iframe');"
        "var __percyResult = [];"
        "for (var i = 0; i < __percyIframes.length; i++) {"
        "  var f = __percyIframes[i];"
        "  var matchesIgnore = false;"
        "  if (__percySelectors && __percySelectors.length) {"
        "    for (var j = 0; j < __percySelectors.length; j++) {"
        "      try { if (f.matches(__percySelectors[j])) { matchesIgnore = true; break; } }"
        "      catch (e) {}"
        "    }"
        "  }"
        "  __percyResult.push({"
        "    src: f.src || '',"
        "    srcdoc: f.getAttribute('srcdoc'),"
        "    percyElementId: f.getAttribute('data-percy-element-id'),"
        "    dataPercyIgnore: f.hasAttribute('data-percy-ignore'),"
        "    matchesIgnoreSelector: matchesIgnore,"
        "    index: i"
        "  });"
        "}"
        "return __percyResult;"
    )


def _should_skip_iframe(iframe, current_origin):
    # pylint: disable=too-many-return-statements
    """Mirror of nightwatch's shouldSkipIframe — pure on the enumerated metadata."""
    if iframe.get('dataPercyIgnore'):
        log(f"Skipping iframe marked with data-percy-ignore: {iframe.get('src') or '(no src)'}",
            "debug")
        return True
    if iframe.get('matchesIgnoreSelector'):
        log(f"Skipping iframe matching ignoreIframeSelectors: "
            f"{iframe.get('src') or '(no src)'}", "debug")
        return True
    # Check srcdoc BEFORE the src-emptiness check: a pure-srcdoc iframe has no
    # src attribute, and we want it routed through the srcdoc-specific branch
    # (where same-origin inlining handles it) rather than silently lumped under
    # "unsupported src".
    if iframe.get('srcdoc'):
        log(f"Skipping srcdoc iframe at index {iframe.get('index')}", "debug")
        return True
    src = iframe.get('src') or ''
    if not src or is_unsupported_iframe_src(src):
        if src:
            log(f"Skipping unsupported iframe src: {src}", "debug")
        return True
    frame_origin = get_origin(src)
    if not frame_origin:
        log(f"Skipping iframe with invalid URL: {src}", "debug")
        return True
    if frame_origin == current_origin:
        log(f"Skipping same-origin iframe: {src}", "debug")
        return True
    if not iframe.get('percyElementId'):
        log(f"Skipping cross-origin iframe without data-percy-element-id: {src}", "debug")
        return True
    return False


def process_frame_tree(driver, iframe_meta, depth, ancestor_urls, ctx):
    # pylint: disable=too-many-return-statements,too-many-statements
    """Recursively capture a cross-origin iframe and any nested cross-origin
    descendants. Bounded by ``ctx['max_frame_depth']`` to prevent runaway
    recursion when pages link to each other in cycles. ``ancestor_urls`` is the
    chain of frame URLs above this one — if the current frame's URL appears in
    the chain we treat it as a cycle and stop descending.
    """
    max_frame_depth = ctx['max_frame_depth']
    ignore_selectors = ctx['ignore_selectors']
    serialize_options = ctx['serialize_options']
    percy_dom_script = ctx['percy_dom_script']

    if depth > max_frame_depth:
        log(f"Reached max iframe nesting depth ({max_frame_depth}); "
            f"stopping at {iframe_meta.get('src')}", "debug")
        return []
    if ancestor_urls and iframe_meta.get('src') in ancestor_urls:
        log(f"Skipping cyclic iframe ({iframe_meta.get('src')} appears in ancestor chain)",
            "debug")
        return []

    collected = []
    switched_in = False
    captured_error = None
    # Track the post-switch URL so we can also detect cycles where a frame's
    # static src differs from its resolved document.URL (redirect chains).
    inside_url = None

    try:
        log(f"Processing cross-origin iframe (depth {depth}): {iframe_meta.get('src')}",
            "debug")

        # Find the iframe element by its data-percy-element-id rather than by
        # numeric index, which avoids drift if the DOM mutated between
        # enumeration and switch.
        find_script = (
            "return document.querySelector("
            "'iframe[data-percy-element-id=\"' + arguments[0] + '\"]'"
            ");"
        )
        iframe_element = driver.execute_script(
            find_script, iframe_meta['percyElementId']
        )
        if not iframe_element:
            log(f"Could not find iframe element with data-percy-element-id: "
                f"{iframe_meta['percyElementId']}", "debug")
            return []

        driver.switch_to.frame(iframe_element)
        switched_in = True

        # Post-switch URL re-check: a frame's src attribute may have pointed
        # somewhere reachable but the actual loaded document can be about:blank
        # or a net-error page. Read document.URL inside the frame and bail if
        # unsupported.
        try:
            inside_url = driver.execute_script("return document.URL;")
        except Exception:  # pylint: disable=broad-except
            inside_url = None
        if is_unsupported_iframe_src(inside_url):
            log(f"Skipping iframe (post-switch URL unsupported): {inside_url}", "debug")
            return []
        # Second cycle check, on the resolved document.URL. A redirect chain
        # (src=A → 30x → B) wouldn't trip the pre-switch guard because the
        # static src doesn't appear in ancestor_urls — but the post-switch URL
        # would. Catch the cycle here before we serialize and recurse.
        if ancestor_urls and inside_url and inside_url in ancestor_urls:
            log(f"Skipping cyclic iframe ({inside_url} appears in ancestor chain "
                "via redirect resolution)", "debug")
            return []

        # Inject PercyDOM and serialize. enableJavaScript is forced to True so
        # that the standard iframe serialization path is bypassed — we handle
        # CORS iframe serialization manually here.
        driver.execute_script(percy_dom_script)
        frame_options = {**serialize_options, 'enableJavaScript': True}
        frame_result = driver.execute_script(
            "return { snapshot: PercyDOM.serialize(" + json.dumps(frame_options) + "),"
            " frameUrl: document.URL };"
        )

        if not frame_result or not frame_result.get('snapshot'):
            log(f"Serialization returned empty result for frame: {iframe_meta.get('src')}",
                "debug")
            return []

        frame_url = frame_result.get('frameUrl') or iframe_meta.get('src') or "unknown-src"
        log(f"Captured cross-origin iframe (depth {depth}): {frame_url}", "debug")

        collected.append({
            "iframeData": {"percyElementId": iframe_meta['percyElementId']},
            "iframeSnapshot": frame_result['snapshot'],
            "frameUrl": frame_url
        })

        # Look for cross-origin iframes nested inside this frame and recurse.
        # Same-origin descendants are already inlined as srcdoc by
        # PercyDOM.serialize above. Compare each nested-frame origin against
        # this frame's origin (the immediate parent), not the page origin.
        if depth < max_frame_depth:
            current_origin = get_origin(frame_url)
            try:
                child_iframes_raw = driver.execute_script(
                    enumerate_iframes_script(ignore_selectors)
                )
            except Exception as e:  # pylint: disable=broad-except
                log(f"Failed to enumerate nested iframes: {e}", "debug")
                child_iframes_raw = []
            child_iframes = child_iframes_raw if isinstance(child_iframes_raw, list) else []
            next_ancestors = set(ancestor_urls or [])
            next_ancestors.add(frame_url)
            if iframe_meta.get('src'):
                next_ancestors.add(iframe_meta['src'])
            for child in child_iframes:
                if _should_skip_iframe(child, current_origin):
                    continue
                nested = process_frame_tree(driver, child, depth + 1, next_ancestors, ctx)
                if nested:
                    collected.extend(nested)

        return collected
    except PercyContextLost as err:
        # Merge any partial capture from the inner level before propagating.
        if err.partial_capture:
            collected.extend(err.partial_capture)
        err.partial_capture = collected
        raise
    except Exception as error:  # pylint: disable=broad-except
        # Top-level (depth==1) failures mean a user-visible iframe didn't get
        # captured. Surface those at info so users notice missing iframes; deeper
        # nested failures stay at debug to avoid log spam in chatty pages.
        failure_lvl = "info" if depth == 1 else "debug"
        log(f"Failed to process cross-origin iframe {iframe_meta.get('src')}: {error}",
            failure_lvl)
        captured_error = error
        return collected
    finally:
        if switched_in:
            # Step up exactly one level so an outer recursion can continue from
            # its own context. If parent_frame fails we have no reliable way to
            # land in the correct parent — fall back to default_content and
            # signal the caller to stop iterating siblings (whose enumeration
            # was performed in a now-lost context).
            try:
                driver.switch_to.parent_frame()
            except Exception as e:  # pylint: disable=broad-except
                log(f"Failed to switch back to parent frame: {e}", "debug")
                try:
                    driver.switch_to.default_content()
                except Exception:  # pylint: disable=broad-except
                    pass
                if depth > 1:
                    lost = PercyContextLost(
                        f"Lost parent frame context: {e}",
                        partial_capture=collected
                    )
                    if captured_error is not None:
                        lost.__cause__ = captured_error
                    # pylint: disable=lost-exception
                    raise lost from e  # noqa: B904


def _capture_cors_iframes(driver, page_url, ctx):
    """Top-level walk: enumerate page iframes, recurse into cross-origin ones."""
    try:
        try:
            iframe_info_raw = driver.execute_script(
                enumerate_iframes_script(ctx['ignore_selectors'])
            )
        except Exception as e:  # pylint: disable=broad-except
            log(f"Failed to enumerate top-level iframes: {e}", "debug")
            return []
        iframe_info = iframe_info_raw if isinstance(iframe_info_raw, list) else []
        if not iframe_info:
            return []

        log(f"Found {len(iframe_info)} top-level iframe(s)", "debug")
        page_origin = get_origin(page_url)
        cors_iframes = []
        skipped = 0

        for iframe in iframe_info:
            if _should_skip_iframe(iframe, page_origin):
                skipped += 1
                continue
            try:
                entries = process_frame_tree(
                    driver, iframe, 1, {page_url} if page_url else set(), ctx
                )
            except PercyContextLost as err:
                log("Aborting further nested CORS capture due to lost frame context",
                    "debug")
                if err.partial_capture:
                    cors_iframes.extend(err.partial_capture)
                break
            if entries:
                cors_iframes.extend(entries)

        log(f"Captured {len(cors_iframes)} cross-origin iframe(s) "
            f"(top-level skipped: {skipped})", "debug")
        return cors_iframes
    except Exception as e:  # pylint: disable=broad-except
        log(f"Error capturing CORS iframes: {e}", "debug")
        return []


def expose_closed_shadow_roots(driver):
    # pylint: disable=too-many-nested-blocks
    """Use CDP to find every closed shadow root in the page and stash each
    {host -> shadowRoot} pair in a WeakMap on ``window``. PercyDOM.serialize
    reads from that map to capture closed-mode shadow DOM that would otherwise
    be invisible to ordinary DOM traversal. Non-Chromium drivers will fail the
    initial CDP call and we silently no-op.
    """
    if not hasattr(driver, 'execute_cdp_cmd'):
        return
    try:
        driver.execute_cdp_cmd("DOM.enable", {})
    except Exception as e:  # pylint: disable=broad-except
        log(f"CDP unavailable for closed shadow DOM capture: {e}", "debug")
        return
    try:
        doc = driver.execute_cdp_cmd(
            "DOM.getDocument", {"depth": -1, "pierce": True}
        )
        root = doc.get("root") if isinstance(doc, dict) else None
        closed_pairs = []

        # Iterative walker. Recursive Python on a very deep DOM blows past
        # CPython's recursion limit (~1000) and raises RecursionError, which
        # the outer broad-except would silently swallow — meaning a deep page
        # would just lose closed-shadow exposure with no diagnostic. A stack
        # keeps memory bounded by tree breadth instead of tree depth.
        if root:
            stack = [root]
            while stack:
                node = stack.pop()
                # Skip nodes inside child frame documents — cross-frame closed
                # shadow roots are not yet supported (their execution context
                # lacks the WeakMap).
                if not isinstance(node, dict) or node.get("contentDocument"):
                    continue
                shadow_roots = node.get("shadowRoots") or []
                for sr in shadow_roots:
                    if sr.get("shadowRootType") == "closed":
                        closed_pairs.append({
                            "hostBackendNodeId": node.get("backendNodeId"),
                            "shadowBackendNodeId": sr.get("backendNodeId")
                        })
                    stack.append(sr)
                for child in (node.get("children") or []):
                    stack.append(child)

        if not closed_pairs:
            return

        log(f"Found {len(closed_pairs)} closed shadow root(s), exposing via CDP",
            "debug")

        # Create the WeakMap on the page (same key as PercyDOM looks up).
        driver.execute_script(
            "window.__percyClosedShadowRoots = "
            "window.__percyClosedShadowRoots || new WeakMap();"
        )

        for pair in closed_pairs:
            try:
                host_obj = driver.execute_cdp_cmd(
                    "DOM.resolveNode", {"backendNodeId": pair["hostBackendNodeId"]}
                )
                shadow_obj = driver.execute_cdp_cmd(
                    "DOM.resolveNode", {"backendNodeId": pair["shadowBackendNodeId"]}
                )
                host_id = (host_obj.get("object") or {}).get("objectId")
                shadow_id = (shadow_obj.get("object") or {}).get("objectId")
                if not host_id or not shadow_id:
                    continue
                driver.execute_cdp_cmd("Runtime.callFunctionOn", {
                    "functionDeclaration":
                        "function(shadowRoot) {"
                        " window.__percyClosedShadowRoots.set(this, shadowRoot); }",
                    "objectId": host_id,
                    "arguments": [{"objectId": shadow_id}]
                })
            except Exception as e:  # pylint: disable=broad-except
                log(f"Failed to expose a closed shadow root via CDP: {e}", "debug")
    except Exception as e:  # pylint: disable=broad-except
        log(f"Could not expose closed shadow roots via CDP: {e}", "debug")
    finally:
        try:
            driver.execute_cdp_cmd("DOM.disable", {})
        except Exception:  # pylint: disable=broad-except
            pass


# ---------------------------------------------------------------------------
# Inlined SDK helpers (mirrors @percy/sdk-utils used by Node SDKs). We do not
# bump a shared utils package — selenium-python ships these directly so that
# behavior stays in sync with percy-nightwatch / percy-webdriverio.
# ---------------------------------------------------------------------------

DEFAULT_MAX_FRAME_DEPTH = 5


def is_unsupported_iframe_src(frame_src):
    """True if a frame's src cannot be navigated/loaded for serialization."""
    if not frame_src:
        return True
    unsupported_exact = ("about:blank", "about:srcdoc")
    unsupported_prefixes = (
        "javascript:", "data:", "vbscript:", "blob:",
        "chrome:", "chrome-extension:", "about:"
    )
    if frame_src in unsupported_exact:
        return True
    for prefix in unsupported_prefixes:
        if frame_src.startswith(prefix):
            return True
    return False


# Backwards-compatible private alias kept for any external callers.
_is_unsupported_iframe_src = is_unsupported_iframe_src


def get_origin(url):
    """Return scheme://netloc for a URL, or None when parsing fails."""
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:  # pylint: disable=broad-except
        return None


def _get_origin(url):
    """Compat shim: previous Feature 1 code expected a non-None string."""
    origin = get_origin(url)
    return origin if origin is not None else ""


def clamp_frame_depth(value, default_max=DEFAULT_MAX_FRAME_DEPTH):
    """Clamp a user-provided depth into [1, default_max]."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default_max
    if n < 1:
        return 1
    if n > default_max:
        return default_max
    return n


def normalize_ignore_selectors(value):
    """Accept str|list|None and return a clean list[str]."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [s for s in value if isinstance(s, str) and s.strip()]
    return []


def resolve_max_frame_depth(options, percy_config):
    """Read maxIframeDepth from per-snapshot options or percy.config.snapshot."""
    options = options or {}
    config = (percy_config or {}).get('snapshot', {}) if isinstance(percy_config, dict) else {}
    raw = options.get('maxIframeDepth')
    if raw is None:
        raw = options.get('max_iframe_depth')
    if raw is None:
        raw = config.get('maxIframeDepth', DEFAULT_MAX_FRAME_DEPTH)
    return clamp_frame_depth(raw)


def resolve_ignore_selectors(options, percy_config):
    """Read ignoreIframeSelectors from per-snapshot options or percy.config.snapshot."""
    options = options or {}
    config = (percy_config or {}).get('snapshot', {}) if isinstance(percy_config, dict) else {}
    raw = options.get('ignoreIframeSelectors')
    if raw is None:
        raw = options.get('ignore_iframe_selectors')
    if raw is None:
        raw = config.get('ignoreIframeSelectors', [])
    return normalize_ignore_selectors(raw)


class PercyContextLost(Exception):
    """Raised when an iframe-context switch goes wrong mid-traversal.

    Carries any partial corsIframes capture already collected so the outer
    caller can still emit a useful payload before bailing on the rest.
    """
    def __init__(self, message, partial_capture=None):
        super().__init__(message)
        self.partial_capture = partial_capture or []

def get_serialized_dom(driver, cookies, percy_dom_script=None, percy_config=None, **kwargs):
    # 1. Serialize the main page first (this adds the data-percy-element-ids)
    dom_snapshot = driver.execute_script(f'return PercyDOM.serialize({json.dumps(kwargs)})')
    # 2. Process CORS iframes (nested, depth-capped, cycle-guarded, ignore-aware)
    if percy_dom_script:
        ctx = {
            'max_frame_depth': resolve_max_frame_depth(kwargs, percy_config),
            'ignore_selectors': resolve_ignore_selectors(kwargs, percy_config),
            'serialize_options': dict(kwargs),
            'percy_dom_script': percy_dom_script,
        }
        try:
            page_url = driver.current_url
        except Exception:  # pylint: disable=broad-except
            page_url = None
        cors_iframes = _capture_cors_iframes(driver, page_url, ctx)
        if cors_iframes:
            dom_snapshot['corsIframes'] = cors_iframes

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
            # Re-prime closed shadow roots after the page reload — the WeakMap
            # on window was destroyed when navigation happened.
            expose_closed_shadow_roots(driver)
            _setup_resize_listener(driver)
            driver.execute_script("PercyDOM.waitForResize();")
            resize_count = 0 # Reset count because the listener just started fresh
        print(f'{width}x{height} ready, taking snapshot...')
        _responsive_sleep()
        dom_snapshot = get_serialized_dom(
            driver, cookies, percy_dom_script=percy_dom_script,
            percy_config=config, **kwargs)
        dom_snapshot['width'] = width
        print(f'Taken snapshot for width: {width}, height: {height}')
        dom_snapshots.append(dom_snapshot)
    # Optional debug dump — gated to avoid polluting the user's CWD on every CI run.
    if PERCY_DEBUG:
        try:
            with open("output_file.json", "w", encoding="utf-8") as file_handle:
                json.dump(dom_snapshots, file_handle, indent=4)
        except Exception as e:  # pylint: disable=broad-except
            log(f"Could not write debug snapshot dump: {type(e).__name__}: {e}", "debug")
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
        # Expose closed shadow roots via CDP before serialization so PercyDOM
        # can find them through the WeakMap (Chromium-only; non-Chromium no-ops).
        expose_closed_shadow_roots(driver)
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
                driver, cookies, percy_dom_script=percy_dom_script,
                percy_config=data['config'], **kwargs)

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

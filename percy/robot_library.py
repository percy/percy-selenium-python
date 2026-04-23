"""Robot Framework library for Percy visual testing.

Provides keywords to capture Percy snapshots from Robot Framework tests
using SeleniumLibrary as the browser backend. All robot-specific imports
are wrapped in try/except for graceful degradation when robotframework
is not installed.

Usage in Robot Framework:
    *** Settings ***
    Library    SeleniumLibrary
    Library    percy.robot_library.PercyLibrary

    *** Test Cases ***
    Homepage Visual Test
        Open Browser    https://example.com    chrome
        Percy Snapshot    Homepage
        Close Browser

Run with:
    percy exec -- robot tests/
"""

import json
import platform

try:
    from robot.api.deco import keyword, library
    from robot.libraries.BuiltIn import BuiltIn
    import robot.version
    ROBOT_AVAILABLE = True
except ImportError:
    ROBOT_AVAILABLE = False

from selenium.webdriver import __version__ as SELENIUM_VERSION

from percy import snapshot as _snapshot_module
from percy.snapshot import (
    create_region,
    is_percy_enabled,
    percy_snapshot,
    percy_automate_screenshot,
)
from percy.version import __version__ as SDK_VERSION

_ROBOT_CLIENT_INFO = None
_ROBOT_ENV_INFO = None
if ROBOT_AVAILABLE:
    _ROBOT_CLIENT_INFO = f'percy-robotframework-selenium/{SDK_VERSION}'
    _ROBOT_ENV_INFO = [
        f'robotframework/{robot.version.VERSION}',
        f'selenium/{SELENIUM_VERSION}',
        f'python/{platform.python_version()}',
    ]


def _apply_robot_client_info():
    """Override snapshot module client/env info for Robot Framework context."""
    if _ROBOT_CLIENT_INFO:
        _snapshot_module.CLIENT_INFO = _ROBOT_CLIENT_INFO
    if _ROBOT_ENV_INFO:
        _snapshot_module.ENV_INFO = _ROBOT_ENV_INFO


def _parse_bool(val):
    if val is None:
        return None
    return str(val).lower() in ("true", "1", "yes")


def _parse_widths(widths):
    if not widths:
        return None
    if isinstance(widths, str):
        return [int(w.strip()) for w in widths.split(",")]
    if isinstance(widths, list):
        return [int(w) for w in widths]
    return None


def _parse_csv(val):
    if not val:
        return None
    if isinstance(val, str):
        return [v.strip() for v in val.split(",")]
    if isinstance(val, list):
        return val
    return None


def _parse_json(val):
    if not val:
        return None
    if isinstance(val, str):
        return json.loads(val)
    if isinstance(val, (dict, list)):
        return val
    return None


def _parse_padding(val):
    """Convert padding to object format Percy expects."""
    if not val:
        return None
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                return parsed
            val = int(val)
        except (json.JSONDecodeError, ValueError):
            return None
    if isinstance(val, (int, float)):
        p = int(val)
        return {"top": p, "bottom": p, "left": p, "right": p}
    if isinstance(val, dict):
        return val
    return None


if ROBOT_AVAILABLE:

    @library(scope="GLOBAL")
    class PercyLibrary:
        """Percy visual testing library for Robot Framework.

        Provides keywords to capture visual snapshots and screenshots using Percy.
        Requires SeleniumLibrary to be imported for browser access.

        Tests must be run under ``percy exec``:
        | percy exec -- robot tests/
        """

        def __init__(self):
            _apply_robot_client_info()

        def _get_driver(self):
            """Get the active Selenium WebDriver from SeleniumLibrary."""
            try:
                selib = BuiltIn().get_library_instance("SeleniumLibrary")
                return selib.driver
            except RuntimeError as exc:
                raise RuntimeError(
                    "PercyLibrary requires SeleniumLibrary to be imported"
                ) from exc

        # --------------------------------------------------------------
        # Percy Snapshot
        # --------------------------------------------------------------

        @keyword("Percy Snapshot")
        def percy_snapshot_keyword(  # pylint: disable=too-many-arguments,too-many-locals
            self, name, widths=None, min_height=None,
            percy_css=None, scope=None, scope_options=None,
            enable_javascript=None, enable_layout=None,
            disable_shadow_dom=None, labels=None,
            test_case=None, sync=None, regions=None,
            responsive_snapshot_capture=None,
        ):
            """Capture a Percy visual snapshot of the current page.

            ``name`` is the snapshot name shown in the Percy dashboard.

            ``widths`` is a comma-separated string of responsive widths
            (e.g., ``375,768,1280``).

            ``min_height`` is the minimum screenshot height in pixels.

            ``percy_css`` is custom CSS injected before the snapshot.

            ``scope`` is a CSS selector to limit the snapshot area.

            ``scope_options`` is a JSON string of scope options.

            ``enable_javascript`` enables JS execution in Percy rendering.

            ``enable_layout`` enables layout comparison mode.

            ``disable_shadow_dom`` disables Shadow DOM capture.

            ``labels`` is a comma-separated string of tags/labels
            (e.g., ``homepage,responsive,v2``).

            ``test_case`` is an optional test case identifier.

            ``sync`` when set to True, waits for snapshot processing.

            ``regions`` is a JSON string of ignore/consider region definitions.
            Use ``Create Percy Region`` to build these.

            ``responsive_snapshot_capture`` enables responsive capture mode.

            Examples:
            | Percy Snapshot    Homepage
            | Percy Snapshot    Login    widths=375,1280    min_height=1024
            | Percy Snapshot    Dashboard    labels=dashboard,admin    enable_layout=True
            | Percy Snapshot    Scoped    scope=.main-content    percy_css=.ad { display: none; }
            """
            driver = self._get_driver()
            percy_snapshot(
                driver,
                name,
                widths=_parse_widths(widths),
                min_height=int(min_height) if min_height else None,
                percy_css=percy_css,
                scope=scope,
                scope_options=_parse_json(scope_options),
                enable_javascript=_parse_bool(enable_javascript),
                enable_layout=_parse_bool(enable_layout),
                disable_shadow_dom=_parse_bool(disable_shadow_dom),
                labels=",".join(_parse_csv(labels)) if labels else None,
                test_case=test_case,
                sync=_parse_bool(sync),
                regions=_parse_json(regions),
                responsive_snapshot_capture=_parse_bool(responsive_snapshot_capture),
            )

        # --------------------------------------------------------------
        # Percy Screenshot (BrowserStack Automate)
        # --------------------------------------------------------------

        @keyword("Percy Screenshot")
        def percy_screenshot_keyword(self, name, options=None,
                                      ignore_region_selenium_elements=None,
                                      consider_region_selenium_elements=None):
            """Take a Percy screenshot on BrowserStack Automate.

            For standard DOM snapshots, use ``Percy Snapshot`` instead.

            ``name`` is the screenshot name shown in the Percy dashboard.

            ``options`` is a JSON string of screenshot options.

            ``ignore_region_selenium_elements`` is a comma-separated list of
            SeleniumLibrary locators whose elements should be ignored.

            ``consider_region_selenium_elements`` is a comma-separated list of
            SeleniumLibrary locators whose elements should be considered.

            == Basic usage ==
            | Percy Screenshot    Homepage

            == Ignore via SeleniumLibrary locators ==
            | Percy Screenshot    Login    ignore_region_selenium_elements=id:cookie-banner

            == Ignore via Create Percy Region ==
            | ${region}=    Create Percy Region    algorithm=ignore    element_xpath=//header
            | Percy Screenshot    Homepage    options=${{json.dumps({"regions": [${region}]})}}

            == Multiple regions ==
            | ${ignore}=    Create Percy Region    element_css=.ad
            | ${consider}=    Create Percy Region
            |    ...    algorithm=standard    element_css=.main
            | Percy Screenshot    Page
            |    ...    options=${{json.dumps({"regions": ...})}}
            """
            driver = self._get_driver()
            parsed_options = _parse_json(options) or {}

            if ignore_region_selenium_elements:
                selib = BuiltIn().get_library_instance("SeleniumLibrary")
                locators = _parse_csv(ignore_region_selenium_elements)
                parsed_options["ignore_region_selenium_elements"] = [
                    selib.find_element(loc) for loc in locators
                ]

            if consider_region_selenium_elements:
                selib = BuiltIn().get_library_instance("SeleniumLibrary")
                locators = _parse_csv(consider_region_selenium_elements)
                parsed_options["consider_region_selenium_elements"] = [
                    selib.find_element(loc) for loc in locators
                ]

            percy_automate_screenshot(driver, name, options=parsed_options)

        # --------------------------------------------------------------
        # Region helpers
        # --------------------------------------------------------------

        @keyword("Create Percy Region")
        def create_percy_region_keyword(  # pylint: disable=too-many-arguments
            self, algorithm="ignore",
            bounding_box=None, element_xpath=None,
            element_css=None, padding=None,
            diff_sensitivity=None,
            image_ignore_threshold=None,
                                         carousels_enabled=None,
                                         banners_enabled=None, ads_enabled=None,
                                         diff_ignore_threshold=None):
            """Create a region definition for Percy ignore/consider regions.

            ``algorithm`` is one of ``ignore``, ``standard``, or ``intelliignore``.

            ``bounding_box`` is a JSON string with x, y, width, height.

            ``element_xpath`` is an XPath selector for the region.

            ``element_css`` is a CSS selector for the region.

            ``padding`` is padding in pixels around the element.

            Returns a region dict to pass via ``regions`` (Percy Snapshot)
            or ``options`` (Percy Screenshot).

            == Usage with Percy Snapshot (DOM) ==
            | ${region}=    Create Percy Region    algorithm=ignore    element_css=.ad-banner
            | Percy Snapshot    Homepage    regions=${{json.dumps([${region}])}}

            == Usage with Percy Screenshot (Automate) ==
            | ${region}=    Create Percy Region
            |    ...    algorithm=ignore    element_xpath=//header
            | Percy Screenshot    Homepage
            |    ...    options=${{json.dumps({"regions": ...})}}

            == Multiple regions ==
            | ${ignore}=    Create Percy Region    element_css=h1
            | ${consider}=    Create Percy Region
            |    ...    algorithm=standard    element_css=.content
            | Percy Snapshot    Mixed
            |    ...    regions=${{json.dumps([${ignore}, ...])}}

            == With padding and bounding box ==
            | ${region}=    Create Percy Region
            |    ...    element_css=.banner    padding=10
            | ${region}=    Create Percy Region
            |    ...    bounding_box={"x":0,"y":0,"width":200}
            """
            return create_region(
                boundingBox=_parse_json(bounding_box),
                elementXpath=element_xpath,
                elementCSS=element_css,
                padding=_parse_padding(padding),
                algorithm=algorithm,
                diffSensitivity=int(diff_sensitivity) if diff_sensitivity else None,
                imageIgnoreThreshold=(
                    float(image_ignore_threshold) if image_ignore_threshold else None
                ),
                carouselsEnabled=_parse_bool(carousels_enabled),
                bannersEnabled=_parse_bool(banners_enabled),
                adsEnabled=_parse_bool(ads_enabled),
                diffIgnoreThreshold=float(diff_ignore_threshold) if diff_ignore_threshold else None,
            )

        # --------------------------------------------------------------
        # Utility
        # --------------------------------------------------------------

        @keyword("Percy Is Running")
        def percy_is_running_keyword(self):
            """Check if the Percy CLI server is running.

            Returns ``True`` if Percy is available, ``False`` otherwise.

            Example:
            | ${running}=    Percy Is Running
            | Run Keyword If    ${running}    Percy Snapshot    Homepage
            """
            return bool(is_percy_enabled())

else:
    # When robotframework is not installed, provide a stub class that
    # raises a clear error if someone tries to use it directly.
    class PercyLibrary:  # pylint: disable=function-redefined,too-few-public-methods
        """Stub -- robotframework is not installed."""
        def __init__(self):
            raise ImportError(
                "robotframework is not installed. "
                "Install it with: pip install robotframework robotframework-seleniumlibrary"
            )

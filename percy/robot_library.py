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

try:
    from robot.api.deco import keyword, library
    from robot.libraries.BuiltIn import BuiltIn
    ROBOT_AVAILABLE = True
except ImportError:
    ROBOT_AVAILABLE = False

from percy.snapshot import (
    create_region,
    is_percy_enabled,
    percy_snapshot,
    percy_automate_screenshot,
)


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


if ROBOT_AVAILABLE:

    @library(scope="GLOBAL")
    class PercyLibrary:
        """Percy visual testing library for Robot Framework.

        Provides keywords to capture visual snapshots and screenshots using Percy.
        Requires SeleniumLibrary to be imported for browser access.

        Tests must be run under ``percy exec``:
        | percy exec -- robot tests/
        """

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

            Example:
            | Percy Screenshot    Homepage
            | Percy Screenshot    Login    ignore_region_selenium_elements=id:cookie-banner
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

            Returns a region dict to pass to ``Percy Snapshot`` via ``regions``.

            Examples:
            | ${region}=    Create Percy Region    algorithm=ignore    element_css=.ad-banner
            | ${region}=    Create Percy Region    algorithm=standard
            |    ...    element_xpath=//div[@id='dynamic']    diff_sensitivity=5
            """
            return create_region(
                boundingBox=_parse_json(bounding_box),
                elementXpath=element_xpath,
                elementCSS=element_css,
                padding=int(padding) if padding else None,
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

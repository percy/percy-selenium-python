# pylint: disable=too-many-lines
import unittest
from unittest.mock import patch, Mock
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
import json

import httpretty
import requests
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.webdriver import WebDriver as FirefoxWebDriver
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.remote.remote_connection import RemoteConnection
from selenium.webdriver.safari.remote_connection import SafariRemoteConnection
from percy.snapshot import create_region

from percy import percy_snapshot, percySnapshot, percy_screenshot
import percy.snapshot as local
from percy.exception import UnsupportedWebDriverException
LABEL = local.LABEL

# mock a simple webpage to snapshot
class MockServerRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(('Snapshot Me').encode('utf-8'))
    def log_message(self, format, *args):
        return

class CommandExecutorMock():
    def __init__(self, url):
        self._url = url

    def dummy_method(self):
        pass

    def dummy_method1(self):
        pass

# daemon threads automatically shut down when the main process exits
mock_server = HTTPServer(('localhost', 8000), MockServerRequestHandler)
mock_server_thread = Thread(target=mock_server.serve_forever)
mock_server_thread.daemon = True
mock_server_thread.start()

# initializing mock data
data_object = {"sync": "true", "diff": 0}


# mock helpers
def mock_healthcheck(fail=False, fail_how='error', session_type=None, widths=None, config=None):
    health_body = { "success": True }
    health_headers = { 'X-Percy-Core-Version': '1.0.0' }
    health_status = 200

    if fail and fail_how == 'error':
        health_body = { "success": False, "error": "test" }
        health_status = 500
    elif fail and fail_how == 'wrong-version':
        health_headers = { 'X-Percy-Core-Version': '2.0.0' }
    elif fail and fail_how == 'no-version':
        health_headers = {}

    if session_type:
        health_body["type"] = session_type

    if widths: health_body['widths'] = widths
    if config: health_body['config'] = config
    health_body = json.dumps(health_body)
    httpretty.register_uri(
        httpretty.GET, 'http://localhost:5338/percy/healthcheck',
        body=health_body,
        adding_headers=health_headers,
        status=health_status)
    httpretty.register_uri(
        httpretty.GET, 'http://localhost:5338/percy/dom.js',
        body='window.PercyDOM = \
         { serialize: () => { return { html: document.documentElement.outerHTML } }, \
           waitForResize: () => { if(!window.resizeCount) { window.addEventListener(\'resize\',\
             () => window.resizeCount++) } window.resizeCount = 0; }}',
        status=200)
    if widths:
        httpretty.register_uri(
            httpretty.GET, 'http://localhost:5338/percy/widths-config',
            body=json.dumps({"widths": [
                {"width": w, "mobile": False}
                for w in (widths.get("config", []) + widths.get("mobile", []))
            ]}),
            status=200)

def mock_snapshot(fail=False, data=False):
    httpretty.register_uri(
        httpretty.POST, 'http://localhost:5338/percy/snapshot',
        body = json.dumps({
            "success": "false" if fail else "true",
            "error": "test" if fail else None,
            "data": data_object if data else None
        }),
        status=(500 if fail else 200))

def mock_logger():
    httpretty.register_uri(
        httpretty.POST, 'http://localhost:5338/percy/log',
        body = json.dumps({ "success": "true" }),
        status=200
    )

def mock_screenshot(fail=False, data=False):

    httpretty.register_uri(
        httpretty.POST, 'http://localhost:5338/percy/automateScreenshot',
        body = json.dumps({
            "success": not fail,
            "error": "test" if fail else None,
            "data": data_object if data else None
        }),
        status=(500 if fail else 200))

class TestPercySnapshot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        options = FirefoxOptions()
        options.add_argument('-headless')
        cls.driver = FirefoxWebDriver(options=options)

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()

    def setUp(self):
        # clear the cached value for testing
        local.is_percy_enabled.cache_clear()
        local.fetch_percy_dom.cache_clear()
        self.driver.get('http://localhost:8000')
        self.driver.delete_all_cookies()
        httpretty.enable()

    def tearDown(self):
        httpretty.disable()
        httpretty.reset()

    def test_throws_error_when_a_driver_is_not_provided(self):
        with self.assertRaises(Exception):
            percy_snapshot()

    def test_throws_error_when_a_name_is_not_provided(self):
        with self.assertRaises(Exception):
            percy_snapshot(self.driver)

    def test_disables_snapshots_when_the_healthcheck_fails(self):
        mock_healthcheck(fail=True)

        with patch('builtins.print') as mock_print:
            percy_snapshot(self.driver, 'Snapshot 1')
            percy_snapshot(self.driver, 'Snapshot 2')

            mock_print.assert_called_with(f'{LABEL} Percy is not running, disabling snapshots')

        self.assertEqual(httpretty.last_request().path, '/percy/healthcheck')

    def test_disables_snapshots_when_the_healthcheck_version_is_wrong(self):
        mock_healthcheck(fail=True, fail_how='wrong-version')

        with patch('builtins.print') as mock_print:
            percy_snapshot(self.driver, 'Snapshot 1')
            percy_snapshot(self.driver, 'Snapshot 2')

            mock_print.assert_called_with(f'{LABEL} Unsupported Percy CLI version, 2.0.0')

        self.assertEqual(httpretty.last_request().path, '/percy/healthcheck')

    def test_disables_snapshots_when_the_healthcheck_version_is_missing(self):
        mock_healthcheck(fail=True, fail_how='no-version')

        with patch('builtins.print') as mock_print:
            percy_snapshot(self.driver, 'Snapshot 1')
            percy_snapshot(self.driver, 'Snapshot 2')

            mock_print.assert_called_with(
                f'{LABEL} You may be using @percy/agent which is no longer supported by this SDK. '
                'Please uninstall @percy/agent and install @percy/cli instead. '
                'https://www.browserstack.com/docs/percy/migration/migrate-to-cli')

        self.assertEqual(httpretty.last_request().path, '/percy/healthcheck')

    def test_posts_snapshots_to_the_local_percy_server(self):
        mock_healthcheck()
        mock_snapshot()
        self.driver.add_cookie({'name': 'foo', 'value': 'bar'})
        expected_cookies = [{'name': 'foo', 'value': 'bar', 'path': '/',
            'domain': 'localhost', 'secure': False, 'httpOnly': False, 'sameSite': 'None'}]

        percy_snapshot(self.driver, 'Snapshot 1')
        response = percy_snapshot(self.driver, 'Snapshot 2', enable_javascript=True)

        self.assertEqual(httpretty.last_request().path, '/percy/snapshot')

        snap_bodies, seen = [], set()
        for req in httpretty.latest_requests():
            if req.path == '/percy/snapshot' and req.method == 'POST':
                body = req.parsed_body if isinstance(req.parsed_body, dict) else {}
                name = body.get('name')
                if name and name not in seen:
                    snap_bodies.append(body)
                    seen.add(name)

        s1 = snap_bodies[0]
        self.assertEqual(s1['name'], 'Snapshot 1')
        self.assertEqual(s1['url'], 'http://localhost:8000/')
        self.assertEqual(s1['dom_snapshot'], { 'cookies': expected_cookies,
            'html': '<html><head></head><body>Snapshot Me</body></html>'})
        self.assertRegex(s1['client_info'], r'percy-selenium-python/\d+')
        self.assertRegex(s1['environment_info'][0], r'selenium/\d+')
        self.assertRegex(s1['environment_info'][1], r'python/\d+')

        s2 = snap_bodies[1]
        self.assertEqual(s2['name'], 'Snapshot 2')
        self.assertEqual(s2['enable_javascript'], True)
        self.assertEqual(response, None)

    def test_posts_snapshots_to_the_local_percy_server_for_sync(self):
        mock_healthcheck()
        mock_snapshot(False, True)

        percy_snapshot(self.driver, 'Snapshot 1')
        response = percy_snapshot(self.driver, 'Snapshot 2', enable_javascript=True, sync=True)

        self.assertEqual(httpretty.last_request().path, '/percy/snapshot')

        snap_bodies, seen = [], set()
        for req in httpretty.latest_requests():
            if req.path == '/percy/snapshot' and req.method == 'POST':
                body = req.parsed_body if isinstance(req.parsed_body, dict) else {}
                name = body.get('name')
                if name and name not in seen:
                    snap_bodies.append(body)
                    seen.add(name)

        s1 = snap_bodies[0]
        self.assertEqual(s1['name'], 'Snapshot 1')
        self.assertEqual(s1['url'], 'http://localhost:8000/')
        self.assertEqual(s1['dom_snapshot'], {
            'html': '<html><head></head><body>Snapshot Me</body></html>', 'cookies': [] })
        self.assertRegex(s1['client_info'], r'percy-selenium-python/\d+')
        self.assertRegex(s1['environment_info'][0], r'selenium/\d+')
        self.assertRegex(s1['environment_info'][1], r'python/\d+')

        s2 = snap_bodies[1]
        self.assertEqual(s2['name'], 'Snapshot 2')
        self.assertEqual(s2['enable_javascript'], True)
        self.assertEqual(s2['sync'], True)
        self.assertEqual(response, data_object)

    def _legacy_posts_snapshots_to_the_local_percy_server_for_responsive_snapshot_capture(self):
        mock_logger()
        mock_healthcheck(widths = { "config": [375, 1280], "mobile": [390]})
        mock_snapshot()
        dom_string = '<html><head></head><body>Snapshot Me</body></html>'
        self.driver.add_cookie({'name': 'foo', 'value': 'bar'})
        expected_cookies = [{'name': 'foo', 'value': 'bar', 'path': '/', 'domain': 'localhost',
            'secure': False, 'httpOnly': False, 'sameSite': 'None'}]
        expected_dom_snapshot = [
            { 'cookies': expected_cookies, 'html': dom_string, 'width': 375 },
            { 'cookies': expected_cookies, 'html': dom_string, 'width': 1280 },
            { 'cookies': expected_cookies, 'html': dom_string, 'width': 390 }
        ]
        window_size = self.driver.get_window_size()

        percy_snapshot(self.driver, 'Snapshot 1', responsiveSnapshotCapture = True)
        percy_snapshot(self.driver, 'Snapshot 2', responsive_snapshot_capture=True, widths=[765])
        percy_snapshot(self.driver, 'Snapshot 3', responsive_snapshot_capture = True, width = 820)

        new_window_size = self.driver.get_window_size()
        self.assertEqual(window_size['width'], new_window_size['width'])
        self.assertEqual(window_size['height'], new_window_size['height'])

        self.assertEqual(httpretty.last_request().path, '/percy/snapshot')

        s1 = httpretty.latest_requests()[5].parsed_body
        self.assertEqual(s1['name'], 'Snapshot 1')
        self.assertEqual(s1['url'], 'http://localhost:8000/')
        self.assertEqual(s1['dom_snapshot'], expected_dom_snapshot)
        self.assertRegex(s1['client_info'], r'percy-selenium-python/\d+')
        self.assertRegex(s1['environment_info'][0], r'selenium/\d+')
        self.assertRegex(s1['environment_info'][1], r'python/\d+')

        s2 = httpretty.latest_requests()[10].parsed_body
        self.assertEqual(s2['name'], 'Snapshot 2')
        self.assertEqual(s2['dom_snapshot'], expected_dom_snapshot)

        s3 = httpretty.latest_requests()[15].parsed_body
        self.assertEqual(s3['name'], 'Snapshot 3')
        self.assertEqual(s3['dom_snapshot'], expected_dom_snapshot)

    def _legacy_posts_snapshots_to_the_local_percy_server_with_defer_and_responsive(self):
        mock_logger()
        mock_healthcheck(widths = { "config": [375, 1280], "mobile": [390]},
                         config = { 'percy': { 'deferUploads': True }})
        mock_snapshot()
        dom_string = '<html><head></head><body>Snapshot Me</body></html>'
        expected_dom_snapshot = { 'html': dom_string, 'cookies': [] }

        percy_snapshot(self.driver, 'Snapshot 1', responsiveSnapshotCapture = True)

        self.assertEqual(httpretty.last_request().path, '/percy/snapshot')

        s1 = httpretty.latest_requests()[2].parsed_body
        self.assertEqual(s1['name'], 'Snapshot 1')
        self.assertEqual(s1['url'], 'http://localhost:8000/')
        self.assertEqual(s1['dom_snapshot'], expected_dom_snapshot)


    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_RELOAD_PAGE', False)
    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_MIN_HEIGHT', None)
    def test_posts_snapshots_to_the_local_percy_server_for_responsive_snapshot_capture(self):
        mock_logger()
        mock_healthcheck(widths = { "config": [375, 1280], "mobile": [390]})
        mock_snapshot()
        dom_string = '<html><head></head><body>Snapshot Me</body></html>'
        self.driver.add_cookie({'name': 'foo', 'value': 'bar'})
        expected_cookies = [{'name': 'foo', 'value': 'bar', 'path': '/', 'domain': 'localhost',
            'secure': False, 'httpOnly': False, 'sameSite': 'None'}]
        expected_dom_snapshot = [
            { 'cookies': expected_cookies, 'html': dom_string, 'width': 375 },
            { 'cookies': expected_cookies, 'html': dom_string, 'width': 1280 },
            { 'cookies': expected_cookies, 'html': dom_string, 'width': 390 }
        ]
        window_size = self.driver.get_window_size()

        percy_snapshot(self.driver, 'Snapshot 1', responsiveSnapshotCapture = True)
        percy_snapshot(self.driver, 'Snapshot 2', responsive_snapshot_capture=True, widths=[765])
        percy_snapshot(self.driver, 'Snapshot 3', responsive_snapshot_capture = True, width = 820)

        new_window_size = self.driver.get_window_size()
        self.assertEqual(window_size['width'], new_window_size['width'])
        self.assertEqual(window_size['height'], new_window_size['height'])

        self.assertEqual(httpretty.last_request().path, '/percy/snapshot')

        # Filter snapshot POSTs robustly (httpretty 1.1.x may double-record;
        # first record sometimes lacks body — deduplicate by snapshot name)
        snap_bodies, seen = [], set()
        for r in httpretty.latest_requests():
            if r.path == '/percy/snapshot' and r.method == 'POST':
                b = r.parsed_body if isinstance(r.parsed_body, dict) else {}
                if 'name' in b and b['name'] not in seen:
                    snap_bodies.append(b)
                    seen.add(b['name'])

        s1 = snap_bodies[0]
        self.assertEqual(s1['name'], 'Snapshot 1')
        self.assertEqual(s1['url'], 'http://localhost:8000/')
        self.assertEqual(s1['dom_snapshot'], expected_dom_snapshot)
        self.assertRegex(s1['client_info'], r'percy-selenium-python/\d+')
        self.assertRegex(s1['environment_info'][0], r'selenium/\d+')
        self.assertRegex(s1['environment_info'][1], r'python/\d+')

        s2 = snap_bodies[1]
        self.assertEqual(s2['name'], 'Snapshot 2')
        self.assertEqual(s2['dom_snapshot'], expected_dom_snapshot)

        s3 = snap_bodies[2]
        self.assertEqual(s3['name'], 'Snapshot 3')
        self.assertEqual(s3['dom_snapshot'], expected_dom_snapshot)

    def test_posts_snapshots_to_the_local_percy_server_with_defer_and_responsive(self):
        mock_logger()
        mock_healthcheck(widths = { "config": [375, 1280], "mobile": [390]},
                         config = { 'percy': { 'deferUploads': True }})
        mock_snapshot()
        dom_string = '<html><head></head><body>Snapshot Me</body></html>'
        expected_dom_snapshot = { 'html': dom_string, 'cookies': [] }

        percy_snapshot(self.driver, 'Snapshot 1', responsiveSnapshotCapture = True)

        self.assertEqual(httpretty.last_request().path, '/percy/snapshot')

        snap_bodies = [
            r.parsed_body for r in httpretty.latest_requests()
            if r.path == '/percy/snapshot' and r.method == 'POST'
            and isinstance(r.parsed_body, dict)
        ]
        s1 = snap_bodies[0]
        self.assertEqual(s1['name'], 'Snapshot 1')
        self.assertEqual(s1['url'], 'http://localhost:8000/')
        self.assertEqual(s1['dom_snapshot'], expected_dom_snapshot)

    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_RELOAD_PAGE', False)
    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_MIN_HEIGHT', None)
    @patch('selenium.webdriver.Chrome')
    @patch.object(local, 'RESPONSIVE_CAPTURE_SLEEP_TIME', 1)
    def test_posts_snapshots_to_the_local_percy_server_for_responsive_dom_chrome(self, MockChrome):
        driver = MockChrome.return_value
        # execute_script calls (reload=False):
        #  [0] inject percy_dom  [1] _setup_resize_listener  [2] waitForResize
        #  [3] resize-check w375  [4] serialize w375  [5] enumerate iframes w375
        #  [6] resize-check w390  [7] serialize w390  [8] enumerate iframes w390
        #  [9] restore resize-check
        driver.execute_script.side_effect = [
            '', '', None,
            1, { 'html': 'some_dom' }, [],
            2, { 'html': 'some_dom_1' }, [],
            3
        ]
        driver.get_cookies.return_value = ''
        driver.execute_cdp_cmd.return_value = ''
        driver.get_window_size.return_value = { 'height': 400, 'width': 800 }
        # find_elements is no longer used by the iframe path; left for any
        # legacy callers.
        driver.find_elements.return_value = []
        mock_logger()
        mock_healthcheck(widths = { "config": [375], "mobile": [390] })
        mock_snapshot()
        # get_responsive_widths now fetches from /percy/widths-config;
        # config [375] + mobile [390] = widths [375, 390]
        expected_dom_snapshot = [
            { 'cookies': '', 'html': 'some_dom', 'width': 375 },
            { 'cookies': '', 'html': 'some_dom_1', 'width': 390 }
        ]

        with patch.object(driver, 'current_url', 'http://localhost:8000/'):
            with patch.object(driver, 'capabilities', new={ 'browserName': 'chrome' }):
                percy_snapshot(driver, 'Snapshot 1', responsiveSnapshotCapture = True, width = 600)

        self.assertEqual(httpretty.last_request().path, '/percy/snapshot')

        s1 = httpretty.last_request().parsed_body
        self.assertEqual(s1['name'], 'Snapshot 1')
        self.assertEqual(s1['url'], 'http://localhost:8000/')
        self.assertEqual(s1['dom_snapshot'], expected_dom_snapshot)

    def test_has_a_backwards_compatible_function(self):
        mock_healthcheck()
        mock_snapshot()
        mock_logger()

        percySnapshot(browser=self.driver, name='Snapshot')

        self.assertEqual(httpretty.last_request().path, '/percy/snapshot')

        snap_bodies = [
            r.parsed_body for r in httpretty.latest_requests()
            if r.path == '/percy/snapshot' and r.method == 'POST'
            and isinstance(r.parsed_body, dict)
        ]
        s1 = snap_bodies[0]
        self.assertEqual(s1['name'], 'Snapshot')
        self.assertEqual(s1['url'], 'http://localhost:8000/')
        self.assertEqual(s1['dom_snapshot'], {
            'html': '<html><head></head><body>Snapshot Me</body></html>', 'cookies': [] })

    def test_handles_snapshot_errors(self):
        mock_healthcheck(session_type="web")
        mock_snapshot(fail=True)
        mock_logger()

        with patch('builtins.print') as mock_print:
            percy_snapshot(self.driver, 'Snapshot 1')

            mock_print.assert_any_call(f'{LABEL} Could not take DOM snapshot "Snapshot 1"')

        log_bodies = [
            req.parsed_body
            for req in httpretty.latest_requests()
            if req.path == '/percy/log'
            and req.method == 'POST'
            and isinstance(req.parsed_body, dict)
        ]
        self.assertIn(
            {
                'message': f'{LABEL} Could not take DOM snapshot "Snapshot 1"',
                'level': 'info'
            },
            log_bodies
        )

    def test_raise_error_poa_token_with_snapshot(self):
        mock_healthcheck(session_type="automate")

        with self.assertRaises(Exception) as context:
            percy_snapshot(self.driver, "Snapshot 1")

        self.assertEqual("Invalid function call - "\
        "percy_snapshot(). Please use percy_screenshot() function while using Percy with Automate."\
        " For more information on usage of PercyScreenshot, refer https://www.browserstack.com"\
        "/docs/percy/integrate/functional-and-visual", str(context.exception))

class TestPercyScreenshot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        driver = cls.get_driver()
        cls.driver = driver

    @classmethod
    def get_driver(cls, mock_driver = WebDriver, mock_connection = RemoteConnection):
        driver = Mock(spec=mock_driver)
        driver.session_id = 'Dummy_session_id'
        driver.capabilities = { 'key': 'value' }
        driver.command_executor = Mock(spec=mock_connection)
        driver.command_executor._url = 'https://hub-cloud.browserstack.com/wd/hub'  # pylint: disable=W0212
        return driver
    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()

    def setUp(self):
        # clear the cached value for testing
        local.is_percy_enabled.cache_clear()
        self.driver.get('http://localhost:8000')
        httpretty.enable()

    def tearDown(self):
        httpretty.disable()
        httpretty.reset()

    def test_throws_error_when_a_driver_is_not_provided(self):
        with self.assertRaises(Exception):
            percy_screenshot()

    def test_throws_error_when_a_name_is_not_provided(self):
        with self.assertRaises(Exception):
            percy_screenshot(self.driver)

    def test_disables_screenshot_when_the_healthcheck_fails(self):
        mock_healthcheck(fail=True)

        with patch('builtins.print') as mock_print:
            percy_screenshot(self.driver, 'Snapshot 1')
            percy_screenshot(self.driver, 'Snapshot 2')

            mock_print.assert_called_with(f'{LABEL} Percy is not running, disabling snapshots')

        self.assertEqual(httpretty.last_request().path, '/percy/healthcheck')

    def test_disables_screenshot_when_the_healthcheck_version_is_wrong(self):
        mock_healthcheck(fail=True, fail_how='wrong-version')

        with patch('builtins.print') as mock_print:
            percy_screenshot(self.driver, 'Snapshot 1')
            percy_screenshot(self.driver, 'Snapshot 2')

            mock_print.assert_called_with(f'{LABEL} Unsupported Percy CLI version, 2.0.0')

        self.assertEqual(httpretty.last_request().path, '/percy/healthcheck')

    def test_disables_screenshot_when_the_healthcheck_version_is_missing(self):
        mock_healthcheck(fail=True, fail_how='no-version')

        with patch('builtins.print') as mock_print:
            percy_screenshot(self.driver, 'Snapshot 1')
            percy_screenshot(self.driver, 'Snapshot 2')

            mock_print.assert_called_with(
                f'{LABEL} You may be using @percy/agent which is no longer supported by this SDK. '
                'Please uninstall @percy/agent and install @percy/cli instead. '
                'https://www.browserstack.com/docs/percy/migration/migrate-to-cli')

        self.assertEqual(httpretty.last_request().path, '/percy/healthcheck')

    def test_disables_screenshot_when_the_driver_is_not_selenium(self):
        mock_healthcheck(fail=True, fail_how='no-version')
        with self.assertRaises(UnsupportedWebDriverException):
            percy_screenshot("dummy_driver", 'Snapshot 1')

    def test_camelcase_options(self):
        mock_healthcheck(session_type="automate")
        mock_screenshot(False, True)

        element = Mock(spec=WebElement)
        element.id = 'Dummy_id'

        consider_element = Mock(spec=WebElement)
        consider_element.id = 'Consider_Dummy_id'
        response = percy_screenshot(self.driver, 'Snapshot C', options = {
            "ignoreRegionSeleniumElements": [element],
            "considerRegionSeleniumElements": [consider_element],
            "sync": "true"
        })

        s = httpretty.latest_requests()[1].parsed_body
        self.assertEqual(s['snapshotName'], 'Snapshot C')
        self.assertEqual(s['options']['ignore_region_elements'], ['Dummy_id'])
        self.assertEqual(s['options']['consider_region_elements'], ['Consider_Dummy_id'])
        self.assertEqual(s['options']['sync'], "true")
        self.assertEqual(response, data_object)

    def posts_screenshot_to_the_local_percy_server(self, driver):
        mock_healthcheck(session_type="automate")
        mock_screenshot()

        element = Mock(spec=WebElement)
        element.id = 'Dummy_id'

        consider_element = Mock(spec=WebElement)
        consider_element.id = 'Consider_Dummy_id'

        percy_screenshot(driver, 'Snapshot 1')
        response = percy_screenshot(driver, 'Snapshot 2', options = {
            "enable_javascript": True,
            "ignore_region_selenium_elements": [element],
            "consider_region_selenium_elements": [consider_element]
        })

        self.assertEqual(httpretty.last_request().path, '/percy/automateScreenshot')

        screenshot_bodies, seen = [], set()
        for req in httpretty.latest_requests():
            if req.path == '/percy/automateScreenshot' and req.method == 'POST':
                body = req.parsed_body if isinstance(req.parsed_body, dict) else {}
                name = body.get('snapshotName')
                if name and name not in seen:
                    screenshot_bodies.append(body)
                    seen.add(name)

        s1 = screenshot_bodies[0]
        self.assertEqual(s1['snapshotName'], 'Snapshot 1')
        self.assertEqual(s1['sessionId'], driver.session_id)
        self.assertEqual(s1['commandExecutorUrl'], driver.command_executor._url)  # pylint: disable=W0212
        self.assertEqual(s1['capabilities'], dict(driver.capabilities))
        self.assertRegex(s1['client_info'], r'percy-selenium-python/\d+')
        self.assertRegex(s1['environment_info'][0], r'selenium/\d+')
        self.assertRegex(s1['environment_info'][1], r'python/\d+')

        s2 = screenshot_bodies[1]
        self.assertEqual(s2['snapshotName'], 'Snapshot 2')
        self.assertEqual(s2['options']['enable_javascript'], True)
        self.assertEqual(s2['options']['ignore_region_elements'], ['Dummy_id'])
        self.assertEqual(s2['options']['consider_region_elements'], ['Consider_Dummy_id'])
        self.assertEqual(response, None)

    def test_posts_screenshot_to_the_local_percy_server_remote_connection(self):
        self.posts_screenshot_to_the_local_percy_server(self.driver)

    def test_posts_screenshot_to_the_local_percy_server_safari_connection(self):
        safari_driver = self.get_driver(WebDriver, SafariRemoteConnection)
        self.posts_screenshot_to_the_local_percy_server(safari_driver)
        safari_driver.quit()

    def test_handles_screenshot_errors(self):
        mock_healthcheck(session_type="automate")
        mock_screenshot(fail=True)
        mock_logger()

        with patch('builtins.print') as mock_print:
            percy_screenshot(self.driver, 'Snapshot 1')

            mock_print.assert_any_call(f'{LABEL} Could not take Screenshot "Snapshot 1"')

        log_bodies = [
            req.parsed_body
            for req in httpretty.latest_requests()
            if req.path == '/percy/log'
            and req.method == 'POST'
            and isinstance(req.parsed_body, dict)
        ]
        self.assertIn(
            {'message': f'{LABEL} Could not take Screenshot "Snapshot 1"', 'level': 'info'},
            log_bodies
        )

    def test_raise_error_web_token_with_screenshot(self):
        mock_healthcheck(session_type="web")

        with self.assertRaises(Exception) as context:
            percy_screenshot(self.driver, "Snapshot 1")

        self.assertEqual("Invalid function call - "\
        "percy_screenshot(). Please use percy_snapshot() function for taking screenshot. "\
        "percy_screenshot() should be used only while using Percy with Automate. "\
        "For more information on usage of percy_snapshot(), refer doc for your language "\
        "https://www.browserstack.com/docs/percy/integrate/overview", str(context.exception))

def get_percy_test_requests():
    response = requests.get('http://localhost:5338/test/requests', timeout=10)
    data = response.json()
    return data['requests']

class TestPercySnapshotIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        options = FirefoxOptions()
        options.add_argument('-headless')
        cls.driver = FirefoxWebDriver(options=options)

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()

    def setUp(self):
        # clear the cached value for testing
        local.is_percy_enabled.cache_clear()
        local.fetch_percy_dom.cache_clear()
        self.driver.get('http://localhost:8000')

    @unittest.skip("Requires Percy CLI running in test mode (localhost:5338/test/requests)")
    def test_posts_snapshots_to_the_local_percy_server(self):
        percy_snapshot(self.driver, 'Snapshot 1')
        percy_snapshot(self.driver, 'Snapshot 2', enable_javascript=True)

        reqs = get_percy_test_requests()
        s1 = reqs[2]['body']
        self.assertEqual(s1['name'], 'Snapshot 1')
        self.assertEqual(s1['url'], 'http://localhost:8000/')
        self.assertRegex(s1['client_info'], r'percy-selenium-python/\d+')
        self.assertRegex(s1['environment_info'][0], r'selenium/\d+')
        self.assertRegex(s1['environment_info'][1], r'python/\d+')

        s2 = reqs[3]['body']
        self.assertEqual(s2['name'], 'Snapshot 2')
        self.assertEqual(s2['enable_javascript'], True)


class TestPercyResponsiveCaptureMinHeight(unittest.TestCase):
    """Tests for PERCY_RESPONSIVE_CAPTURE_MIN_HEIGHT using real headless Firefox + httpretty."""

    @classmethod
    def setUpClass(cls):
        options = FirefoxOptions()
        options.add_argument('-headless')
        cls.driver = FirefoxWebDriver(options=options)

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()

    def setUp(self):
        local.is_percy_enabled.cache_clear()
        local.fetch_percy_dom.cache_clear()
        self.driver.get('http://localhost:8000')
        self.driver.delete_all_cookies()
        httpretty.enable()

    def tearDown(self):
        httpretty.disable()
        httpretty.reset()

    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_MIN_HEIGHT', 'true')
    def test_snapshot_uses_min_height_from_kwarg(self):
        """With minHeight kwarg, responsive snapshots are captured at the requested
        viewport height and window is restored to its original size afterward."""
        mock_logger()
        mock_healthcheck(widths={'config': [375, 1280], 'mobile': []})
        mock_snapshot()

        original_size = self.driver.get_window_size()
        percy_snapshot(
            self.driver,
            'MinHeight Kwarg',
            responsiveSnapshotCapture=True,
            minHeight=400
        )
        restored_size = self.driver.get_window_size()

        self.assertEqual(original_size['width'], restored_size['width'])
        self.assertEqual(original_size['height'], restored_size['height'])

        s1 = httpretty.last_request().parsed_body
        self.assertEqual(s1['name'], 'MinHeight Kwarg')
        # each dom_snapshot entry must carry the correct width
        widths_in_snap = sorted(d['width'] for d in s1['dom_snapshot'])
        self.assertEqual(widths_in_snap, [375, 1280])

    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_MIN_HEIGHT', 'true')
    def test_snapshot_uses_min_height_from_cli_config(self):
        """With minHeight in CLI config, snapshots captured with computed height and
        window is restored afterward."""
        mock_logger()
        mock_healthcheck(
            widths={'config': [375], 'mobile': []},
            config={'snapshot': {'minHeight': 400}}
        )
        mock_snapshot()

        original_size = self.driver.get_window_size()
        percy_snapshot(self.driver, 'MinHeight Config', responsiveSnapshotCapture=True)
        restored_size = self.driver.get_window_size()

        self.assertEqual(original_size['width'], restored_size['width'])
        self.assertEqual(original_size['height'], restored_size['height'])

        s1 = httpretty.last_request().parsed_body
        self.assertEqual(s1['name'], 'MinHeight Config')
        self.assertIsInstance(s1['dom_snapshot'], list)

    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_MIN_HEIGHT', None)
    def test_no_height_change_when_env_var_not_set(self):
        """When env var is not set, window height stays at current value even if
        minHeight is passed as kwarg."""
        mock_logger()
        mock_healthcheck(widths={'config': [375, 1280], 'mobile': []})
        mock_snapshot()

        original_size = self.driver.get_window_size()
        percy_snapshot(self.driver, 'No MinHeight', responsiveSnapshotCapture=True, minHeight=400)
        restored_size = self.driver.get_window_size()

        self.assertEqual(original_size['width'], restored_size['width'])
        self.assertEqual(original_size['height'], restored_size['height'])

    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_MIN_HEIGHT', 'true')
    def test_snapshot_count_matches_widths(self):
        """Number of dom_snapshot entries equals number of responsive widths."""
        mock_logger()
        mock_healthcheck(widths={'config': [375, 1280], 'mobile': [390]})
        mock_snapshot()

        percy_snapshot(self.driver, 'Count Check', responsiveSnapshotCapture=True, minHeight=400)

        s1 = httpretty.last_request().parsed_body
        # mobile [390] + config [375, 1280] = 3 widths
        self.assertEqual(len(s1['dom_snapshot']), 3)


class TestPercyResponsiveCaptureReloadPage(unittest.TestCase):
    """Tests for PERCY_RESPONSIVE_CAPTURE_RELOAD_PAGE using real headless Firefox + httpretty."""

    @classmethod
    def setUpClass(cls):
        options = FirefoxOptions()
        options.add_argument('-headless')
        cls.driver = FirefoxWebDriver(options=options)

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()

    def setUp(self):
        local.is_percy_enabled.cache_clear()
        local.fetch_percy_dom.cache_clear()
        self.driver.get('http://localhost:8000')
        self.driver.delete_all_cookies()
        # allow_net_connect=True lets geckodriver WebSocket traffic through
        # while still intercepting localhost:5338 (Percy CLI) calls
        httpretty.enable(allow_net_connect=True)

    def tearDown(self):
        httpretty.disable()
        httpretty.reset()

    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_RELOAD_PAGE', 'true')
    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_MIN_HEIGHT', None)
    def test_snapshot_succeeds_with_reload_enabled(self):
        """With reload enabled, percy_snapshot completes, posts to /percy/snapshot,
        and driver.refresh() is called once per responsive width."""
        mock_logger()
        mock_healthcheck(widths={'config': [375, 1280], 'mobile': []})
        mock_snapshot()

        # Patch refresh and window-resize to avoid geckodriver socket conflicts
        # while httpretty is active; assert on call counts instead
        with patch.object(self.driver, 'refresh') as mock_refresh, \
             patch.object(local, 'change_window_dimension_and_wait'):
            percy_snapshot(self.driver, 'Reload Enabled', responsiveSnapshotCapture=True)
            # refresh must be called once per width (2 widths → 2 calls)
            self.assertEqual(mock_refresh.call_count, 2)

        self.assertEqual(httpretty.last_request().path, '/percy/snapshot')
        s1 = httpretty.last_request().parsed_body
        self.assertEqual(s1['name'], 'Reload Enabled')
        self.assertIsInstance(s1['dom_snapshot'], list)

    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_RELOAD_PAGE', 'true')
    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_MIN_HEIGHT', None)
    def test_window_restored_after_reload_capture(self):
        """Window dimensions are restored to original after a reload-enabled capture.
        The restore call (last) must use original width × height."""
        mock_logger()
        mock_healthcheck(widths={'config': [375, 1280], 'mobile': []})
        mock_snapshot()

        original_size = self.driver.get_window_size()
        with patch.object(self.driver, 'refresh'), \
             patch.object(local, 'change_window_dimension_and_wait') as mock_resize:
            percy_snapshot(self.driver, 'Reload Restore', responsiveSnapshotCapture=True)
            # Last resize call must restore the original dimensions
            last_call = mock_resize.call_args_list[-1]
            self.assertEqual(last_call[0][1], original_size['width'])
            self.assertEqual(last_call[0][2], original_size['height'])

    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_RELOAD_PAGE', False)
    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_MIN_HEIGHT', None)
    def test_snapshot_succeeds_with_reload_disabled(self):
        """With reload disabled, snapshot still succeeds and posts correct data."""
        mock_logger()
        mock_healthcheck(widths={'config': [375, 1280], 'mobile': []})
        mock_snapshot()
        dom_string = '<html><head></head><body>Snapshot Me</body></html>'

        percy_snapshot(self.driver, 'Reload Disabled', responsiveSnapshotCapture=True)

        self.assertEqual(httpretty.last_request().path, '/percy/snapshot')
        s1 = httpretty.last_request().parsed_body
        self.assertEqual(s1['name'], 'Reload Disabled')
        for snap in s1['dom_snapshot']:
            self.assertEqual(snap['html'], dom_string)

    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_RELOAD_PAGE', 'true')
    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_MIN_HEIGHT', 'true')
    def test_reload_and_min_height_together(self):
        """Both reload and minHeight active: snapshot succeeds with correct widths,
        window is restored, and refresh is called per width."""
        mock_logger()
        mock_healthcheck(widths={'config': [375, 1280], 'mobile': []})
        mock_snapshot()

        original_size = self.driver.get_window_size()
        with patch.object(self.driver, 'refresh') as mock_refresh, \
             patch.object(local, 'change_window_dimension_and_wait') as mock_resize:
            percy_snapshot(
                self.driver,
                'Reload + MinHeight',
                responsiveSnapshotCapture=True,
                minHeight=400
            )
            self.assertEqual(mock_refresh.call_count, 2)
            last_call = mock_resize.call_args_list[-1]
            self.assertEqual(last_call[0][1], original_size['width'])
            self.assertEqual(last_call[0][2], original_size['height'])

        s1 = httpretty.last_request().parsed_body
        self.assertEqual(s1['name'], 'Reload + MinHeight')
        widths_in_snap = sorted(d['width'] for d in s1['dom_snapshot'])
        self.assertEqual(widths_in_snap, [375, 1280])

    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_RELOAD_PAGE', 'true')
    @patch.object(local, 'PERCY_RESPONSIVE_CAPTURE_MIN_HEIGHT', None)
    def test_snapshot_count_matches_widths_with_reload(self):
        """Number of dom_snapshot entries equals number of responsive widths when reload is on."""
        mock_logger()
        mock_healthcheck(widths={'config': [375, 1280], 'mobile': [390]})
        mock_snapshot()

        with patch.object(self.driver, 'refresh') as mock_refresh, \
             patch.object(local, 'change_window_dimension_and_wait'):
            percy_snapshot(self.driver, 'Reload Count', responsiveSnapshotCapture=True)
            self.assertEqual(mock_refresh.call_count, 3)

        s1 = httpretty.last_request().parsed_body
        self.assertEqual(len(s1['dom_snapshot']), 3)


class TestIframeCaptureUnit(unittest.TestCase):
    def test_iframe_context_switches_to_frame_and_back(self):
        """iframe_context enters the frame and switches back to parent on clean exit."""
        driver = Mock()
        frame_el = Mock()

        with local.iframe_context(driver, frame_el):
            driver.switch_to.frame.assert_called_once_with(frame_el)

        driver.switch_to.parent_frame.assert_called_once()

    def test_iframe_context_switches_back_on_exception(self):
        """iframe_context always switches back to parent even when the body raises."""
        driver = Mock()
        frame_el = Mock()

        with self.assertRaises(RuntimeError):
            with local.iframe_context(driver, frame_el):
                raise RuntimeError("boom")

        driver.switch_to.parent_frame.assert_called_once()

    def _make_frame_element(self, src="https://other.example.com/page",
                             percy_id="elem-123"):
        frame_el = Mock()
        frame_el.get_attribute = lambda attr: src if attr == 'src' else percy_id
        return frame_el

    def test_process_frame_returns_result_on_success(self):
        """process_frame returns a dict with iframeData, iframeSnapshot, and frameUrl."""
        driver = Mock()
        driver.execute_script.return_value = {"html": "<html/>"}
        frame_el = self._make_frame_element()

        result = local.process_frame(driver, frame_el, {}, "percy_dom_script")

        self.assertIsNotNone(result)
        self.assertEqual(result["iframeData"]["percyElementId"], "elem-123")
        self.assertEqual(result["iframeSnapshot"], {"html": "<html/>"})
        self.assertEqual(result["frameUrl"], "https://other.example.com/page")

    def test_process_frame_returns_none_when_no_percy_element_id(self):
        """process_frame returns None when data-percy-element-id attribute is missing."""
        driver = Mock()
        driver.execute_script.return_value = {"html": "<html/>"}

        frame_el = Mock()
        frame_el.get_attribute = lambda attr: (
            "https://other.example.com/" if attr == 'src' else None
        )

        result = local.process_frame(driver, frame_el, {}, "percy_dom_script")

        self.assertIsNone(result)

    def test_process_frame_returns_none_on_script_injection_failure(self):
        """process_frame returns None when execute_script raises inside the iframe."""
        driver = Mock()
        driver.execute_script.side_effect = Exception("injection error")

        frame_el = self._make_frame_element()

        result = local.process_frame(driver, frame_el, {}, "percy_dom_script")

        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # Helpers for nested-frame-tree tests. The new flow drives iframe
    # discovery via driver.execute_script(enumerate_iframes_script(...))
    # which returns a list of metadata dicts (no real WebElements).
    # ------------------------------------------------------------------
    @staticmethod
    def _meta(src, percy_id, *, srcdoc=None, ignore=False, matches=False, index=0):
        return {
            "src": src,
            "srcdoc": srcdoc,
            "percyElementId": percy_id,
            "dataPercyIgnore": ignore,
            "matchesIgnoreSelector": matches,
            "index": index,
        }

    def test_get_serialized_dom_populates_cors_iframes(self):
        driver = Mock()
        # execute_script call order:
        #  [0] main serialize       [1] enumerate top-level iframes
        #  [2] querySelector (find iframe by id)
        #  [3] post-switch document.URL re-check
        #  [4] inject PercyDOM in frame
        #  [5] serialize the frame  [6] enumerate nested iframes (empty)
        driver.execute_script.side_effect = [
            {"html": '<html><iframe data-percy-element-id="cid-1"></iframe></html>',
             "resources": [{"url": "https://cdn/main.css", "content": "m"}]},
            [self._meta("http://main.example.com/inner", None),
             self._meta("https://cross.example.com/page", "cid-1", index=1)],
            Mock(name="iframe_element"),
            "https://cross.example.com/page",
            None,
            {"snapshot": {"html": "<iframe-html/>",
                          "resources": [{"url": "https://cdn/frame.css", "content": "f"}]},
             "frameUrl": "https://cross.example.com/page"},
            [],
        ]
        driver.current_url = "http://main.example.com/"

        dom = local.get_serialized_dom(driver, [], percy_dom_script="some_script")

        self.assertIn("corsIframes", dom)
        self.assertEqual(len(dom["corsIframes"]), 1)
        entry = dom["corsIframes"][0]
        self.assertEqual(entry["iframeData"]["percyElementId"], "cid-1")
        self.assertEqual(entry["iframeSnapshot"]["html"], "<iframe-html/>")
        self.assertEqual(entry["frameUrl"], "https://cross.example.com/page")
        self.assertNotIn("srcdoc", dom["html"])

    def test_get_serialized_dom_skips_blank_src_frames(self):
        """Frames with no src or src='about:blank' are not processed."""
        driver = Mock()
        driver.execute_script.side_effect = [
            {"html": '<html><body><iframe></iframe></body></html>'},
            [self._meta("about:blank", None),
             self._meta("", None, index=1)],
        ]
        driver.current_url = "http://main.example.com/"

        dom = local.get_serialized_dom(driver, [], percy_dom_script="some_script")

        self.assertNotIn("corsIframes", dom)

    def test_get_serialized_dom_no_cors_iframes_without_script(self):
        """Without a percy_dom_script, cross-origin iframes are not processed."""
        driver = Mock()
        driver.execute_script.return_value = {
            "html": '<html><body><iframe data-percy-element-id="cid-1"></iframe></body></html>'
        }
        driver.current_url = "http://main.example.com/"

        dom = local.get_serialized_dom(driver, [], percy_dom_script=None)

        self.assertNotIn("corsIframes", dom)

    def test_get_serialized_dom_cookies_always_attached(self):
        """Cookies are always added to the dom_snapshot regardless of iframes."""
        driver = Mock()
        driver.execute_script.side_effect = [{"html": "<html/>"}, []]
        driver.current_url = "http://main.example.com/"

        cookies = [{"name": "session", "value": "abc"}]
        dom = local.get_serialized_dom(driver, cookies, percy_dom_script="script")

        self.assertEqual(dom["cookies"], cookies)

    def test_get_serialized_dom_same_host_different_scheme_is_cross_origin(self):
        """http://example.com vs https://example.com → cross-origin."""
        driver = Mock()
        driver.execute_script.side_effect = [
            {"html": '<html><iframe data-percy-element-id="percy-id-1"></iframe></html>'},
            [self._meta("https://main.example.com/widget", "percy-id-1")],
            Mock(),
            "https://main.example.com/widget",
            None,
            {"snapshot": {"html": "<frame/>"}, "frameUrl": "https://main.example.com/widget"},
            [],
        ]
        driver.current_url = "http://main.example.com/"

        dom = local.get_serialized_dom(driver, [], percy_dom_script="script")

        self.assertIn("corsIframes", dom)
        self.assertEqual(dom["corsIframes"][0]["iframeSnapshot"]["html"], "<frame/>")

    def test_get_serialized_dom_same_host_different_port_is_cross_origin(self):
        """http://example.com:3000 vs http://example.com:4000 → cross-origin."""
        driver = Mock()
        driver.execute_script.side_effect = [
            {"html": '<html><iframe data-percy-element-id="percy-id-port"></iframe></html>'},
            [self._meta("http://main.example.com:4000/widget", "percy-id-port")],
            Mock(),
            "http://main.example.com:4000/widget",
            None,
            {"snapshot": {"html": "<frame/>"},
             "frameUrl": "http://main.example.com:4000/widget"},
            [],
        ]
        driver.current_url = "http://main.example.com:3000/"

        dom = local.get_serialized_dom(driver, [], percy_dom_script="script")

        self.assertIn("corsIframes", dom)
        self.assertEqual(dom["corsIframes"][0]["iframeSnapshot"]["html"], "<frame/>")

    def test_get_serialized_dom_same_origin_is_not_cross_origin(self):
        """Same-origin iframes are skipped before any frame switch."""
        driver = Mock()
        driver.execute_script.side_effect = [
            {"html": "<html/>"},
            [self._meta("http://main.example.com/inner.html", "percy-id-same")],
        ]
        driver.current_url = "http://main.example.com/"

        dom = local.get_serialized_dom(driver, [], percy_dom_script="script")

        self.assertNotIn("corsIframes", dom)

    def test_process_frame_passes_enable_javascript_option(self):
        """process_frame serializes the frame with enableJavaScript=True, mirroring
        Node.js's { ...options, enableJavascript: true } (JS) / enableJavaScript (Python)."""
        driver = Mock()
        driver.execute_script.return_value = {"html": "<html/>"}

        frame_el = Mock()
        frame_el.get_attribute = lambda attr: (
            "https://other.example.com/page" if attr == 'src' else "elem-abc"
        )

        local.process_frame(driver, frame_el, {"someOpt": 1}, "percy_dom_script")

        # Second execute_script call is the PercyDOM.serialize; confirm enableJavaScript=True
        # is embedded in the JSON argument string
        serialize_call_args = driver.execute_script.call_args_list[1][0][0]
        self.assertIn('"enableJavaScript": true', serialize_call_args)
        # Original option must also be forwarded
        self.assertIn('"someOpt": 1', serialize_call_args)

    def test_process_frame_uses_unknown_src_fallback(self):
        """When frame element has no src attribute, frameUrl falls back to 'unknown-src'."""
        driver = Mock()
        driver.execute_script.return_value = {"html": "<html/>"}

        frame_el = Mock()
        frame_el.get_attribute = lambda attr: (
            None if attr == 'src' else "elem-no-src"
        )

        result = local.process_frame(driver, frame_el, {}, "percy_dom_script")

        self.assertIsNotNone(result)
        self.assertEqual(result["frameUrl"], "unknown-src")

    def test_get_serialized_dom_corsIframes_entry_has_correct_structure(self):
        dom_html = '<html><body><iframe data-percy-element-id="cid-1"></iframe></body></html>'
        frame_resource = {"url": "https://cdn/frame.css", "content": "f"}
        driver = Mock()
        driver.execute_script.side_effect = [
            {"html": dom_html, "resources": []},
            [self._meta("https://cross.example.com/page", "cid-1")],
            Mock(),
            "https://cross.example.com/page",
            None,
            {"snapshot": {"html": '<html><body><h1>Frame</h1></body></html>',
                          "resources": [frame_resource]},
             "frameUrl": "https://cross.example.com/page"},
            [],
        ]
        driver.current_url = "http://main.example.com/"

        dom = local.get_serialized_dom(driver, [], percy_dom_script="some_script")

        self.assertIn("corsIframes", dom)
        entry = dom["corsIframes"][0]
        self.assertEqual(entry["frameUrl"], "https://cross.example.com/page")
        self.assertEqual(entry["iframeData"], {"percyElementId": "cid-1"})
        self.assertIn("Frame", entry["iframeSnapshot"]["html"])
        self.assertIn(frame_resource, entry["iframeSnapshot"]["resources"])

    def test_get_serialized_dom_multiple_cross_origin_frames(self):
        """All cross-origin frames are collected; same-origin frames are skipped."""
        driver = Mock()
        driver.execute_script.side_effect = [
            {"html": '<html><iframe data-percy-element-id="pid-1"></iframe>'
                     '<iframe data-percy-element-id="pid-2"></iframe>'
                     '<iframe data-percy-element-id="pid-same"></iframe></html>'},
            [self._meta("https://a.other.com/w1", "pid-1"),
             self._meta("http://main.example.com/inner", "pid-same", index=1),
             self._meta("https://b.other.com/w2", "pid-2", index=2)],
            # frame 1
            Mock(), "https://a.other.com/w1", None,
            {"snapshot": {"html": "<frame1/>"}, "frameUrl": "https://a.other.com/w1"},
            [],
            # frame 2
            Mock(), "https://b.other.com/w2", None,
            {"snapshot": {"html": "<frame2/>"}, "frameUrl": "https://b.other.com/w2"},
            [],
        ]
        driver.current_url = "http://main.example.com/"

        dom = local.get_serialized_dom(driver, [], percy_dom_script="script")

        self.assertIn("corsIframes", dom)
        self.assertEqual(len(dom["corsIframes"]), 2)
        pids = [e["iframeData"]["percyElementId"] for e in dom["corsIframes"]]
        self.assertIn("pid-1", pids)
        self.assertIn("pid-2", pids)
        self.assertNotIn("pid-same", pids)

    def test_get_serialized_dom_handles_enumerate_exception(self):
        """If iframe enumeration raises, the error is swallowed, cookies are still
        attached, and CORS iframe stitching is skipped."""
        driver = Mock()
        driver.execute_script.side_effect = [
            {"html": "<html/>"},
            Exception("enumerate error"),
        ]
        driver.current_url = "http://main.example.com/"

        dom = local.get_serialized_dom(driver, [{"name": "k", "value": "v"}],
                                       percy_dom_script="script")

        self.assertNotIn("corsIframes", dom)
        self.assertEqual(dom["cookies"], [{"name": "k", "value": "v"}])

    def test_get_serialized_dom_process_frame_failure_is_skipped(self):
        """If a cross-origin frame fails to process, it is omitted and the rest succeed."""
        driver = Mock()
        driver.execute_script.side_effect = [
            {"html": '<html><iframe data-percy-element-id="pid-fail"></iframe>'
                     '<iframe data-percy-element-id="pid-ok"></iframe></html>'},
            [self._meta("https://fail.example.com/page", "pid-fail"),
             self._meta("https://ok.example.com/page", "pid-ok", index=1)],
            # fail frame: querySelector returns, document.URL ok, inject raises
            Mock(), "https://fail.example.com/page", Exception("inject failed"),
            # ok frame
            Mock(), "https://ok.example.com/page", None,
            {"snapshot": {"html": "<ok/>"}, "frameUrl": "https://ok.example.com/page"},
            [],
        ]
        driver.current_url = "http://main.example.com/"
        # switch_to.frame succeeds for both; parent_frame called in finally
        dom = local.get_serialized_dom(driver, [], percy_dom_script="script")

        self.assertIn("corsIframes", dom)
        self.assertEqual(len(dom["corsIframes"]), 1)
        self.assertEqual(dom["corsIframes"][0]["iframeData"]["percyElementId"], "pid-ok")
        self.assertEqual(dom["corsIframes"][0]["iframeSnapshot"]["html"], "<ok/>")


class TestIframeHelpers(unittest.TestCase):
    """Unit tests for the inlined sdk-utils helpers."""

    def test_is_unsupported_iframe_src_truthy_cases(self):
        for src in [None, '', 'about:blank', 'about:srcdoc', 'javascript:alert(1)',
                    'data:text/html;base64,', 'blob:http://foo', 'chrome://settings',
                    'vbscript:msg']:
            self.assertTrue(local.is_unsupported_iframe_src(src), msg=src)

    def test_is_unsupported_iframe_src_falsy_cases(self):
        for src in ['http://x', 'https://x.example.com/p', 'http://x.example.com:8080/']:
            self.assertFalse(local.is_unsupported_iframe_src(src), msg=src)

    def test_clamp_frame_depth_bounds(self):
        self.assertEqual(local.clamp_frame_depth(0), 1)
        self.assertEqual(local.clamp_frame_depth(-3), 1)
        self.assertEqual(local.clamp_frame_depth(1), 1)
        self.assertEqual(local.clamp_frame_depth(3), 3)
        self.assertEqual(local.clamp_frame_depth(5), 5)
        self.assertEqual(local.clamp_frame_depth(99), local.DEFAULT_MAX_FRAME_DEPTH)
        self.assertEqual(local.clamp_frame_depth("not-a-number"), local.DEFAULT_MAX_FRAME_DEPTH)
        self.assertEqual(local.clamp_frame_depth(None), local.DEFAULT_MAX_FRAME_DEPTH)

    def test_normalize_ignore_selectors(self):
        self.assertEqual(local.normalize_ignore_selectors(None), [])
        self.assertEqual(local.normalize_ignore_selectors(''), [])
        self.assertEqual(local.normalize_ignore_selectors('  '), [])
        self.assertEqual(local.normalize_ignore_selectors('.x'), ['.x'])
        self.assertEqual(local.normalize_ignore_selectors(['.x', '', None, '.y']),
                         ['.x', '.y'])
        self.assertEqual(local.normalize_ignore_selectors(123), [])

    def test_resolve_max_frame_depth_precedence(self):
        # options camel takes precedence
        self.assertEqual(local.resolve_max_frame_depth(
            {'maxIframeDepth': 2}, {'snapshot': {'maxIframeDepth': 4}}), 2)
        # options snake works
        self.assertEqual(local.resolve_max_frame_depth(
            {'max_iframe_depth': 3}, None), 3)
        # falls back to config
        self.assertEqual(local.resolve_max_frame_depth(
            {}, {'snapshot': {'maxIframeDepth': 4}}), 4)
        # defaults when nothing
        self.assertEqual(local.resolve_max_frame_depth({}, {}),
                         local.DEFAULT_MAX_FRAME_DEPTH)

    def test_resolve_ignore_selectors_precedence(self):
        self.assertEqual(local.resolve_ignore_selectors(
            {'ignoreIframeSelectors': '.a'}, None), ['.a'])
        self.assertEqual(local.resolve_ignore_selectors(
            {'ignore_iframe_selectors': ['.b', '.c']}, None), ['.b', '.c'])
        self.assertEqual(local.resolve_ignore_selectors(
            {}, {'snapshot': {'ignoreIframeSelectors': ['.d']}}), ['.d'])
        self.assertEqual(local.resolve_ignore_selectors({}, {}), [])

    def test_enumerate_iframes_script_embeds_selectors(self):
        script = local.enumerate_iframes_script(['.a', '.b'])
        self.assertIn('".a"', script)
        self.assertIn('".b"', script)
        self.assertIn('querySelectorAll(\'iframe\')', script)
        self.assertIn('data-percy-element-id', script)
        self.assertIn("hasAttribute('data-percy-ignore')", script)

    def test_enumerate_iframes_script_with_no_selectors(self):
        script = local.enumerate_iframes_script(None)
        self.assertIn('= [];', script)


class TestIframeTreeBehavior(unittest.TestCase):
    """Tests exercising the depth cap, cycle guard, ignore attrs, and selectors."""

    @staticmethod
    def _meta(src, percy_id, *, srcdoc=None, ignore=False, matches=False, index=0):
        return {
            "src": src,
            "srcdoc": srcdoc,
            "percyElementId": percy_id,
            "dataPercyIgnore": ignore,
            "matchesIgnoreSelector": matches,
            "index": index,
        }

    def test_data_percy_ignore_attribute_skips_frame(self):
        """An iframe carrying data-percy-ignore is dropped before any switch."""
        driver = Mock()
        driver.execute_script.side_effect = [
            {"html": "<html/>"},
            [self._meta("https://cross.example.com/p", "pid-1", ignore=True)],
        ]
        driver.current_url = "http://main.example.com/"

        dom = local.get_serialized_dom(driver, [], percy_dom_script="script")
        self.assertNotIn("corsIframes", dom)

    def test_ignore_iframe_selectors_option_skips_matched_frame(self):
        """A frame whose `matchesIgnoreSelector` was set by the enumerate
        script is dropped. The selector list is forwarded into the JS."""
        driver = Mock()
        driver.execute_script.side_effect = [
            {"html": "<html/>"},
            [self._meta("https://cross.example.com/p", "pid-1", matches=True)],
        ]
        driver.current_url = "http://main.example.com/"

        dom = local.get_serialized_dom(
            driver, [], percy_dom_script="script",
            ignoreIframeSelectors=['.ad', '.tracker']
        )
        self.assertNotIn("corsIframes", dom)

        # The selectors must have been baked into the enumerate JS the SDK ran.
        enumerate_call = driver.execute_script.call_args_list[1][0][0]
        self.assertIn('".ad"', enumerate_call)
        self.assertIn('".tracker"', enumerate_call)

    def test_post_switch_url_recheck_drops_about_blank(self):
        """If document.URL inside the frame becomes unsupported (about:blank,
        net-error), drop the frame and do not serialize."""
        driver = Mock()
        driver.execute_script.side_effect = [
            {"html": "<html/>"},
            [self._meta("https://cross.example.com/p", "pid-1")],
            Mock(),                  # querySelector for the iframe element
            "about:blank",           # post-switch document.URL is unsupported
        ]
        driver.current_url = "http://main.example.com/"

        dom = local.get_serialized_dom(driver, [], percy_dom_script="script")
        self.assertNotIn("corsIframes", dom)
        # parent_frame called in finally
        driver.switch_to.parent_frame.assert_called_once()

    def test_max_iframe_depth_caps_recursion(self):
        """With maxIframeDepth=1, nested iframes are not entered."""
        driver = Mock()
        # Top-level frame at depth 1 is captured; nested children are skipped
        # because depth+1 > max.
        driver.execute_script.side_effect = [
            {"html": "<html/>"},
            [self._meta("https://a.example.com/", "pid-1")],
            Mock(),
            "https://a.example.com/",
            None,
            {"snapshot": {"html": "<a/>"}, "frameUrl": "https://a.example.com/"},
        ]
        driver.current_url = "http://main.example.com/"

        dom = local.get_serialized_dom(
            driver, [], percy_dom_script="script", maxIframeDepth=1
        )
        self.assertEqual(len(dom["corsIframes"]), 1)
        # Only 6 execute_script calls — no nested-enumerate call.
        self.assertEqual(driver.execute_script.call_count, 6)

    def test_ancestor_cycle_guard_stops_descent(self):
        """If a nested iframe's src appears in the ancestor chain, it is not
        recursed into."""
        driver = Mock()
        driver.execute_script.side_effect = [
            {"html": "<html/>"},
            # top-level
            [self._meta("https://a.example.com/", "pid-a")],
            # switch into a
            Mock(),
            "https://a.example.com/",
            None,
            {"snapshot": {"html": "<a/>"}, "frameUrl": "https://a.example.com/"},
            # nested enumeration inside a: cycles back to the page URL
            [self._meta("http://main.example.com/", "pid-cycle")],
        ]
        driver.current_url = "http://main.example.com/"

        dom = local.get_serialized_dom(driver, [], percy_dom_script="script")
        self.assertEqual(len(dom["corsIframes"]), 1)
        self.assertEqual(dom["corsIframes"][0]["iframeData"]["percyElementId"], "pid-a")

    def test_percy_context_lost_preserves_partial_capture(self):
        """If parent_frame() fails at depth > 1, raise PercyContextLost and have
        the top-level walk include whatever was captured so far."""
        driver = Mock()
        # First top-level frame captures successfully and recurses; the nested
        # frame raises on parent_frame, which surfaces as PercyContextLost.
        driver.execute_script.side_effect = [
            {"html": "<html/>"},
            [self._meta("https://a.example.com/", "pid-a"),
             self._meta("https://b.example.com/", "pid-b", index=1)],
            # frame a
            Mock(), "https://a.example.com/", None,
            {"snapshot": {"html": "<a/>"}, "frameUrl": "https://a.example.com/"},
            # nested enumeration inside a
            [self._meta("https://c.example.com/", "pid-c")],
            # frame c (nested)
            Mock(), "https://c.example.com/", None,
            {"snapshot": {"html": "<c/>"}, "frameUrl": "https://c.example.com/"},
            [],
        ]
        driver.current_url = "http://main.example.com/"
        # parent_frame fails on the *second* call (returning from c -> a).
        call_count = {'n': 0}
        def parent_frame_side_effect():
            call_count['n'] += 1
            if call_count['n'] == 1:
                raise RuntimeError("lost context")
        driver.switch_to.parent_frame.side_effect = parent_frame_side_effect

        dom = local.get_serialized_dom(driver, [], percy_dom_script="script")

        self.assertIn("corsIframes", dom)
        # The partial capture from inside frame a (a + c) must be preserved.
        pids = [e["iframeData"]["percyElementId"] for e in dom["corsIframes"]]
        self.assertIn("pid-a", pids)
        self.assertIn("pid-c", pids)
        # frame b must NOT have been processed — sibling iteration aborted.
        self.assertNotIn("pid-b", pids)


class TestExposeClosedShadowRoots(unittest.TestCase):
    def test_noop_on_driver_without_cdp(self):
        """A driver without execute_cdp_cmd is a silent no-op."""
        class StubDriver:  # pylint: disable=too-few-public-methods
            # no execute_cdp_cmd
            pass
        # Should not raise
        local.expose_closed_shadow_roots(StubDriver())

    def test_noop_when_dom_enable_fails(self):
        """If DOM.enable raises (non-Chromium), we exit silently before
        anything else runs."""
        driver = Mock()
        driver.execute_cdp_cmd.side_effect = Exception("cdp not supported")
        local.expose_closed_shadow_roots(driver)
        # only the failed DOM.enable call happens
        driver.execute_cdp_cmd.assert_called_once_with("DOM.enable", {})

    def test_exposes_closed_roots_via_weakmap(self):
        """When CDP returns a closed shadow root, exposeClosedShadowRoots
        creates the WeakMap and calls Runtime.callFunctionOn to populate it."""
        driver = Mock()
        cdp_calls = []
        def cdp(cmd, params):  # pylint: disable=too-many-return-statements
            cdp_calls.append(cmd)
            if cmd == "DOM.enable":
                return {}
            if cmd == "DOM.getDocument":
                return {
                    "root": {
                        "backendNodeId": 1,
                        "shadowRoots": [],
                        "children": [{
                            "backendNodeId": 2,
                            "shadowRoots": [{
                                "backendNodeId": 3,
                                "shadowRootType": "closed",
                                "children": []
                            }],
                        }]
                    }
                }
            if cmd == "DOM.resolveNode":
                if params["backendNodeId"] == 2:
                    return {"object": {"objectId": "host-obj"}}
                if params["backendNodeId"] == 3:
                    return {"object": {"objectId": "shadow-obj"}}
            if cmd == "Runtime.callFunctionOn":
                return {}
            if cmd == "DOM.disable":
                return {}
            return {}
        driver.execute_cdp_cmd.side_effect = cdp

        local.expose_closed_shadow_roots(driver)

        # WeakMap was created on the page
        scripts = [c[0][0] for c in driver.execute_script.call_args_list]
        self.assertTrue(any("__percyClosedShadowRoots" in s for s in scripts))
        self.assertIn("Runtime.callFunctionOn", cdp_calls)
        self.assertIn("DOM.resolveNode", cdp_calls)

    def test_skips_content_document_subtrees(self):
        """Closed shadow roots inside an iframe's contentDocument are not
        exposed (their JS context is separate from the page main world)."""
        driver = Mock()
        def cdp(cmd, _params):
            if cmd == "DOM.getDocument":
                return {
                    "root": {
                        "backendNodeId": 1,
                        "children": [{
                            "backendNodeId": 2,
                            "contentDocument": {
                                "backendNodeId": 100,
                                "shadowRoots": [{
                                    "backendNodeId": 101,
                                    "shadowRootType": "closed",
                                }],
                            },
                        }]
                    }
                }
            return {}
        driver.execute_cdp_cmd.side_effect = cdp
        local.expose_closed_shadow_roots(driver)
        # No Runtime.callFunctionOn — the closed root inside contentDocument
        # was skipped, and execute_script for the WeakMap was never run.
        scripts = [c[0][0] for c in driver.execute_script.call_args_list]
        self.assertFalse(any("__percyClosedShadowRoots" in s for s in scripts))


class TestCreateRegion(unittest.TestCase):

    def test_create_region_with_all_params(self):
        result = create_region(
            boundingBox={"x": 10, "y": 20, "width": 100, "height": 200},
            elementXpath="//*[@id='test']",
            elementCSS=".test-class",
            padding=10,
            algorithm="intelliignore",
            diffSensitivity=0.8,
            imageIgnoreThreshold=0.5,
            carouselsEnabled=True,
            bannersEnabled=False,
            adsEnabled=True,
            diffIgnoreThreshold=0.2
        )

        expected_result = {
            "algorithm": "intelliignore",
            "elementSelector": {
                "boundingBox": {"x": 10, "y": 20, "width": 100, "height": 200},
                "elementXpath": "//*[@id='test']",
                "elementCSS": ".test-class"
            },
            "padding": 10,
            "configuration": {
                "diffSensitivity": 0.8,
                "imageIgnoreThreshold": 0.5,
                "carouselsEnabled": True,
                "bannersEnabled": False,
                "adsEnabled": True
            },
            "assertion": {
                "diffIgnoreThreshold": 0.2
            }
        }

        self.assertEqual(result, expected_result)

    def test_create_region_with_minimal_params(self):
        result = create_region(
            algorithm="standard",
            boundingBox={"x": 10, "y": 20, "width": 100, "height": 200}
        )

        expected_result = {
            "algorithm": "standard",
            "elementSelector": {
                "boundingBox": {"x": 10, "y": 20, "width": 100, "height": 200}
            }
        }

        self.assertEqual(result, expected_result)

    def test_create_region_with_padding(self):
        result = create_region(
            algorithm="ignore",
            padding=15
        )

        expected_result = {
            "algorithm": "ignore",
            "elementSelector": {},
            "padding": 15
        }

        self.assertEqual(result, expected_result)

    def test_create_region_with_configuration_only_for_valid_algorithms(self):
        result = create_region(
            algorithm="intelliignore",
            diffSensitivity=0.9,
            imageIgnoreThreshold=0.7
        )

        expected_result = {
            "algorithm": "intelliignore",
            "elementSelector": {},
            "configuration": {
                "diffSensitivity": 0.9,
                "imageIgnoreThreshold": 0.7
            }
        }

        self.assertEqual(result, expected_result)

    def test_create_region_with_diffIgnoreThreshold_in_assertion(self):
        result = create_region(
            algorithm="standard",
            diffIgnoreThreshold=0.3
        )

        expected_result = {
            "algorithm": "standard",
            "elementSelector": {},
            "assertion": {
                "diffIgnoreThreshold": 0.3
            }
        }

        self.assertEqual(result, expected_result)

    def test_create_region_with_invalid_algorithm(self):
        result = create_region(
            algorithm="invalid_algorithm"
        )

        expected_result = {
            "algorithm": "invalid_algorithm",
            "elementSelector": {}
        }

        self.assertEqual(result, expected_result)


class TestShouldSkipIframeOrdering(unittest.TestCase):
    """Regression tests for the srcdoc-vs-unsupported-src ordering fix."""

    def test_pure_srcdoc_iframe_with_empty_src_takes_srcdoc_branch(self):
        """A pure-srcdoc iframe has empty src; the srcdoc branch must run BEFORE
        the unsupported-src check so we route it the same way percy-nightwatch
        does (inlined via PercyDOM.serialize)."""
        with patch.object(local, 'log') as mock_log:
            iframe = {
                'src': '',
                'srcdoc': '<p>hello</p>',
                'percyElementId': 'pid-srcdoc',
                'dataPercyIgnore': False,
                'matchesIgnoreSelector': False,
                'index': 3,
            }
            self.assertTrue(local._should_skip_iframe(
                iframe, "http://main.example.com"))
            # The skip reason must be the srcdoc branch, not 'unsupported src'.
            messages = [c.args[0] for c in mock_log.call_args_list]
            self.assertTrue(any("Skipping srcdoc iframe" in m for m in messages),
                            f"Expected srcdoc skip reason, got: {messages}")
            self.assertFalse(any("unsupported iframe src" in m for m in messages))

    def test_srcdoc_with_supported_src_still_takes_srcdoc_branch(self):
        """If a frame carries srcdoc AND a real src, srcdoc still wins —
        the inlined HTML takes precedence over the cross-origin load."""
        with patch.object(local, 'log') as mock_log:
            iframe = {
                'src': 'https://cross.example.com/page',
                'srcdoc': '<p>doc</p>',
                'percyElementId': 'pid-both',
                'dataPercyIgnore': False,
                'matchesIgnoreSelector': False,
                'index': 0,
            }
            self.assertTrue(local._should_skip_iframe(
                iframe, "http://main.example.com"))
            messages = [c.args[0] for c in mock_log.call_args_list]
            self.assertTrue(any("Skipping srcdoc iframe" in m for m in messages))


class TestRedirectCycleGuardOnFrameUrl(unittest.TestCase):
    """A frame whose static src differs from its resolved document.URL still
    has to be caught by the cycle guard."""

    @staticmethod
    def _meta(src, percy_id, *, srcdoc=None, ignore=False, matches=False, index=0):
        return {
            "src": src, "srcdoc": srcdoc, "percyElementId": percy_id,
            "dataPercyIgnore": ignore, "matchesIgnoreSelector": matches,
            "index": index,
        }

    def test_redirect_chain_cycle_caught_on_frameUrl(self):
        """page_url=B. Frame on page has src=A which redirects → document.URL=B.
        The cycle must be detected via the post-switch frameUrl, not just src."""
        driver = Mock()
        # Calls in order:
        # 0: main serialize
        # 1: enumerate top-level iframes
        # 2: querySelector for the iframe element
        # 3: post-switch document.URL → resolves to the page URL (cycle!)
        driver.execute_script.side_effect = [
            {"html": "<html/>"},
            [self._meta("https://a.example.com/redirector", "pid-a")],
            Mock(name="iframe_element"),
            "http://main.example.com/",   # resolved to page URL → cycle
        ]
        driver.current_url = "http://main.example.com/"

        dom = local.get_serialized_dom(
            driver, [], percy_dom_script="some_script"
        )

        # Frame was switched into and back out of, but never serialized.
        self.assertNotIn("corsIframes", dom)
        # We did the post-switch URL read (call #3 above) and then bailed —
        # no PercyDOM.serialize on this frame, no nested enumeration.
        self.assertEqual(driver.execute_script.call_count, 4)
        driver.switch_to.parent_frame.assert_called_once()


class TestTopLevelIframeFailureLog(unittest.TestCase):
    """When a depth==1 iframe fails to process, the user should see it at
    info level (not buried at debug)."""

    @staticmethod
    def _meta(src, percy_id):
        return {
            "src": src, "srcdoc": None, "percyElementId": percy_id,
            "dataPercyIgnore": False, "matchesIgnoreSelector": False,
            "index": 0,
        }

    def test_depth1_failure_logs_at_info(self):
        driver = Mock()
        driver.execute_script.side_effect = [
            {"html": "<html/>"},
            [self._meta("https://cross.example.com/page", "pid-1")],
            Mock(),                              # iframe element
            "https://cross.example.com/page",    # post-switch document.URL
            Exception("dom inject blew up"),     # injection of PercyDOM raises
        ]
        driver.current_url = "http://main.example.com/"

        with patch.object(local, 'log') as mock_log:
            local.get_serialized_dom(driver, [], percy_dom_script="script")

        # One of the log calls must mention the failure AT info level.
        failure_calls = [
            c for c in mock_log.call_args_list
            if "Failed to process cross-origin iframe" in c.args[0]
        ]
        self.assertEqual(len(failure_calls), 1, "expected exactly one failure log")
        # Second positional arg, if present, is the level. Default is 'info'.
        lvl = failure_calls[0].args[1] if len(failure_calls[0].args) > 1 \
            else failure_calls[0].kwargs.get('lvl', 'info')
        self.assertEqual(lvl, "info")

    def test_nested_failure_stays_at_debug(self):
        """Depth > 1 failures stay at debug to avoid log spam on chatty pages."""
        driver = Mock()
        driver.execute_script.side_effect = [
            {"html": "<html/>"},
            [self._meta("https://a.example.com/", "pid-a")],
            Mock(), "https://a.example.com/", None,
            {"snapshot": {"html": "<a/>"}, "frameUrl": "https://a.example.com/"},
            # nested enumeration inside a returns a child
            [self._meta("https://b.example.com/", "pid-b")],
            # nested child blows up at injection
            Mock(), "https://b.example.com/",
            Exception("nested inject blew up"),
        ]
        driver.current_url = "http://main.example.com/"

        with patch.object(local, 'log') as mock_log:
            local.get_serialized_dom(driver, [], percy_dom_script="script")

        nested_failures = [
            c for c in mock_log.call_args_list
            if "Failed to process cross-origin iframe https://b.example.com" in c.args[0]
        ]
        self.assertEqual(len(nested_failures), 1)
        lvl = nested_failures[0].args[1] if len(nested_failures[0].args) > 1 else "info"
        self.assertEqual(lvl, "debug")


if __name__ == '__main__':
    unittest.main()

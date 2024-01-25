import unittest
from unittest.mock import patch, Mock
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
import json

import httpretty
import requests
from selenium.webdriver import Firefox, FirefoxOptions
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.remote.remote_connection import RemoteConnection
from selenium.webdriver.safari.remote_connection import SafariRemoteConnection

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
def mock_healthcheck(fail=False, fail_how='error', session_type=None):
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

    health_body = json.dumps(health_body)
    httpretty.register_uri(
        httpretty.GET, 'http://localhost:5338/percy/healthcheck',
        body=health_body,
        adding_headers=health_headers,
        status=health_status)
    httpretty.register_uri(
        httpretty.GET, 'http://localhost:5338/percy/dom.js',
        body='window.PercyDOM = { serialize: () => document.documentElement.outerHTML };',
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
        cls.driver = Firefox(options=options)

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()

    def setUp(self):
        # clear the cached value for testing
        local.is_percy_enabled.cache_clear()
        local.fetch_percy_dom.cache_clear()
        self.driver.get('http://localhost:8000')
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
                'https://docs.percy.io/docs/migrating-to-percy-cli')

        self.assertEqual(httpretty.last_request().path, '/percy/healthcheck')

    def test_posts_snapshots_to_the_local_percy_server(self):
        mock_healthcheck()
        mock_snapshot()

        percy_snapshot(self.driver, 'Snapshot 1')
        response = percy_snapshot(self.driver, 'Snapshot 2', enable_javascript=True)

        self.assertEqual(httpretty.last_request().path, '/percy/snapshot')

        s1 = httpretty.latest_requests()[2].parsed_body
        self.assertEqual(s1['name'], 'Snapshot 1')
        self.assertEqual(s1['url'], 'http://localhost:8000/')
        self.assertEqual(s1['dom_snapshot'], '<html><head></head><body>Snapshot Me</body></html>')
        self.assertRegex(s1['client_info'], r'percy-selenium-python/\d+')
        self.assertRegex(s1['environment_info'][0], r'selenium/\d+')
        self.assertRegex(s1['environment_info'][1], r'python/\d+')

        s2 = httpretty.latest_requests()[3].parsed_body
        self.assertEqual(s2['name'], 'Snapshot 2')
        self.assertEqual(s2['enable_javascript'], True)
        self.assertEqual(response, None)

    def test_posts_snapshots_to_the_local_percy_server_for_sync(self):
        mock_healthcheck()
        mock_snapshot(False, True)

        percy_snapshot(self.driver, 'Snapshot 1')
        response = percy_snapshot(self.driver, 'Snapshot 2', enable_javascript=True, sync=True)

        self.assertEqual(httpretty.last_request().path, '/percy/snapshot')

        s1 = httpretty.latest_requests()[2].parsed_body
        self.assertEqual(s1['name'], 'Snapshot 1')
        self.assertEqual(s1['url'], 'http://localhost:8000/')
        self.assertEqual(s1['dom_snapshot'], '<html><head></head><body>Snapshot Me</body></html>')
        self.assertRegex(s1['client_info'], r'percy-selenium-python/\d+')
        self.assertRegex(s1['environment_info'][0], r'selenium/\d+')
        self.assertRegex(s1['environment_info'][1], r'python/\d+')

        s2 = httpretty.latest_requests()[3].parsed_body
        self.assertEqual(s2['name'], 'Snapshot 2')
        self.assertEqual(s2['enable_javascript'], True)
        self.assertEqual(s2['sync'], True)
        self.assertEqual(response, data_object)

    def test_has_a_backwards_compatible_function(self):
        mock_healthcheck()
        mock_snapshot()

        percySnapshot(browser=self.driver, name='Snapshot')

        self.assertEqual(httpretty.last_request().path, '/percy/snapshot')

        s1 = httpretty.latest_requests()[2].parsed_body
        self.assertEqual(s1['name'], 'Snapshot')
        self.assertEqual(s1['url'], 'http://localhost:8000/')
        self.assertEqual(s1['dom_snapshot'], '<html><head></head><body>Snapshot Me</body></html>')

    def test_handles_snapshot_errors(self):
        mock_healthcheck(session_type="web")
        mock_snapshot(fail=True)

        with patch('builtins.print') as mock_print:
            percy_snapshot(self.driver, 'Snapshot 1')

            mock_print.assert_any_call(f'{LABEL} Could not take DOM snapshot "Snapshot 1"')

    def test_raise_error_poa_token_with_snapshot(self):
        mock_healthcheck(session_type="automate")

        with self.assertRaises(Exception) as context:
            percy_snapshot(self.driver, "Snapshot 1")

        self.assertEqual("Invalid function call - "\
        "percy_snapshot(). Please use percy_screenshot() function while using Percy with Automate."\
        " For more information on usage of PercyScreenshot, refer https://docs.percy.io/docs"\
        "/integrate-functional-testing-with-visual-testing", str(context.exception))

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
        driver.desired_capabilities = { 'key': 'value' }
        driver.command_executor = Mock(spec=mock_connection)
        driver.command_executor._url = 'https://hub-cloud.browserstack.com/wd/hub' # pylint: disable=W0212
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
                'https://docs.percy.io/docs/migrating-to-percy-cli')

        self.assertEqual(httpretty.last_request().path, '/percy/healthcheck')

    def test_disables_screenshot_when_the_driver_is_not_selenium(self):
        mock_healthcheck(fail=True, fail_how='no-version')
        with self.assertRaises(UnsupportedWebDriverException):
            percy_screenshot("dummy_driver", 'Snapshot 1')

    def test_camelcase_options(self):
        mock_healthcheck()
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
        mock_healthcheck()
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

        s1 = httpretty.latest_requests()[1].parsed_body
        self.assertEqual(s1['snapshotName'], 'Snapshot 1')
        self.assertEqual(s1['sessionId'], driver.session_id)
        self.assertEqual(s1['commandExecutorUrl'], driver.command_executor._url) # pylint: disable=W0212
        self.assertEqual(s1['capabilities'], dict(driver.capabilities))
        self.assertEqual(s1['sessionCapabilites'], dict(driver.desired_capabilities))
        self.assertRegex(s1['client_info'], r'percy-selenium-python/\d+')
        self.assertRegex(s1['environment_info'][0], r'selenium/\d+')
        self.assertRegex(s1['environment_info'][1], r'python/\d+')

        s2 = httpretty.latest_requests()[2].parsed_body
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

        with patch('builtins.print') as mock_print:
            percy_screenshot(self.driver, 'Snapshot 1')

            mock_print.assert_any_call(f'{LABEL} Could not take Screenshot "Snapshot 1"')

    def test_raise_error_web_token_with_screenshot(self):
        mock_healthcheck(session_type="web")

        with self.assertRaises(Exception) as context:
            percy_screenshot(self.driver, "Snapshot 1")

        self.assertEqual("Invalid function call - "\
        "percy_screenshot(). Please use percy_snapshot() function for taking screenshot. "\
        "percy_screenshot() should be used only while using Percy with Automate. "\
        "For more information on usage of percy_snapshot(), refer doc for your language "\
        "https://docs.percy.io/docs/end-to-end-testing", str(context.exception))

def get_percy_test_requests():
    response = requests.get('http://localhost:5338/test/requests', timeout=10)
    data = response.json()
    return data['requests']

class TestPercySnapshotIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        options = FirefoxOptions()
        options.add_argument('-headless')
        cls.driver = Firefox(options=options)

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()

    def setUp(self):
        # clear the cached value for testing
        local.is_percy_enabled.cache_clear()
        local.fetch_percy_dom.cache_clear()
        self.driver.get('http://localhost:8000')

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


if __name__ == '__main__':
    unittest.main()

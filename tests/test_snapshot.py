import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import httpretty
from selenium.webdriver import Firefox, FirefoxOptions

from percy import percy_snapshot, percySnapshot
import percy.snapshot as local
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

# daemon threads automatically shut down when the main process exits
mock_server = HTTPServer(('localhost', 8000), MockServerRequestHandler)
mock_server_thread = Thread(target=mock_server.serve_forever)
mock_server_thread.setDaemon(True)
mock_server_thread.start()

# mock helpers
def mock_healthcheck(fail=False):
    httpretty.register_uri(
        httpretty.GET, 'http://localhost:5338/percy/healthcheck',
        body=('{ "success": ' + ('true' if not fail else 'false, "error": "test"') + '}'),
        status=(500 if fail else 200))
    httpretty.register_uri(
        httpretty.GET, 'http://localhost:5338/percy/dom.js',
        body='window.PercyDOM = { serialize: () => document.documentElement.outerHTML };',
        status=200)

def mock_snapshot(fail=False):
    httpretty.register_uri(
        httpretty.POST, 'http://localhost:5338/percy/snapshot',
        body=('{ "success": ' + ('true' if not fail else 'false, "error": "test"') + '}'),
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

    def test_disables_snapshots_when_the_healthcheck_errors(self):
        # no mocks will cause the request to throw an error

        with patch('builtins.print') as mock_print:
            percy_snapshot(self.driver, 'Snapshot 1')
            percy_snapshot(self.driver, 'Snapshot 2')

            mock_print.assert_called_with(f'{LABEL} Percy is not running, disabling snapshots')

        self.assertEqual(len(httpretty.latest_requests()), 0)

    def test_posts_snapshots_to_the_local_percy_server(self):
        mock_healthcheck()
        mock_snapshot()

        percy_snapshot(self.driver, 'Snapshot 1')
        percy_snapshot(self.driver, 'Snapshot 2', enable_javascript=True)

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
        mock_healthcheck()
        mock_snapshot(fail=True)

        with patch('builtins.print') as mock_print:
            percy_snapshot(self.driver, 'Snapshot 1')

            mock_print.assert_any_call(f'{LABEL} Could not take DOM snapshot "Snapshot 1"')

if __name__ == '__main__':
    unittest.main()

# pylint: disable=[abstract-class-instantiated, arguments-differ]
import unittest
from unittest.mock import patch, Mock
from selenium.webdriver.remote.webdriver import WebDriver

from percy.driver_metadata import DriverMetaData

class TestDriverMetadata(unittest.TestCase):
    @patch('selenium.webdriver.remote.webdriver.WebDriver')
    @patch('percy.cache.Cache.CACHE', {})
    def setUp(self, mock_webdriver) -> None:
        mock_webdriver.__class__ = WebDriver  # pylint: disable=invalid-class-object
        self.mock_webdriver = mock_webdriver
        self.mock_webdriver.session_id = 'session_id_123'
        self.mock_webdriver.command_executor._url = 'https://example-hub:4444/wd/hub' # pylint: disable=W0212
        self.mock_webdriver.capabilities = {
            'browser': 'chrome',
            'platform': 'windows',
            'browserVersion': '115.0.1'
        }

        self.mock_webdriver.desired_capabilities = {
            'browser': 'chrome',
            'platform': 'windows',
            'browserVersion': '115.0.1',
            'session_name': 'abc'
          }

        self.metadata = DriverMetaData(self.mock_webdriver)

    @patch('percy.cache.Cache.CACHE', {})
    def test_session_id(self):
        session_id = 'session_id_123'
        self.mock_webdriver.session_id = session_id
        self.assertEqual(self.metadata.session_id, session_id)

    @patch('percy.cache.Cache.CACHE', {})
    def test_command_executor_url(self):
        url = 'https://example-hub:4444/wd/hub'
        self.assertEqual(self.metadata.command_executor_url, url)

    @patch('percy.cache.Cache.CACHE', {})
    def test_command_executor_url_falls_back_to_remote_server_addr(self):
        # Newer Selenium clients drop command_executor._url; fall back to
        # client_config.remote_server_addr instead of failing.
        command_executor = Mock(spec=['client_config'])
        command_executor.client_config.remote_server_addr = 'https://fallback-hub:4444/wd/hub'
        self.mock_webdriver.command_executor = command_executor
        self.assertEqual(
            self.metadata.command_executor_url, 'https://fallback-hub:4444/wd/hub'
        )

    @patch('percy.cache.Cache.CACHE', {})
    def test_capabilities(self):
        capabilities = {
            'browser': 'chrome',
            'platform': 'windows',
            'browserVersion': '115.0.1'
        }

        self.mock_webdriver.capabilities = capabilities
        self.assertDictEqual(self.metadata.capabilities, capabilities)

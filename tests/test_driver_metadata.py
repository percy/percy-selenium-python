# pylint: disable=[abstract-class-instantiated, arguments-differ]
import unittest
from unittest.mock import patch
from selenium.webdriver import Remote

from percy.driver_metadata import DriverMetaData

class TestDriverMetadata(unittest.TestCase):
    @patch('selenium.webdriver.Remote')
    @patch('percy.cache.Cache.CACHE', {})
    def setUp(self, mock_webdriver) -> None:
        mock_webdriver.__class__ = Remote
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
    def test_capabilities(self):
        capabilities = {
            'browser': 'chrome',
            'platform': 'windows',
            'browserVersion': '115.0.1'
        }

        self.mock_webdriver.capabilities = capabilities
        self.assertDictEqual(self.metadata.capabilities, capabilities)

    @patch('percy.cache.Cache.CACHE', {})
    def test_session_capabilities(self):
        session_capabilities = {
            'browser': 'chrome',
            'platform': 'windows',
            'browserVersion': '115.0.1',
            'session_name': 'abc'
          }

        self.mock_webdriver.session_capabilities = session_capabilities
        self.assertDictEqual(self.metadata.session_capabilities, session_capabilities)

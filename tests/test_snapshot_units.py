# pylint: disable=protected-access
"""Focused unit tests for snapshot.py helper functions and their error/
fallback paths. These run as plain unittests (no Percy CLI needed) and
target branches the end-to-end suite in test_snapshot.py does not reach."""
import os
import unittest
from unittest.mock import patch, MagicMock

import percy.snapshot as local


class TestLog(unittest.TestCase):
    @patch('percy.snapshot.PERCY_DEBUG', True)
    @patch('percy.snapshot.requests.post', side_effect=Exception('network down'))
    def test_log_swallows_post_failure(self, _mock_post):
        # Failing to ship the log to the CLI must never raise to the caller.
        local.log('hello', 'info')  # should not raise


class TestWaitForReady(unittest.TestCase):
    def _driver(self):
        driver = MagicMock()
        driver.timeouts.script = 5.0
        driver.execute_async_script.return_value = {'ok': True}
        return driver

    def test_set_script_timeout_failure_is_best_effort(self):
        driver = self._driver()
        driver.set_script_timeout.side_effect = Exception('unsupported')
        # timeoutMs > 0 so the timeout-adjust path runs; its failure is swallowed
        result = local._wait_for_ready(driver, None, {'readiness': {'timeoutMs': 5000}})
        self.assertEqual(result, {'ok': True})

    def test_restore_previous_timeout_failure_is_swallowed(self):
        driver = self._driver()
        # first call (set new timeout) succeeds, restore in finally raises
        driver.set_script_timeout.side_effect = [None, Exception('cannot restore')]
        result = local._wait_for_ready(driver, None, {'readiness': {'timeoutMs': 5000}})
        self.assertEqual(result, {'ok': True})


class TestGetSerializedDomIframe(unittest.TestCase):
    @patch('percy.snapshot._get_origin', side_effect=['https://page', Exception('bad url')])
    def test_iframe_origin_error_is_skipped(self, _mock_origin):
        driver = MagicMock()
        driver.execute_script.return_value = {'html': '<html></html>'}
        frame = MagicMock()
        frame.get_attribute.return_value = 'https://other.example/widget'
        driver.find_elements.return_value = [frame]
        driver.current_url = 'https://page/index'

        result = local.get_serialized_dom(
            driver, {'c': 1}, percy_config={}, percy_dom_script='PERCY_DOM',
        )
        # the un-parseable iframe is skipped; serialize result still returns
        self.assertEqual(result['cookies'], {'c': 1})
        self.assertNotIn('corsIframes', result)


class TestGetResponsiveWidths(unittest.TestCase):
    @patch('percy.snapshot.requests.get')
    def test_non_list_widths_raises_upgrade_hint(self, mock_get):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {'widths': 'not-a-list'}
        mock_get.return_value = response
        with self.assertRaises(Exception) as cm:
            local.get_responsive_widths([375])
        self.assertIn('Update Percy CLI', str(cm.exception))

    @patch('percy.snapshot.requests.get', side_effect=Exception('connection refused'))
    def test_request_failure_raises_upgrade_hint(self, _mock_get):
        with self.assertRaises(Exception) as cm:
            local.get_responsive_widths([375])
        self.assertIn('Update Percy CLI', str(cm.exception))


class TestChangeWindowDimension(unittest.TestCase):
    def test_resize_falls_back_to_set_window_size(self):
        driver = MagicMock()
        driver.capabilities = {'browserName': 'firefox'}
        # first resize attempt raises, fallback set_window_size succeeds
        driver.set_window_size.side_effect = [Exception('resize failed'), None]
        driver.execute_script.return_value = 1  # resize counter already matches
        local.change_window_dimension_and_wait(driver, 800, 600, 1)
        self.assertEqual(driver.set_window_size.call_count, 2)


class TestResponsiveSleep(unittest.TestCase):
    @patch('percy.snapshot.RESPONSIVE_CAPTURE_SLEEP_TIME', 'not-a-number')
    def test_invalid_sleep_time_is_ignored(self):
        local._responsive_sleep()  # int() fails -> swallowed, no raise

    @patch('percy.snapshot.RESPONSIVE_CAPTURE_SLEEP_TIME', None)
    def test_unset_sleep_time_returns_early(self):
        local._responsive_sleep()


class TestCaptureResponsiveDomMinHeight(unittest.TestCase):
    def tearDown(self):
        if os.path.exists('output_file.json'):
            os.remove('output_file.json')

    @patch('percy.snapshot.PERCY_RESPONSIVE_CAPTURE_MIN_HEIGHT', True)
    @patch('percy.snapshot.change_window_dimension_and_wait', MagicMock())
    @patch('percy.snapshot._wait_for_ready', MagicMock(return_value=None))
    @patch('percy.snapshot.get_responsive_widths', MagicMock(return_value=[]))
    def test_invalid_min_height_falls_back_to_window_height(self):
        driver = MagicMock()
        driver.get_window_size.return_value = {'width': 1000, 'height': 800}
        # minHeight is non-numeric -> int() fails -> logged, window height kept
        result = local.capture_responsive_dom(
            driver, {}, {}, percy_dom_script='PERCY_DOM', minHeight='abc'
        )
        self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()

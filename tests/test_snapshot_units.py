# pylint: disable=protected-access
"""Focused unit tests for snapshot.py helper functions and their error/
fallback paths. These run as plain unittests (no Percy CLI needed) and
target branches the end-to-end suite in test_snapshot.py does not reach."""
import importlib
import os
import unittest
from unittest.mock import patch, MagicMock, Mock

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


class TestResizeSettleSecondsParsing(unittest.TestCase):
    def test_invalid_env_value_falls_back_to_default(self):
        # Module-level parse: an unparseable PERCY_RESIZE_SETTLE_SECONDS must
        # fall back to the 0.5s default instead of raising at import time.
        try:
            with patch.dict(os.environ, {'PERCY_RESIZE_SETTLE_SECONDS': 'not-a-float'}):
                importlib.reload(local)
                self.assertEqual(local.RESIZE_SETTLE_SECONDS, 0.5)
        finally:
            # Restore module globals from the real (unpatched) environment.
            importlib.reload(local)


class TestShouldSkipIframeEdgeCases(unittest.TestCase):
    @staticmethod
    def _meta(src, percy_id):
        return {
            'src': src, 'srcdoc': None, 'percyElementId': percy_id,
            'dataPercyIgnore': False, 'matchesIgnoreSelector': False, 'index': 0,
        }

    def test_unparseable_origin_is_skipped(self):
        # src passes the unsupported-scheme check but has no scheme://netloc,
        # so get_origin returns None -> "invalid URL" skip branch.
        iframe = self._meta('not-a-url', 'pid-1')
        self.assertTrue(local._should_skip_iframe(iframe, 'http://main.example.com'))

    def test_cross_origin_without_percy_element_id_is_skipped(self):
        iframe = self._meta('https://other.example.com/widget', None)
        self.assertTrue(local._should_skip_iframe(iframe, 'http://main.example.com'))


def _tree_ctx(max_depth=3):
    return {
        'max_frame_depth': max_depth,
        'ignore_selectors': [],
        'serialize_options': {},
        'percy_dom_script': 'PERCY_DOM',
    }


class TestProcessFrameTreeGuards(unittest.TestCase):
    @staticmethod
    def _meta(src, percy_id, ignore=False):
        return {
            'src': src, 'srcdoc': None, 'percyElementId': percy_id,
            'dataPercyIgnore': ignore, 'matchesIgnoreSelector': False, 'index': 0,
        }

    def test_depth_beyond_max_returns_empty(self):
        driver = MagicMock()
        result = local.process_frame_tree(
            driver, self._meta('https://x.example.com/', 'pid-x'), 4, set(),
            _tree_ctx(max_depth=3))
        self.assertEqual(result, [])
        driver.switch_to.frame.assert_not_called()

    def test_missing_iframe_element_returns_empty(self):
        driver = MagicMock()
        # querySelector by data-percy-element-id finds nothing (DOM mutated).
        driver.execute_script.side_effect = [None]
        result = local.process_frame_tree(
            driver, self._meta('https://x.example.com/', 'pid-x'), 1, set(),
            _tree_ctx())
        self.assertEqual(result, [])
        driver.switch_to.frame.assert_not_called()

    def test_document_url_read_failure_is_treated_as_unsupported(self):
        driver = MagicMock()
        driver.execute_script.side_effect = [
            Mock(name='iframe_element'),   # querySelector
            Exception('document.URL blew up'),  # post-switch URL read
        ]
        result = local.process_frame_tree(
            driver, self._meta('https://x.example.com/', 'pid-x'), 1, set(),
            _tree_ctx())
        self.assertEqual(result, [])
        driver.switch_to.parent_frame.assert_called_once()

    def test_empty_serialize_result_returns_empty(self):
        driver = MagicMock()
        driver.execute_script.side_effect = [
            Mock(name='iframe_element'),      # querySelector
            'https://x.example.com/page',     # post-switch document.URL
            None,                             # inject PercyDOM
            None,                             # serialize returned nothing
        ]
        result = local.process_frame_tree(
            driver, self._meta('https://x.example.com/page', 'pid-x'), 1, set(),
            _tree_ctx())
        self.assertEqual(result, [])

    def test_nested_enumeration_failure_keeps_parent_capture(self):
        driver = MagicMock()
        driver.execute_script.side_effect = [
            Mock(name='iframe_element'),
            'https://x.example.com/page',
            None,
            {'snapshot': {'html': '<x/>'}, 'frameUrl': 'https://x.example.com/page'},
            Exception('nested enumerate blew up'),
        ]
        result = local.process_frame_tree(
            driver, self._meta('https://x.example.com/page', 'pid-x'), 1, set(),
            _tree_ctx())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['iframeData']['percyElementId'], 'pid-x')

    def test_skipped_nested_child_is_not_recursed_into(self):
        driver = MagicMock()
        driver.execute_script.side_effect = [
            Mock(name='iframe_element'),
            'https://x.example.com/page',
            None,
            {'snapshot': {'html': '<x/>'}, 'frameUrl': 'https://x.example.com/page'},
            # nested enumeration returns one child carrying data-percy-ignore
            [self._meta('https://c.example.com/', 'pid-c', ignore=True)],
        ]
        result = local.process_frame_tree(
            driver, self._meta('https://x.example.com/page', 'pid-x'), 1, set(),
            _tree_ctx())
        self.assertEqual(len(result), 1)
        # Only one frame switch happened — the ignored child was never entered.
        self.assertEqual(driver.switch_to.frame.call_count, 1)

    def test_nested_child_capture_is_collected(self):
        driver = MagicMock()
        driver.execute_script.side_effect = [
            # depth-1 frame
            Mock(name='iframe_element_x'),
            'https://x.example.com/page',
            None,
            {'snapshot': {'html': '<x/>'}, 'frameUrl': 'https://x.example.com/page'},
            [self._meta('https://c.example.com/', 'pid-c')],
            # nested depth-2 frame
            Mock(name='iframe_element_c'),
            'https://c.example.com/',
            None,
            {'snapshot': {'html': '<c/>'}, 'frameUrl': 'https://c.example.com/'},
            [],
        ]
        result = local.process_frame_tree(
            driver, self._meta('https://x.example.com/page', 'pid-x'), 1, set(),
            _tree_ctx())
        pids = [e['iframeData']['percyElementId'] for e in result]
        self.assertEqual(pids, ['pid-x', 'pid-c'])

    def test_default_content_failure_is_swallowed_at_depth_1(self):
        driver = MagicMock()
        driver.execute_script.side_effect = [
            Mock(name='iframe_element'),
            'https://x.example.com/page',
            Exception('inject blew up'),
        ]
        driver.switch_to.parent_frame.side_effect = Exception('lost parent')
        driver.switch_to.default_content.side_effect = Exception('lost everything')
        # At depth 1 a total context loss must not raise — the walk just ends.
        result = local.process_frame_tree(
            driver, self._meta('https://x.example.com/page', 'pid-x'), 1, set(),
            _tree_ctx())
        self.assertEqual(result, [])

    def test_context_lost_links_original_error_as_cause(self):
        driver = MagicMock()
        driver.execute_script.side_effect = [
            Mock(name='iframe_element'),
            'https://c.example.com/',
            Exception('inject blew up'),
        ]
        driver.switch_to.parent_frame.side_effect = Exception('lost parent')
        # depth > 1 with a captured processing error: PercyContextLost is
        # raised and chains the original error for diagnosis.
        with self.assertRaises(local.PercyContextLost) as cm:
            local.process_frame_tree(
                driver, self._meta('https://c.example.com/', 'pid-c'), 2, set(),
                _tree_ctx())
        self.assertEqual(cm.exception.partial_capture, [])
        self.assertIsNotNone(cm.exception.__cause__)


class TestCaptureCorsIframesErrorHandling(unittest.TestCase):
    def test_unexpected_error_returns_empty_list(self):
        driver = MagicMock()
        # Enumeration "succeeds" but yields a malformed (non-dict) entry;
        # the resulting AttributeError is swallowed by the outer guard.
        driver.execute_script.side_effect = [[None]]
        result = local._capture_cors_iframes(
            driver, 'http://main.example.com/', _tree_ctx())
        self.assertEqual(result, [])


class TestExposeClosedShadowRootsEdgeCases(unittest.TestCase):
    def test_capabilities_access_failure_is_treated_as_non_chromium(self):
        cdp_calls = []

        class StubDriver:
            @property
            def capabilities(self):
                raise Exception('capabilities unavailable')

            def execute_cdp_cmd(self, cmd, params):
                cdp_calls.append((cmd, params))

        local.expose_closed_shadow_roots(StubDriver())
        self.assertEqual(cdp_calls, [])

    def test_missing_object_ids_are_skipped(self):
        driver = MagicMock()
        driver.capabilities = {'browserName': 'chrome'}

        def cdp(cmd, _params):
            if cmd == 'DOM.getDocument':
                return {'root': {
                    'backendNodeId': 1,
                    'shadowRoots': [{'backendNodeId': 2, 'shadowRootType': 'closed'}],
                    'children': [],
                }}
            if cmd == 'DOM.resolveNode':
                return {'object': {}}  # no objectId -> pair is skipped
            return {}
        driver.execute_cdp_cmd.side_effect = cdp

        local.expose_closed_shadow_roots(driver)
        cdp_cmds = [c.args[0] for c in driver.execute_cdp_cmd.call_args_list]
        self.assertNotIn('Runtime.callFunctionOn', cdp_cmds)

    def test_resolve_node_failure_is_swallowed(self):
        driver = MagicMock()
        driver.capabilities = {'browserName': 'chrome'}

        def cdp(cmd, _params):
            if cmd == 'DOM.getDocument':
                return {'root': {
                    'backendNodeId': 1,
                    'shadowRoots': [{'backendNodeId': 2, 'shadowRootType': 'closed'}],
                    'children': [],
                }}
            if cmd == 'DOM.resolveNode':
                raise Exception('node gone')
            return {}
        driver.execute_cdp_cmd.side_effect = cdp

        local.expose_closed_shadow_roots(driver)  # must not raise

    def test_get_document_failure_and_dom_disable_failure_are_swallowed(self):
        driver = MagicMock()
        driver.capabilities = {'browserName': 'chrome'}

        def cdp(cmd, _params):
            if cmd == 'DOM.enable':
                return {}
            raise Exception(f'{cmd} failed')  # DOM.getDocument AND DOM.disable
        driver.execute_cdp_cmd.side_effect = cdp

        local.expose_closed_shadow_roots(driver)  # must not raise


class TestGetOriginHelpers(unittest.TestCase):
    def test_get_origin_returns_none_without_scheme_or_netloc(self):
        self.assertIsNone(local.get_origin('not-a-url'))
        self.assertIsNone(local.get_origin('http://'))

    def test_get_origin_returns_none_when_parsing_raises(self):
        # urlparse raises AttributeError on non-str input
        self.assertIsNone(local.get_origin(123))

    def test_get_origin_compat_shim_returns_empty_string(self):
        self.assertEqual(local._get_origin('https://a.example.com/x'),
                         'https://a.example.com')
        self.assertEqual(local._get_origin('not-a-url'), '')


class TestGetSerializedDomPageUrlFailure(unittest.TestCase):
    def test_current_url_failure_degrades_to_no_page_url(self):
        class StubDriver:
            def __init__(self):
                self.calls = 0

            def execute_script(self, _script, *args):
                self.calls += 1
                if self.calls == 1:
                    return {'html': '<html/>'}  # main serialize
                return []  # iframe enumeration

            @property
            def current_url(self):
                raise Exception('no url available')

        dom = local.get_serialized_dom(
            StubDriver(), [{'name': 'k', 'value': 'v'}],
            percy_dom_script='PERCY_DOM')
        self.assertNotIn('corsIframes', dom)
        self.assertEqual(dom['cookies'], [{'name': 'k', 'value': 'v'}])


class TestResponsiveDebugDump(unittest.TestCase):
    def tearDown(self):
        if os.path.exists('output_file.json'):
            os.remove('output_file.json')

    @staticmethod
    def _driver():
        driver = MagicMock()
        driver.get_window_size.return_value = {'width': 1000, 'height': 800}
        return driver

    @patch('percy.snapshot.PERCY_DEBUG', True)
    @patch('percy.snapshot.change_window_dimension_and_wait', MagicMock())
    @patch('percy.snapshot._wait_for_ready', MagicMock(return_value=None))
    @patch('percy.snapshot.get_responsive_widths', MagicMock(return_value=[]))
    def test_debug_dump_is_written_when_percy_debug(self):
        result = local.capture_responsive_dom(
            self._driver(), {}, {}, percy_dom_script='PERCY_DOM')
        self.assertEqual(result, [])
        self.assertTrue(os.path.exists('output_file.json'))

    @patch('percy.snapshot.PERCY_DEBUG', True)
    @patch('percy.snapshot.change_window_dimension_and_wait', MagicMock())
    @patch('percy.snapshot._wait_for_ready', MagicMock(return_value=None))
    @patch('percy.snapshot.get_responsive_widths', MagicMock(return_value=[]))
    @patch('percy.snapshot.json.dump', side_effect=TypeError('not serializable'))
    def test_debug_dump_failure_is_logged_not_raised(self, _mock_dump):
        result = local.capture_responsive_dom(
            self._driver(), {}, {}, percy_dom_script='PERCY_DOM')
        self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()

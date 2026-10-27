# pylint: disable=protected-access,too-many-lines
"""Focused unit tests for snapshot.py helper functions and their error/
fallback paths. These run as plain unittests (no Percy CLI needed) and
target branches the end-to-end suite in test_snapshot.py does not reach."""
import importlib
import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, Mock

import percy.snapshot as local


class TestLog(unittest.TestCase):
    @patch('percy.snapshot.PERCY_DEBUG', True)
    @patch('percy.snapshot.requests.post', side_effect=Exception('network down'))
    def test_log_swallows_post_failure(self, _mock_post):
        # Failing to ship the log to the CLI must never raise to the caller.
        local.log('hello', 'info')  # should not raise


class TestCliApiAddress(unittest.TestCase):
    def _resolve(self, env):
        cleared = {k: v for k, v in os.environ.items()
                   if k not in ('PERCY_CLI_API', 'PERCY_SERVER_ADDRESS')}
        try:
            with patch.dict(os.environ, {**cleared, **env}, clear=True):
                importlib.reload(local)
                return local.PERCY_CLI_API
        finally:
            importlib.reload(local)

    def test_prefers_percy_cli_api(self):
        self.assertEqual(self._resolve({
            'PERCY_CLI_API': 'http://localhost:1111',
            'PERCY_SERVER_ADDRESS': 'http://localhost:2222',
        }), 'http://localhost:1111')

    def test_falls_back_to_percy_server_address(self):
        self.assertEqual(self._resolve({'PERCY_SERVER_ADDRESS': 'http://localhost:2222'}),
                         'http://localhost:2222')

    def test_empty_percy_cli_api_falls_back_to_percy_server_address(self):
        self.assertEqual(self._resolve({
            'PERCY_CLI_API': '',
            'PERCY_SERVER_ADDRESS': 'http://localhost:2222',
        }), 'http://localhost:2222')

    def test_defaults_to_5338(self):
        self.assertEqual(self._resolve({}), 'http://localhost:5338')


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


class TestConfigMergeIntoSerialize(unittest.TestCase):
    """PER-8053: the global .percy.yml `snapshot` config must be deep-merged
    into the per-call serialize options (per-call wins at the leaves) BEFORE
    PercyDOM.serialize. Regression guard: a CORS-iframe refactor once dropped
    this merge so config-only keys never reached serialize."""

    def _capture_serialize(self, percy_config, **kwargs):
        """Run get_serialized_dom against a stub driver and return the exact
        options object handed to PercyDOM.serialize."""
        captured = {}
        driver = MagicMock()

        def _exec(script, *_args):
            if 'PercyDOM.serialize' in script:
                start = script.index('serialize(') + len('serialize(')
                end = script.rindex(')')
                captured['opts'] = json.loads(script[start:end])
                return {'html': '<html></html>'}
            return None

        driver.execute_script.side_effect = _exec
        local.get_serialized_dom(driver, {'c': 1}, percy_config=percy_config, **kwargs)
        return captured['opts']

    def test_config_only_key_reaches_serialize(self):
        opts = self._capture_serialize({'snapshot': {'enableJavaScript': True}})
        self.assertTrue(opts.get('enableJavaScript'))

    def test_per_call_option_wins_over_config(self):
        opts = self._capture_serialize(
            {'snapshot': {'percyCSS': 'from-config'}}, percyCSS='from-call')
        self.assertEqual(opts['percyCSS'], 'from-call')

    def test_deep_merge_keeps_sibling_and_per_call_nested_wins(self):
        opts = self._capture_serialize(
            {'snapshot': {'discovery': {'networkIdleTimeout': 50, 'disableCache': False}}},
            discovery={'disableCache': True})
        self.assertEqual(opts['discovery']['networkIdleTimeout'], 50)  # config sibling kept
        self.assertTrue(opts['discovery']['disableCache'])             # per-call nested wins

    def test_no_config_leaves_per_call_untouched(self):
        opts = self._capture_serialize(None, percyCSS='only-call')
        self.assertEqual(opts, {'percyCSS': 'only-call'})

    # --- helper defensive branches (called directly: these degrade to the raw
    #     per-call kwargs rather than raising, for any malformed config shape) ---
    def test_merge_helper_non_dict_config_returns_kwargs(self):
        self.assertEqual(
            local._merge_config_into_serialize_options('nope', {'a': 1}), {'a': 1})

    def test_merge_helper_non_dict_snapshot_returns_kwargs(self):
        self.assertEqual(
            local._merge_config_into_serialize_options({'snapshot': 'x'}, {'a': 1}), {'a': 1})

    def test_merge_helper_empty_snapshot_returns_kwargs(self):
        self.assertEqual(
            local._merge_config_into_serialize_options({'snapshot': {}}, {'a': 1}), {'a': 1})


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
        driver.execute_script.return_value = 800  # innerWidth already exact
        local.change_window_dimension_and_wait(driver, 800, 600, 1)
        self.assertEqual(driver.set_window_size.call_count, 2)

    @patch('percy.snapshot.RESIZE_SETTLE_SECONDS', 0)
    def test_legacy_mode_keeps_exact_chrome_match(self):
        driver = MagicMock()
        driver.capabilities = {'browserName': 'Chrome'}
        local.change_window_dimension_and_wait(driver, 800, 600, 1, exact=False)
        driver.execute_cdp_cmd.assert_not_called()
        driver.set_window_size.assert_called_once_with(800, 600)

    @patch('percy.snapshot.RESIZE_SETTLE_SECONDS', 0)
    def test_stops_using_cdp_after_remote_rejects_it(self):
        driver = MagicMock()
        driver.capabilities = {'browserName': 'chrome'}
        driver.execute_cdp_cmd.side_effect = AssertionError(
            'Unrecognised command executeCdpCommand')
        local.change_window_dimension_and_wait(driver, 375, 600, 1)
        local.change_window_dimension_and_wait(driver, 1280, 600, 2)
        self.assertEqual(driver.execute_cdp_cmd.call_count, 1)
        self.assertEqual(driver.set_window_size.call_args_list,
                         [((375, 600),), ((1280, 600),)])

    @patch('percy.snapshot.RESIZE_SETTLE_SECONDS', 0)
    def test_keeps_using_cdp_after_other_failures(self):
        driver = MagicMock()
        driver.capabilities = {'browserName': 'chrome'}
        driver.execute_cdp_cmd.side_effect = [Exception('target closed'), {}]
        local.change_window_dimension_and_wait(driver, 375, 600, 1)
        local.change_window_dimension_and_wait(driver, 1280, 600, 2)
        self.assertEqual(driver.execute_cdp_cmd.call_count, 2)
        driver.set_window_size.assert_called_once_with(375, 600)


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

    def test_browser_name_is_case_insensitive(self):
        driver = MagicMock()
        driver.capabilities = {'browserName': 'Chrome'}
        driver.execute_cdp_cmd.return_value = {}
        local.expose_closed_shadow_roots(driver)
        driver.execute_cdp_cmd.assert_any_call('DOM.enable', {})

    def test_skips_cdp_after_remote_rejects_it(self):
        driver = MagicMock()
        driver.capabilities = {'browserName': 'chrome'}
        driver.execute_cdp_cmd.side_effect = AssertionError(
            'Unrecognised command executeCdpCommand')
        local.expose_closed_shadow_roots(driver)
        local.expose_closed_shadow_roots(driver)
        driver.execute_cdp_cmd.assert_called_once_with('DOM.enable', {})

    def test_rejection_is_shared_with_responsive_resize(self):
        driver = MagicMock()
        driver.capabilities = {'browserName': 'chrome'}
        driver.execute_cdp_cmd.side_effect = Exception(
            'Message: unknown command: executeCdpCommand')
        local.expose_closed_shadow_roots(driver)
        with patch('percy.snapshot.RESIZE_SETTLE_SECONDS', 0):
            local.change_window_dimension_and_wait(driver, 375, 600, 1)
        driver.execute_cdp_cmd.assert_called_once_with('DOM.enable', {})
        driver.set_window_size.assert_called_once_with(375, 600)

    def test_unhashable_driver_is_never_remembered(self):
        class UnhashableDriver:  # pylint: disable=too-few-public-methods
            __hash__ = None
            capabilities = {'browserName': 'chrome'}
            calls = 0

            def execute_cdp_cmd(self, _cmd, _params):
                UnhashableDriver.calls += 1
                raise AssertionError('Unrecognised command executeCdpCommand')

        driver = UnhashableDriver()
        local.expose_closed_shadow_roots(driver)
        local.expose_closed_shadow_roots(driver)
        self.assertEqual(UnhashableDriver.calls, 2)

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

            def execute_script(self, _script, *_args):
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


def _driver(browser, **caps):
    driver = MagicMock()
    driver.capabilities = {'browserName': browser, **caps}
    return driver


class TestExactViewport(unittest.TestCase):
    def setUp(self):
        patcher = patch('percy.snapshot.RESIZE_SETTLE_SECONDS', 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_chromium_family_uses_cdp(self):
        for name in ('chrome', 'Chrome', 'chromium', 'MicrosoftEdge', 'msedge',
                     'chrome-headless-shell'):
            driver = _driver(name)
            driver.execute_script.return_value = 375
            mechanism = local.change_window_dimension_and_wait(driver, 375, 600, 1)
            self.assertEqual(mechanism, 'cdp', name)
            driver.execute_cdp_cmd.assert_called_once_with(
                'Emulation.setDeviceMetricsOverride',
                {'height': 600, 'width': 375, 'deviceScaleFactor': 1, 'mobile': False})
            driver.set_window_size.assert_not_called()

    def test_firefox_and_safari_never_use_cdp(self):
        for name in ('firefox', 'Safari'):
            driver = _driver(name)
            driver.execute_script.return_value = 1280
            mechanism = local.change_window_dimension_and_wait(driver, 1280, 600, 1)
            self.assertEqual(mechanism, 'window', name)
            driver.execute_cdp_cmd.assert_not_called()

    def test_cdp_skipped_when_executor_lacks_the_command(self):
        # Survives `python -O`, where Selenium's "Unrecognised command" assert
        # (and so its message) disappears.
        driver = _driver('MicrosoftEdge')
        driver.command_executor._commands = {'newSession': ('POST', '/session')}
        driver.command_executor.extra_commands = {}
        driver.execute_script.return_value = 1280
        self.assertEqual(local.change_window_dimension_and_wait(driver, 1280, 600, 1), 'window')
        driver.execute_cdp_cmd.assert_not_called()

    def test_cdp_used_when_executor_registers_the_command(self):
        driver = _driver('chrome')
        driver.command_executor._commands = {'executeCdpCommand': ('POST', '/x')}
        driver.execute_script.return_value = 375
        self.assertEqual(local.change_window_dimension_and_wait(driver, 375, 600, 1), 'cdp')

    def test_cdp_method_error_does_not_disable_cdp(self):
        driver = _driver('chrome')
        driver.execute_cdp_cmd.side_effect = [
            Exception("'Emulation.x' wasn't found: unknown method"), {}]
        driver.execute_script.return_value = 375
        local.change_window_dimension_and_wait(driver, 375, 600, 1)
        local.change_window_dimension_and_wait(driver, 375, 600, 2)
        self.assertEqual(driver.execute_cdp_cmd.call_count, 2)

    @patch('percy.snapshot._bidi_command')
    @patch('percy.snapshot._bidi_available', return_value=True)
    def test_bidi_set_viewport_when_session_has_websocket(self, _available, bidi):
        driver = _driver('firefox', webSocketUrl='ws://localhost/session')
        driver.current_window_handle = 'ctx-1'
        driver.execute_script.return_value = 375
        self.assertEqual(local.change_window_dimension_and_wait(driver, 375, 600, 1), 'bidi')
        bidi.assert_called_once_with(driver, 'browsingContext.setViewport', {
            'context': 'ctx-1', 'viewport': {'width': 375, 'height': 600},
            'devicePixelRatio': 1})
        driver.set_window_size.assert_not_called()

    @patch('percy.snapshot._bidi_command', side_effect=Exception('no bidi'))
    @patch('percy.snapshot._bidi_available', return_value=True)
    def test_bidi_failure_falls_back_to_window(self, _available, _bidi):
        driver = _driver('firefox')
        driver.execute_script.return_value = 1280
        self.assertEqual(local.change_window_dimension_and_wait(driver, 1280, 600, 1), 'window')
        driver.set_window_size.assert_called_once_with(1280, 600)

    def test_window_path_corrects_border_once(self):
        driver = _driver('firefox')
        driver.execute_script.side_effect = [1272, 1280]
        local.change_window_dimension_and_wait(driver, 1280, 600, 1)
        self.assertEqual(driver.set_window_size.call_args_list,
                         [((1280, 600),), ((1288, 600),)])

    @patch('percy.snapshot.log')
    def test_window_path_warns_once_per_session_when_still_wrong(self, mock_log):
        driver = _driver('firefox')
        driver.session_id = 'warn-once-session'
        driver.execute_script.return_value = 500
        local.change_window_dimension_and_wait(driver, 375, 600, 1)
        local.change_window_dimension_and_wait(driver, 390, 600, 2)
        warns = [c for c in mock_log.call_args_list if c.args[1:] == ('warn',)]
        self.assertEqual(len(warns), 1)
        self.assertIn('firefox', warns[0].args[0])
        self.assertIn('webSocketUrl: true', warns[0].args[0])

    def test_no_wait_skips_verification(self):
        driver = _driver('firefox')
        local.change_window_dimension_and_wait(driver, 375, 600, 1, wait_for_resize=False)
        driver.execute_script.assert_not_called()


class TestExactResponsiveWidthPrecedence(unittest.TestCase):
    def test_defaults_to_true(self):
        self.assertTrue(local._exact_responsive_width({}, {}))
        self.assertTrue(local._exact_responsive_width(None, {}))

    def test_config_value(self):
        config = {'snapshot': {'exactResponsiveWidth': False}}
        self.assertFalse(local._exact_responsive_width(config, {}))

    def test_per_snapshot_wins(self):
        config = {'snapshot': {'exactResponsiveWidth': False}}
        self.assertTrue(local._exact_responsive_width(config, {'exactResponsiveWidth': True}))
        self.assertFalse(local._exact_responsive_width({}, {'exact_responsive_width': False}))


class TestRestoreViewport(unittest.TestCase):
    def test_cdp_is_cleared_not_reapplied(self):
        driver = _driver('chrome')
        local._restore_viewport(driver, 1000, 800, {'cdp'})
        driver.execute_cdp_cmd.assert_called_once_with('Emulation.clearDeviceMetricsOverride', {})
        driver.set_window_size.assert_not_called()

    @patch('percy.snapshot._bidi_command')
    def test_bidi_viewport_is_reset(self, bidi):
        driver = _driver('firefox')
        driver.current_window_handle = 'ctx'
        local._restore_viewport(driver, 1000, 800, {'bidi'})
        bidi.assert_called_once_with(driver, 'browsingContext.setViewport', {
            'context': 'ctx', 'viewport': None, 'devicePixelRatio': None})

    def test_window_is_resized_back(self):
        driver = _driver('firefox')
        local._restore_viewport(driver, 1000, 800, {'window'})
        driver.set_window_size.assert_called_once_with(1000, 800)

    def test_failures_are_swallowed(self):
        driver = _driver('chrome')
        driver.execute_cdp_cmd.side_effect = Exception('gone')
        driver.set_window_size.side_effect = Exception('gone')
        local._restore_viewport(driver, 1000, 800, {'cdp', 'window'})

    @patch('percy.snapshot._bidi_command', side_effect=Exception('gone'))
    def test_bidi_reset_failure_is_swallowed(self, _bidi):
        local._restore_viewport(_driver('firefox'), 1000, 800, {'bidi'})

    @patch('percy.snapshot.RESIZE_SETTLE_SECONDS', 0)
    @patch('percy.snapshot.get_serialized_dom', MagicMock(return_value={}))
    @patch('percy.snapshot._wait_for_ready', MagicMock(return_value=None))
    @patch('percy.snapshot.get_responsive_widths',
           MagicMock(return_value=[{'width': 375}, {'width': 1280}]))
    def test_legacy_capture_clears_cdp_on_restore(self):
        driver = _driver('chrome')
        driver.get_window_size.return_value = {'width': 1280, 'height': 800}
        local.capture_responsive_dom(
            driver, [], {'snapshot': {'exactResponsiveWidth': False}}, percy_dom_script='x')
        cmds = [c.args[0] for c in driver.execute_cdp_cmd.call_args_list]
        self.assertEqual(cmds.count('Emulation.setDeviceMetricsOverride'), 2)
        self.assertEqual(cmds[-1], 'Emulation.clearDeviceMetricsOverride')


def _fake_bidi_modules():
    """Stand-ins for the BiDi binding modules (absent before Selenium 4.32), so
    these tests behave the same on every Selenium the CI matrix installs."""
    def command_builder(method, params=None):
        result = yield {'method': method, 'params': params or {}}
        return result

    class BrowsingContext:  # pylint: disable=too-few-public-methods
        def set_viewport(self):
            pass

    common = MagicMock(command_builder=command_builder)
    browsing_context = MagicMock(BrowsingContext=BrowsingContext)
    return patch.dict(sys.modules, {
        'selenium.webdriver.common.bidi.common': common,
        'selenium.webdriver.common.bidi.browsing_context': browsing_context,
    })


class TestBidiPlumbing(unittest.TestCase):
    def test_unavailable_without_websocket_url(self):
        with _fake_bidi_modules():
            self.assertFalse(local._bidi_available(_driver('firefox')))
            self.assertFalse(local._bidi_available(_driver('firefox', webSocketUrl=True)))

    def test_available_with_websocket_url(self):
        with _fake_bidi_modules():
            self.assertTrue(local._bidi_available(_driver('firefox', webSocketUrl='ws://x')))

    def test_unavailable_when_bindings_are_missing(self):
        with patch.dict(sys.modules, {'selenium.webdriver.common.bidi.browsing_context': None}):
            self.assertFalse(local._bidi_available(_driver('firefox', webSocketUrl='ws://x')))

    def test_capabilities_failure_is_unavailable(self):
        class Broken:  # pylint: disable=too-few-public-methods
            @property
            def capabilities(self):
                raise Exception('boom')
        self.assertFalse(local._bidi_available(Broken()))

    def test_command_starts_connection_once(self):
        driver = MagicMock()
        driver._websocket_connection = None

        def start():
            driver._websocket_connection = MagicMock()
            driver._websocket_connection.execute.return_value = {'ok': True}
        driver._start_bidi.side_effect = start
        with _fake_bidi_modules():
            self.assertEqual(local._bidi_command(driver, 'm.x', {'a': 1}), {'ok': True})
            local._bidi_command(driver, 'm.x', {})
        driver._start_bidi.assert_called_once_with()
        command = driver._websocket_connection.execute.call_args_list[0].args[0]
        self.assertEqual(next(command), {'method': 'm.x', 'params': {'a': 1}})


class TestIsLocalDriver(unittest.TestCase):
    def _with_url(self, url, attr='_url'):
        driver = MagicMock()
        driver.service = None
        if attr == '_url':
            driver.command_executor = MagicMock(spec=['_url'])
            driver.command_executor._url = url
        else:
            driver.command_executor = MagicMock(spec=['_client_config'])
            driver.command_executor._client_config.remote_server_addr = url
        return driver

    def test_localhost_urls_are_local(self):
        for url in ('http://localhost:4444', 'http://127.0.0.1:9515', 'http://[::1]:4444/wd/hub'):
            self.assertTrue(local._is_local_driver(self._with_url(url)), url)
        self.assertTrue(local._is_local_driver(
            self._with_url('http://localhost:1', attr='client_config')))

    def test_remote_urls_are_not_local(self):
        self.assertFalse(local._is_local_driver(
            self._with_url('https://hub-cloud.browserstack.com/wd/hub')))
        self.assertFalse(local._is_local_driver(MagicMock()))

    def test_service_driver_is_local(self):
        from selenium.webdriver.common.service import Service  # pylint: disable=import-outside-toplevel
        driver = MagicMock()
        driver.service = MagicMock(spec=Service)
        self.assertTrue(local._is_local_driver(driver))


class _Host:  # pylint: disable=too-few-public-methods
    def __init__(self, root=None, error=None):
        self._root, self._error = root, error

    @property
    def shadow_root(self):
        if self._error:
            raise self._error
        return self._root


class _Root:  # pylint: disable=too-few-public-methods
    def __init__(self, ident):
        self.id = ident


class TestClosedShadowStrategies(unittest.TestCase):
    def test_disable_shadow_dom_skips_everything(self):
        for options, config in (({'disableShadowDOM': True}, None),
                                ({'disable_shadow_dom': True}, None),
                                ({}, {'snapshot': {'disableShadowDOM': True}})):
            driver = _driver('chrome')
            local.expose_closed_shadow_roots(driver, options, config)
            driver.execute_cdp_cmd.assert_not_called()
            driver.execute_script.assert_not_called()

    def test_per_snapshot_false_overrides_config(self):
        driver = _driver('chrome')
        driver.execute_cdp_cmd.return_value = {}
        local.expose_closed_shadow_roots(
            driver, {'disableShadowDOM': False}, {'snapshot': {'disableShadowDOM': True}})
        driver.execute_cdp_cmd.assert_any_call('DOM.enable', {})

    def test_edge_uses_cdp(self):
        driver = _driver('MicrosoftEdge')
        driver.execute_cdp_cmd.return_value = {}
        local.expose_closed_shadow_roots(driver)
        driver.execute_cdp_cmd.assert_any_call('DOM.enable', {})
        driver.execute_script.assert_not_called()

    @patch('percy.snapshot._is_local_driver', return_value=True)
    def test_cdp_failure_falls_back_to_classic(self, _local):
        driver = _driver('chrome')
        driver.execute_cdp_cmd.side_effect = Exception('target crashed')
        driver.execute_script.return_value = []
        local.expose_closed_shadow_roots(driver)
        driver.execute_script.assert_called_once_with(local._CLOSED_SHADOW_CANDIDATES_JS)

    @patch('percy.snapshot._is_local_driver', return_value=True)
    def test_classic_exposes_nested_roots_and_skips_null(self, _local):
        inner_root = _Root('inner')
        outer_root = _Root('outer')
        inner = _Host(inner_root)
        outer = _Host(outer_root)
        safari_like = _Host(None)
        no_root = _Host(error=Exception('no such shadow root'))
        driver = _driver('firefox')

        def script(js, *args):
            if js == local._CLOSED_SHADOW_CANDIDATES_JS:
                return {(): [outer, safari_like, no_root], (outer_root,): [inner],
                        (inner_root,): []}[args]
            return None
        driver.execute_script.side_effect = script
        self.assertEqual(local._expose_closed_shadow_roots_classic(driver), 2)
        sets = [c.args[1:] for c in driver.execute_script.call_args_list
                if c.args[0] == local._SET_CLOSED_SHADOW_JS]
        self.assertEqual(sets, [(outer, outer_root), (inner, inner_root)])

    @patch('percy.snapshot.CLOSED_SHADOW_MAX_HOSTS', 2)
    @patch('percy.snapshot.log')
    def test_classic_stops_at_host_cap(self, mock_log):
        hosts = [_Host(error=Exception('none')) for _ in range(5)]
        driver = _driver('firefox')
        driver.execute_script.return_value = hosts
        self.assertEqual(local._expose_closed_shadow_roots_classic(driver), 0)
        self.assertTrue(any('PERCY_CLOSED_SHADOW_MAX_HOSTS' in c.args[0]
                            for c in mock_log.call_args_list))

    def test_classic_ignores_non_list_result(self):
        driver = _driver('firefox')
        driver.execute_script.return_value = None
        self.assertEqual(local._expose_closed_shadow_roots_classic(driver), 0)

    @patch('percy.snapshot._is_local_driver', return_value=False)
    @patch('percy.snapshot.log')
    def test_remote_session_skips_classic_and_logs_once(self, mock_log, _local):
        driver = _driver('firefox')
        driver.session_id = 'remote-skip-session'
        local.expose_closed_shadow_roots(driver)
        local.expose_closed_shadow_roots(driver)
        driver.execute_script.assert_not_called()
        skipped = [c for c in mock_log.call_args_list
                   if 'skipped on remote session' in c.args[0]]
        self.assertEqual(len(skipped), 1)

    @patch('percy.snapshot.PERCY_CLOSED_SHADOW_CLASSIC', True)
    @patch('percy.snapshot._is_local_driver', return_value=False)
    def test_remote_session_classic_opt_in(self, _local):
        driver = _driver('firefox')
        driver.execute_script.return_value = []
        local.expose_closed_shadow_roots(driver)
        driver.execute_script.assert_called_once_with(local._CLOSED_SHADOW_CANDIDATES_JS)

    @patch('percy.snapshot._is_local_driver', return_value=True)
    @patch('percy.snapshot.log')
    def test_classic_failure_warns_and_continues(self, mock_log, _local):
        driver = _driver('firefox')
        driver.execute_script.side_effect = Exception('stale')
        local.expose_closed_shadow_roots(driver)
        self.assertTrue(any(c.args[1:] == ('warn',) for c in mock_log.call_args_list))

    @patch('percy.snapshot.CDP_SUPPORT_SELENIUM', False)
    @patch('selenium.webdriver.remote.webelement.WebElement', new=type('W', (), {}))
    def test_selenium_3_is_a_noop(self):
        driver = _driver('chrome')
        local.expose_closed_shadow_roots(driver)
        driver.execute_cdp_cmd.assert_not_called()
        driver.execute_script.assert_not_called()

    @patch('percy.snapshot._bidi_available', return_value=True)
    @patch('percy.snapshot._bidi_command')
    def test_bidi_exposes_nested_closed_roots(self, bidi, _available):
        def closed(host, root):
            return {'type': 'node', 'sharedId': host, 'value': {
                'shadowRoot': {'sharedId': root, 'value': {'mode': 'closed'}}}}
        open_node = {'type': 'node', 'sharedId': 'o', 'value': {
            'shadowRoot': {'sharedId': 'or', 'value': {'mode': 'open'}}}}
        plain = {'type': 'node', 'sharedId': 'p', 'value': {}}
        responses = iter([
            {'result': {'type': 'array', 'value': [closed('h1', 'r1'), open_node, plain]}},
            {'result': {'type': 'array', 'value': [closed('h2', 'r2')]}},
            {'result': {'type': 'array', 'value': []}},
            {},
        ])
        bidi.side_effect = lambda *_a: next(responses)
        driver = _driver('firefox', webSocketUrl='ws://x')
        driver.current_window_handle = 'ctx'
        local.expose_closed_shadow_roots(driver)
        calls = [c.args[2] for c in bidi.call_args_list]
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0]['arguments'], [])
        self.assertEqual(calls[0]['serializationOptions'],
                         {'maxDomDepth': 0, 'includeShadowTree': 'all'})
        self.assertEqual(calls[1]['arguments'], [{'sharedId': 'r1'}])
        self.assertEqual(calls[3]['arguments'], [{'sharedId': i} for i in ('h1', 'r1', 'h2', 'r2')])
        driver.execute_cdp_cmd.assert_not_called()
        driver.execute_script.assert_not_called()

    @patch('percy.snapshot._bidi_available', return_value=True)
    @patch('percy.snapshot._bidi_command',
           return_value={'result': {'type': 'array', 'value': []}})
    def test_bidi_without_closed_roots_sets_nothing(self, bidi, _available):
        driver = _driver('firefox', webSocketUrl='ws://x')
        local.expose_closed_shadow_roots(driver)
        bidi.assert_called_once()

    @patch('percy.snapshot._is_local_driver', return_value=True)
    @patch('percy.snapshot._bidi_available', return_value=True)
    @patch('percy.snapshot._bidi_command', side_effect=Exception('ws closed'))
    def test_bidi_failure_falls_back_to_classic(self, _bidi, _available, _local):
        driver = _driver('firefox', webSocketUrl='ws://x')
        driver.execute_script.return_value = []
        local.expose_closed_shadow_roots(driver)
        driver.execute_script.assert_called_once_with(local._CLOSED_SHADOW_CANDIDATES_JS)


class TestParityEdgeCases(unittest.TestCase):
    def test_invalid_max_hosts_env_falls_back_to_default(self):
        try:
            with patch.dict(os.environ, {'PERCY_CLOSED_SHADOW_MAX_HOSTS': 'many'}):
                importlib.reload(local)
                self.assertEqual(local.CLOSED_SHADOW_MAX_HOSTS, 200)
        finally:
            importlib.reload(local)

    def test_generic_unknown_command_is_not_remembered(self):
        driver = _driver('chrome')
        local._remember_cdp_rejection(driver, Exception('unknown command: session/x/print'))
        self.assertFalse(local._cdp_rejected(driver))
        local._remember_cdp_rejection(
            driver, Exception('unknown command: POST /session/x/goog/cdp/execute'))
        self.assertTrue(local._cdp_rejected(driver))

    def test_malformed_executor_url_is_not_local(self):
        driver = MagicMock()
        driver.service = None
        driver.command_executor = MagicMock(spec=['_url'])
        driver.command_executor._url = 'http://[::1'
        self.assertFalse(local._is_local_driver(driver))

    def test_non_dict_snapshot_config_is_ignored(self):
        self.assertFalse(local._shadow_dom_disabled(None, {'snapshot': 'yes'}))

    def test_inner_width_read_failure_is_none(self):
        driver = MagicMock()
        driver.execute_script.side_effect = Exception('gone')
        self.assertIsNone(local._read_inner_width(driver))

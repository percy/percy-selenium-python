"""Tests for Robot Framework library integration."""
import importlib
import sys
import unittest
from unittest.mock import MagicMock, patch

from percy.robot_library import (
    PercyLibrary,
    _parse_bool,
    _parse_csv,
    _parse_json,
    _parse_padding,
    _parse_widths,
)


class TestImports(unittest.TestCase):
    def test_import_robot_library(self):
        """robot_library should import without error."""
        from percy import robot_library  # pylint: disable=import-outside-toplevel
        self.assertTrue(hasattr(robot_library, 'PercyLibrary'))

    def test_percy_library_exists(self):
        """PercyLibrary should be importable from percy package."""
        from percy import PercyLibrary as PL  # pylint: disable=import-outside-toplevel
        self.assertIsNotNone(PL)


class TestParseHelpers(unittest.TestCase):
    def test_parse_bool_none(self):
        self.assertIsNone(_parse_bool(None))

    def test_parse_bool_true(self):
        self.assertTrue(_parse_bool("True"))
        self.assertTrue(_parse_bool("true"))
        self.assertTrue(_parse_bool("1"))
        self.assertTrue(_parse_bool("yes"))

    def test_parse_bool_false(self):
        self.assertFalse(_parse_bool("False"))
        self.assertFalse(_parse_bool("false"))
        self.assertFalse(_parse_bool("0"))
        self.assertFalse(_parse_bool("no"))

    def test_parse_widths_string(self):
        self.assertEqual(_parse_widths("375,768,1280"), [375, 768, 1280])

    def test_parse_widths_list(self):
        self.assertEqual(_parse_widths([375, 1280]), [375, 1280])

    def test_parse_widths_none(self):
        self.assertIsNone(_parse_widths(None))

    def test_parse_csv_string(self):
        self.assertEqual(_parse_csv("regression, homepage, v2"), ["regression", "homepage", "v2"])

    def test_parse_csv_none(self):
        self.assertIsNone(_parse_csv(None))

    def test_parse_json_string(self):
        result = _parse_json('{"fullPage": true}')
        self.assertEqual(result, {"fullPage": True})

    def test_parse_json_dict(self):
        result = _parse_json({"key": "value"})
        self.assertEqual(result, {"key": "value"})

    def test_parse_json_none(self):
        self.assertIsNone(_parse_json(None))

    def test_parse_widths_unsupported_type(self):
        self.assertIsNone(_parse_widths(123))

    def test_parse_csv_list(self):
        self.assertEqual(_parse_csv(["a", "b"]), ["a", "b"])

    def test_parse_csv_unsupported_type(self):
        self.assertIsNone(_parse_csv(123))

    def test_parse_json_unsupported_type(self):
        self.assertIsNone(_parse_json(123))


class TestParsePadding(unittest.TestCase):
    def test_parse_padding_none(self):
        self.assertIsNone(_parse_padding(None))

    def test_parse_padding_json_object_string(self):
        self.assertEqual(
            _parse_padding('{"top": 1, "bottom": 2, "left": 3, "right": 4}'),
            {"top": 1, "bottom": 2, "left": 3, "right": 4},
        )

    def test_parse_padding_numeric_string(self):
        self.assertEqual(_parse_padding("10"), {"top": 10, "bottom": 10, "left": 10, "right": 10})

    def test_parse_padding_invalid_string(self):
        self.assertIsNone(_parse_padding("not-json-not-int"))

    def test_parse_padding_int(self):
        self.assertEqual(_parse_padding(5), {"top": 5, "bottom": 5, "left": 5, "right": 5})

    def test_parse_padding_dict(self):
        self.assertEqual(_parse_padding({"top": 1}), {"top": 1})

    def test_parse_padding_unsupported_type(self):
        self.assertIsNone(_parse_padding(["unexpected"]))


class TestPercyLibraryKeywords(unittest.TestCase):
    @patch("percy.robot_library.percy_snapshot")
    @patch("percy.robot_library.BuiltIn")
    def test_percy_snapshot_keyword_basic(self, mock_builtin, mock_snapshot):
        mock_driver = MagicMock()
        mock_builtin.return_value.get_library_instance.return_value.driver = mock_driver

        lib = PercyLibrary()
        lib.percy_snapshot_keyword("Homepage")

        mock_snapshot.assert_called_once()
        args = mock_snapshot.call_args
        self.assertIs(args[0][0], mock_driver)
        self.assertEqual(args[0][1], "Homepage")

    @patch("percy.robot_library.percy_snapshot")
    @patch("percy.robot_library.BuiltIn")
    def test_percy_snapshot_keyword_with_options(self, mock_builtin, mock_snapshot):
        mock_driver = MagicMock()
        mock_builtin.return_value.get_library_instance.return_value.driver = mock_driver

        lib = PercyLibrary()
        lib.percy_snapshot_keyword(
            "Test",
            widths="375,1280",
            min_height="1024",
            percy_css="h1 { color: red; }",
            enable_javascript="True",
            labels="regression,v2",
        )

        mock_snapshot.assert_called_once()
        call_kwargs = mock_snapshot.call_args[1]
        self.assertEqual(call_kwargs["widths"], [375, 1280])
        self.assertEqual(call_kwargs["min_height"], 1024)
        self.assertEqual(call_kwargs["percy_css"], "h1 { color: red; }")
        self.assertIs(call_kwargs["enable_javascript"], True)
        self.assertEqual(call_kwargs["labels"], ["regression", "v2"])

    @patch("percy.robot_library.is_percy_enabled")
    def test_percy_is_running_keyword(self, mock_enabled):
        mock_enabled.return_value = {"session_type": "web", "config": {}}
        lib = PercyLibrary()
        self.assertIs(lib.percy_is_running_keyword(), True)

        mock_enabled.return_value = False
        self.assertIs(lib.percy_is_running_keyword(), False)

    @patch("percy.robot_library.create_region")
    def test_create_percy_region_keyword(self, mock_create):
        mock_create.return_value = {"algorithm": "ignore", "elementSelector": {"elementCSS": ".ad"}}
        lib = PercyLibrary()
        result = lib.create_percy_region_keyword(algorithm="ignore", element_css=".ad")

        mock_create.assert_called_once()
        self.assertEqual(result["algorithm"], "ignore")

    @patch("percy.robot_library.BuiltIn")
    def test_get_driver_requires_selenium_library(self, mock_builtin):
        mock_builtin.return_value.get_library_instance.side_effect = RuntimeError("not imported")
        lib = PercyLibrary()
        with self.assertRaises(RuntimeError) as cm:
            lib._get_driver()  # pylint: disable=protected-access
        self.assertIn("SeleniumLibrary", str(cm.exception))

    @patch("percy.robot_library.percy_automate_screenshot")
    @patch("percy.robot_library.BuiltIn")
    def test_percy_screenshot_keyword_basic(self, mock_builtin, mock_screenshot):
        mock_driver = MagicMock()
        mock_builtin.return_value.get_library_instance.return_value.driver = mock_driver
        lib = PercyLibrary()
        lib.percy_screenshot_keyword("Homepage")
        mock_screenshot.assert_called_once()
        args, kwargs = mock_screenshot.call_args
        self.assertIs(args[0], mock_driver)
        self.assertEqual(args[1], "Homepage")
        self.assertEqual(kwargs["options"], {})

    @patch("percy.robot_library.percy_automate_screenshot")
    @patch("percy.robot_library.BuiltIn")
    def test_percy_screenshot_keyword_with_region_elements(self, mock_builtin, mock_screenshot):
        mock_driver = MagicMock()
        selib = mock_builtin.return_value.get_library_instance.return_value
        selib.driver = mock_driver
        selib.find_element.side_effect = lambda loc: f"el:{loc}"
        lib = PercyLibrary()
        lib.percy_screenshot_keyword(
            "Page",
            ignore_region_selenium_elements="id:banner, css:.ad",
            consider_region_selenium_elements="id:main",
        )
        options = mock_screenshot.call_args[1]["options"]
        self.assertEqual(options["ignore_region_selenium_elements"], ["el:id:banner", "el:css:.ad"])
        self.assertEqual(options["consider_region_selenium_elements"], ["el:id:main"])


class TestRobotNotInstalled(unittest.TestCase):
    """When robotframework is absent, PercyLibrary degrades to a stub that
    raises a clear, actionable error on use."""

    def test_stub_library_raises_without_robotframework(self):
        from percy import robot_library as rl  # pylint: disable=import-outside-toplevel
        blocked = {name: None for name in (
            'robot', 'robot.api', 'robot.api.deco',
            'robot.libraries', 'robot.libraries.BuiltIn', 'robot.version',
        )}
        with patch.dict(sys.modules, blocked):
            importlib.reload(rl)
            try:
                self.assertFalse(rl.ROBOT_AVAILABLE)
                with self.assertRaises(ImportError) as cm:
                    rl.PercyLibrary()
                self.assertIn("robotframework is not installed", str(cm.exception))
            finally:
                importlib.reload(rl)

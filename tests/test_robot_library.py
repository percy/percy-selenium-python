"""Tests for Robot Framework library integration."""
import unittest
from unittest.mock import MagicMock, patch

from percy.robot_library import (
    PercyLibrary,
    _parse_bool,
    _parse_csv,
    _parse_json,
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

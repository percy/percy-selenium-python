"""Tests for Robot Framework library integration."""
import json
from unittest.mock import MagicMock, patch

import pytest

# Test that the module loads gracefully regardless of robot availability
def test_import_robot_library():
    """robot_library should import without error even if robotframework is installed."""
    from percy import robot_library
    assert hasattr(robot_library, 'PercyLibrary')


def test_percy_library_exists():
    """PercyLibrary should be importable from percy package."""
    from percy import PercyLibrary
    assert PercyLibrary is not None


class TestParseHelpers:
    """Test the argument parsing helpers used by Robot keywords."""

    def test_parse_bool_none(self):
        from percy.robot_library import _parse_bool
        assert _parse_bool(None) is None

    def test_parse_bool_true(self):
        from percy.robot_library import _parse_bool
        assert _parse_bool("True") is True
        assert _parse_bool("true") is True
        assert _parse_bool("1") is True
        assert _parse_bool("yes") is True

    def test_parse_bool_false(self):
        from percy.robot_library import _parse_bool
        assert _parse_bool("False") is False
        assert _parse_bool("false") is False
        assert _parse_bool("0") is False
        assert _parse_bool("no") is False

    def test_parse_widths_string(self):
        from percy.robot_library import _parse_widths
        assert _parse_widths("375,768,1280") == [375, 768, 1280]

    def test_parse_widths_list(self):
        from percy.robot_library import _parse_widths
        assert _parse_widths([375, 1280]) == [375, 1280]

    def test_parse_widths_none(self):
        from percy.robot_library import _parse_widths
        assert _parse_widths(None) is None

    def test_parse_csv_string(self):
        from percy.robot_library import _parse_csv
        assert _parse_csv("regression, homepage, v2") == ["regression", "homepage", "v2"]

    def test_parse_csv_none(self):
        from percy.robot_library import _parse_csv
        assert _parse_csv(None) is None

    def test_parse_json_string(self):
        from percy.robot_library import _parse_json
        result = _parse_json('{"fullPage": true}')
        assert result == {"fullPage": True}

    def test_parse_json_dict(self):
        from percy.robot_library import _parse_json
        result = _parse_json({"key": "value"})
        assert result == {"key": "value"}

    def test_parse_json_none(self):
        from percy.robot_library import _parse_json
        assert _parse_json(None) is None


class TestPercyLibraryKeywords:
    """Test that PercyLibrary keywords call percy_snapshot correctly."""

    @patch("percy.robot_library.percy_snapshot")
    @patch("percy.robot_library.BuiltIn")
    def test_percy_snapshot_keyword_basic(self, mock_builtin, mock_snapshot):
        from percy.robot_library import PercyLibrary

        mock_driver = MagicMock()
        mock_builtin.return_value.get_library_instance.return_value.driver = mock_driver

        lib = PercyLibrary()
        lib.percy_snapshot_keyword("Homepage")

        mock_snapshot.assert_called_once()
        args, kwargs = mock_snapshot.call_args
        assert args[0] is mock_driver
        assert args[1] == "Homepage"

    @patch("percy.robot_library.percy_snapshot")
    @patch("percy.robot_library.BuiltIn")
    def test_percy_snapshot_keyword_with_options(self, mock_builtin, mock_snapshot):
        from percy.robot_library import PercyLibrary

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
        _, kwargs = mock_snapshot.call_args
        assert kwargs["widths"] == [375, 1280]
        assert kwargs["min_height"] == 1024
        assert kwargs["percy_css"] == "h1 { color: red; }"
        assert kwargs["enable_javascript"] is True
        assert kwargs["labels"] == ["regression", "v2"]

    @patch("percy.robot_library.is_percy_enabled")
    def test_percy_is_running_keyword(self, mock_enabled):
        from percy.robot_library import PercyLibrary

        mock_enabled.return_value = {"session_type": "web", "config": {}}
        lib = PercyLibrary()
        assert lib.percy_is_running_keyword() is True

        mock_enabled.return_value = False
        assert lib.percy_is_running_keyword() is False

    @patch("percy.robot_library.create_region")
    def test_create_percy_region_keyword(self, mock_create):
        from percy.robot_library import PercyLibrary

        mock_create.return_value = {"algorithm": "ignore", "elementSelector": {"elementCSS": ".ad"}}
        lib = PercyLibrary()
        result = lib.create_percy_region_keyword(algorithm="ignore", element_css=".ad")

        mock_create.assert_called_once()
        assert result["algorithm"] == "ignore"

# pylint: disable=too-few-public-methods
"""Tests for the percy package entrypoint, focused on percy_screenshot
dispatch across connection types."""
import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import percy
from percy.exception import UnsupportedWebDriverException


class _WebDriver:  # class name must be exactly "WebDriver" for the dispatch check
    def __init__(self, command_executor):
        self.command_executor = command_executor


class _RemoteConnection:
    pass


class _AppiumConnection:
    pass


class _OtherConnection:
    pass


# the dispatch keys on the class name string, so name the wrapper classes to match
_WebDriver.__name__ = "WebDriver"
_RemoteConnection.__name__ = "RemoteConnection"
_AppiumConnection.__name__ = "AppiumConnection"


class TestPercyScreenshotDispatch(unittest.TestCase):
    def test_rejects_unsupported_driver(self):
        with self.assertRaises(UnsupportedWebDriverException):
            percy.percy_screenshot(MagicMock(), "name")

    @patch("percy.percy_automate_screenshot", return_value={"link": "x"})
    def test_remote_connection_uses_automate_screenshot(self, mock_automate):
        driver = _WebDriver(_RemoteConnection())
        result = percy.percy_screenshot(driver, "name")
        self.assertEqual(result, {"link": "x"})
        mock_automate.assert_called_once()

    def test_appium_connection_delegates_when_installed(self):
        fake_module = types.ModuleType("percy.screenshot")
        fake_module.percy_screenshot = MagicMock(return_value="delegated")
        with patch.dict(sys.modules, {"percy.screenshot": fake_module}):
            driver = _WebDriver(_AppiumConnection())
            result = percy.percy_screenshot(driver, "name")
        self.assertEqual(result, "delegated")
        fake_module.percy_screenshot.assert_called_once()

    def test_appium_connection_raises_when_not_installed(self):
        # percy.screenshot is not shipped here; the import must fail and surface
        # a helpful "install percy-appium" error.
        with patch.dict(sys.modules, {"percy.screenshot": None}):
            driver = _WebDriver(_AppiumConnection())
            with self.assertRaises(ModuleNotFoundError) as cm:
                percy.percy_screenshot(driver, "name")
        self.assertIn("percy-appium", str(cm.exception))

    def test_unknown_connection_returns_none(self):
        driver = _WebDriver(_OtherConnection())
        self.assertIsNone(percy.percy_screenshot(driver, "name"))


class TestOptionalImportFallbacks(unittest.TestCase):
    """Exercise the package's defensive optional-import fallbacks by reloading
    percy/__init__ with the relevant imports forced to fail."""

    def test_robot_library_import_failure_is_swallowed(self):
        # Block percy.robot_library so `from percy.robot_library import
        # PercyLibrary` raises ImportError and the `except ImportError: pass`
        # branch runs.
        try:
            with patch.dict(sys.modules, {"percy.robot_library": None}):
                importlib.reload(percy)
                # package still loads past the swallowed import failure
                self.assertTrue(callable(percy.percy_screenshot))
        finally:
            importlib.reload(percy)

    def test_percy_snapshot_fallback_when_snapshot_import_fails(self):
        # A stand-in percy.snapshot that provides percy_automate_screenshot (so
        # the top-level import on line 2 succeeds) but NOT percy_snapshot, so the
        # `from percy.snapshot import percy_snapshot` import fails and the
        # ModuleNotFoundError fallback is defined and executed.
        fake = types.ModuleType("percy.snapshot")
        fake.percy_automate_screenshot = lambda *a, **k: None
        try:
            with patch.dict(sys.modules, {"percy.snapshot": fake}):
                importlib.reload(percy)
                with self.assertRaises(ModuleNotFoundError) as cm:
                    percy.percy_snapshot(driver=MagicMock())
                self.assertIn("percy-selenium", str(cm.exception))
        finally:
            importlib.reload(percy)


if __name__ == "__main__":
    unittest.main()

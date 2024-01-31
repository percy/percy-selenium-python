from percy.version import __version__
from percy.snapshot import percy_automate_screenshot
from percy.exception import UnsupportedWebDriverException

# import snapshot command
try:
    from percy.snapshot import percy_snapshot
except ImportError:
    def percy_snapshot(driver, *a, **kw):
        raise ModuleNotFoundError("[percy] `percy-selenium` package is not installed, "\
                        "please install it to use percy_snapshot command")

# for better backwards compatibility
def percySnapshot(browser, *a, **kw):
    return percy_snapshot(driver=browser, *a, **kw)

# import screenshot command
def percy_screenshot(driver, name, **kw):
    if driver.__class__.__name__ != "WebDriver":
        raise UnsupportedWebDriverException("Provided driver is not supported")

    if "RemoteConnection" in driver.command_executor.__class__.__name__:
        return percy_automate_screenshot(driver, name, **kw)
    if "AppiumConnection" in driver.command_executor.__class__.__name__:
        try:
            from percy.screenshot import percy_screenshot # pylint: disable=W0621,C0415
            return percy_screenshot(driver, name, **kw)
        except ImportError as exc:
            raise ModuleNotFoundError("[percy] `percy-appium` package is not installed, "\
                "please install it to use percy_screenshot command with appium") from exc
    return None

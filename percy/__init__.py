from percy.version import __version__

# import snapshot command
try:
  from percy.snapshot import percy_snapshot
except:
  def percy_snapshot(driver, *a, **kw):
    raise Exception("[percy] `percy-selenium` package is not installed, please install it to use percy_snapshot command")
  
# for better backwards compatibility
def percySnapshot(browser, *a, **kw):
  return percy_snapshot(driver=browser, *a, **kw)

# improt screenshot command
try:
  from percy.screenshot import percy_screenshot
except:
  def percy_screenshot(driver, *a, **kw):
    raise Exception("[percy] `percy-appium-app` package is not installed, please install it to use percy_screenshot command")


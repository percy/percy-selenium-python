from percy.snapshot import percy_snapshot
from percy.version import __version__

# for better backwards compatibility
def percySnapshot(browser, *a, **kw):
    return percy_snapshot(driver=browser, *a, **kw)

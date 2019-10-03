# percy-python-selenium
[![CircleCI](https://circleci.com/gh/percy/percy-python-selenium.svg?style=svg)](https://circleci.com/gh/percy/percy-python-selenium)

[Percy](https://percy.io) visual testing for Python Selenium.

## Quick start

- Install `@percy/agent` from NPM: `npm i -D @percy/agent`
- Install the Python SDK: `pip install percy-python-selenium`
- Import `percySnapshot` in your test file: `from percy import percySnapshot`
- Add a `percySnapshot` call to your test:
``` python
from percy import percySnapshot

browser = webdriver.Firefox()
browser.get('http://example.com')
​
# take a snapshot
percySnapshot(browser=browser, name='Home page')
```
- Set your `PERCY_TOKEN` in the current env (you can get this in your Percy
  project settings)
- Run your tests with `percy exec -- [test command]`: `npx percy exec -- python
  ./tests.py` (or `yarn percy exec -- python ./tests.py`)

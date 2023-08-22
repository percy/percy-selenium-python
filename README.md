# percy-selenium-python
![Test](https://github.com/percy/percy-python-selenium/workflows/Test/badge.svg)

[Percy](https://percy.io) visual testing for Python Selenium.

## Installation

npm install `@percy/cli`:

```sh-session
$ npm install --save-dev @percy/cli
```

pip install Percy selenium package:

```ssh-session
$ pip install percy-selenium
```

## Usage

This is an example test using the `percy_snapshot` function.

``` python
from percy import percy_snapshot

browser = webdriver.Firefox()
browser.get('http://example.com')
​
# take a snapshot
percy_snapshot(browser, 'Python example')
```

Running the test above normally will result in the following log:

```sh-session
[percy] Percy is not running, disabling snapshots
```

When running with [`percy
exec`](https://github.com/percy/cli/tree/master/packages/cli-exec#percy-exec), and your project's
`PERCY_TOKEN`, a new Percy build will be created and snapshots will be uploaded to your project.

```sh-session
$ export PERCY_TOKEN=[your-project-token]
$ percy exec -- [python test command]
[percy] Percy has started!
[percy] Created build #1: https://percy.io/[your-project]
[percy] Snapshot taken "Python example"
[percy] Stopping percy...
[percy] Finalized build #1: https://percy.io/[your-project]
[percy] Done!
```

## Configuration

`percy_snapshot(driver, name[, **kwargs])`

- `driver` (**required**) - A selenium-webdriver driver instance
- `name` (**required**) - The snapshot name; must be unique to each snapshot
- `**kwargs` - [See per-snapshot configuration options](https://docs.percy.io/docs/cli-configuration#per-snapshot-configuration)

### Migrating Config

If you have a previous Percy configuration file, migrate it to the newest version with the
[`config:migrate`](https://github.com/percy/cli/tree/master/packages/cli-config#percy-configmigrate-filepath-output) command:

```sh-session
$ percy config:migrate
```

## Percy on Automate

## Usage

This is an example test using the `percy_screenshot` function.
`percy_screenshot(driver, name, options)` [ needs @percy/cli 1.27.0-beta.0+ ];

``` python
from percy import percy_screenshot

driver = webdriver.Remote("https://hub-cloud.browserstack.com/wd/hub", caps) # using automate session
driver.get('http://example.com')
​
# take a snapshot
percy_screenshot(driver, name = 'Screenshot 1')
```

- `driver` (**required**) - A Selenium driver instance
- `name` (**required**) - The screenshot name; must be unique to each screenshot
- `options` (**optional**) - There are various options supported by percy_screenshot to server further functionality.
    - `freeze_animation` - Boolean value by default it falls back to `false`, you can pass `true` and percy will freeze image based animations.
    - `percy_css` - Custom CSS to be added to DOM before the screenshot being taken. Note: This gets removed once the screenshot is taken.
    - `ignore_region_xpaths` - elements in the DOM can be ignored using xpath
    - `ignore_region_selectors` - elements in the DOM can be ignored using selectors.
    - `ignore_region_selenium_elements` - elements can be ignored using selenium_elements.
    - `custom_ignore_regions` - elements can be ignored using custom boundaries
      - IgnoreRegion:-
        - Description: This class represents a rectangular area on a screen that needs to be ignored for visual diff.

        - Constructor:
          ```
          init(self, top, bottom, left, right)
          ```

        - Parameters:

          `top` (int): Top coordinate of the ignore region.
          `bottom` (int): Bottom coordinate of the ignore region.
          `left` (int): Left coordinate of the ignore region.
          `right` (int): Right coordinate of the ignore region.
        - Raises:ValueError: If top, bottom, left, or right is less than 0 or top is greater than or equal to bottom or left is greater than or equal to right.
        - valid: Ignore region should be within the boundaries of the screen.

### Creating Percy on automate build
Note: Automate Percy Token starts with `auto` keyword. The command can be triggered using `exec` keyword.

```sh-session
$ export PERCY_TOKEN=[your-project-token]
$ percy exec -- [python test command]
[percy] Percy has started!
[percy] [Python example] : Starting automate screenshot ...
[percy] Screenshot taken "Python example"
[percy] Stopping percy...
[percy] Finalized build #1: https://percy.io/[your-project]
[percy] Done!
```

Refer to docs here: [Percy on Automate](https://docs.percy.io/docs/integrate-functional-testing-with-visual-testing)

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

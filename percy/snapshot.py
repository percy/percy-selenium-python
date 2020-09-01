import os
import requests

from selenium import webdriver

CLI_PORT = os.environ.get('PERCY_CLI_PORT')

CLI_URL = 'http://localhost' + CLI_PORT or '5338'
print CLI_URL
VERSION = 'v1.0.0'

percyIsRunning = True

# Fetches the JS that serializes the DOM
def getDOMJS():
    global percyIsRunning

    try:
        domJS = requests.get(CLI_URL + '/dom.js')
        return domJS.text
    except requests.exceptions.RequestException as e:
        if isDebug():
            print(e)
        if percyIsRunning == True:
            percyIsRunning = False
        print('[percy] failed to fetch dom.js, disabling Percy')
        return percyIsRunning


# POSTs the serialized DOM to the percy-agent server for asset discovery
def postSnapshot(postData):
    try:
        requests.post(CLI_URL + '/percy/snapshot', json=postData)
    except requests.exceptions.RequestException as e:
        if isDebug():
            print(e)

        print('[percy] failed to POST snapshot to the Percy CLI:' + postData.get('name'))
        return

def clientInfo():
    return 'percy-selenium-python/' + VERSION

def envInfo(capabilities):
    return 'python-selenium: ' + webdriver.__version__ + '; ' + capabilities.get('browserName') + ': ' + capabilities.get('browserVersion')

def isDebug():
    return os.environ.get('LOG_LEVEL') == 'debug'

def percySnapshot(browser, name, **kwargs):
    global percyIsRunning

    # Exit if we have failed to connect to the percy-agent server
    if percyIsRunning == False:
        return

    domJS = getDOMJS()

    # Exit if we fail to grab the JS that serializes the DOM
    if domJS == False:
        return

    browser.execute_script(domJS)
    domSnapshot = browser.execute_script('PercyDOM.serialize(arguments[0])', {
        'enableJavaScript': kwargs.get('enableJavaScript') or False,
        'domTransformation': kwargs.get('domTransformation') or '',
    })

    postData = {
        'name': name,
        'url': browser.current_url,
        'widths': kwargs.get('widths') or [],
        'percyCSS': kwargs.get('percyCSS') or '',
        'minHeight': kwargs.get('minHeight') or '',
        'enableJavaScript': kwargs.get('enableJavaScript') or False,
        'domSnapshot': domSnapshot,
        'clientInfo': clientInfo(),
        'environmentInfo': envInfo(browser.capabilities)
    }

    postSnapshot(postData)

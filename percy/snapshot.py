import os
import requests

from selenium import webdriver

AGENT_URL = 'http://localhost:5338'
VERSION = 'v0.1.0'

percyIsRunning = True

# Fetches the JS that serializes the DOM
def getAgentJS():
    global percyIsRunning

    try:
        agentJS = requests.get(AGENT_URL + '/percy-agent.js')
        return agentJS.text
    except requests.exceptions.RequestException as e:
        if isDebug():
            print(e)
        if percyIsRunning == True:
            percyIsRunning = False
        print('[percy] failed to fetch percy-agent.js, disabling Percy')
        return percyIsRunning


# POSTs the serialized DOM to the percy-agent server for asset discovery
def postSnapshot(postData):
    try:
        requests.post(AGENT_URL + '/percy/snapshot', json=postData)
    except requests.exceptions.RequestException as e:
        if isDebug():
            print(e)

        print('[percy] failed to POST snapshot to percy-agent:' + postData.get('name'))
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

    agentJS = getAgentJS()

    # Exit if we fail to grab the JS that serializes the DOM
    if agentJS == False:
        return

    browser.execute_script(agentJS)
    domSnapshot = browser.execute_script('var agent = new PercyAgent({ handleAgentCommunication: false }); return agent.snapshot("name")')
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

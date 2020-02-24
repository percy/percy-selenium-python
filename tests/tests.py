from selenium import webdriver
from percy import percySnapshot

def runTests(browser):
    browserName = browser.capabilities.get('browserName')
    browser.get('https://sdk-test.percy.dev')
    browser.implicitly_wait(10)
    browser.find_element_by_class_name("note")

    percySnapshot(browser=browser, name=browserName + ' Snapshots HTTPS, CSP, HSTS sites')

    percySnapshot(
        browser=browser,
        name=browserName + ' With options', widths=[666],
        minHeight=1500,
        percyCSS=".note { background-color: purple; }"
    )

    browser.get('https://sdk-test.percy.dev/redirects')
    browser.implicitly_wait(10)
    browser.find_element_by_class_name("note")

    percySnapshot(browser=browser, name=browserName + ' Snapshots redirected assets')

    browser.quit()


chrome_options = webdriver.ChromeOptions()
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-extensions')
chrome_options.add_argument('--disable-dev-shm-usage')
chrome_options.add_argument('--disable-setuid-sandbox')
chrome_options.add_argument('--headless')

chromeBrowser = webdriver.Chrome(options=chrome_options)
runTests(chromeBrowser)

firefoxBrowser = webdriver.Firefox()
runTests(firefoxBrowser)

from selenium import webdriver
from percy import percySnapshot

browser = webdriver.Firefox()

browser.get('https://sdk-test.percy.dev')
browser.implicitly_wait(10)
browser.find_element_by_class_name("note")

percySnapshot(browser=browser, name='Snapshots HTTPS, CSP, HSTS sites')

percySnapshot(
    browser=browser,
    name='With options', widths=[666],
    minHeight=1500,
    percyCSS=".note { background-color: purple; }"
)

browser.get('https://sdk-test.percy.dev/redirects')
browser.implicitly_wait(10)
browser.find_element_by_class_name("note")

percySnapshot(browser=browser, name='Snapshots redirected assets')

browser.quit()

*** Settings ***
Library    SeleniumLibrary
Library    percy.robot_library.PercyLibrary
Library    Collections

Suite Setup       Open Test Browser
Suite Teardown    Close All Browsers

*** Keywords ***
Open Test Browser
    Open Browser    https://example.com    chrome    options=add_argument("--headless")

*** Test Cases ***

# ===================================================================
# BASIC SNAPSHOTS
# ===================================================================

Basic Snapshot
    [Documentation]    Simplest snapshot -- just a name
    Percy Snapshot    Basic - Example.com

Named Snapshot After Navigation
    [Documentation]    Navigate and take snapshot
    Go To    https://example.com
    Percy Snapshot    Navigation - After GoTo

Multiple Snapshots Same Page
    [Documentation]    Multiple snapshots of the same page
    Go To    https://example.com
    Percy Snapshot    Multi - First
    Percy Snapshot    Multi - Second

# ===================================================================
# RESPONSIVE WIDTHS
# ===================================================================

Single Width Mobile
    [Documentation]    Snapshot at mobile width only
    Percy Snapshot    Width - Mobile 375    widths=375

Multiple Widths
    [Documentation]    Snapshot at mobile, tablet, desktop
    Percy Snapshot    Width - All Breakpoints    widths=375,768,1280

# ===================================================================
# MIN HEIGHT
# ===================================================================

Min Height
    [Documentation]    Snapshot with minimum height
    Percy Snapshot    MinHeight - 1024    min_height=1024

Widths Plus Min Height
    [Documentation]    Combine widths and min height
    Percy Snapshot    Widths+Height    widths=375,1280    min_height=1500

# ===================================================================
# PERCY CSS
# ===================================================================

Percy CSS Hide Heading
    [Documentation]    Hide the h1 heading
    Percy Snapshot    CSS - Hide H1    percy_css=h1 { display: none !important; }

Percy CSS Custom Background
    [Documentation]    Change background color
    Percy Snapshot    CSS - Background    percy_css=body { background-color: #f0f0f0 !important; }

# ===================================================================
# SCOPED SNAPSHOTS
# ===================================================================

Scoped To Body Div
    [Documentation]    Capture only the main content div
    Percy Snapshot    Scope - Body Div    scope=body > div

Scope With Percy CSS
    [Documentation]    Combine scope and CSS injection
    Percy Snapshot    Scope+CSS    scope=body > div    percy_css=p { color: blue !important; }

# ===================================================================
# RENDERING OPTIONS
# ===================================================================

JavaScript Enabled
    [Documentation]    Snapshot with JS enabled in Percy rendering
    Percy Snapshot    JS - Enabled    enable_javascript=True

Layout Mode
    [Documentation]    Layout comparison mode
    Percy Snapshot    Layout - Basic    enable_layout=True

Shadow DOM Disabled
    [Documentation]    Snapshot without Shadow DOM capture
    Percy Snapshot    ShadowDOM - Disabled    disable_shadow_dom=True

# ===================================================================
# LABELS / TAGS
# ===================================================================

Single Label
    [Documentation]    Snapshot with one label
    Percy Snapshot    Labels - Single    labels=smoke-test

Multiple Labels
    [Documentation]    Snapshot with multiple labels
    Percy Snapshot    Labels - Multiple    labels=regression,homepage,v2

Labels With Widths
    [Documentation]    Labels combined with responsive widths
    Percy Snapshot    Labels+Widths    labels=responsive,cross-browser    widths=375,768,1280

# ===================================================================
# TEST CASE METADATA
# ===================================================================

Test Case ID
    [Documentation]    Snapshot with test case identifier
    Percy Snapshot    TestCase - TC001    test_case=TC-001-homepage

# ===================================================================
# IGNORE REGIONS
# ===================================================================

Ignore Region By CSS
    [Documentation]    Create ignore region using CSS selector
    ${region}=    Create Percy Region    algorithm=ignore    element_css=h1
    Percy Snapshot    Region - Ignore CSS    regions=${{json.dumps([${region}])}}

Ignore Region By XPath
    [Documentation]    Create ignore region using XPath
    ${region}=    Create Percy Region    algorithm=ignore    element_xpath=//h1
    Percy Snapshot    Region - Ignore XPath    regions=${{json.dumps([${region}])}}

# ===================================================================
# CONSIDER REGIONS
# ===================================================================

Consider Region Standard
    [Documentation]    Standard algorithm with diff sensitivity
    ${region}=    Create Percy Region    algorithm=standard    element_css=body > div    diff_sensitivity=5
    Percy Snapshot    Region - Standard    regions=${{json.dumps([${region}])}}

IntelliIgnore Region
    [Documentation]    IntelliIgnore with carousel detection
    ${region}=    Create Percy Region    algorithm=intelliignore    element_css=body > div    carousels_enabled=True
    Percy Snapshot    Region - IntelliIgnore    regions=${{json.dumps([${region}])}}

# ===================================================================
# MULTIPLE REGIONS
# ===================================================================

Mixed Ignore And Consider Regions
    [Documentation]    Combine ignore and consider regions
    ${ignore}=    Create Percy Region    algorithm=ignore    element_css=h1
    ${consider}=    Create Percy Region    algorithm=standard    element_css=p    diff_sensitivity=8
    Percy Snapshot    Region - Mixed    regions=${{json.dumps([${ignore}, ${consider}])}}

# ===================================================================
# RESPONSIVE CAPTURE
# ===================================================================

Responsive Capture
    [Documentation]    Responsive capture resizes browser per width
    Percy Snapshot    Responsive - Basic    responsive_snapshot_capture=True

# ===================================================================
# ALL OPTIONS COMBINED
# ===================================================================

All Options Combined
    [Documentation]    Every option in a single snapshot call
    ${ignore}=    Create Percy Region    algorithm=ignore    element_css=h1    padding=5
    Percy Snapshot    Full Options - Everything
    ...    widths=375,768,1280
    ...    min_height=1024
    ...    percy_css=a { text-decoration: none !important; }
    ...    enable_javascript=True
    ...    enable_layout=True
    ...    labels=full-test,regression,v2
    ...    test_case=TC-999-all-options
    ...    regions=${{json.dumps([${ignore}])}}

# ===================================================================
# UTILITY KEYWORDS
# ===================================================================

Percy Is Running Returns True
    [Documentation]    Verify Percy Is Running keyword works
    ${running}=    Percy Is Running
    Should Be True    ${running}

Conditional Snapshot
    [Documentation]    Take snapshot only if Percy is running
    ${running}=    Percy Is Running
    Run Keyword If    ${running}    Percy Snapshot    Conditional - If Running

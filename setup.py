import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name='percy-python-selenium',
    version='0.1.2',
    description='Python client for visual regression testing with Percy (https://percy.io).',
    author='Perceptual Inc.',
    author_email='team@percy.io',
    url='https://github.com/percy/percy-python-selenium',
    packages=[
        'percy',
    ],
    include_package_data=True,
    install_requires=[
        'selenium==3.*',
        'requests==2.*'
    ],
    license='MIT',
    zip_safe=False,
    keywords='percy',
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Natural Language :: English',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
    ],
    test_suite='tests',
    tests_require=['selenium']
)

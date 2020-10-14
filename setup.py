from setuptools import setup
import percy

setup(
    name='percy-selenium',
    description='Python client for visual testing with Percy',
    version=percy.__version__,
    license='MIT',
    author='Perceptual Inc.',
    author_email='team@percy.io',
    url='https://github.com/percy/percy-selenium-python',
    keywords='selenium percy visual testing',
    packages=['percy'],
    include_package_data=True,
    install_requires=[
        'selenium==3.*',
        'requests==2.*'
    ],
    python_requires='>=3.6',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Natural Language :: English',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
    ],
    test_suite='tests',
    tests_require=['selenium', 'httpretty'],
    zip_safe=False
)

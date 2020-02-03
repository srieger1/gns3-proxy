# -*- coding: utf-8 -*-
"""
    gns3-proxy

    GNS3 Proxy Server in Python.

    based on proxy.py - HTTP Proxy Server in Python - copyright: (c) 2013-2018 by Abhinav Singh

    :copyright: (c) 2020 by Sebastian Rieger.
    :license: BSD, see LICENSE for more details.
"""
from setuptools import setup
import gns3_proxy

classifiers = [
    'Development Status :: 5 - Production/Stable',
    'Environment :: No Input/Output (Daemon)',
    'Environment :: Web Environment',
    'Intended Audience :: Education',
    'Intended Audience :: System Administrators',
    'License :: OSI Approved :: BSD License',
    'Operating System :: MacOS',
    'Operating System :: MacOS :: MacOS 9',
    'Operating System :: MacOS :: MacOS X',
    'Operating System :: POSIX',
    'Operating System :: POSIX :: Linux',
    'Operating System :: Unix',
    'Operating System :: Microsoft',
    'Operating System :: OS Independent',
    # 'Programming Language :: Python :: 2',
    # 'Programming Language :: Python :: 2.7',
    'Programming Language :: Python :: 3',
    'Programming Language :: Python :: 3.7',
    'Topic :: Internet :: Proxy Servers',
    'Topic :: Internet :: WWW/HTTP',
    'Topic :: Internet :: WWW/HTTP :: HTTP Servers',
    'Topic :: Software Development :: Libraries :: Python Modules',
    'Topic :: System :: Networking :: Monitoring',
    'Topic :: Utilities',
]

setup(
    name='gns3-proxy',
    version=gns3_proxy.__version__,
    author=gns3_proxy.__author__,
    author_email=gns3_proxy.__author_email__,
    url=gns3_proxy.__homepage__,
    description=gns3_proxy.__description__,
    long_description=open('README.md').read().strip(),
    long_description_content_type='text/markdown',
    download_url=gns3_proxy.__download_url__,
    classifiers=classifiers,
    license=gns3_proxy.__license__,
    py_modules=['gns3_proxy'],
    scripts=['gns3_proxy.py'],
    install_requires=['requests', 'packaging'],
)

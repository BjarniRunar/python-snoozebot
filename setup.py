from setuptools import setup

from snoozebot import __version__, __author__, __author_email__

classifiers = [
    'Development Status :: 4 - Beta',
    'Intended Audience :: Developers',
    'License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)',
    'Programming Language :: Python',
    'Programming Language :: Python :: 3',
    'Topic :: Software Development :: Libraries :: Python Modules']

setup(
    name = 'snoozebot',
    version = __version__,
    author = __author__,
    author_email = __author_email__,
    license = 'LGPLv3+',
    description = 'Thread- and battery-friendly time.sleep() replacement',
    long_description = open('README.md').read(),
    long_description_content_type = 'text/markdown',
    url = 'https://github.com/BjarniRunar/python-snoozebot',
    download_url = 'https://github.com/BjarniRunar/python-snoozebot/archive/v0.0.1.tar.gz',
    keywords = 'sleep powersaving threads threading',
    install_requires = [],
    classifiers = classifiers,
    packages = ['snoozebot'])

from setuptools import setup

from snoozebot import __version__, __author__

classifiers = [
    'Development Status :: 4 - Beta',
    'Intended Audience :: Developers',
    'License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)',
    'Programming Language :: Python',
   #'Programming Language :: Python :: 3',
    'Topic :: Software Development :: Libraries :: Python Modules']

setup(
    name = 'snoozebot',
    version = __version__,
    author = __author__,
    license = 'LGPLv3+',
    description = 'Thread- and battery-friendly time.sleep() replacement',
    url = 'https://github.com/BjarniRunar/python-snoozebot',
    download_url = 'https://github.com/BjarniRunar/python-snoozebot/archive/v0.0.1.tar.gz',
    keywords = 'sleep powersaving threads threading',
    install_requires = [],
    classifiers = classifiers,
    packages = ['snoozebot'])

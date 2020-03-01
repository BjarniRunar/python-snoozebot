# This file is part of snoozebot
#
# Snoozebot is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as
# published by the Free Software Foundation, either version 3 of
# the License, or (at your option) any later version.
#
# Snoozebot is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with snoozebot. If not, see <https://www.gnu.org/licenses/>.
#
__author__ = 'Bjarni R. Einarsson <bre@klaki.net>'
__author_email__ = 'bre@klaki.net'
__version__ = '0.0.1'
__doc__ = """A coordinated and interruptible time.sleep() replacement

This module implements a time.sleep() replacement, snoozebot.snooze(), which
is more suitable for use in multithreaded apps:

  * Threads can be woken up (optionally, with an exception) to facilitate
    prompt, clean shutdown.
  * Snoozing can be cut short if file descriptor(s) becomes readable
  * Snoozing threads can specify an interval of acceptable wakeup times,
    to facilitate grouping jobs together (thus hopefully playing nice with
    powersavings on modern/mobile CPUs).

Usage:

    from snoozebot import snooze, wake_all

    # Interruptable sleep for 1 second
    snooze(1.0)

    # Sleep for anywhere between 1 minute and 1 hour
    snooze(60, 3600)

    # Sleep an hour, but wake up early for activity on sys.stdin
    snooze(3600, watch_fds=[sys.stdin.fileno()])

    # Just wake all snoozers
    wake_all()

    # Wake all snoozers with a KeyboardInterrupt!
    wake_all(KeyboardInterrupt, 'Time to die')
"""

import select
import socket
import time
import threading
import traceback

try:
    from queue import Queue, Empty  # python 3+
except ImportError:
    from Queue import Queue, Empty  # python 2.7


_conductor_lock = threading.Lock()
_conductor = None


class _SnoozeBot(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True

        self.sleepers = []
        self.changed = False
        self.keep_running = True

        self.fd_watchers = {}
        self.dir_watchers = {}

        self.wql = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.wql.bind(('localhost', 0))
        self.wql_addr = self.wql.getsockname()
        self.select_r = [self.wql.fileno()]

        # FIXME: See if we have inotify?

    def _wake_up(self):
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'\1', self.wql_addr)

    def _sleep(self, seconds=None):
        try:
            wql_fno = self.wql.fileno()
            r, w, x = select.select(self.select_r, [], self.select_r, seconds)
            for fno in set(r + x):
                if fno == wql_fno:
                    self.wql.recvfrom(1024)
                for sleeper in self.fd_watchers.get(fno, []):
                    sleeper[0] = sleeper[1] = 0
            return self.keep_running
        except:
            return None

    def _reconfigure(self):
        if self.changed:
            global _conductor_lock
            with _conductor_lock:
                self.select_r = [self.wql.fileno()] + list(self.fd_watchers.keys())
                self.sleepers.sort()
                self.changed = False

    def _wake(self, sleeper, throw=None, targs=None):
        global _conductor_lock
        with _conductor_lock:
            sleeper[0] = sleeper[1] = 0
            for fno in (sleeper[-2] or []):
                try:
                    self.fd_watchers[fno].remove(sleeper)
                    if not self.fd_watchers[fno]:
                        del self.fd_watchers[fno]
                    self.changed = True
                except KeyError:
                    pass
            # FIXME: Clean up dir_watchers , set changed=True
        sleeper[-1].put((throw, targs))

    def _ready(self, now):
        return [s for s in self.sleepers if s[0] <= now or s[1] <= now]

    def snooze(self, seconds, maximum, watch_fds, snoozeq):
        if snoozeq is None:
            snoozeq = Queue()
        if maximum is None:
            maximum = seconds

        now = time.time()
        wake = False
        global _conductor_lock
        with _conductor_lock:
            d0 = self.sleepers[0][:2] if self.sleepers else [0, 0]
            sleeper = [
                now + maximum, now + seconds, maximum, seconds,
                watch_fds, snoozeq]
            for fd in watch_fds or []:
                self.fd_watchers[fd] = self.fd_watchers.get(fd, [])
                self.fd_watchers[fd].append(sleeper)
            self.sleepers.append(sleeper)
            self.sleepers.sort()
            self.changed = True
            wake = (self.sleepers[0][:2] != d0) or self._ready(now)

        if wake:
            self._wake_up()
        return snoozeq

    def wake_all(self, throw, *targs):
        global _conductor_lock
        with _conductor_lock:
            sleepers = self.sleepers
            self.keep_running = False
            self.sleepers = []
            self.fd_watchers = {}
            self.dir_watchers = {}
            self._wake_up()

        for sleeper in sleepers:
            self._wake(sleeper, throw=throw, targs=targs)

    def run(self):
        global _conductor_lock, _conductor
        while self.keep_running and self._sleep():
            self._reconfigure()
            while self.keep_running and self.sleepers:
                try:
                    self._reconfigure()
                    latest, earliest = self.sleepers[0][:2]

                    now = time.time()
                    if now < earliest:
                        self._sleep(latest - now)
                        now = time.time()

                    waking = [s for s in self.sleepers if now >= s[0]]
                    if not waking:
                        waking = self._ready(now)
                        waking.sort(key=lambda s: s[1])
                        waking = waking[:1]

                    if waking:
                        for sleeper in waking:
                            self._wake(sleeper)
                        with _conductor_lock:
                            self.sleepers = [s for s in self.sleepers if s[0] > 0]
                except:
                    traceback.print_exc()

        with _conductor_lock:
            _conductor = None


def snooze(seconds, maximum=None, watch_fds=None, snoozeq=None):
    """
    Put this thread to sleep for up to `seconds` seconds.

    If a maximum is specified, the thread may opportunistically be woken up
    any time after seconds have passed, up to the maximum time.

    If watch_fds is a list of file descriptors, the snooze will be aborted
    early if any of them becomes readable according to select().

    If a snoozeq is provided, it should be a Queue object which can be used
    to wake this thread up.
    """
    global _conductor_lock, _conductor

    if maximum is None:
        maximum = seconds
    if maximum < 0.5:
        # Special case short circuit: just do the simple thing.
        with _conductor_lock:
            if _conductor is not None and _conductor._ready(time.time()):
                _conductor._wake_up()
        return time.sleep(maximum)

    with _conductor_lock:
        if _conductor is None:
            _conductor = _SnoozeBot()
            _conductor.start()
    try:
        exc, targs = _conductor.snooze(
            seconds, maximum, watch_fds, snoozeq
            ).get(True, seconds)
        if exc is not None:
            raise(exc(*(targs or [])))  # Woken up?
    except Empty:
        pass


def wake_all(*exception_and_args):
    """
    Wake up all snoozing threads immediately, optionally causing them to
    raise an exception with the given args or kwargs.
    """
    try:
        _conductor.wake_all(*exception_and_args)
    except AttributeError:
        pass


if __name__ == '__main__':
    import sys, random

    counter = [3]
    def snoozer(seconds, maximum, fds):
        def sn():
            try:
                print('%d: Snoozing for up to %ss' % (time.time(), seconds))
                snooze(seconds, maximum, watch_fds=fds)
                snooze(0.1)
                print('%d: Snoozing complete! (%s/%s)' % (time.time(), seconds, maximum))
            except:
                traceback.print_exc()
        return sn

    print('The time is %.2f' % time.time())
    try:
        threading.Thread(target=snoozer(2.2, None, None)).start()
        threading.Thread(target=snoozer(1, 3600, None)).start()
        snooze(1.5)
        threading.Thread(target=snoozer(20, None, [sys.stdin.fileno()])).start()
        snooze(8.5)
        wake_all(IOError)
    except KeyboardInterrupt:
        print('Interrupted at %.2f' % time.time())
        wake_all(KeyboardInterrupt, ('User aborted',))
    print('The time is %.2f' % time.time())
    time.sleep(1)

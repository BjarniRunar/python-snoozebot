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
__version__ = '0.0.2'
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


_conductor_lock = threading.RLock()
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
                    sleeper.earliest = sleeper.latest = 0
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
        sleeper.wake(throw, targs)  # This calls remove() for us

    def _ready(self, now):
        return [s for s in self.sleepers if s._ready()]

    def remove(self, sleeper):
        global _conductor_lock
        with _conductor_lock:
            sleeper.earliest = sleeper.latest = 0
            for fno in sleeper.watch_fds:
                try:
                    self.fd_watchers[fno].remove(sleeper)
                    if not self.fd_watchers[fno]:
                        del self.fd_watchers[fno]
                    self.changed = True
                except KeyError:
                    pass

    def snooze(self, sleeper):
        now = time.time()
        global _conductor_lock
        with _conductor_lock:
            for fd in sleeper.watch_fds:
                self.fd_watchers[fd] = self.fd_watchers.get(fd, [])
                self.fd_watchers[fd].append(sleeper)

            d0 = self.sleepers[0]._deadlines() if self.sleepers else (0, 0)
            self.sleepers.append(sleeper)
            self.sleepers.sort()
            self.changed = True

            # Note: The _ready(now) call is how we batch thread activity; we
            #       are putting one thread to sleep, so we check if any other
            #       snoozer is willing to wake up and take their place.
            if (self.sleepers[0]._deadlines() != d0) or self._ready(now):
                self._wake_up()

        return sleeper

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
            sleeper.wake(throw, *targs)

    def run(self):
        global _conductor_lock, _conductor
        while self.keep_running and self._sleep():
            self._reconfigure()
            while self.keep_running and self.sleepers:
                try:
                    self._reconfigure()
                    latest, earliest = self.sleepers[0]._deadlines()

                    now = time.time()
                    if now < earliest:
                        self._sleep(latest - now)
                        now = time.time()

                    waking = [s for s in self.sleepers if now >= s.latest]
                    if not waking:
                        waking = self._ready(now)
                        waking.sort(key=lambda s: s.earliest)
                        waking = waking[:1]

                    if waking:
                        for sleeper in waking:
                            self._wake(sleeper)
                        with _conductor_lock:
                            self.sleepers = [s for s in self.sleepers if s.latest]
                except:
                    traceback.print_exc()

        with _conductor_lock:
            _conductor = None


class Snoozer:
    """
    This is a reusable Snooze schedule, which also provides a wake() function
    so other parts of an app can wake up a specific snoozing thread.
    """
    def __init__(self, seconds, maximum=None, watch_fds=None, snoozeq=None):
        """
        Create a new Snoozer. See the documentation for snoozebot.snooze() for
        an explanation of the arguments and their meanings.
        """
        self._lock = threading.Lock()
        self.snoozeq = snoozeq or Queue()
        self.seconds = seconds
        self.maximum = max(maximum or 0, seconds)
        self.earliest = 0
        self.latest = 0
        self.watch_fds = watch_fds or []

    def __lt__(s, o): return s._deadlines() < o._deadlines()
    def __le__(s, o): return s._deadlines() <= o._deadlines()
    def __gt__(s, o): return s._deadlines() > o._deadlines()
    def __ge__(s, o): return s._deadlines() >= o._deadlines()

    def _deadlines(self):
        return (self.latest, self.earliest)

    def _ready(self, now=None):
        return (self.earliest <= (now if (now is not None) else time.time()))

    def wake(self, *exception_and_args):
        """
        Wake up the snoozing thread. If any arguments are given, the first
        should be an exception to raise in the woken thread, followed by
        arguments to the exception itself. Examples:

            snoozer.wake()
            snoozer.wake(KeyboardInterrupt)
            snoozer.wake(KeyboardInterrupt, 'User aborted')
        """
        throw = exception_and_args[0] if exception_and_args else None
        targs = exception_and_args[1:] or None
        with _conductor_lock:
            if _conductor is not None:
                _conductor.remove(self)
        self.snoozeq.put((throw, targs))

    def snooze(self):
        """
        Put the calling thread to sleep, according to the defined schedule.
        """
        global _conductor_lock, _conductor
        if self._lock.locked():
            raise IOError("Snoozer is already in use!")
        with self._lock:
            now = time.time()

            if self.maximum < 0.5:
                with _conductor_lock:
                    if _conductor is not None and _conductor._ready(now):
                        # We are going to sleep, does anyone want to wake up?
                        _conductor._wake_up()
                # Special case short circuit: just do the simple thing.
                return time.sleep(self.maximum)

            with _conductor_lock:
                if _conductor is None:
                    _conductor = _SnoozeBot()
                    _conductor.start()
            try:
                # Empty the queue, in case we are being reused
                while not self.snoozeq.empty():
                    self.snoozeq.get(False)

                # Calculate our deadliens
                self.earliest = now + self.seconds
                self.latest = now + self.maximum

                # Hand over to the conductor!
                exc, targs = _conductor.snooze(self).snoozeq.get(True, self.maximum)
                if exc is not None:
                    raise(exc(*(targs or [])))  # Woken up?
            except Empty:
                pass


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
    return Snoozer(seconds, maximum, watch_fds, snoozeq).snooze()


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

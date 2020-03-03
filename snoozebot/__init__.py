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
__version__ = '0.0.3'
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

    from snoozebot import snooze, wake_all, WOKE_FD

    # Interruptable sleep for 1 second
    snooze(1.0)

    # Sleep for anywhere between 1 minute and 1 hour
    snooze(60, 3600)

    # Sleep an hour, but wake up early for activity on sys.stdin
    reason = snooze(3600, watch_fds=[sys.stdin.fileno()])
    if reason[0] == WOKE_FD:
        data = reason[1].read()

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


# The order of these matter; higher priority reasons override
# lower-priority ones internally.
WOKE_UNKNOWN = 0
WOKE_OTHER   = 1
WOKE_NORMAL  = 10
WOKE_EARLY   = 20
WOKE_FD      = 30
WOKE_ALL     = 40
WOKE_CUSTOM  = 99

_WOKE_UNKNOWN = (WOKE_UNKNOWN, 'Unknown')
_WOKE_OTHER   = (WOKE_OTHER,   'App-internal wake-up')
_WOKE_NORMAL  = (WOKE_NORMAL,  'Normal')
_WOKE_EARLY   = (WOKE_EARLY,   'Woke early')
_WOKE_ALL     = (WOKE_ALL,     'All snoozes were aborted')


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
                    sleeper.reason(WOKE_FD, fno)
                    sleeper._earliest = sleeper._latest = 0
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
            sleeper._earliest = sleeper._latest = 0
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
            sleeper.reason(*_WOKE_ALL).wake(throw, *targs)

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

                    reason = _WOKE_NORMAL
                    waking = [s for s in self.sleepers if now >= s._latest]
                    if not waking:
                        reason = _WOKE_EARLY
                        waking = self._ready(now)
                        waking.sort(key=lambda s: s._earliest)
                        waking = waking[:1]

                    if waking:
                        for sleeper in waking:
                            self._wake(sleeper.reason(*reason))
                        with _conductor_lock:
                            self.sleepers = [s for s in self.sleepers if s._latest]
                except:
                    traceback.print_exc()

        with _conductor_lock:
            _conductor = None


class Snoozer:
    """
    This is a reusable Snooze schedule, which also provides a wake()
    function so other parts of an app can wake a snoozing thread.
    """
    def __init__(self, seconds, maximum=None, watch_fds=None):
        """
        Create a new Snoozer. See the snoozebot.snooze() help for
        an explanation of the arguments and their meanings.
        """
        self._lock = threading.Lock()
        self.seconds = seconds
        self.maximum = max(maximum or 0, seconds)
        self.watch_fds = watch_fds or []
        self._snoozeq = Queue()
        self._earliest = 0
        self._latest = 0
        self._wake_reason = _WOKE_UNKNOWN

    def __lt__(s, o): return s._deadlines() < o._deadlines()
    def __le__(s, o): return s._deadlines() <= o._deadlines()
    def __gt__(s, o): return s._deadlines() > o._deadlines()
    def __ge__(s, o): return s._deadlines() >= o._deadlines()

    def _deadlines(self):
        return (self._latest, self._earliest)

    def _ready(self, now=None):
        return (self._earliest <= (now if (now is not None) else time.time()))

    def reason(self, rcode, details):
        """
        If called right before wake(), this will configure the reason
        returned by the in-flight snooze() call. Apps should use the
        WOKE_CUSTOM code, otherwise the reason might get overwritten.

        Returns the Snoozer object itself, for chaining with woke().
        """
        if rcode >= self._wake_reason[0]:
            self._wake_reason = (rcode, details)
        return self

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
        self.reason(*_WOKE_OTHER)._snoozeq.put((throw, targs))

    def snooze(self):
        """
        Put the calling thread to sleep, according to the defined schedule.
        Returns a tuple (WOKE_x, reason) explaining why the snooze ended.

        If the snooze ended because of a watched file descriptor, the reason
        will be the file descriptor that showed activity, otherwise reasons
        are human-readable text to aid with debugging.
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
                time.sleep(self.maximum)
                return _WOKE_NORMAL

            with _conductor_lock:
                if _conductor is None:
                    _conductor = _SnoozeBot()
                    _conductor.start()
            try:
                # Reset state, in case we are being reused
                while not self._snoozeq.empty():
                    self._snoozeq.get(False)
                self._wake_reason = _WOKE_UNKNOWN

                # Calculate our deadliens
                self._earliest = now + self.seconds
                self._latest = now + self.maximum

                # Hand over to the conductor!
                exc, targs = (
                    _conductor.snooze(self)._snoozeq.get(True, self.maximum))
                if exc is not None:
                    raise(exc(*(targs or [])))  # Woken up?
                return self._wake_reason
            except Empty:
                return _WOKE_NORMAL


def snooze(seconds, maximum=None, watch_fds=None):
    """
    Put this thread to sleep for up to `seconds` seconds.
    Returns a tuple (WOKE_x, reason) explaining why the snooze ended.

    If a maximum is specified, the thread may opportunistically be woken up
    any time after seconds have passed, up to the maximum time.

    If watch_fds is a list of file descriptors, the snooze will be aborted
    early if any of them becomes readable according to select().

    If the snooze ended because of a watched file descriptor, the reason
    will be the file descriptor that showed activity, otherwise reasons
    are human-readable text to aid with debugging.
    """
    return Snoozer(seconds, maximum, watch_fds).snooze()


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
                r1 = snooze(seconds, maximum, watch_fds=fds)
                r2 = snooze(0.1)
                print('%d: Snoozing complete! (%s/%s) reasons=%s/%s' % (time.time(), seconds, maximum, r1, r2[0]))
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

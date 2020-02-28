"""
A coordinated and interruptible time.sleep() replacement.
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

    def _wake(self, sleeper, throw=None, targs=None, tkwargs=None):
        global _conductor_lock
        with _conductor_lock:
            sleeper[0] = sleeper[1] = 0
            for fno in (sleeper[-3] or []):
                try:
                    self.fd_watchers[fno].remove(sleeper)
                    if not self.fd_watchers[fno]:
                        del self.fd_watchers[fno]
                    self.changed = True
                except KeyError:
                    pass
            # FIXME: Clean up dir_watchers , set changed=True
        sleeper[-1].put((throw, targs, tkwargs))

    def _ready(self, now):
        return [s for s in self.sleepers if s[0] <= now or s[1] <= now]

    def snooze(self, seconds, minimum, watch_fds, cond, snoozeq):
        if snoozeq is None:
            snoozeq = Queue()
        if minimum is None:
            minimum = 0.9 * seconds

        now = time.time()
        wake = False
        global _conductor_lock
        with _conductor_lock:
            d0 = self.sleepers[0][:2] if self.sleepers else [0, 0]
            sleeper = [
                now + seconds, now + minimum, seconds, minimum,
                watch_fds, cond, snoozeq]
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

    def wake_all(self, throw, targs, tkwargs):
        global _conductor_lock
        with _conductor_lock:
            sleepers = self.sleepers 
            self.keep_running = False
            self.sleepers = []
            self.fd_watchers = {}
            self.dir_watchers = {}
            self._wake_up()

        for sleeper in sleepers:
            self._wake(sleeper, throw=throw, targs=targs, tkwargs=tkwargs)

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

                    for sleeper in waking:
                        if (sleeper[-2] is None) or sleeper[-2](now):
                            self._wake(sleeper)
                        else:
                            while sleeper[1] <= now or sleeper[0] <= now:
                                sleeper[0] += sleeper[2]
                                sleeper[1] += sleeper[3]
                            self.changed = True

                    if waking:
                        with _conductor_lock:
                            self.sleepers = [s for s in self.sleepers if s[0] > 0]
                except:
                    traceback.print_exc()

        with _conductor_lock:
            _conductor = None


def snooze(seconds=24*3600,
        minimum=None, watch_fds=None, condition=None, snoozeq=None):
    """
    Put this thread to sleep for up to `seconds` seconds.

    If a minimum is specified, the thread may opportunistically be woken up
    after that long instead (defaults to 90% of `seconds`).

    If a conditions is specified, it must be a function that takes a single
    argument (the current Unix timestamp) and returns a true value if the
    caller is allowed to wake up. If the caller is not allowed to wake up,
    but deadlines have passed, then the deadlines will be extended.

    If watch_fds is a list of file descriptors, the snooze will be aborted
    early if any of them becomes readable according to select().

    If a snoozeq is provided, it should be a Queue object which can be used
    to wake this thread up.
    """
    global _conductor_lock, _conductor

    if seconds < 0.5:
        # Special case short circuit: just do the simple thing.
        with _conductor_lock:
            if _conductor is not None and _conductor._ready(time.time()):
                _conductor._wake_up()
        return time.sleep(seconds)
    if minimum is None:
        minimum = 0.9 * seconds

    with _conductor_lock:
        if _conductor is None:
            _conductor = _SnoozeBot()
            _conductor.start()
    try:
        exc, targs, tkwargs = _conductor.snooze(
            seconds, minimum, watch_fds, condition, snoozeq
            ).get(True, seconds if (condition is None) else 24*3600)
        if exc is not None:
            raise(exc(*(targs or []), **(tkwargs or {})))  # Woken up?
    except Empty:
        pass


def wake_all(throw=None, targs=None, tkwargs=None):
    """
    Wake up all snoozing threads immediately, optionally causing them to
    raise an exception with the given args or kwargs.
    """
    try:
        _conductor.wake_all(throw, targs, tkwargs)
    except AttributeError:
        pass


if __name__ == '__main__':
    import sys, random

    counter = [3]
    def silly_condition(now):
        print('Checking if we are allowed to wake up...')
        counter[0] -= 1
        return (counter[0] < 1)

    def snoozer(seconds, mnm, fds, cond):
        def sn():
            try:
                print('%d: Snoozing for up to %ss' % (time.time(), seconds))
                snooze(seconds, minimum=mnm, watch_fds=fds, condition=cond)
                snooze(0.1)
                print('%d: Snoozing complete! (%s/%s)' % (time.time(), seconds, mnm))
            except:
                traceback.print_exc()
        return sn

    print('The time is %.2f' % time.time())
    try:
        threading.Thread(target=snoozer(2.2, None, None, silly_condition)).start()
        threading.Thread(target=snoozer(3600, 1, None, None)).start()
        snooze(1.5)
        threading.Thread(target=snoozer(20, None, [sys.stdin.fileno()], None)).start()
        snooze(8.5)
        wake_all(throw=IOError)
    except KeyboardInterrupt:
        print('Interrupted at %.2f' % time.time())
        wake_all(throw=KeyboardInterrupt, targs=('User aborted',))
    print('The time is %.2f' % time.time())
    time.sleep(1)

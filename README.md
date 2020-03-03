# python-snoozebot: A coordinated and interruptible time.sleep() replacement

This module implements a time.sleep() replacement, snoozebot.snooze(), which
is more suitable for use in multithreaded apps:

  * Threads can be woken up (optionally, with an exception) to facilitate
    prompt, clean shutdown.
  * Snoozing can be cut short if file descriptor(s) becomes readable
  * Snoozing threads can specify an interval of acceptable wakeup times,
    to facilitate grouping jobs together (thus hopefully playing nice with
    powersavings on modern/mobile CPUs).

## Usage:

    import snoozebot
    from snoozebot import Snoozer, snooze, wake_all

    # Interruptable sleep for 1 second
    snooze(1.0)

    # Sleep for anywhere between 1 minute and 1 hour
    snooze(60, 3600)

    # Sleep an hour, but wake up early for activity on sys.stdin
    reason = snooze(3600, watch_fds=[sys.stdin.fileno()])
    if reason[0] == snoozebot.WOKE_FD:
        data = reason[1].read()

    # Create a re-usable schedule which can be woken by other means
    sn = Snoozer(60, 3600, watch_fds=[sys.stdin.fileno()]))
    sn.snooze()

    # Other thread wakes us up...
    sn.reason(snoozebot.WOKE_CUSTOM, 'Good morning!').wake()

    # Just wake all snoozers
    wake_all()

    # Wake all snoozers with a KeyboardInterrupt!
    wake_all(KeyboardInterrupt, 'Time to die')


## Dependencies

You will need:

   * Python 2.7 or 3.x (author tested on 3.7)


## Bugs

This code will fail if firewalls prevent localhost from speaking UDP with
itself.


## Copyright, license, credits

This code is: (C) Copyright 2020, Bjarni R. Einarsson <bre@klaki.net>

Snoozebot is free software: you can redistribute it and/or modify
it under the terms of the GNU Lesser General Public License as
published by the Free Software Foundation, either version 3 of
the License, or (at your option) any later version.

Snoozebot is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU Lesser General Public
License along with snoozebot. If not, see <https://www.gnu.org/licenses/>.

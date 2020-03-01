# python-snoozebot: Smart sleep replacment for threaded apps

This app provides a replacement for time.sleep(), with the following properites:

   - Snooze tries to be battery-friendly and avoid busy waits
   - All snoozing threads can be woken up at once
   - Interrupted snoozers can be awoken with an exception
   - Threads can request they be woken up if a socket or file is ready to read
   - Snoozing threads can specify they're ok being woken up a bit early if that
     facilitates batching together of operations (fewer wakeup events)


## Dependencies

You will need:

   * Python 2.7 or 3.x (author tested on 3.7)


## Code example

    from snoozebot import snooze, wake_all

    # Thread 1
    snooze(1.5)  # Sleep for at least 1.5 seconds

    # Thread 2
    snooze(300, watch_fds=[mysocket.fileno()])

    # Thread 3
    wake_all(KeyboardInterrupt, 'Boo')


## Bugs

This code will fail if firewalls prevent localhost from speaking UDP with
itself.


## Copyright, license, credits

This code is: (C) Copyright 2020, Bjarni R. Einarsson <bre@mailpile.is>

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

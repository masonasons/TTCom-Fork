"""Sound player with queue support.

Copyright (C) 2011-2019 Doug Lee

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 3 of the License, or (at your
option) any later version.

This program is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
for more details.

You should have received a copy of the GNU General Public License along
with this program.  If not, see <http://www.gnu.org/licenses/>.

"""

import os
from subprocess import call, PIPE
import threading
from queue import Queue, Empty
from mplib.mycmd import MyCmd
callWithRetry = MyCmd.callWithRetry

def consumer():
	"""The consumer for the sound file queue. Runs in its own thread.
	"""
	while (True):
		lst = []
		# Wait for a file name.
		lst.append(queue.get())
		# Collect as many as are immediately available after that one.
		while True:
			try: lst.append(queue.get(block=False))
			except Empty: break
		cmd = ["play", "-q"]
		cmd.extend(lst)
		try: call(cmd, stdin=PIPE, stdout=PIPE, text=True)
		except OSError: pass

def sendFile(fname):
	"""Add a file to the play queue.
	"""
	queue.put(fname)

queue = Queue()
th = threading.Thread(target=consumer)
# Let the play queue die quietly on program exit.
th.daemon = True
th.start()


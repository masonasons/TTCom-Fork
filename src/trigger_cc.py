"""Load custom trigger code if it exists.
Also defines a support class for such code.
Hard-codes the Python module file name for this code.

How to make custom triggers:
Make a Python file called ttcom_triggers.py. The following directions apply there.

Import trigger_cc's TriggerBase class so you can inherit from it:
	from trigger_cc import TriggerBase
It is also useful to include these:
	from time import sleep
	from mplib.mycmd import say as mycmd_say
and for any reference to configuration options:
	from conf import conf

For server srv triggers, you can define class Trigger_srv(TriggerBase).
For multi-server triggers, define class Trigger(TriggerBase).
(This of course can also handle server-specific triggers.)
Trigger_srv classes are instantiated before Trigger().

In a Trigger class, include this:
	def __init__(self, *args, **kwargs):
		super(Trigger, self).__init__(*args, **kwargs)
		# Whatever event checking/actions, i.e., triggers, you need.

A Trigger object is instantiated when an event fires and released when
its trigger processing is completed.

Properties given via TriggerBase:
	event: The event that just fired as a ParmLine object.
		event.event is the event keyword, event.parms is an AttrDict of parameters.
		So event.parms.userid, event.parms.udpaddr, etc. work.
	server: The TeamTalkServer object that fired the event.
		server.users, server.info, etc.
		self.server.users[self.event.parms.userid]: Record for this event's user.
			(Sometimes userid will need to be srcuserid, destuserid, etc.)
	runCommand: A function that can execute a user-level command string.
		self.runCommand("system play ...")
	myIP: The IP address of this client on the server that fired this event.
		Warning: myIP may be None before the "loggedin" event is completed.

Examples of what can be done from Trigger methods:
self.runCommand("system play ...")
self.server.outputFromEvent("Blah that prints only if server isn't silenced.")
self.server.errorFromEvent("blah that prints even for silent servers.")
mycmd_say("Blah") but warning, that might suspend TTCom a while.
time.sleep(0.5)
self.server.send[WithWait]("kick userid=%s" % (event.parms.userid))
(That one can serve to "ban" someone by more than just IP address.)

See also methods in the TriggerBase class in this module.
See also ttapi classes for info on server, user, and other object types.

Copyright (C) 2011-2019- Doug Lee

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

import importlib

class TriggerBase(object):
	def __init__(self, server, event, runCommand):
		self.server = server
		self.event = event
		self.runCommand = runCommand
		# This can be None before login is completed.
		try: self.myIP = self.server.me.ipaddr
		except AttributeError: self.myIP = None

	def nameFromID(self, userid):
		"""Return a printable user id string from a userid.
		"""
		return self.server.nonEmptyNickname(self.server.users[userid], shortenFacebook=True)

try:
	customCode
	importlib.reload(customCode)
	print("Custom trigger code reloaded.")
except NameError:
	try:
		import ttcom_triggers as customCode
		print("Custom trigger code imported.")
	except ImportError:
		pass

def apply(server, parmline, runCommand):
	# Is there custom code at all?
	try: customCode
	except NameError: return
	# How about a server-specific Trigger_* class?
	func = "customCode.Trigger_" +server.shortname
	try: func = eval(func)
	except AttributeError: func = None
	if func: func(server, parmline, runCommand)
	# And finally, an all-server Trigger class?
	try: customCode.Trigger
	except AttributeError: return
	customCode.Trigger(server, parmline, runCommand)

"""Triggers, for actions tied to TeamTalk server events.

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

import re
import threading
from time import sleep
from parmline import ParmLine
from mplib.mycmd import say as mycmd_say
from collections import OrderedDict
import trigger_cc
import importlib

class Struct(object):
	def __hash__(self):
		"""For sets.
		"""
		return hash(self.__dict__)

	def __eq__(self, other):
		"""Makes comparison for equality work reasonably.
		"""
		return self.__dict__ == other.__dict__
	def __ne__(self, other):
		"""Makes comparison for equality work reasonably.
		"""
		return not self.__eq__(other)


class Trigger(object):
	"""Match/action triggers for a server.
	All objects in this class are created by Triggers objects.
	"""
	def __init__(self, parent, name):
		# Parent is the Triggers object that created this one.
		# name is a unique name for this trigger.
		self.parent = parent
		self.name = name
		self.matches = OrderedDict()
		self.actions = OrderedDict()

	def __hash__(self):
		"""For sets.
		"""
		return hash(self.name)

	def __eq__(self, other):
		"""Makes comparison for equality work reasonably.
		Action and match lists must match; parents need not.
		"""
		return (
			self.name == other.name
			and self.matches == other.matches
			and self.actions == other.actions
		)
	def __ne__(self, other):
		"""Makes comparison for equality work reasonably.
		"""
		return not self.__eq__(other)

	def addMatch(self, matchSpec, matchName=""):
		"""Add one match to this trigger.
		matchSpec should be a ParmLine where the event and parameter values are regexps.
		matchName can name the match arbitrarily.
		"""
		if not matchName: matchName = "(match%03d)" % (len(self.matches)+1)
		match = Struct()
		match.name = matchName
		match.value = matchSpec
		# This allows replacements by exact name match.
		self.matches[matchName] = match

	def addAction(self, actionSpec, actionName=""):
		"""Add one action to this trigger.
		actionSpec should be an action to perform.
		actionName can name the action arbitrarily.
		"""
		if not actionName: actionName = "(action%03d)" % (len(self.actions)+1)
		action = Struct()
		action.name = actionName
		action.value = actionSpec
		# This allows replacements by exact name match.
		self.actions[actionName] = action

	def apply(self, parmline):
		"""Apply actions if and only if there is a match.
		"""
		for match in self.matches.values():
			if not self._isMatch(match, parmline): continue
			uinfo = ""
			if parmline.parms.get("userid"):
				uinfo = " (userid %s)" % (parmline.parms.userid)
			# Use errorFromEvent instead of outputFromEvent so it's not
			# silent if the server is marked silent.
			self.parent.server.errorFromEvent("%s triggers %s %s%s" % (
				parmline.event,
				self.name,
				match.name or match.value,
				uinfo
			))
			for action in self.actions.values():
				actionData = Struct()
				actionData.parmline = parmline
				actionData.match = match
				actionData.action = action
				self._doAction(actionData)
			return True
		return False

	def _isMatch(self, match, eventline):
		"""Return True on a match.
		match is a name,value struct where value is a
		ParmLine where the event and parameter values are regexps.
		eventline is an actual event line as the name implies.
		Matching is forced to be case-insensitive.
		Matches also implicitly start with ^ and end with $,
		so they must match the entire event or parameter value.
		Special cases of match.value:
			line match=...: A regular expression match against the whole line.
		"""
		m = match.value
		# Whole-line matches.
		# Format: line match=<re>.
		if m.event.lower() == "line" and m.parms.get("match"):
			regexp = m.parms["match"]
			if re.match('^'+regexp+'$', eventline.initLine, re.IGNORECASE):
				return True
			return False
		# Normal RE event match and parms.
		if not re.match('^'+m.event+'$', eventline.event, re.IGNORECASE):
			return False
		# matchKey and matchRE are keys and regexps to match against
		# event parameter values.
		for matchKey in m.parms:
			matchRE = m.parms[matchKey]
			if matchKey == "address":
				# This one is special/magical:
				# It tries to match against any ".*addr" eventline key,
				# and it uses special logic, not regexp logic, to match.
				# First collect the address keys, e.g., ipaddr/udpaddr.
				addrkeys = [k for k in eventline.parms.keys() if k.endswith("addr")]
				# Then check each for a match.
				matched = False
				for ak in addrkeys:
					addr = eventline.parms[ak]
					if self._matchAddress(matchRE, addr):
						matched = True
						break
				if not matched: return False
			# Not a "magical" address match.
			elif matchKey not in eventline.parms: return False
			elif not re.match('^'+matchRE+'$', eventline.parms[matchKey], re.IGNORECASE):
				return False
		return True

	def _matchAddress(self, matchval, addr):
		"""Indicate if the given address matches matchval.
		Matchval should be a full address or the first part of one.
		Addr should be an event parameter value.
		Helper for isMatch().
		"""
		# Remove any extra brackets/port, often found on UDP address values.
		if "[" in addr and "]" in addr:
			addr = re.findall(r'^\[(.*?)]', addr)[0]
		# Allow IPV4 and IPV6 addresses with the same content to match.
		if not matchval.startswith(":"):
			addr = re.sub(r'^(?i)::ffff:', '', addr)
		# Remove any trailing port from an IPV4 address.
		addr = re.sub(r':\d+$', '', addr)
		# If this is a partial address, ad a dott to avoid partial number matches.
		if len(matchval.split(".")) < 4:
			matchval += "."
		# And finally do a left-side simple match.
		return addr.startswith(matchval)

	def _doAction(self, actionData):
		"""Perform one action.
		actionData properties:
			eventline: The ParmLine for the event that fired the trigger.
			match: The name,value struct for the match that matched this event.
				match.value is the ParmLine that matched this event.
			action: The name,value struct for the action to perform.
				action.value is the actual command to perform.
		match.name and action.name may be null.
		Substitutions: A string in an action like %(userid)
		becomes something like userid="123" in the actually sent command.
		If the action begins with "send," it is not sent through the
		command processor but sent directly to this server, after any
		substitutions.
		If the command begins with "say," the rest of the line is
		spoken if possible.
		"""
		a = actionData.action.value
		parmline = actionData.parmline
		# Include any parameters from the matched line.
		# We do this manually rather than with % so we can control
		# what happens when the author asks for something that doesn't exist.
		# We throw an error in such a case.
		ms = lambda m: self._doSubs(m, parmline.parms)
		a = re.sub(r'%\((\S+?)\)', ms, a)
		sendFunc = None
		if a.lower().startswith("send "):
			sendFunc = self.parent.server.send
		elif a.lower().startswith("sendwithwait "):
			sendFunc = self.parent.server.sendWithWait
		if sendFunc:
			# Remove the "send[WithWait]" part, then send it.
			a = a.split(None, 1)[1]
			sendFunc(ParmLine(a))
			return
		if a.lower().startswith("say "):
			# Remove the "say" part, then say it.
			a = a.split(None, 1)[1]
			mycmd_say(a)
			return
		# Make the action apply to the right server.
		a = "server %s %s" % (self.parent.server.shortname, a)
		# Run the command as if typed by the user.
		self.parent.runCommand(a)

	def _doSubs(self, m, parms):
		"""Do substitutions like for %(userid) in actions.
		"""
		k = m.groups()[0]
		excludeParmName = False
		if k.startswith("!"):
			excludeParmName = True
			k = k[1:]
		val = parms[k]
		if not excludeParmName:
			val = '%s="%s"' % (k, val)
		return val

class Triggers(object):
	"""Match/action triggers for a server.
	"""
	def __init__(self, commandFunc):
		self.runCommand = commandFunc
		self.triggers = OrderedDict()
		self.thr = None
		self._q = []

	def __hash__(self):
		"""For sets.
		"""
		return hash(self.triggers)

	def __eq__(self, other):
		"""Makes comparison for equality work reasonably.
		"""
		return self.triggers == other.triggers
	def __ne__(self, other):
		"""Makes comparison for equality work reasonably.
		"""
		return not self.__eq__(other)

	def get(self, name):
		"""Get and/or make the trigger object for the given name.
		"""
		if not self.triggers.get(name):
			self.triggers[name] = Trigger(self, name)
		return self.triggers[name]

	def addMatch(self, triggerName, matchSpec, matchName=""):
		"""Add one match to a trigger.
		triggerName should uniquely identify a set of matches and associated
		actions, to separate them from any other match/action sets.
		matchSpec should be a ParmLine where the event and parameter values are regexps.
		matchName can name the match arbitrarily.
		"""
		trigger = self.get(triggerName)
		trigger.addMatch(matchSpec, matchName)

	def addAction(self, triggerName, actionSpec, actionName=""):
		"""Add one action to a trigger.
		triggerName should uniquely identify a set of matches and associated
		actions, to separate them from any other match/action sets.
		actionSpec should be an action to perform.
		actionName can name the action arbitrarily.
		"""
		trigger = self.get(triggerName)
		trigger.addAction(actionSpec, actionName)

	def apply(self, parmline):
		"""Apply actions where there is a match.
		As many match/action sets as match will have their actions applied.
		"""
		# config file triggers first.
		[trigger.apply(parmline) for trigger in self.triggers.values()]
		# Then custom code triggers if any.
		trigger_cc.apply(self.server, parmline, self.runCommand)

	@classmethod
	def loadCustomCode(cls):
		"""Load custom trigger code if it exists.
		"""
		importlib.reload(trigger_cc)

	def queue(self, parmline):
		"""Queues a trigger check instead of applying it immediately.
		"""
		self._q.append(parmline)
		if not self.thr:
			thr = threading.Thread(target=self._queueWatch)
			thr.daemon = True
			thr.start()

	def _queueWatch(self):
		"""Watch the queue for things to do.
		Runs in its own thread as started by queue().
		"""
		while True:
			if not self._q:
				sleep(0.5)
				continue
			parmline = self._q.pop(0)
			self.apply(parmline)

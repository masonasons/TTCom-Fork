"""ParmLine - an object for managing dual-format parameter lines.
Lines can come in as a text line, an event and parameters, or a mixture.

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

"""
Expected grammar of a TeamTalk TCP protocol line:
line = event | event ' ' parms
event = (event name, all letters but TTCom allows underscores for internal events)
parms = parm | parms ' ' parm
parm = name '=' value
name = (parameter name, all letters but numbers also allowed except for first char)
value = int | string | list
int = (positive or negative number or 0, no decimals)
string = '"' (UTF8-encoded string with \"=" \\=backslash \r=return \n=newline) '"'
list = '[' ']' | '[' values ']'
values = int | int ',' values

Note: List values are currently assumed to be ints only, based on impirical evidence.

Also, utf-8 encoding is handled at the borders (when bytes go to/from files and servers).
"""

import shlex, re
from tt_attrdict import AttrDict

class Parser(object):
	"""Parser for one TeamTalk text protocol line.
	Also used to parse lines from TTCom users sometimes.
	"""
	def __init__(self, line):
		self.line = line

	def next(self, relaxed=False):
		"""Return the next parameter from the line and remove it from the line.
		If relaxed is True, non-conforming keywords like -m are allowed.
		Otherwise, strict TT protocol adherance is required except that keyword identifiers may start with an underscore.
		Keywords that violate protocol and are accepted with relaxed=True consist of the next string of non-whitespace characters or a quoted string.
		"""
		line = self.line.strip()
		self.line = line
		if not line: raise StopIteration
		parm = ""
		kw = re.match(r'^[a-zA-Z_][a-zA-Z0-9_-]*', line)
		if kw is None:
			if not relaxed: raise ValueError("Line not parsable; remaining text: " +line)
			kw,line = self._nextString(line)
		else:
			kw = kw.group()
			line = line[len(kw):]
		self.line = line
		if not line or line[0] != "=":
			return KeywordParm(kw)
		line = line[1:]  # discard = sign
		# Note that parameter specs like username= (with nothing after the =) are not supported, nor have they been seen to date. [DGL, 2017-04-04, TeamTalk5Classic 5.2.1.4781]
		if line[0] == "[":
			# A list of ints.
			val = re.match(r'^\[[^]]*\]', line).group()
			self.line = line[len(val):]
			return ListParm(kw, val)
		elif line[0] in "-0123456789":
			# An int, possibly negative.
			val = re.match(r'^[\d-][\d]*', line).group()
			self.line = line[len(val):]
			return IntParm(kw, val)
		# All we have left are strings, always quoted by TeamTalk but permitted here without quotes for TTCom user convenience.
		val,line = self._nextString(line)
		self.line = line
		return StringParm(kw, val)

	def _nextString(self, line):
		"""Pull the next string value from line and return val,line, where this line is the original line with val removed.
		"""
		quoting = False
		if line[0] == '"':
			line = line[1:]
			quoting = True
		val = ""
		while line:
			ch,line = line[0], line[1:]
			if ch == "\\":
				val += ch +line[0]
				line = line[1:]
				continue
			if quoting:
				if ch == '"':
					quoting = False
					break
				else: val += ch
				continue
			elif ch in " \t\r\n":
				# Put back just for consistency with above code.
				line = ch +line
				break
			val += ch
		return val,line

	def getParms(self, relaxed=False):
		"""Convert line into its parameters, nondestructively, and return the resulting list.
		If relaxed is True, non-conforming keywords like -m are allowed.
		Otherwise, strict TT protocol adherance is required except that keyword identifiers may start with an underscore.
		Keywords that violate protocol and are accepted with relaxed=True consist of the next string of non-whitespace characters.
		"""
		parms = []
		line = self.line
		try:
			while self.line:
				nextParm = self.next(relaxed)
				parms.append(nextParm)
		finally: self.line = line
		return parms

class TTParm(str):
	"""Base class for parameter objects.
	"""
	pass

class TTParms(list):
	"""A list of TT parameters easily convertable to a protocol-ready string.
	Can be initialized as either a string to parse or a sequence already parsed or hand-made.
	If passing a line that may contain non-TT-protocol elements (like -m), set relaxed=True.
	Objects in this class store everything as a list though, not string or unicode parameter lines.
	The list may be manipulated per usual for Python lists; the string/unicode value is not static.
	"""
	def __init__(self, init=None, relaxed=False):
		if isinstance(init, str):
			list.__init__(self)
			self.extend(Parser(init).getParms(relaxed))
		else:
			list.__init__(self, init)

	def __str__(self):
		return " ".join(map(str, self))

class KeywordParm(TTParm):
	"""A parameter without a value; just a keyword. Used for the event and command keywords in TeamTalk.
	"""
	def __init__(self, kw):
		self.name = kw
		self.value = None

class IntParm(TTParm):
	"""An integer parameter.
	"""
	def __new__(cls, kw, val):
		txt = "{0}={1}".format(kw, val)
		self = TTParm.__new__(cls, txt)
		self.name = kw
		self.value = int(val)
		return self

class StringParm(TTParm):
	"""A string parameter. Quotes not included on call.
	If passing an unencoded string (from userland), add rawValue=True.
	"""
	def __new__(cls, kw, val, rawValue=False):
		if rawValue:
			raw = val
			encoded = val.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
		else:
			raw = val.replace("\\\\", "\\").replace("\\r", "\r").replace("\\n", "\n")
			encoded = val
		txt = '{0}="{1}"'.format(kw, encoded)
		self = TTParm.__new__(cls, txt)
		self.name = kw
		self.value = raw
		return self

class ListParm(TTParm):
	"""A list parameter.
	"""
	def __new__(cls, kw, val):
		txt = "{0}={1}".format(kw, val)
		self = TTParm.__new__(cls, txt)
		self.name = kw
		self.value = val[1:-1].split(",")
		return self

class ParmLine(object):
	"""A dual-format parameter/line object.
	Construct with a line and parameters or just a line.
	Access .line for the raw text or .event and .parms for the broken-out version.
	.initLine and .initParms are what was passed to the constructor.
	Caveats:
		- Parameters in line may be reordered from what was passed.
		- This class does not handle duplicate parameter names on a line.
	"""

	def __init__(self, line, parms={}):
		"""Set up a line.
		Line is an event name with possible key=value parameters after it.
		parms is a dict of parameters and may be empty.
		If parms includes parameters that are also in line, parms governs.
		"""
		self.initLine = line
		line = str(line)
		self.initParms = AttrDict(parms)
		line,parms1 = self.splitline(line)
		parms1.update(parms)
		self.event = line
		self.parms = parms1
		self.line = self.makeline(self.event, self.parms)

	def __hash__(self):
		"""For sets.
		"""
		return hash(self.event +" ".join(self.parms) +self.line)

	def __eq__(self, other):
		"""Implements ==.
		"""
		return (self.event == other.event
		and self.parms == other.parms
		and self.line == other.line
		)
	def __ne__(self, other):
		"""Makes comparison for equality work reasonably.
		"""
		return not self.__eq__(other)

	def splitline(self,  line):
		"""Split one line up into its command keyword and its parameters.
		Returns event,parms, where parms is an AttrDict.
		Rules honored and allowed in input lines:
			- Values containing spaces are quoted with "".
			- A quote is escaped with a backslash.
			- A backslash is escaped by being doubled.
			- Unquoted space separates parameters.
			- The first parameter is a keyword with no value assignment.
			- All other parameters are of the form keyword=value.
		"""
		parts = shlex.split(line.strip())
		if not parts: return None,AttrDict()
		event = parts.pop(0)
		if "=" in event:
			raise ValueError("No event keyword")
		parms = AttrDict()
		for part in parts:
			if "=" in part: k,v = part.split("=", 1)
			else: k,v = (part,None)
			parms[k] = v
		return event,parms

	def makeline(self, event, parms):
		"""Build a line from event name and parms.
		Inverse of splitline().
		"""
		line = event
		for k,v in parms.items():
			v = self._fixParm(v, True)
			line += " %s=%s" % (k, v)
		return line

	def _fixParm(self, parm, quoteStrings=False):
		"""Fix up parms, events, etc., for makeline().
		"""
		if str(parm).isdigit() and " " not in str(parm): return str(parm)
		if parm is None:
			if quoteStrings: return '""'
			else: return ""
		parm = parm.replace('"', r'\"').replace(r"\'", "'")
		if quoteStrings: parm = '"' +parm +'"'
		return parm

	def __str__(self):
		"""Makes .line the default property in effect.
		"""
		return str(self.line)

	def __add__(self, other):
		return self.line +str(other)


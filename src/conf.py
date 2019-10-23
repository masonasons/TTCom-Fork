"""TTCom configuration class.

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

import os, sys
from collections import OrderedDict
import iniparse

class Conf(object):
	"""
	Configuration object.
		conf = Conf()
		conf.option(optname[, val]) --> optval for getting/setting arbitrary options.
		conf.ininame for name of config file managed by this program.
	"""
	def machineType(self):
		"""Returns "mac", "linux, windows," or sys.platform."""
		plat = sys.platform.lower()
		if plat[:3] in ["mac", "dar"]:
			return "mac"
		elif "linux" in plat:  # e.g., linux2
			return "linux"
		elif "win" in plat:  # windows, win32, cygwin
			return "windows"
		return plat

	def __init__(self, ininame):
		self.ininame = ininame
		self.inipath = self.ininame
		self.plat = self.machineType()
		self._sectsDone = set()

	def opt(self, sSect, sOpt, newval=None):
		"""
		Get or set an option in any section of the ini file.
		Intended for internal use in this class.
		"""
		c = iniparse.ConfigParser()
		c.read(self.inipath)
		curval = self.getopt(c, sSect, sOpt, "")
		if newval is None: return curval
		try: c.set(sSect, sOpt, newval)
		except iniparse.NoSectionError:
			c.add_section(sSect)
			c.set(sSect, sOpt, newval)
		c.write(open(self.inipath, "w"))
		return self.opt(sSect, sOpt)

	def option(self, sOpt, newval=None, section="Options"):
		"""
		Get or set an option in the Options or given section of the ini file.
		Returns the current value whether or not it is first changed.
		"""
		return self.opt(section, sOpt, newval)

	def getopt(self, c, sSect, sOpt, dfl=""):
		"""
		Return the requested value or the given default value if not found.
		"""
		try:
			sResult = c.get(sSect, sOpt, raw=True)
		except (iniparse.NoSectionError, iniparse.NoOptionError):
			sResult = dfl
		return sResult

	def sections(self):
		"""
		Return the list of ini file section names.
		"""
		c = iniparse.ConfigParser()
		c.read(self.inipath)
		return c.sections()

	def servers(self):
		"""
		Return an ordered dict of the servers defined in the ini file.
		Each server is a list of parameters provided for it.
		Parameter lists are lists of key,value tuples.
		"""
		c = iniparse.RawConfigParser()
		c.read(self.inipath)
		servers = c.sections()
		servers = [s for s in servers if s.lower().startswith("server ")
			and s.lower() != "server defaults"]
		results = OrderedDict()
		for server in servers:
			name = server.split(None, 1)[1]
			if name in results:
				raise ValueError("Server %s defined more than once" % (name))
			items = []
			results[name] = items
			self._sectsDone.clear()
			try: self._includeItems(items, "server defaults", c)
			except (iniparse.NoSectionError, iniparse.NoOptionError): pass
			self._includeItems(items, server, c)
		self._sectsDone.clear()
		return results

	def _includeItems(self, lst, sectname, c):
		"""Collect key/value pairs from sectname and process any include= lines.
		include=s1,s2,s3 is how to include sections into a section.
		Those sections must be named [include s1] etc.
		There is protection against recursive inclusions.
		Trying to include a nonexistent section throws an error.
		The results are added to lst as k/v pairs.
		Included k/v pairs replace their include= lines,
		which allows file authors to decide what overrides what
		by the order of lines in a section.
		"""
		if sectname in self._sectsDone: return
		self._sectsDone.add(sectname)
		items = c.items(sectname)
		for item in items:
			if item[0] == "include":
				incs = item[1].split(",")
				for inc in incs:
					inc = inc.strip()
					inc = "include " +inc
					self._includeItems(lst, inc, c)
				continue
			lst.append(item)

conf = Conf("ttcom.conf")

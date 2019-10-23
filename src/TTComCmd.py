"""Command implementation module for TTCom.

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

import gzip
import time
from datetime import datetime
import os, sys, re, socket, shlex
import threading
from tt_attrdict import AttrDict
from ttapi import TeamtalkServer
import player
from mplib.mycmd import MyCmd, say as mycmd_say, classproperty, ArgumentParser, CommandError
from mplib.TableFormatter import TableFormatter
from conf import conf
from triggers import Triggers
from parmline import ParmLine, TTParms, KeywordParm, IntParm, StringParm, ListParm
from mplib.textblock import TextBlock

def callWithRetry(func, *args, **kwargs):
	"""For Cygwin 1.8 on Windows:
	Forks can ffail randomly in the presence of things like antivirus software,
	because DLLs attaching to the process can cause address mapping problems.
	This function retries such calls so they don't fail.
	"""
	i = 1
	while i <= 50:
		try:
			return func(*args, **kwargs)
		except OSError as e:
			i += 1
			print("Retrying, attempt #" +str(i))
	print("Retry count exceeded.")

class NullLog(object):
	def write(self, *args, **kwargs):
		return

class MyTeamtalkServer(TeamtalkServer):
	def __init__(self, parent, *args, **kwargs):
		# This is a TTComCmd object.
		self.parent = parent
		self.silent = 0
		self.hidden = 0
		self.encrypted = False
		self.logstream = parent.servers.logstream
		# TODO: triggers can't be set here because we don't have a
		# command processor object.
		TeamtalkServer.__init__(self, *args, **kwargs)

	def outputFromEvent(self, line, raw=False):
		"""For event output. See output() for details.
		Only outputs for current and non-silenced servers,
		"""
		if self.silent > 1:
			# Unconditional silence, even if it's the current server.
			return
		if self.silent and self.shortname != self.parent.curServer.shortname:
			# Silence unless it's the current server.
			return
		TeamtalkServer.outputFromEvent(self, line, raw)

	def hookEvents(self, eventline, afterDispatch):
		"""Called on each event with the event's parmline as a parameter.
		This method is called twice per event:
		once before and once after the event is dispatched.
		The afterDispatch parameter indicates which type of call is occurring.
		"""
		TeamtalkServer.hookEvents(self, eventline, afterDispatch)
		if not afterDispatch:
			self.logstream.write("%s\n  %s: %s\n" % (
				datetime.now().ctime(),
				self.shortname,
				eventline.initLine.rstrip()
			))
			return
		if eventline.event in ["userbanned", "useraccount"]:
			# These events are responses to listing commands and
			# should not trigger activity.
			return
		try: self.triggers.apply(eventline)
		except Exception as e:
			self.output("Trigger failure: %s" % (str(e)))

class Servers(dict):
	def __init__(self, parent):
		# This is a TTComCmd object.
		self.parent = parent
		self.logfilename = "ttcom.log"
		self.logstream = NullLog()
		if os.path.exists(self.logfilename):
			self.logstream = open(self.logfilename, "a", encoding="utf-8")
		else:
			# If the file exists, make sure it's not damaged before appending to it.
			# Otherwise the new entries may be hard to read.
			if os.path.exists(self.logfilename +".gz"):
				try:
					ftmp = gzip.open(self.logfilename+".gz", encoding="utf-8")
					for l in ftmp: pass
				except (IOError, gzip.zlib.error):
					exit("Rename or expand the log file first.")
					del ftmp
			else:
				# No log file exists.
				return
			# It should now be safe to append to this file.
			self.logstream = gzip.open(self.logfilename+".gz", "a", encoding="utf-8")
		self.thFlusher = threading.Thread(target = self.flusher)
		self.thFlusher.daemon = True
		self.thFlusher.name = "flusher"
		self.thFlusher.start()
		self.logGlobalEvent("starting")

	def logGlobalEvent(self, event):
		self.logstream.write("%s\n  %s: %s\n" % (
			datetime.now().ctime(),
			"*TTCom*",
			event
		))

	def flusher(self):
		"""Flushes the log periodically.
		Runs in the Flusher() thread.
		"""
		while True:
			time.sleep(5.0)
			self.logstream.flush()

	def add(self, newServer):
		"""Add a new server.
		"""
		shortname = newServer.shortname
		if shortname in self:
			self.remove(shortname)
		self[shortname] = newServer

	def remove(self, shortname):
		"""Stop and remove a server connection.
		Shortname may be a shortname or an actual server object.
		"""
		if issubclass(type(shortname), TeamtalkServer):
			shortname = shortname.shortname
		server = self[shortname]
		server.disconnect()
		del self[shortname]

class TTComCmd(MyCmd):
	@classproperty
	def speakEvents(cls):
		"Whether to speak events."
		return conf.option("speakEvents")

	def __init__(self, noAutoLogins=False, logins=[]):
		if logins:
			noAutoLogins = True
		self.noAutoLogins = noAutoLogins
		self.servers = Servers(self)
		self._curShortname = ""
		MyCmd.__init__(self)
		TeamtalkServer.write = self.msg
		TeamtalkServer.writeEvent = self.msgFromEvent
		self.readServers(logins)

	@property
	def curServer(self):
		if not self._curShortname:
			raise CommandError("No current server has been set.")
		try: return self.servers[self._curShortname]
		except KeyError: raise CommandError("Server {0} is no longer in the server list.".format(self._curShortname))

	def precmd(self, line):
		"""Handles >-to-"server " translation.
		Also handles :/;-to-"summary " translation.
		"""
		l = line.strip()
		if l.startswith("?"): l = l[1:].lstrip()
		elif re.match(r'^help\s', l.lower()): l = l.split(None, 1)[1]
		if l and l[0] == ">":
			line = line.replace(">", "server ", 1)
		if l and l[0] == ":":
			line = line.replace(":", "summary ", 1)
		elif l and l[0] == ";":
			line = line.replace(";", "summary ", 1)
		return MyCmd.precmd(self, line)

	def readServers(self, logins=[]):
		waitFors = []
		curservers = conf.servers()
		curset = set(curservers.keys())
		oldset = set(self.servers.keys())
		anyDel = False
		for oldserver in oldset-curset:
			print("Deleting " +oldserver)
			del self.servers[oldserver]
			anyDel = True
		if anyDel and self._curShortname not in self.servers:
			ns = ""
			if len(self.servers): ns = list(self.servers.keys())[0]
			print("Current server is {0}".format(ns) if ns else "No current server")
			self._curShortname = ns
		for shortname,pairs in curservers.items():
			if not self._curShortname:
				self._curShortname = shortname
			host = ""
			tcpport = None
			loginParms = {}
			autoLogin = 0
			soundsdir=""
			soundvolume=0
			silent = 0
			hidden = 0
			encrypted = False
			triggers = Triggers(self.onecmd)
			doLogin = None
			for k,v in pairs:
				if k.lower() == "host":
					host = v
				elif k.lower() == "tcpport":
					tcpport = int(v)
				elif k.lower() == "autologin":
					if not int(self.noAutoLogins):
						autoLogin = int(v)
				elif k.lower() == "silent":
					silent = int(v)
				elif k.lower() == "soundsdir":
					soundsdir = v
				elif k.lower() == "soundvolume":
					soundvolume = int(v)
				elif k.lower() == "hidden":
					hidden = int(v)
				elif k.lower() == "encrypted":
					if v.lower() in ["1", "true"]: encrypted = True
					elif v.lower() in ["0", "false"]: encrypted = False
					else: encrypted  = False
				elif k.lower().startswith("match ") or k.lower().startswith("action "):
					which,what = k.split(None, 1)
					if "." in what:
						triggerName,subname = what.split(".", 1)
					else:
						triggerName,subname = what,""
					if which.lower() == "match":
						triggers.addMatch(triggerName, ParmLine(v), subname)
					else:  # action
						triggers.addAction(triggerName, v, subname)
				else:
					loginParms[k.lower()] = v
			newServer = MyTeamtalkServer(self, host, tcpport, shortname, loginParms)
			reconfig = False
			if autoLogin:
				newServer.autoLogin = autoLogin
			if silent:
				newServer.silent = silent
			if hidden:
				newServer.hidden = hidden
			if encrypted:
				newServer.encrypted = encrypted
			if soundsdir:
				newServer.soundsdir = soundsdir
			if soundvolume:
				newServer.sound_volume = soundvolume
			# TODO: This is an odd way to get this link made.
			triggers.server = newServer
			newServer.triggers = triggers
			if shortname in self.servers:
				oldServer = self.servers[shortname]
				if (oldServer.host != newServer.host
				or oldServer.tcpport != newServer.tcpport
				or oldServer.encrypted != newServer.encrypted
				):
					print("Changing connection information for " +shortname)
					oldServer.terminate()
					oldServer.disconnect()
					self.servers[shortname] = newServer
					doLogin = int(newServer.autoLogin)
					reconfig = True
					if doLogin: doLogin = newServer
				elif oldServer.loginParms != newServer.loginParms:
					print("Changing login information for " +shortname)
					oldServer.logout()
					oldServer.loginParms = newServer.loginParms
					doLogin = int(newServer.autoLogin)
					if doLogin: doLogin = oldServer
				elif newServer.autoLogin and not oldServer.autoLogin and oldServer.state != "loggedIn":
					doLogin = oldServer
				if oldServer.autoLogin != newServer.autoLogin:
					if not reconfig: print("autoLogin for %s changing to %d" % (shortname, newServer.autoLogin))
				oldServer.autoLogin = newServer.autoLogin
				if oldServer.silent != newServer.silent:
					if not reconfig: print("silent for %s changing to %d" % (shortname, newServer.silent))
				oldServer.silent = newServer.silent
				if oldServer.hidden != newServer.hidden:
					if not reconfig: print("hidden for %s changing to %d" % (shortname, newServer.hidden))
				oldServer.hidden = newServer.hidden
				if oldServer.triggers != newServer.triggers:
					if not reconfig: print("Updating triggers for %s" % (shortname))
				oldServer.triggers = newServer.triggers
				# TODO: Again, weird way to set this link up.
				oldServer.triggers.server = oldServer
			else:
				self.servers.add(newServer)
				doLogin = int(newServer.autoLogin)
				if doLogin: doLogin = newServer
			if doLogin and self.noAutoLogins:
				doLogin = None
			if not doLogin and shortname in logins:
				doLogin = newServer
			if doLogin:
				doLogin.login(True)
				waitFors.append(doLogin)
		halfsecs = 0
		incomplete = False
		while any([server.state != "loggedIn" for server in waitFors]):
			halfsecs += 1
			if halfsecs == 20:
				incomplete = True
				break
			time.sleep(0.5)
		time.sleep(0.5)
		Triggers.loadCustomCode()
		#self.do_shortSummary()
		unfinished = []
		for server in waitFors:
			if server.state != "loggedIn":
				unfinished.append(server.shortname)
		if len(unfinished):
			print("Servers that did not connect: " +", ".join(unfinished))
		if not len(self.servers):
			print("Warning: No servers defined. Make sure you have created and filled out the configuration file " +conf.inipath)

	def userMatch(self, u, checkAll=False):
		"""Match a user to what was typed/passed, asking for a
		selection if necessary. Returns a user object.
		The passed string is checked for containment in nickname,
		username, and userid fields. To match a userid exactly, use a
		number sign ("#") followed with no spaces by the userid;
		example: #247. If the userid matches a user, that user is used.
		"""
		# If checkAll is True, all servers' users are checked (not well tested or used).
		if checkAll:
			users = []
			list(map(lambda s: users.extend(s.users),
				self.servers
			))
		else:
			users = self.curServer.users
		if u.startswith("#") and u[1:].isdigit():
			users = [u1 for u1 in users.values() if u1.userid == u[1:]]
		else:
			users = [u1 for u1 in users.values() if u.lower() in self.curServer.nonEmptyNickname(u1, True).lower()]
		if checkAll:
			flt = lambda u1: u1.server.shortname +"/" +self.curServer.nonEmptyNickname(u1, True)
		else:
			flt = lambda u1: self.curServer.nonEmptyNickname(u1, True)
		return self.selectMatch(users, "Select a User", flt)

	def channelMatch(self, c, noPrompt=False):
		"""Match a channel to what was typed/passed, asking for a
		selection if necessary and allowed by the caller. Returns a channel object.
		If the channel spec given includes an equal sign (=), a channel is selected by property; e.g., chanid=5.
		Otherwise the passed string is checked for containment in the channel name.
		If c contains a slash (/), the full name is checked;
		otherwise just the final component of channel names are checked.
		A channel name of "/" always matches just the root channel (channelid 1).
		A channel name starting and ending with "/" must match a
		channel exactly, except for case.
		If noPrompt is passed and True, a KeyError is thrown if more than one channel matches.
		"""
		channels = self.curServer.channels
		if c == "/":
			return channels["1"]
		elif c.startswith("/") and c.endswith("/"):
			# Exact match (except for case) required.
			channels = [c1 for c1 in channels.values() if c.lower() == self.curServer.channelname(c1["channelid"]).lower()]
		elif "=" in c:
			# Specific parameter search like chanid=5.
			channels = [chan for chan in channels.values() if self.filterPasses(chan, [c])]
		elif "/" in c:
			# Containment match against full channel paths, case ignored.
			channels = [c1 for c1 in channels.values() if c.lower() in self.curServer.channelname(c1["channelid"]).lower()]
		else:
			# Match against channel names (no paths), case and final / ignored.
			channels = [c1 for c1 in channels.values() if c.lower() in self.curServer.channelname(c1["channelid"])[:-1].rpartition("/")[2].lower()]
		# selectMatch handles the 0 and 1 match cases properly without prompting.
		if not noPrompt or len(channels) <= 1:
			return self.selectMatch(channels, "Select a Channel",
				lambda c1: self.curServer.channelname(c1["channelid"])
			)
		# Too many matches when we can't prompt for a selection.
		raise CommandError("Error: More than one channel matched")

	def serverMatch(self, s):
		"""Match a server to what was typed/passed, asking for a
		selection if necessary. Returns a server object.
		Matches are for containment, but an exact match takes precedence;
		so "nick" matches "nick" even if "nick1" is also a server.
		"""
		servers = list(self.servers.keys())
		servers = [s1 for s1 in servers if s.lower() in s1.lower()]
		try: return self.servers[s]
		except KeyError: pass
		return self.servers[self.selectMatch(servers, "Select a Server")]

	def versionString(self):
		"""Return the version string for TTCom.
		"""
		return (
"""TeamTalk Commander (TTCom)
Copyright (c) 2011-2019 Doug Lee.

This program is covered by version 3 of the GNU General Public License.
This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it under
certain conditions.
See the file LICENSE.txt for further information.
The iniparse module is under separate license and copyright;
see that file for details.

TTCom version %ver%
""".strip()).replace("%ver%", conf.version)

	def do_about(self, line=""):
		"""Show the copyright and version information for TTCom.
		"""
		self.msg(self.versionString())

	def do_version(self, line=""):
		"""Shows the version of the currently selected server when connected.
		With an argument, shows the version of the indicated user's client, and client name if available.
		"""
		line = line.strip()
		server = self.curServer
		if not line:
			try: sver = server.info.version
			except: pass
			if not sver:
				raise CommandError("Server {0} version not available".format(server.shortname))
			self.msg("{0} server version {1}".format(server.shortname, sver))
			if server.state != "loggedIn":
				self.msg("Warning: Not logged in, version information may be out of date.")
			return
		# Client indicated.
		user = self.userMatch(line)
		if not user: return
		cname = user.get("clientname").strip()
		cver = user.get("version").strip()
		if cname and cver: cname = "%s version %s" % (cname, cver)
		elif cver: cname = "Version %s" % (cver)
		cname = "{0}:\n    {1}".format(
			self.curServer.nonEmptyNickname(user),
			cname
		)
		self.msg(cname)

	def do_vlist(self, line=""):
		"""List users sorted by TeamTalk packet protocol, client name, and client version number.
		-p[<num>] filter: -p0 means text-only clients, -p means voice-capable clients, -p<num> restricts to a particular packetprotocol.
		(TT5 supports only packetprotocol 1 at this time; TT4 supported several.)
		"""
		proto = None
		if line:
			if not line.startswith("-p"):
				self.msg("Unknown options: " +line)
				return
			line = line[2:].strip()
			if not line: proto = -1
			elif line.isdigit(): proto = int(line)
			else:
				self.msg("Unknown packet protocol number: " +line)
				return
		server = self.curServer
		server.summarizeVersions(proto)

	def do_server(self, line):
		"""Get or change the server to which subsequent commands will apply,
		or apply a specific command to a specific server without
		changing the current one.
		Usage: server [serverName [command]]
		Without arguments, just indicates which server is current.
		With one argument, changes the current server.
		With more arguments, runs command against a server without
		changing the current one.
		"""
		args = shlex.split(line)
		newServer = None
		if len(args) >= 1:
			newServer = self.serverMatch(args.pop(0))
		if len(args) == 0:
			if newServer: self._curShortname = newServer.shortname
			print("Current server is %s" % (self.curServer.shortname))
			return
		# A command to run against a specific server (newServer).
		oldname = self._curShortname
		# Reparse to avoid spacing issues.
		tmp,line = line.split(None, 1)
		try:
			self._curShortname = newServer.shortname
			self.onecmd(line)
		finally:
			self._curShortname = oldname

	def do_refresh(self, line=""):
		"""Refresh server info and update connections as necessary.
		"""
		line = line.strip()
		if not line:
			self.readServers()
			return
		shortnames = line.split()
		for shortname in shortnames:
			server = self.serverMatch(shortname)
			self.servers.remove(server)
			self.servers.add(server)

	def do_summary(self, line=""):
		"""Summarize the users and active channels on this or a given server.
		"""
		if line:
			server = self.serverMatch(line)
		else:
			server = self.curServer
		server.summarizeChannels()

	def do_allSummarize(self, line=""):
		"""Summarize user/channel info on all connected servers.
		Servers marked hidden in the config file are omitted.
		"""
		if len(self.servers) == 0:
			print("No servers.")
			return
		offs = {}
		empties = []
		sums = []
		serverCount = 0
		stateCounts = {}
		for shortname in sorted(self.servers):
			server = self.servers[shortname]
			stateCounts.setdefault(server.state, 0)
			stateCounts[server.state] += 1
			serverCount += 1
			if server.hidden: continue
			if server.state != "loggedIn":
				offs.setdefault(server.state, [])
				offs[server.state].append(shortname)
			elif len(server.users) <= 1:
				# 1 allows for this user.
				empties.append(shortname)
			else:
				sums.append(shortname)
		if len(offs):
			for k in sorted(offs.keys()):
				print("%s: %s" % (
					k,
					", ".join(offs[k])
				))
		if len(empties):
			print("No users: " +", ".join(empties))
		for shortname in sums:
			server = self.servers[shortname]
			server.summarizeChannels()
		print("Server count {0:d}: {1}".format(
			serverCount,
			", ".join(["{0:d} {1}".format(stateCounts[state], state) for state in stateCounts])
		))

	def do_shortSummary(self, line=""):
		"""Short summary of who's on all logged-in servers with people.
		Servers marked hidden in the config file are omitted.
		"""
		if len(self.servers) == 0:
			print("No servers.")
			return
		offs = {}
		sums = []
		serverCount = 0
		stateCounts = {}
		for shortname in sorted(self.servers):
			server = self.servers[shortname]
			stateCounts.setdefault(server.state, 0)
			stateCounts[server.state] += 1
			serverCount += 1
			if server.hidden: continue
			if server.state != "loggedIn":
				if server.state == "disconnected" and not server.autoLogin:
					continue
				state = server.state
				if server.conn and server.conn.state and server.conn.state != state:
					state += self.conn.state
				offs.setdefault(state, [])
				offs[state].append(shortname)
			elif len(server.users) <= 1:
				# 1 allows for this user.
				continue
			else:
				sums.append(shortname)
		if len(offs):
			for k in sorted(offs.keys()):
				print("%s: %s" % (
					k,
					", ".join(offs[k])
				))
		for shortname in sums:
			server = self.servers[shortname]
			self.oneShortSum(server)
		print("Server count {0:d}: {1}".format(
			serverCount,
			", ".join(["{0:d} {1}".format(stateCounts[state], state) for state in stateCounts])
		))

	def oneShortSum(self, server):
		"""Short-form summary for one server.
		"""
		# Users other than me and that are actuallly in a channel.
		users = [u for u in server.users.values() if (u.get("channelid") or u.get("chanid"))
			and u.userid != server.me.userid]
		if not len(users):
			return
		users = [server.nonEmptyNickname(u, shortenFacebook=True) for u in users]
		users.sort(key=lambda u: u.lower())
		line = "%s (%d): %s" % (
			server.shortname,
			len(users),
			", ".join(users)
		)
		print(line)

	def do_join(self, line):
		"""Join a channel.
		Usage: join channelname [password]
		channelname and/or password can contain spaces if quoted.
		Channel / always refers to the root channel.
		A channel starting and ending with / must match exactly except for letter casing.
		A channel containing a / is matched against all full channel names (path included).
		Otherwise the channel is matched against only the actual channel names, without paths.
		This command will not create temporary channels as was once true in TeamTalk 4.
		"""
		args = shlex.split(line)
		channel,password = "",""
		if args: channel = args.pop(0)
		if args: password = args.pop(0)
		channel = self.channelMatch(channel)
		if self.curServer.is5():
			self.do_send('join chanid=%s password="%s"' % (channel.chanid, password))
		else:
			self.do_send('join channel="%s" password="%s"' % (channel.channel, password))

	def do_leave(self, line):
		"""Leave a channel.
		Usage: leave [channelname]
		channelname can be multiple words and can optionally be quoted.
		channelname can also be omitted to leave the current channel.
		"""
		if not line.strip():
			self.do_send("leave")
			return
		line = self.dequote(line)
		ch = self.channelMatch(line)
		if self.curServer.is5():
			self.do_send('leave channel=' +ch.chanid)
		else:
			self.do_send('leave channel="' +ch.channel +'"')

	def do_nickname(self, line):
		"""Set a new nickname or check the current one.
		"""
		if line:
			line = self.dequote(line)
			self.do_send("changenick nickname=\"%s\"" % (line))
			return
		nick = self.curServer.me.nickname
		print("You are now %s" % (
			self.curServer.nonEmptyNickname(self.curServer.me)
		))

	def do_connect(self, line=""):
		"""Connect to a server without logging in.
		"""
		self.curServer.connect()

	def do_disconnect(self, line=""):
		"""Disconnect from a server.
		"""
		# Sending "quit" can make other clients notice the disconnect sooner.
		self.curServer.send("quit")
		time.sleep(0.5)
		if self.curServer.state != "disconnected":
			self.curServer.disconnect()

	def do_login(self, line=""):
		"""Log into a server, connecting first if necessary.
		"""
		self.curServer.login()

	def do_logout(self, line=""):
		"""Log out of a server.
		"""
		self.curServer.logout()

	def do_broadcast(self, line):
		"""Send a broadcast message to all people on a server,
		even those who are currently not in a channel. The message
		shows up in the main message window for each user.
		Example usage: Broadcast Server going down in five minutes.
		This command requires the Broadcast user right on TT5 servers and admin privileges on TT4 servers.
		"""
		if not line:
			print("No broadcast message specified.")
			return
		line = self.dequote(line)
		self.do_send('message type=3 content="%s"' % (line))

	def do_move(self, line):
		"""Move one or more users to a new channel.
		Usage: move user1[, user2 ...] channel
		Users and channels can be ids or partial names.
		A user can also be @channelName, which means all users in that channel.
		Example: move doug "bill cosby" @main" away
		means move doug, Bill Cosby, and everyone in main to away,
		where "main" and "away" are contained in channel names on the server.
		This command requires the Move user right on TT5 servers and admin privileges on TT4 servers.
		"""
		args = shlex.split(line)
		if not args: raise SyntaxError("No user(s) or channel specified")
		if len(args) < 2: raise SyntaxError("At least one user and a channel are required")
		users = []
		channel = None
		for u in args[:-1]:
			if u.startswith("@"):
				chan = self.channelMatch(u[1:])
				cid = self.curServer.channels[chan["channelid"]]["channelid"]
				for u1 in self.curServer.users.values():
					if u1.get("channelid") == cid:
						users.append(u1)
			else:
				users.append(self.userMatch(u))
		channel = self.channelMatch(args[-1])
		is5 = self.curServer.is5()
		for u in users:
			if is5:
				self.do_send("moveuser userid=%s chanid=%s" % (
					u["userid"],
					channel["chanid"]
				))
			else:
				self.do_send("moveuser userid=%s destchannel=\"%s\"" % (
					u["userid"],
					channel["channel"]
				))

	def do_cmsg(self, line):
		"""Send a message to the current channel or, for admins, another channel.
		Usage: cmsg [@<channelname>] <message>
		Sends to the current channel unless another channel name is given.
		Examples:
			cmsg Hello to everyone in my current channel...
			cmsg @blah Hello to the people in the blah channel.
		"""
		if line.startswith("@"):
			channel,msg = line.split(None, 1)
			channel = self.channelMatch(channel[1:])
		else:
			msg = line
			try: channel = self.curServer.channels[self.curServer.me.chanid]
			except KeyError:
				raise CommandError("You are not in a channel")
		msg = msg.strip()
		if not msg:
			raise SyntaxError("A message must be specified")
		if self.curServer.is5():
			self.do_send('message type=2 chanid=%s content="%s"' % (
				channel.chanid,
				msg
			))
		else:
			self.do_send('message type=2 channel="%s" content="%s"' % (
				channel.channel,
				msg
			))

	def _handleSubscriptions(self, isIntercept, line):
		"""Does the work for do_subscribe and do_intercept.
		"""
		args = shlex.split(line)
		if len(args) < 1:
			raise SyntaxError("A user must be specified")
		user = self.userMatch(args.pop(0))
		firstBit = 1
		typename = "Subscriptions"
		if isIntercept:
			if self.curServer.is5():
				firstBit = 65536
			else:
				firstBit = 256
			typename = "Intercepts"
		bitnames = self.curServer.subBitNames()
		subs = 0
		unsubs = 0
		for arg in args:
			isUnsub = False
			if arg.startswith("-"):
				isUnsub = True
				arg = arg[1:]
			matches = [bn for bn in bitnames if bn.lower().startswith(arg.lower())]
			arg = self.selectMatch(matches, "Select an option:")
			idx = bitnames.index(arg)
			if isUnsub:
				unsubs += (firstBit << idx)
			else:
				subs += (firstBit << idx)
		# Issue any unsubscribes, then any subscribes.
		if unsubs:
			self.do_send("unsubscribe userid=%s sublocal=%s" % (
				user.userid,
				str(unsubs)
			))
		if subs:
			self.do_send("subscribe userid=%s sublocal=%s" % (
				user.userid,
				str(subs)
			))
		# Then list what remains active.
		subs = int(user.sublocal)
		curbit = firstBit
		bits = []
		for idx,bitname in enumerate(bitnames):
			if subs & (firstBit << idx):
				bits.append(bitname)
		bits = ", ".join(bits)
		if not bits:
			bits = "none"
		print("%s: %s" % (typename, bits))

	def do_subscribe(self, line):
		"""Subscribe to and/or unsubscribe from any of the following from a user:
			User messages: Messages sent by this user to another user.
			Channel messages: Messages sent by this user to a channel.
			Broadcast messages: Messages sent by this user to the entire server.
			Audio: Sound sent by this user (but see below).
			Video: Video sent by this user.
			Desktop: This user's shared desktop.
		Use a dash (-) before any item to remove it.
		Example: subscribe doug -chan audio
			Stops channel messages and starts audio subscription.
		Specifying no subscriptions just lists the active ones.
		Note that audio, video, and desktop data are neither supported
		nor noticed by this program.
		"""
		self._handleSubscriptions(False, line)

	def do_intercept(self, line):
		"""Start or stop intercepting any of the following from a user:
			User messages: Messages sent by this user to another user.
			Channel messages: Messages sent by this user to a channel.
			Broadcast messages: Messages sent by this user to the entire server.
			Audio: Sound sent by this user (but see below).
			Video: Video sent by this user.
			Desktop: This user's shared desktop.
		Use a dash (-) before any item to remove it.
		Example: intercept doug -chan audio
			Stops intercepting channel messages and starts intercepting audio.
		Specifying no interceptions just lists the active ones.
		Administrative rights are required to start interceptions.
		Note that audio, video, and desktop data are neither supported
		nor noticed by this program.
		"""
		self._handleSubscriptions(True, line)

	def do_umsg(self, line):
		"""Send a message to a user.
		Usage: umsg [-i] <user> <message>
		<user> can be anything that matches a user; e.g., full or partial nickname or username, or full or partial IP address when those are visible to this user.
		An exact userid can be indicated with a number sign: umsg #332 Hi there.
		If -i is specified, the message is sent in a way that does not appear in TeamTalk server logs and that appears not possible for admin clients to intercept.
		Warning: The -i feature is not officially supported by TeamTalk itself and may fail to work on some servers.
		-i messages also do not display in standard TeamTalk clients; they only work among text clients that implement this feature.
		Neither the TeamTalk author nor the TTCom author can guarantee privacy with this feature, nor be sure that future or old TeamTalk server versions will treat these messages in the manner described here.
		"""
		invisible = False
		args = line.split(None, 1)
		if args[0] == "-i":
			args = line.split(None, 2)
			args.pop(0)
			invisible = True
		if len(args) < 2:
			raise SyntaxError("A user and a message must be specified")
		userid,content = args
		if userid[0] == "#" and userid[1:].isdigit(): userid = userid[1:]
		else:
			userid = self.userMatch(userid).userid
		mtype = 1
		if invisible: mtype = 4
		self.do_send(ParmLine("message", {
			"type": mtype,
			"destuserid": userid,
			"content": content
		}))

	def do_stats(self, line=""):
		"""Show statistics for a server.
		This requires admin privileges on the server.
		"""
		self.do_send("querystats")

	def userAction(self, cmd, user, useChannel=False, useChannelPath=False):
		"""Perform an action on a user that just requires a userid or a userid and a channel id or channel path.
		If useChannel is True, the user parameter must start with @channelSpec. Otherwise this client's current channel is used.
		If useChannel is False, the user parameter is just a user specification.
		If channelPath is True, the sent server command will specify the channel by path rather than by chanid.
		"""
		if useChannel:
			if user.startswith("@"):
				channel,user = user.split(None, 1)
				channel = self.channelMatch(channel[1:])
			else:
				try: channel = self.curServer.channels[self.curServer.me.chanid]
				except KeyError:
					raise CommandError("You are not in a channel")
			if useChannelPath: chan = ' channel="{0}"'.format(channel.channel)
			else: chan = ' chanid={0}'.format(channel.chanid)
		else: chan = ""
		user = user.strip()
		if not user:
			raise SyntaxError("A user name or partial name must be specified")
		user = self.userMatch(user)
		if not user: return
		self.do_send('%s userid="%s"%s' % (cmd, user.userid, chan))

	def do_kick(self, line):
		"""Kick a user by name or ID from the server.
		This command requires the Kick user right on TT5 servers and admin privileges on TT4 servers.
		"""
		self.userAction("kick", line)

	def do_ckick(self, line):
		"""Kick a user by name or ID from the channel.
		This command requires the Kick user right or channel op status in the affected channel.
		"""
		self.userAction("kick", line, True)

	def do_ban(self, line):
		"""Ban management. Requires admin privileges or the Ban right under TT5.
		Run without arguments for a list of subcommands, or type a subcommand and -h for help with that subcommand.
		Example: ban list -h.
		"""
		args = TTParms(line, True)
		self.dispatchSubcommand("ban_", args)

	def ban_list(self, args):
		"Use -h to get a full syntax description for this subcommand."
		parser = ArgumentParser(prog="ban list", description="List all or selected bans.", epilog="Examples: ban list, ban li bob, ban li 24.114., ban li channel=/, ban li !nickname=Bob")
		parser.add_argument("filter", nargs="*", help='fieldname=value to match exactly against a specific field, or just value to match against any field. Fields include bantime, username, nickname, ipaddr, and channel. More than one filter can be given. Prefix fieldname with "!" to select mismatches instead of matches. Quote any values that contain spaces.')
		opts = parser.parse_args(args)
		bans = self.getBans()
		if opts.filter: ttl = "Matching Bans"
		else: ttl = "Bans"
		parmsets = []
		for ban in bans:
			parms = ban.parms
			if not self.filterPasses(parms, opts.filter, True): continue
			parmsets.append(parms)
		tbl = TableFormatter(ttl, [
			"Ban Type", "Username", "IP Address", "Time"
		])
		for parms in parmsets:
			tbl.addRow([
				self._banTypeText(parms.type),
				parms.username,
				parms.ipaddr,
				time.ctime(float(parms.bantime))
			])
			tbl.addRow("     Nickname was {0}".format(parms.nickname))
			tbl.addRow("     Channel was {0}".format(parms.channel))
		self.msg(tbl.format(2))

	def _banTypeText(self, btype):
		"""Translate the given raw server or channel ban type into text.
		Assumes proper context will indicate if this is a server ban or a channel ban; only includes username or IP address indication in the return value.
		"""
		btype = int(btype)
		if btype == 2 or btype == 3: return "IP address"
		if btype == 4 or btype == 5: return "Username"
		return "Type {0}".format(btype)

	def ban_add(self, args):
		"Use -h to get a full syntax description for this subcommand."
		parser = ArgumentParser(prog="ban add", description="Add a new ban (does not also kick; see kb for this)", epilog="Examples: ban add bob, ban add nickname=Bob, ban add 24.114., ban add -k 295")
		parser.add_argument("-k", "--kick", action="store_true", help="Also kick the user(s) being banned.")
		parser.add_argument("filter", nargs="*", help='fieldname=value to match exactly against a specific user field, or just value to match against any field. Fields include userid, username, usertype, userdata, nickname, ipaddr, udpaddr, clientname, version, packetprotocol, statusmode, statusmsg, sublocal, and subpeer (not all of these are likely to prove useful).  More than one filter can be given. Prefix fieldname with "!" to select mismatches instead of matches. Quote any values that contain spaces. As a special case, a plain integer like 295 matches an exact userid.')
		opts = parser.parse_args(args)
		users = self.curServer.users
		parmsets = []
		for user in users:
			parms = users[user]
			if not self.filterPasses(parms, opts.filter, True): continue
			parmsets.append(parms)
		flt = lambda u1: self.curServer.nonEmptyNickname(u1, True)
		parmsets = self.selectMatch(parmsets, "Select One or More Users", flt, allowMultiple=True)
		for user in parmsets:
			parms = TTParms([KeywordParm("ban"),
				IntParm("userid", user.userid)
			])
			self.do_send(parms)
			if not opts.kick: continue
			parms = TTParms([KeywordParm("kick"),
				IntParm("userid", user.userid)
			])
			self.do_send(parms)

	def ban_delete(self, args):
		"Use -h to get a full syntax description for this subcommand."
		parser = ArgumentParser(prog="ban delete", description="Delete all or selected bans.", epilog="Examples: ban delete, ban del bob, ban del 24.114., ban del !nickname=Bob")
		parser.add_argument("filter", nargs="*", help='fieldname=value to match exactly against a specific field, or just value to match against any field. Fields include bantime, username, nickname, ipaddr, and channel. More than one filter can be given. Prefix fieldname with "!" to select mismatches instead of matches. Quote any values that contain spaces.')
		opts = parser.parse_args(args)
		bans0 = self.getBans()
		bans = []
		for ban in bans0:
			if not self.filterPasses(ban.parms, opts.filter, True): continue
			bans.append(ban.parms)
		if not bans:
			raise CommandError("No matching bans")
		# Select from remaining candidates exactly which ban(s) to delete.
		bans = self.selectMatch(bans, "Select One or More Bans To Remove", allowMultiple=True)
		if not bans: raise CommandError("No bans selected")
		if not self.confirm("Delete {0} bans (y/n)?".format(len(bans))): return
		for ban in bans:
			self.do_send(TTParms([
				KeywordParm("unban"),
				StringParm("ipaddr", ban.ipaddr)
			]))

	def do_kb(self, line):
		"""Kick and ban a user by name or ID.
		This command requires the Kick and Ban user rights on TT5 servers and admin privileges on TT4 servers.
		This command is a shortcut for ban add -k.  See the Ban Add subcommand for further information on how to select users (type ban add -h).
		"""
		self.do_ban("add -k "+str(line))

	def getAccounts(self):
		"""Return the set of accounts on this server.
		Returns a dict of ParmLines, one for each account.
		The keys are the usernames.
		"""
		accts = self.request("listaccounts")
		# Remove the final Ok event.
		resp = accts.pop()
		if resp.event != "ok":
			# TODO: This ignores any but the last response line.
			raise CommandError(resp)
		d = {}
		for acct in accts:
			if acct.event == "ok": continue
			d[acct.parms.username] = acct
		return d

	def getBans(self, chan=None):
		"""Returns the bans on this server as a list of ParmLine objects.
		If chan is not None, it should be the channel object for the channel where this list should be sought.
		The lines are the responses to the "listbans" command, one line per ban.
		"""
		if chan is not None: chan = " chanid={0}".format(chan.chanid)
		else: chan = ""
		bans = self.request("listbans{0}".format(chan))
		resp = bans.pop()
		if resp.event != "ok":
			# TODO: This ignores any but the last response line.
			raise CommandError(resp)
		return bans

	def getAllChannelBans(self):
		"""Return a list of all channel-level bans on the entire server.
		Warning: This may take a while on a server with hundreds of channels.
		"""
		bans = []
		[bans.extend(self.getBans(channel)) for channel in self.curServer.channels]
		return bans

	def do_account(self, line):
		"""Account management. Requires admin privileges.
		Run without arguments for a list of subcommands, or type a subcommand and -h for help with that subcommand.
		Example: account list -h.
		"""
		args = TTParms(line, True)
		self.dispatchSubcommand("account_", args)

	def filterPasses(self, parms, filters, nullIsAnonymousAccount=False):
		"""Returns True if the given parameter set passes the given filter list and False if not.
		"""
		if not filters: return True
		if isinstance(parms, dict): vals = list(parms.values())
		else: vals = parms
		try: vals = ", ".join(vals)
		except TypeError:
			vals1 = []
			for val in vals:
				val1 = None
				try: val1 = str(val)
				except:
					try: val1 = str(val)
					except: pass
				if val1 is None: continue
				vals1.append(val1)
			vals = ", ".join(vals1)
		for filter in filters:
			# ToDo: Bit of a kludge here.
			if filter.startswith('"') and filter.endswith('"'): filter = filter[1:-1]
			elif filter.endswith('"') and '="' in filter: filter = filter.replace('="', '=', 1)[:-1]
			if "=" in filter:
				fname,fvalWanted = filter.split("=", 1)
				invert = False
				if fname.startswith("!"):
					fname = fname[1:]
					invert = True
				fvalActual = parms[fname]
				if not invert and fvalActual != fvalWanted: return False
				elif invert and fvalActual == fvalWanted: return False
			elif filter == "" and nullIsAnonymousAccount:
				# Special case for matching the anonymous account in an account list.
				if parms["username"] != "": return False
			else:
				if filter.lower() not in repr(vals).lower(): return False
		return True

	def account_list(self, args):
		"Use -h to get a full syntax description for this subcommand."
		parser = ArgumentParser(prog="account list", description="List all or selected accounts.", epilog="Examples: account list, acc li -a, acc li usertype=1, acc li -l doug, acc li !userrights=259591")
		parser.add_argument("-a", "--admin", action="store_true", help="List only admin accounts (usertype=2).")
		parser.add_argument("-l", "--long", action="store_true", help="Long listing; include all non-empty fields except passwords.")
		parser.add_argument("-e", "--everything", action="store_true", help="Full listing; include all fields, even empty fields, except passwords. Useful for determining what fields exist.")
		parser.add_argument("-p", "--passwords", action="store_true", help="Includes passwords in output.")
		parser.add_argument("filter", nargs="*", help='fieldname=value to match exactly against a specific field, or just value to match against any field. Fields include username, password, usertype, userdata, userrights, note, initchan, opchannels, and audiocodeclimit. More than one filter can be given. Prefix fieldname with "!" to select mismatches instead of matches. Quote any values that contain spaces. As a special case, "" matches the anonymous account.')
		opts = parser.parse_args(args)
		if opts.admin: opts.filter.append("usertype=2")
		accts = self.getAccounts()
		if opts.filter: ttl = "Matching User Accounts"
		else: ttl = "User Accounts"
		parmsets = []
		for username in sorted(accts):
			acct = accts[username]
			parms = acct.parms
			if not self.filterPasses(parms, opts.filter, True): continue
			parmsets.append(parms)
		if not opts.long and not opts.everything:
			# Short, tabular listing.
			cols = ["Username", "Type", "Rights"]
			if opts.passwords: cols.append("Password")
			tbl = TableFormatter(ttl, cols)
			for parms in parmsets:
				row = ([
					parms.username,
					["0","Default","Admin","3","4","5"][int(parms.usertype)],
					"{0:7d}".format(int(parms.userrights))
				])
				if opts.passwords: row.append(parms.password)
				tbl.addRow(row)
				if parms.note.strip():
					tbl.addRow("        " +parms.note.strip())
			self.msg(tbl.format(2))
			return
		# Long, multiline listing showing all or all non-empty fields.
		if not parmsets:
			self.msg("{0}:  0".format(ttl))
			return
		buf = "{0} ({1}):\n".format(ttl, str(len(parmsets)))
		for parms in parmsets:
			if opts.passwords:
				buf += 'Account username "{0}" type {1} password "{2}"\n'.format(parms.username, parms.usertype, parms.password)
			else:
				buf += 'Account username "{0}" type {1}\n'.format(parms.username, parms.usertype)
			for k,v in sorted(parms.items()):
				if k.lower() in ["username", "usertype", "password", "note"]: continue
				if not opts.everything and (not v or (v.isdigit() and not int(v))): continue
				if not opts.everything and "chan" in k.lower() and v == "[]": continue
				buf += "    {0} {1}\n".format(k, v)
			if parms.note: buf += 'Note: "{0}"\n'.format(parms.note)
		self.msg(buf)

	def account_add(self, args):
		"Use -h to get a full syntax description for this subcommand."
		parser = ArgumentParser(prog="account add", description="Add a new account", epilog="Examples: account add "" "" 1 (makes anonymous account), acc add Bill B1llPw 2, acc add Doug DougsPassword "" (uses the annonymous account for Doug's user rights)")
		parser.add_argument("username", help='The username of the new account. Use "" to make the anonymous account. Use quotes if the name contains spaces.')
		parser.add_argument("password", help='The password for the new account. Use "" to make an account with no password. Use quotes if the password contains spaces.')
		parser.add_argument("usertype", help="1 for regular account, 2 for admin account, or the username of an account to use for user rights (TT5 only).")
		parser.add_argument("field", nargs="*", help="fieldname=value pairs to set other fields for the account. More than one pair may be specified. Example fields include note and userdata. Use quotes if a field value contains spaces. Warning: If you specify an invalid field name, such as by misspelling a field name, the field value will be ignored and will not be set on the account.")
		opts = parser.parse_args(args)
		acctDict = self.getAccounts()
		pat = r'''[\s.,?/;:@#$%^&*'"!+=_-]+'''
		u0 = re.sub(pat, '', opts.username.lower())
		for username in acctDict.keys():
			if username == opts.username: raise CommandError('Account "{0}" already exists'.format(opts.username))
			if username.lower() == opts.username.lower():
				if not self.confirm('Warning: There is already an account named "{0}" (same name but different letter casing). Proceed anyway (y/n)?'.format(username)):
					return
				else: continue
			u1 = re.sub(pat, '', username.lower())
			if u1 == u0:
				if not self.confirm('Warning: There is already an account similarly named "{0}". Proceed anyway (y/n)?'.format(username)):
					return
				else: continue
		userRights = None
		if self.curServer.is5():
			# Default user rights as of TeamTalk5Classic 5.2.1.4781. [DGL, 2017-04-04
			userRights = 0x0003F607  # decimal 259591
		utype = opts.usertype
		if utype not in ["1", "2"] and self.curServer.is5():
			if utype == "" and "" in acctDict:
				acct = acctDict[""]
			else:
				accts = [a for a in acctDict if utype.lower() in a.lower()]
				rightsAcct = self.selectMatch(accts, "Select an Account For User Rights")
				acct = acctDict[rightsAcct]
			utype = acct.parms.userType
			if int(utype) == 2 and not self.confirm("{0} is an admin account. Make {1} an admin account also (y/n)?".format(acct.parms.username, opts.username)):
				utype = "1"
			userRights = acct.parms.userRights
		# Username, password, and any other string values are assumed to be raw values; see the StringParm class.
		parms = TTParms([KeywordParm("newaccount"),
			StringParm("username", opts.username, True),
			StringParm("password", opts.password, True),
			IntParm("usertype", int(utype))
		])
		if userRights is not None:
			parms.append(IntParm("userrights", userRights))
		for field in opts.field:
			field = TTParms(field)[0]
			if field.name.lower() in ["username", "password", "usertype"]:
				raise CommandError("username, password, and usertype may not be repeated as fieldname=value pairs")
			parms.append(field)
		self.do_send(parms)

	def account_delete(self, args):
		"Use -h to get a full syntax description for this subcommand."
		parser = ArgumentParser(prog="account delete", description="Delete one or more existing accounts, with confirmation", epilog="Examples: account delete, acc del -a, acc del usertype=1, acc del !userrights=259591")
		parser.add_argument("-a", "--admin", action="store_true", help="Consider only admin accounts (usertype=1).")
		parser.add_argument("filter", nargs="*", help='fieldname=value to match exactly against a specific field, or just value to match against any field. More than one filter can be given. Prefix fieldname with "!" to select mismatches instead of matches. Quote any values that contain spaces. As a special case, "" matches the anonymous account.')
		opts = parser.parse_args(args)
		if opts.admin: opts.filter.append("usertype=2")
		accts = self.getAccounts()
		acctDict = {}
		for username in sorted(accts):
			acct = accts[username]
			parms = acct.parms
			if not self.filterPasses(parms, opts.filter, True): continue
			acctDict[username] = parms
		if not acctDict:
			raise CommandError("No matching accounts")
		# Select from remaining candidates exactly which account(s) to delete.
		dels = self.selectMatch(list(acctDict.keys()), "Select One or More Accounts To Delete", allowMultiple=True)
		if not dels: raise CommandError("No accounts selected")
		if not self.confirm("Delete {0} (y/n)?".format(", ".join(['"'+d+'"' for d in dels]))):
			return
		for username in dels:
			self.do_send('delaccount username="%s"' % (username))

	def account_modify(self, args):
		"Use -h to get a full syntax description for this subcommand."
		parser = ArgumentParser(prog="account modify", description="Modify an existing account", epilog="Examples: account modify Doug password=blah, acc mod Doug usertype=2 (make admin).")
		parser.add_argument("username", help='The username of the existing account. Use "" to modify the anonymous account. Use quotes if the name contains spaces.')
		parser.add_argument("field", nargs="*", help="fieldname=value pairs to set other fields for the account. More than one pair may be specified. Example fields include note and userdata. Use quotes if a field value contains spaces. Warning: If you specify an invalid field name, such as by misspelling a field name, the field value will be ignored and will not be set on the account.")
		opts = parser.parse_args(args)
		acctDict = self.getAccounts()
		try: acct = acctDict[opts.username]
		except KeyError: raise CommandError('Account "{0}" does not exist.'.format(opts.username))
		# Get TTParms interpretation of the account parameters.
		acctParms = TTParms(acct.initLine.strip())
		# Remove the Useraccount keyword.
		acctParms.pop(0)
		parmDict = {}
		for parm in acctParms:
			# ToDo: Hack for ParmLine's mishandling of list types.
			if parm.name.lower() == "opchannels" and isinstance(parm, StringParm):
				parm = ListParm(parm.name, parm.value)
			parmDict[parm.name] = parm
		for field in opts.field:
			field = TTParms(field)[0]
			if field.name.lower() == "username":
				raise CommandError("username may not be repeated as a fieldname=value pair")
			parmDict[field.name] = field
		parms = TTParms([KeywordParm("newaccount"),
			StringParm("username", opts.username, True)
		])
		for k,v in parmDict.items():
			if k.lower() == "username": continue
			parms.append(v)
		self.do_send(parms)

	def do_channel(self, line):
		"""Channel management.
		Run without arguments for a list of subcommands, or type a subcommand and -h for help with that subcommand.
		Example: channel list -h.
		"""
		args = TTParms(line, True)
		self.dispatchSubcommand("channel_", args)

	def channel_list(self, args):
		"Use -h to get a full syntax description for this subcommand."
		parser = ArgumentParser(prog="channel list", description="List all or selected channels.", epilog="Examples: channel list, chan li protected=1, chan li !type=1")
		parser.add_argument("-l", "--long", action="store_true", help="Long listing; include all non-empty fields except passwords.")
		parser.add_argument("-e", "--everything", action="store_true", help="Full listing; include all fields, even empty fields, except passwords. Useful for determining what fields exist.")
		parser.add_argument("-p", "--passwords", action="store_true", help="Includes passwords in output.")
		parser.add_argument("filter", nargs="*", help='fieldname=value to match exactly against a specific field, or just value to match against any field. Useful fields include name, topic, protected, maxusers, and type. More than one filter can be given. Prefix fieldname with "!" to select mismatches instead of matches. Quote any values that contain spaces.')
		opts = parser.parse_args(args)
		chans = self.curServer.channels
		if opts.filter: ttl = "Matching Channels"
		else: ttl = "Channels"
		parmsets = []
		# This sort order is meant to mirror that of the TeamTalk channel tree when fully expanded.
		for chanid,parms in sorted(list(chans.items()), key=lambda id_p: id_p[1].channel.lower()):
			if not self.filterPasses(parms, opts.filter): continue
			parmsets.append(parms)
		if not opts.long and not opts.everything:
			# Short, tabular listing.
			if opts.passwords:
				cols = ["chanid", "HasPW", "Password", "Type", "MaxUsers", "Channel"]
			else:
				cols = ["chanid", "HasPW", "Type", "MaxUsers", "Channel"]
			tbl = TableFormatter(ttl, cols)
			for parms in parmsets:
				row = ([
					"{0:5d}".format(int(parms.chanid)),
					["No", "Yes"][int(parms.protected)],
					"{0:4d}".format(int(parms.type)),
					"{0:6d}".format(int(parms.maxusers)),
					self.curServer.channelname(parms.chanid, False, True)
				])
				if opts.passwords: row.insert(2, parms.password)
				tbl.addRow(row)
				if parms.topic.strip():
					tbl.addRow("        " +parms.topic.strip())
			self.msg(tbl.format(2))
			return
		# Long, multiline listing showing all or all non-empty fields.
		if not parmsets:
			self.msg("{0}:  0".format(ttl))
			return
		buf = "{0} ({1}):\n".format(ttl, str(len(parmsets)))
		for parms in parmsets:
			buf += 'Chanid {0} {1} type {2}\n'.format(parms.chanid, self.curServer.channelname(parms.chanid, False, True), parms.type)
			for k,v in sorted(parms.items()):
				if k.lower() in ["chanid", "channel", "name", "type", "topic"]: continue
				if not opts.everything and (not v or (v.isdigit() and not int(v))): continue
				if not opts.everything and not "topic" in k.lower() and v == "[]": continue
				if not opts.passwords and "password" in k.lower(): continue
				buf += "    {0} {1}\n".format(k, v)
			if parms.topic: buf += 'Topic: "{0}"\n'.format(parms.topic)
		self.msg(buf)

	def do_tt(self, line):
		"""Create a .tt file for a user account.
		Usage: tt [clientVersion] ttFileName [userName [channelToJoin]]
		If no clientVersion is given, the current server's version is used.
		Adding a userName includes the user's login and password credentials in the generated file. Requires admin privileges.
		Adding a channelToJoin makes the tt file cause the user to land in the given channel on login.
		"""
		if self.curServer.state != "loggedIn":
			raise CommandError("Not logged in")
		args = shlex.split(line)
		verGiven = None
		try:
			verGiven = (float(args[0]) > 0.0)
			if verGiven: verGiven = args.pop(0)
		except IndexError: verGiven = None
		except ValueError: verGiven = None
		if not args:
			raise SyntaxError("Must specify a .tt file name to generate")
		fname = args.pop(0)
		if not fname.lower().endswith(".tt"):
			fname += ".tt"
		if (os.path.exists(fname)
		and not self.confirm("File %s already exists. Replace it (y/n)?" % (
			fname
		))):
			return
		if not args:
			acct = ParmLine("fakeEvent username=\"\" password=\"\"")
		else:
			acct = args.pop(0)
			acctDict = self.getAccounts()
			accts = [a for a in acctDict if acct.lower() in a.lower()]
			acct = self.selectMatch(accts, "Select an Account")
			acct = acctDict[acct]
		if args: channel = args.pop(0)
		else: channel = None
		if channel:
			cid = self.channelMatch(channel).channelid
		else:
			cid = None
		tt = self.curServer.makeTTString(acct.parms, cid, verGiven)
		with open(fname, "w", encoding="utf-8") as f:
			f.write(tt)

	def do_say(self, line):
		"""Say the given line if possible.
		Quoting is not necessary or desirable.
		"""
		mycmd_say(line)

	def do_play(self, line):
		"""Play a sound file via the SoX play command. Requires the play command to be on the path.
		Files are played in the order received, in their own thread to avoid delaying the entire application.
		When the queue of files to play grows, files will be batched onto multi-file play commands for speed.
		"""
		player.sendFile(line)

	def do_system(self, line):
		"""Run a system command in a subshell.
		"""
		task = lambda: callWithRetry(os.system, line)
		thr = threading.Thread(target=task)
		thr.daemon = True
		thr.start()

	def do_motd(self, line=""):
		"""Show the message of the day (motd) for the current server.
		"""
		# Raw value from server.
		motd = self.curServer.info.motd
		# Trick to reconstruct its printable format.
		motd = 'motd="{0}"'.format(motd)
		motd = TTParms(motd, True).pop(0).value
		self.msg(motd)

	def do_whoIs(self, line=""):
		"""Show information about a user.
		Syntax: whoIs <name>, where <name> can be a full or partial user name.
		If name is omitted, this current user is used.
		"""
		line = line.strip()
		isMe = False
		if line:
			user = self.userMatch(line)
			if not user: return
		else:
			user = self.curServer.me
			isMe = True
		u = AttrDict(user.copy())
		buf = TextBlock()
		userid = u.pop("userid")
		buf += "UserId %s" % (userid)
		if not u.get("username") and not u.get("nickname"):
			buf += ", no nickname or username"
		else:
			buf.add("Username", u.get("username"), True)
			buf.add("Nickname", u.get("nickname"), True)
		u.pop("username", "")
		u.pop("nickname", "")
		buf.add("UserType", u.get("usertype"))
		u.pop("usertype", "")
		buf.add("StatusMode", u.get("statusmode"), True)
		statusmsg = u.get("statusmsg")
		if statusmsg: statusmsg = statusmsg.strip()
		if statusmsg: buf += " (" +statusmsg +")"
		statustime = u.get("statustime")
		if statustime:
			diff = time.time() -statustime
			diff = self.curServer.secsToTime(diff)
			statustime = "for {0}".format(diff)
			buf += " " +statustime
		u.pop("statustime", "")
		u.pop("statusmode", "")
		u.pop("statusmsg", "")
		ipaddr = u.get("ipaddr", "") or ""
		# This fixes IPV6-format versions of IPV4 addresses into a straight IPV4 address.
		if ipaddr.lower().startswith("::ffff:"): ipaddr = ipaddr[7:]
		buf.add("IP Address", self.formattedAddress(ipaddr))
		u.pop("ipaddr", "")
		cname = u.pop("clientname", "").strip()
		cver = u.pop("version", "").strip()
		if cname and cver: cname = "%s version %s" % (cname, cver)
		elif cver: cname = "Version %s" % (cver)
		buf.add("Client", cname)
		buf.add("Packet Protocol", u.pop("packetprotocol", ""), True)
		channelid = u.pop("channelid", "")
		channel = u.pop("channel", "")
		if channelid or channel:
			if not channel:
				channel = self.curServer.channels[channelid].channel
			buf += "\nOn channel %s (%s)" % (channelid, channel)
		server = u.pop("server", None)
		if server:
			channels = list(server.channels.values())
		else:
			channels = []
		for which in [
			("voiceusers", "Can speak in"),
			("videousers", "Can share video in"),
			("mediafileusers", "Can share media files in"),
			("desktopusers", "Can share desktop in"),
			("operators", "Operator in"),
			("opchannels", "Automatically operator in")
		]:
			k,name = which
			matches = [c for c in channels if userid in (c.get(k) or [])]
			# Substring match is not enough though; false positives like 4095 for userid 9 are possible.
			matches = [c for c in matches if userid in ListParm(name, c[k]).value]
			matches = ", ".join([c.channel for c in matches])
			buf.add(name, matches)
			try: u.pop(k)
			except KeyError: pass
		buf.add("SubLocal", u.pop("sublocal", ""))
		buf.add("SubPeer", u.pop("subpeer", ""), True)
		userdata = u.pop("userdata", "")
		if userdata == "0": userdata = ""
		buf.add("Userdata", userdata)
		buf.add("Note", u.pop("note", ""), True)
		# Anything non-empty value not handled above goes here.
		for k in sorted(u):
			# Except for TTCom-internal stuff.
			if k == "temporary": continue
			buf.add(k, u[k])
		self.msg(str(buf))
		if isMe: self.curServer.reportRightsIssues()

	def formattedAddress(self, addr):
		"""Return the given address with FQDN where possible.
		Assumes addr is a numeric address (IPV4 or IPV6).
		"""
		if not addr: return addr
		fqdn = socket.getfqdn(addr)
		if (fqdn == addr
			or fqdn.endswith(".in-addr.arpa")
		): return addr
		return "%s (%s)" % (fqdn, addr)

	def do_address(self, line=""):
		"""Show IP address for a user when available.
		Syntax: address <name>, where <name> can be a full or partial user name.
		If name is omitted, this current user is used.
		"""
		line = line.strip()
		if line:
			user = self.userMatch(line)
			if not user: return
		else:
			user = self.curServer.me
		u = AttrDict(user.copy())
		buf = TextBlock()
		ipaddr = u.get("ipaddr") or ""
		buf.add("IP Address", self.formattedAddress(ipaddr))
		self.msg(str(buf))

	def do_op(self, line):
		"""Op or deop a user in one or more channels or check ops.
		Syntax: op [-a|-d] [<user> [<channel> ...]]
		Op with no arguments lists all ops on the server.
		Op with just a user lists that user's ops.
		Op with -a or -d and a user adds or deletes that user's ops from channels.
		If no channel is specified, the user's current channel is used.
		Otherwise, the command affects all channels listed.
		Changing ops requires admin rights or ops in the affected channel(s).
		Note that this command deals with active ops, not ops set as part of user accounts; see the Account command for those.
		"""
		server = self.curServer
		k = "operators"
		line = line.strip()
		if not line:
			# List all ops on server.
			for u in sorted(list(server.users.values()), key=lambda u1: server.nonEmptyNickname(u1)):
				userid = u.userid
				matches = [c for c in server.channels.values() if userid in (c.get(k) or [])]
				matches = ", ".join([c.channel for c in matches])
				if matches:
					self.msg("%s: %s" % (
						server.nonEmptyNickname(u),
						matches
					))
			return
		# Add, delete, or just show ops for a user.
		act = ""
		if line.startswith("-"):
			if line.startswith("-a"):
				act = "add"
			elif line.startswith("-d"):
				act = "del"
			else:
				raise SyntaxError("Unknown option: %s" % (line[:2]))
		args = shlex.split(line)
		# Get rid of -a or -d.
		if act: args.pop(0)
		if not args: raise SyntaxError("Must specify a user.")
		u = self.userMatch(args.pop(0))
		if args and not act:
			raise SyntaxError("No channels needed when just listing ops")
		if not args and u.get("channel") and act:
			# When no channel is given and ops are being changed,
			# use the user's current channel as the target.
			args.append(u.channel)
		opstatus = 0
		if act == "add": opstatus = 1
		# This loop is skipped if not act because we didn't allow that
		# case above.
		for chanName in args:
			c = self.channelMatch(chanName)
			if self.curServer.is5(): chspec = "chanid=%s" % (c.chanid)
			else: chspec = 'channel="%s"' % (c.channel)
			self.do_send('op userid=%s %s opstatus=%s' % (
				u.userid,
				chspec,
				opstatus
			))
			# Let the op list print after those modifications.
		# List ops for just this user.
		userid = u.userid
		matches = [c for c in server.channels.values() if userid in (c.get(k) or [])]
		matches = ", ".join([c.channel for c in matches])
		if matches:
			self.msg("%s: %s" % (
				server.nonEmptyNickname(u),
				matches
			))

	def do_admins(self, line=""):
		"""List the admins currently on server and where they are and come from.
		"""
		channelname = self.curServer.channelname
		for u in self.curServer.users.values():
			if not u.usertype or int(u.usertype) != 2: continue
			ch = None
			if u.chanid: ch = channelname(u.chanid)
			print("%s: %s, %s" % (
				self.curServer.nonEmptyNickname(u),
				u.ipaddr,
				ch
			))

	def do_ping(self, line=""):
		"""Send a ping to the server.
		A pong should come back.
		"""
		self.do_send("ping")

	def do_run(self, fname):
		"""Run, or replay, a file of raw TeamTalk API commands at the current server.
		"""
		if not fname:
			print("No file name specified.")
			return
		fname = self.dequote(fname)
		# TODO: Consider security of unrestricted filesystem access here.
		if not os.path.exists(fname):
			print("File %s not found." % (fname))
			return
		for line in open(fname, encoding="utf-8"):
			line = line.strip()
			if line.startswith("addchannel"):
				line = line.replace("addchannel", "makechannel", 1)
			elif line.startswith("serverupdate"):
				line = line.replace("serverupdate", "updateserver", 1)
			if (line.startswith("updateserver")
			and "userrights=" in line
			):
				# Some userrights bits can throw out the whole updateserver request.
				i = line.find("userrights=")
				line1,sep,rest = line[i:].partition(" ")
				line = line[:i-1] +rest
				line1 = "updateserver " +line1
				self.rawSend(line1)
			self.rawSend(line)
		return

	def rawSend(self, line):
		"""Send a raw line to the server.
		"""
		self.curServer.conn.send(line)

	def do_send(self, line):
		"""Send a raw command to the current server.
		"""
		# Line can be a text line or a ParmLine object.
		self.servers.logstream.write("%s\n  %s: %s\n" % (
			datetime.now().ctime(),
			self.curServer.shortname,
			"_send_ " +str(line)
		))
		self.curServer.sendWithWait(line)

	def request(self, line):
		"""Send a command and return its results as a list of ParmLines.
		Line can be a text line or a ParmLine object.
		"""
		return self.curServer.sendWithWait(line, True)

	def do_option(self, line=""):
		"""Get or set a TTCom option by its name.  Valid options:
			queueMessages: Set non-zero to make messages print only when Enter is pressed.
				This keeps events from disrupting input lines.
			speakEvents: Set non-zero to make events speak through MacOS on arrival.
		Type with no parameters for a list of all options and their values.
		"""
		optname,sep,newval = line.partition(" ")
		optname = optname.strip()
		newval = newval.strip()
		if not newval: newval = None
		opts = [
			("queueMessages", "Queue messages on arrival and print on Enter."),
			("speakEvents", "Speak events through MacOS on arrival")
		]
		if not optname:
			lst = []
			for opt in opts:
				optname = opt[0]
				lst.append("%s = %s" % (
					optname,
					conf.option(optname)
				))
			self.msg("\n".join(lst))
			return
		f = lambda o: ": ".join(o)
		opts = [o for o in opts if optname.lower() in o[0].lower()]
		opt = self.selectMatch(opts, "Select an Option:", f)[0]
		self.msg("%s = %s" % (
			opt,
			conf.option(opt, newval)
		))


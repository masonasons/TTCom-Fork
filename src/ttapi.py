"""TeamTalk server connection manager.

Author:  Doug Lee
Credits to Chris Nestrud and Simon Jaeger for some ideas and a bit of code.

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

import re, socket, ssl, time
import threading
from tt_attrdict import AttrDict
from parmline import ParmLine
from conf import conf

from mplib import log
from sound import soundpool
pool=soundpool.SoundPool()
class ServerState(object):
	"""Connection states for a server.
	Usage:
		s = ServerState([initialState]) # defaults to "disconnected"
		s("connected")  # change
		s()  # get current state
	index is also public but should not be set directly.
	"""
	states = [
		"disconnected",  # No connection exists.
		"connecting",  # Connection being made.
		"connected",  # Connected and welcome message received (also after logout).
		"loggingIn",  # Connected, login request sent.
		"loginError",  # Connected, login tried but rejected.
		"loggingOut",  # Logged in, logout request sent.
		"loggedIn"   # Login sequence completed.
	]

	def __init__(self, initialState="disconnected"):
		self(initialState)
		# Sets self.index.

	def __call__(self, newState=None):
		if newState is not None:
			self.index = self.states.index(newState)
		return self.states[self.index]


class TeamTalkServerConnection(object):
	"""Objects in this class represent connections to a TeamTalk
	server.  Calling connect() on one of these objects will
	either create a connection or raise an IOError (specifically
	a socket.error or an IOError with a homegrown message)
	exception. When the connection is broken, either by a
	disconnect() call or by a connection failure, the object
	should be abandoned, and any new connection to the server
	should be made via a new object.  The object, on successful
	connection, will internally manage keep-alive pinging,
	sufficient "welcome" line parsing to obtain the "usertimeout"
	value (for figuring out ping frequency), and the passing
	of incoming text lines (events) back to the object's creator
	via a callback.

	The creator of an object in this class is notified of important
	events via a callback function passed to __init__(). The callback
	function is called with the following for the possible events:

		"_connected_": Connection established.
		string of arbitrary length: An inbound text line, with line ending.
		"_disconnected_": Connection lost or ended.

	This class spawns the following threads for each object:
		- watcher() watches for and processes all inbound text until the connection ends.
		- pinger manages pinging when the connection is active.
	Call send() with raw lines (without line endings) to send commands
	to the server. To find out why the connection ended, examine the
	disconnectReason string. The welcomeParms AttrDict contains the
	parameters from the server's "welcome" line. For convenience,
	userid contains the connection's TeamTalk userid as a string.
	usertimeout is the effective usertimeout value (from the "welcome"
	line, but a "serverupdate" line could change it).
	"""
	SSLContext = ssl.SSLContext()
	SSLContext.verify_mode = ssl.CERT_NONE
	SSLContext.check_hostname = False

	def __init__(self, parent, shortname, host, port=None, encrypted=False, callback=None):
		"""Create a TeamTalk server connection object. Host and port define
		the connection endpoint. shortname is used to refer to the
		connection and in the naming of the threads created for this
		connection. callback is called to handle events.
		Parent is provided for convenience in debugging.
		"""
		self.parent = parent
		self.shortname = shortname
		self.host = host
		self.port = port
		if not self.port: self.port = 10333
		self.encrypted = encrypted
		self.state = "starting"
		if not callback:
			callback = self.simpleCallback
		self.callback = callback
		self.shuttingDown = False
		self.sock = None
		self.sockfile = None
		self.welcomeParms = None
		self.userid = None
		self.usertimeout = None
		self.disconnectReason = ""
		self.threads = {}
		self.curid = None

	def __del__(self):
		"""Called when this object is garbage-collected.
		"""
		self.terminate()

	def terminate(self):
		"""Call to destroy this connection object.
		"""
		self.shuttingDown = True
		self.callback = None

	def notifyCaller(self, msg):
		"""Send msg (str or int) to the caller.
		"""
		if self.callback: self.callback(msg)

	def newThread(self, target):
		"""Start a new thread for this server connection.
		"""
		th = threading.Thread(target = target)
		th.daemon = True
		th.name = self.shortname +"_" +target.__name__ +"_" +th.name
		self.threads[target.__name__] = th
		th.start()
		return th

	def threadEnding(self):
		"""Returns True if this method's caller's thread is scheduled to end.
		"""
		return self.shuttingDown

	def threadName(self, which):
		"""Return the full name of the thread based on its local name,
		e.g., "pinger."
		"""
		return self.threads[which].name

	def simpleCallback(self, cargo):
		"""A simple example of a callback, and the default if none is
		provided at object creation time. Simply prints lines received
		and indicates non-line events in a user-friendly manner.
		"""
		if type(cargo) is string:
			print("%s: %s" % (self.shortname, cargo.rstrip()))
			return
		# int.
		print("*** %s %s" % (
			self.shortname,
			["disconnected", "connected", "welcome received"][cargo]
		))

	def connect(self):
		"""Connect to the server. Call only once per object.
		Raises an IOError on failure to connect.
		This may be a socket.error or a homegrown-message IOError
		indicating a problem with the start of the TeamTalk
		client-server protocol.
		This call also does the following:
			- Signals connection to server.
			- Accepts and parses the "welcome" server message.
			- Passes the welcome message back to the caller.
			- Gets the usertimeout for determining ping frequency.
			- Collects other welcome-line parameters into self.welcomeParms.
			- Starts the pinger thread for this server.
			- Sends a UDP packet that prevents Windows XP clients on
			  this server from freezing briefly on this client's login.
			- Signals disconnection on error during all that.
		"""
		self.state = "connecting"
		self.sock = socket.socket()
		self.sock.settimeout(10)
		self.sock.connect((self.host, int(self.port)))
		# The above line may raise a socket.error.
		if self.encrypted:
			ssock = self.SSLContext.wrap_socket(self.sock, server_hostname=self.host)
			self.sock = ssock
		self.sockfile = self.sock.makefile("r", encoding="utf-8")
		# Signal connection.
		self.state = "notifyConnect"
		self.notifyCaller('_connected_ ipaddr="{0}" tcpport={1}'.format(*self.sock.getpeername()))
		try:
			# Get the welcome line and use it.
			self.state = "welcomeWait"
			self.sock.settimeout(20)
			welcomeLine = self.sockfile.readline()
			self.state = "notifyWelcome"
			if welcomeLine.startswith("teamtalk "):
				welcomeLine = "welcome " +welcomeLine[9:]
			self.notifyCaller(welcomeLine)
			welcomeLine = ParmLine(welcomeLine)
			if welcomeLine.event != "welcome":
				raise IOError("Welcome line expected, got '%s' instead" % (
					welcomeLine
				))
			self.welcomeParms = welcomeLine.parms
			self.userid = welcomeLine.parms.userid
			self.usertimeout = int(welcomeLine.parms.usertimeout)
			self.protocol = welcomeLine.parms.protocol
			self.state = "makeThreads"
			self.newThread(self.watcher)
			self.newThread(self.pinger)
			self.state = "connected"
			# No timeouts after connect so packets don't split up.
			self.sock.settimeout(None)
		except Exception as e:
			self.state = "disconnecting"
			self.disconnect()
			self.state = "disconnected"
			raise

	def disconnect(self, reason=""):
		"""Disconnect and send the corresponding callback signal.
		Does nothing if there is no connection established.
		This is called internally on error and can also be called by
		this object's creator. reason, if passed, is placed in the
		disconnectReason instance variable for examination by the
		object's creator after a disconnect.
		"""
		if not self.callback: return
		self.disconnectReason = reason
		self.notifyCaller("_disconnected_")
		try: self.sock.close()
		except: pass
		self.play("died.wav")
		self.callback = None

	def pinger(self):
		"""Ping the server as needed.
		This runs in its own thread.
		"""
		while not self.threadEnding():
			try: self.sock.send(b"ping\r\n")
			except socket.error as e:
				self.disconnect("Error during ping: %s" % (str(e)))
				return
			pingtime = float(self.usertimeout)
			# 0.5 sec for very short usertimeouts, 3/4 of usertimeout otherwise.
			# 0.3 works for timeout=0, which stock tt clients can't handle!
			if pingtime < 1: pingtime = 0.3
			elif pingtime < 1.5: pingtime = 0.5
			else: pingtime *= 0.75
			time.sleep(pingtime)

	def _isConnected(self):
		"""Returns True if this stream appears to be connected.
		There might be a better way to write this.
		"""
		if not self.sock or not self.sockfile: return False
		fileno = None
		try: fileno = self.sock.fileno()
		except: pass
		return (fileno is not None)

	def watcher(self):
		"""Handles all inbound text.
		Eats pongs that answer pings sent by this object.
		Runs as its own thread.
		"""
		err = None
		try:
			for line in self.sockfile:
				if not line:
					# This probably won't happen; EOF should end the loop.
					self.disconnect("EOF encountered during read")
					return
				if line.startswith("teamtalk "):
					# TeamTalk 5 protocol starts with this instead of welcome.
					line = "welcome " +line[9:]
				if self.threadEnding():
					self.disconnect("Shutting down")
					return
				ll = line.rstrip().lower()
				if ll.startswith("begin id="):
					self.curid = ll.split("=")[1]
				elif ll.startswith("end id="):
					self.curid = None
				elif not self.curid and ll == "pong":
					# Pongs sent as part of a user command should be in an id block.
					continue
				self.notifyCaller(line)
		except IOError as e:
			err = e
		# Connection failure by error or just end of stream.
		if err:
			self.disconnect("Error during read: %s" % (str(err)))
		else:
			self.disconnect("EOF during read")

	def send(self, line):
		"""Send a command to this server.
		line is a plain text line without line ending.
		Returns True on success and False on error.
		disconnect() is called on an IOError.
		"""
		line = str(line).rstrip() +"\r\n"
		bline = line.encode("utf-8")
		try: self.sock.send(bline)
		except IOError:
			self.disconnect("Error during send")
			return False
		return True


class TeamtalkServer(object):
	"""Each object in this class represents a single TeamTalk server.
	send() and sendWithWait() are used to send commands to the server,
	and processLine() handles incoming lines from the server.
	processLine also dispatches incoming events (each line is an
	event) to the various event_*() methods in this class.
	"""

	def _getState(self): return self._state()
	def _setState(self, val): self._state(val)
	state = property(_getState, _setState, None, "Current connection state")

	def __init__(self, host, tcpport=10333, shortname="", parms={}):
		self._state = ServerState()
		self.conn = None
		self.ev_loggedIn = threading.Event()
		self.ev_loggedOut = threading.Event()
		self.ev_idblockDone = threading.Event()
		self.manualCM = False
		self.lastError = None
		self.curID = 0
		self.waitID = 0
		self.soundsdir = "default"
		self.sound_volume=0
		self.play_sounds = 0
		self.maxID = 127
		self._collecting = 0
		self._outputCollection = []
		self.host = host
		self.tcpport = tcpport
		self.encrypted = False
		if not shortname: shortname = host
		self.shortname = shortname
		self.autoLogin = 0
		parms["clientname"] = "TTCom"
		parms["version"] = conf.version
		parms.setdefault("udpport", self.tcpport)
		# Teamtalk 4.3 clients pop up an error like this if there is
		# no nickname= parameter on the login command line:
		#	An error occurred while perform a requested command:
		#	User not found
		#	OK  
		# So we force a null nickname if none is provided.
		# [DGL, 2012-01-02]
		parms.setdefault("nickname", "")
		self.loginParms = parms
		self.disconnect()

	def play(self,soundname):
		if self.play_sounds==1:
			pool.play_stationary_extended("sounds/"+self.soundsdir+"/"+soundname,False,0,0,self.sound_volume,100)

	def clear(self):
		"""Clear this object (on init or disconnect).
		"""
		self.conn = None
		self.waitID = 0
		self.curID = 0
		self.ev_loggedIn.clear()
		self.ev_loggedOut.clear()
		self.state = "disconnected"
		self.info = AttrDict()
		self.channels = dict()
		self.users = dict()
		self.files = dict()
		self.me = None

	def disconnect(self):
		"""Disconnect from server and clean up.
		"""
		if self.conn:
			self.conn.disconnect()
			if self.conn: self.conn.terminate()
		self.clear()

	def terminate(self):
		"""Called to destroy this object.
		"""
		self.autoLogin = 0
		self.disconnect()

	def connect(self, retry=False):
		"""Connect to the server.
		Returns True if there is a connection on exit and False if not.
		If retry is True, tries until successful.
		The pause between retries is 10 seconds.
		"""
		while True:
			if self.conn:
				if self.conn.threadEnding():
					return False
				return True
			self.conn = TeamTalkServerConnection(self,
				self.shortname, self.host, self.tcpport, self.encrypted,
				self.processLine
			)
			self.state = "connecting"
			try:
				self.conn.connect()
			except IOError:
				self.state = "disconnected"
				self.conn = None
				if retry:
					time.sleep(10)
					continue
				return False
			self.state = "connected"
			return True

	def waitOn(self, event, timeout=5.0):
		"""Wait on an event with the given timeout.
		Return True if the vent fired and False if not.
		Used by threads to allow shutdown.
		"""
		event.wait(timeout)
		return event.isSet()

	def login(self, background=False):
		"""Log into the server.
		If background is True, tries connecting until successful,
		and does so in the background, returning immediately.
		Does not retry actual login though.
		If background is False, returns True if logged in on exit and False if not.
		If background is True, returns True unconditionally.
		"""
		# This lets manual login reset the stoppage of autoLogins.
		self.manualCM = False
		if background:
			th = threading.Thread(target=self.login)
			th.daemon = True
			th.start()
			return True
		retry = True
		if not self.connect(retry):
			self.errorFromEvent("Connect failed, login aborted")
			return False
		if self.ev_loggedIn.isSet(): return True
		self.state = "loggingIn"
		try:
			lp = self.loginParms.copy()
			for k in ["chanid", "channel", "chanpassword"]:
				if k in lp: del lp[k]
			self.send(ParmLine("login", lp))
		except IOError:
			# Connection failure.
			self.errorFromEvent("Connection failed during login attempt")
			self.disconnect()
			return False
		# event_ok() and event_error() can set this event.
		if not self.waitOn(self.ev_loggedIn, 10):
			self.errorFromEvent("Login timed out")
			return False
		if self.state == "loginError":
			# event_error() did this, and already printed the message.
			self.ev_loggedIn.clear()
			self.state = "connected"
			return True
		self.state = "loggedIn"
		return True

	def processLine(self, line):
		"""Callback to process inbound text a line at a time.
		Passed by connect() as the TeamTalkServerConnection callback for events.
		Uses ParmLine to get eventname,parms (AttrDict) from the line,
		then dispatches the event to a method named event_<eventname>.
		If no such method exists for an event, handles this condition.
		"""
		parmline = ParmLine(line)
		# When collecting text, don't dispatch events.
		if self._handleCollection(parmline):
			return
		# When we internally set an id and this is a start/end-block
		# line for it, don't call hookEvents for it.
		isOurBlockMarker = (self.waitID > 0
			and parmline.event in ["begin", "end"]
			and parmline.parms.id == str(self.waitID)
		)
		if not isOurBlockMarker:
			self.hookEvents(parmline, False)
		# Protect from rogue transmissions, or somebody could execute random code here.
		# This would require a custom TeamTalk server though.
		# This check makes sure nothing but underscores and letters
		# appear in an event name.
		if not parmline.event.replace("_", "").isalpha():
			self.errorFromEvent("Invalid line:  %s" % (line))
			return
		try: eventFunc = eval("self.event_" +parmline.event)
		except:
			self.errorFromEvent("Unrecognized line:  %s" % (line))
			return
		try:
			if not eventFunc(parmline.parms):
				self.outputFromEvent(line.rstrip())
		except Exception as e:
			self.errorFromEvent("Event dispatch failure: %s" % (line))
			raise
		finally:
			if not isOurBlockMarker:
				self.hookEvents(parmline, True)

	def _handleRecycling(self, force=False):
		"""Handle autoLogin-on-logout as appropriate.
		"""
		if force or (self.autoLogin and not self.manualCM):
			self.outputFromEvent("Reconnecting")
			task = lambda: self.login(True)
			th = threading.Timer(5, task)
			th.setDaemon(True)
			th.start()

	def _handleCollection(self, parmline):
		"""Manages the process of collecting a command response.
		Helper for processLine() and sendWithWait().
		sendWithWait() signals a collection start by calling self._startCollecting():
		_startCollecting() sets self.waitID and sets self._collecting to 1.
		processLine() calls this method when collection is in progress,
		instead of dispatching events.
		This method handles the transition of _collecting from 1 to 2 and back to 0.
		It also eats the relevant Begin and End events.
		When the response block is done, this method resets waitID to 0.
		sendWithWait() watches for this then collects the output by calling self._stopCollecting().
		_stopCollecting() returns the collected output and clears it.
		"""
		isConnect = (parmline.event == "_connected_")
		isDisconnect = (parmline.event == "_disconnected_")
		# If no collection is in progress.
		if not self._collecting: return False
		# If collection has been requested but the response hasn't started.
		if self._collecting == 1:
			if parmline.event == "begin" and parmline.parms.id == str(self.waitID):
				# Start of atomic response line set.
				# No unrelated line should interrupt this.
				# It terminates with an End id=... event.
				# Eat the Begin event and start collecting.
				self._outputCollection = []
				self._collecting = 2
				self.ev_idblockDone.clear()
				return True
			# Something unrelated slipped in between this command and
			# its response block.
			# If this is a connect/disconnect, clean up the mess.
			if isConnect or isDisconnect:
				self.errorFromEvent("Output collection aborted by server connection interruption")
				# Treat like the end of the response line set.
				# TODO: Might need to do more here.
				self._collecting = 0
				self.waitID = 0
				self.ev_idblockDone.set()
			# Handle this line normally even if it cut short a response collection.
			return False
		# Finally if the response is ongoing (self._collecting==2).
		if ((isConnect or isDisconnect)
		or parmline.event == "end" and parmline.parms.id == str(self.waitID)):
			# End of response line set.
			self._collecting = 0
			self.waitID = 0
			if isConnect or isDisconnect:
				self.errorFromEvent("Output collection truncated by server connection interruption")
				self.ev_idblockDone.set()
				# Let connect/disconnect events through.
				return False
			self.ev_idblockDone.set()
			# Eat the closing "end id=..." event.
			return True
		# Not end of set, so collect the line.
		self._outputCollection.append(parmline)
		# And don't pass it through as an event to process now.
		return True

	def hookEvents(self, parmline, afterDispatch):
		"""Stub that subclasses can override for multi-event processing.
		This method is called twice per event:
		once before and once after the event is dispatched.
		The afterDispatch parameter indicates which type of call is occurring.
		"""
		pass

	def is5(self):
		"""Returns True for a tt5 server and False for a tt4 server.
		"""
		ver = self.info.version
		if not ver: return False
		ver = ver[0]
		return ver == "5"

	def send(self, line):
		"""Send a command to this server.
		line can be anything with an str value.
		Raises a custom IOError on failure.
		"""
		if not self.conn.send(str(line)):
			raise IOError("Connection lost")

	def sendWithWait(self, line, returnResults=False):
		"""Send a command to this server and wait for it to complete.
		line can be anything with an str value.
		If returnResults is True, the command's response is returned
		instead of generating events. Returned responses take
		the form of a list of ParmLine objects.
		See _handleCollection() for a description of the response collection process.
		IOErrors and EOF cause a connection reset but also bubble up.
		"""
		self.curID += 1
		if self.curID > self.maxID:
			self.curID = 1
		line = str(line).rstrip()
		line += " id={0:0d}".format(self.curID)
		self.ev_idblockDone.clear()
		if returnResults: self._startCollecting(self.curID)
		else:
			self.waitID = self.curID
		try: self.send(line)
		except IOError:
			# Connection failure.
			self.disconnect()
			# Break any waiting code so everything can restart.
			raise
		if not self.waitOn(self.ev_idblockDone, 8):
			self.errorFromEvent("Timeout on %s command" % (line.split(None, 1)[0]))
			self.waitID = 0
		if returnResults:
			return self._stopCollecting()

	def nonEmptyNickname(self, user, forceDetails=False, includeUserType=False, shortenFacebook=False):
		"""Make sure not to output a null string for a user with no nickname.
		This method can handle user and ban parmlines as input, and also str or int userid.
		forceDetails causes userid and IP address to be included.
		If includeUserType is True, "User" or "Admin" will precede the user information.
		If shortenFacebook is True, Facebook ids are replaced with "Facebook" when both user and server versions are 5.3 or later.
		"""
		if isinstance(user, int): user = str(user)
		if isinstance(user, str):
			try: user = self.users[user]
			except KeyError:
				# Happens on servers where we can't see participants without being in the same channel.
				# These servers can make admins visible to an extent, and admins can send user messages.
				name = "<userid %s>" % (user)
				# We can't get any more info for this one.
				return name
		nickname = user.get("nickname")
		username = user.get("username")
		if shortenFacebook:
			sver = self.info.get("version")
			uver = user.get("version")
			if sver and uver and sver >= "5.3" and uver >= "5.3":
				username = re.sub(r'^\d+@facebook.com', 'Facebook', username)
		name = nickname
		idIncluded = False
		if name:
			name = '"' +name +'"'
			if username: name += " (" +username +")"
		else:
			if username:
				name = "(" +username +")"
			else:
				name = "<nameless user %s>" % (user.userid)
				forceDetails = True
				idIncluded = True
		if includeUserType:
			utype = user.usertype
			if utype == "1": utype = "User"
			elif utype == "2": utype = "Admin"
			else: utype = "UserType%s" % (utype)
			name = "%s %s" % (utype, name)
		if not forceDetails: return name
		ip = user.get("ipaddr")
		if not ip or ip.startswith("0.0.0.0"):
			ip = user.get("udpaddr")
			if not ip or ip.startswith("0.0.0.0"): ip = ""
			if ip: ip = "UDP " +ip.rsplit(":", 1)[0]
		if ip: ip = "from %s" % (ip)
		if ip: name += " " +ip
		if not idIncluded:
			name += " (userid %s)" % (user.userid)
		return name

	def channelname(self, id, isRawName=False, preserveRootName=False):
		"""Adjust channel names for printing as appropriate.
		Pass a channel ID, or a channel name with isRawName=True.
		"/" becomes "the root channel" unless preserveRootName is True.
		"""
		if isRawName: name = id
		else:
			ch = self.channels[id]
			try: name = ch.channel
			except: name = None
			if not name:
				# In case .channel goes away...
				# TT5 introduced .name and .parentid.
				name = ""
				while True:
					name = "/".join(ch.name, name)
					ch = self.channels[str(ch.parentid)]
					if not int(ch.parentid): break
				name += "/"
		if name == "/" and not preserveRootName:
			name = "the root channel"
		return name

	def collectingOutput(self, line):
		"""Indicate if output is being collected and collect it if so.
		"""
		if self._collecting == 2:
			self._outputCollection.append(ParmLine(line))
			return True
		return False

	def _startCollecting(self, id):
		"""Start collecting output for return to caller.
		See _handleCollection() for a description of the collection process.
		"""
		self.ev_idblockDone.clear()
		self.waitID = int(id)
		self._outputCollection = []
		self._collecting = 1

	def _stopCollecting(self):
		"""Stop collecting output for return to caller.
		Returns the output collected so far.
		See _handleCollection() for a description of the collection process.
		"""
		self._collecting = 0
		lines = self._outputCollection
		self._outputCollection = []
		return lines

	def output(self, line, raw=False, fromEvent=False):
		"""Call to print a line to the user about this server connection.
		Raw=True means leave out the server's shortname.
		fromEvent=True means this is from an asynchronous event.
		Material from events is still handled like non-event text
		if we are waiting for a command result.
		"""
		msg = TeamtalkServer.write
		if fromEvent and not self.waitID:
			msg = TeamtalkServer.writeEvent
		if raw: msg(line)
		else: msg("[%s] %s" % (self.shortname, line))

	def outputFromEvent(self, line, raw=False):
		"""For event output. See output() for details.
		"""
		log.log("ttcom",self.shortname+": "+line)
		log.log(self.shortname,line)
		self.output(line, raw, fromEvent=True)

	def errorFromEvent(self, line, raw=False):
		"""For event error output. See output() for details.
		"""
		self.output(line, raw, fromEvent=True)

	def summarizeChannels(self):
		"""Summarize who is where on this server.
		This current user is omitted.
		"""
		if self.state != "loggedIn":
			state = self.state
			if self.conn and self.conn.state and self.conn.state != self.state:
				state += "/" +self.conn.state
			self.output(state)
			return
		users = [u for u in self.users.values() if u.userid != self.me.userid]
		if not len(users):
			self.output("No users are connected.")
			return
		activeChannels = {}
		for user in users:
			channel = user.get("channel")
			if channel is None:
				cid = user.get("chanid")
				# ToDo: The next line threw a KeyError once, Jan 29 2019, on Laura's server.
				if cid: channel = self.channels[cid].channel
			if not channel: channel = ""
			activeChannels.setdefault(channel, [])
			activeChannels[channel].append(self.nonEmptyNickname(user, shortenFacebook=True))
		lines = []
		nchannels = 0
		nusers = 0
		for channel in sorted(activeChannels):
			people = activeChannels[channel]
			people.sort(key=lambda p: p.lower())
			n = len(people)
			nusers += n
			if channel:
				nchannels += 1
				lines.append("    %s (%d): %s" % (
					self.channelname(channel, True),
					n,
					", ".join(people)
				))
			else:
				lines.append("    %d not in a channel: %s" % (
					n,
					", ".join(people)
				))
		lines.insert(0, "Users %d, active channels %d:" % (nusers, nchannels))
		self.output("\n".join(lines))

	def summarizeVersions(self, proto=None):
		"""Summarize users by TeamTalk packet protocol, client name, and client version on this server.
		This current user is omitted.
		proto, if given, restricts to a particular packet protocol by number. -1 means all but 0.
		"""
		if self.state != "loggedIn":
			state = self.state
			if self.conn and self.conn.state and self.conn.state != self.state:
				state += "/" +self.conn.state
			self.output(state)
			return
		users = [u for u in self.users.values() if u.userid != self.me.userid]
		if not len(users):
			self.output("No users are connected.")
			return
		versions = {}
		for user in users:
			version = user.get("version")
			if version is None: version = ""
			client = user.get("clientname")
			if client is None: client = ""
			protocol = user.get("packetprotocol")
			if proto == -1:
				if protocol == "0": continue
			elif proto is not None:
				if protocol != str(proto): continue
			if protocol is None: protocol = "pp<unknown>"
			else: protocol = "pp{0}".format(protocol)
			version = "{0} {1} {2}".format(protocol, client, version).strip()
			versions.setdefault(version, set())
			versions[version].add(self.nonEmptyNickname(user, shortenFacebook=True))
		lines = []
		nversions = 0
		nusers = 0
		for version in sorted(versions):
			people = list(versions[version])
			people.sort(key=lambda p: p.lower())
			n = len(people)
			nusers += n
			if version:
				nversions += 1
				lines.append("%6d %s: %s" % (
					n, version, ", ".join(people)
				))
			else:
				lines.append("%6d without version or clientname: %s" % (
					n,
					", ".join(people)
				))
		if not len(lines):
			self.output("No users matched the filter.")
			return
		lines.insert(0, "Users %d, versions/clients %d:" % (nusers, nversions))
		self.output("\n".join(lines))

	def subBitNames(self):
		"""Return a list of bit names for sublocal and subpeer.
		"""
		if self.is5():
			bitnames = [
				"user messages", "channel messages",
				"broadcast messages", "notUsed",
				"audio", "video",
				"desktop", "desktopAccess",
				"stream"
			]
		else:
			bitnames = [
				"user messages", "channel messages",
				"broadcast messages",
				"audio", "video",
				"desktop", "desktopAccess",
			]
		return bitnames

	def updateParms(self, category, parms, newParms, silent=False, preserve=[]):
		"""Update parms with newParms and report changes as appropriate.
		If preserve is a nonempty list or tuple, any parameters not included in preserve or newParms are removed from parms; i.e., newParms replaces parms except for preserved elements.
		"""
		oldParms = parms.copy()
		if preserve:
			parms.clear()
			for k in preserve: parms[k] = oldParms[k]
		parms.update(newParms)
		# Special handling of chanid on TT5 servers.
		if (("parentid" in newParms and "chanid" in oldParms)
		or "name" in newParms):
			self._updateChannelValue(parms)
		# Special handling of status changes.
		if ("statusmode" in newParms or "statusmsg" in newParms) and "statustime" not in parms:
			parms["statustime"] = time.time()
		if silent: return
		all = set(oldParms.keys()) | set(parms.keys())
		buf = []
		statusDone = False
		for k in sorted(all):
			if k == "statustime": continue
			v1 = oldParms.get(k)
			v2 = parms.get(k)
			# Type-independent way to avoid None problems below.
			if v1 is None and v2 is not None: v1 = v2.__class__()
			elif v2 is None and v1 is not None: v2 = v1.__class__()
			# Special handling of statuses (mode and message).
			if k == "statusmsg" or k == "statusmode":
				if v1 == v2: continue
				if not statusDone: self.doStatus(buf, parms, oldParms)
				statusDone = True
				continue
			# Special handling of sublocal and subpeer.
			if k == "sublocal" or k == "subpeer":
				if v1 == v2: continue
				# Lower case are subscriptions, upper case are intercepts.
				# See .subBitNames() for longer names.
				if self.is5():
					bitcount = 32
					bitnames = [
						"u", "c", "b", "0", "a", "v", "d", "x", "s", "1", "2", "3", "4", "5", "6", "7",
						"U", "C", "B", "00", "A", "V", "D", "X", "S", "11", "22", "33", "44", "55", "66", "77"
					]
				else:
					bitcount = 16
					bitnames = [
						"u", "c", "b", "a", "v", "d", "x", "s",
						"U", "C", "B", "A", "V", "D", "X", "S"
					]
				if k == "sublocal":
					ki = "local subscription changes"
				else:
					ki = "remote subscription changes"
				mask = 1
				bitbuf = []
				for b in range(0, bitcount):
					b1 = 0 if v1 == '' else int(v1) & mask
					b2 = 0 if v2 == '' else int(v2) & mask
					if b1 == b2:
						mask <<= 1
						continue
					if b2 and not b1:
						item = "+"
					else:
						item = "-"
					item += bitnames[b]
					bitbuf.append(item)
					mask <<= 1
				bitbuf = " ".join(bitbuf)
				buf.append("%s: %s" % (ki, bitbuf))
				continue
			# Special things we don't want to report normally (or at all).
			if k == "udpaddr":
				# Ignore UDP ports, which can change really often.
				v1,v2 = v1.rsplit(":", 1)[0], v2.rsplit(":", 1)[0]
				if v1 == "[::]" or v1 == "0.0.0.0": v1 = ""
				if v2 == "[::]" or v2 == "0.0.0.0": v2 = ""
			# Skip parms that did not change in substance.
			if v1 == v2 or (not v1 and not v2):
				continue
			elif v1 and not v2:
				buf.append("%s cleared" % (k))
				continue
			elif v2 and not v1:
				buf.append("%s \"%s\"" % (k, v2))
				continue
			# v1 != v2:
			if v1.startswith("[") and v2.startswith("["):
				# A list value.
				l1 = v1[1:-1].split(",")
				l2 = v2[1:-1].split(",")
				if len(l1) == len(l2):
					for i in range(0, len(l1)):
						v1 = str(l1[i])
						v2 = str(l2[i])
						if v1 != v2:
							ki = "%s[%d]" % (k, i+1)
							self.includeUpdate(buf, ki, v1, v2)
					continue
			self.includeUpdate(buf, k, v1, v2)
		buf = ", ".join(buf)
		if not buf: return
		if category:
			buf = "%s: %s" % (category, buf)
		self.outputFromEvent(buf)

	def _updateChannelValue(self, chan):
		"""For TT5 servers, update chan.channel in case name or parentid changed.
		The updateChannel event does not include the .channel property on TT5.
		"""
		path = "/"
		c = chan
		while c.parentid and c.parentid != "0":
			path = "/%s%s" % (c.name, path)
			c = self.channels[c.parentid]
		chan["channel"] = path

	def addrAndPort(self, udpaddr):
		"""Split and return address and port out of a UDP address.
		Input formats: 1.2.3.4:5678 or [IPV6addr]:5678.
		"""
		if not udpaddr: return ("","")
		if "]:" in udpaddr:
			addr,port = udpaddr.split("]:", 1)
			addr = addr.replace("[", "")
		else:
			addr,port = udpaddr.split(":", 1)
		return addr,port

	def makeTTString(self, userInfo=None, cid=None, verGiven=None):
		"""Make a string that can be saved as a .tt file.
		userInfo, if passed, is a dict containing username and password keys.
		cid, if passed, is a string or integer channelid to join.
		verGiven, if passed, is the intended TeamTalk client version (i.e., "5.1").
		Returns the string formed.
		Requires an active and logged-in server connection.
		"""
		if self.state != "loggedIn":
			return ""
		if verGiven: ver = verGiven
		else:
			ver = self.info.version
			if ver < "5.0": ver = "4.0"
			else:
				try: ver = re.sub(r'(\d\.\d)\..*', r'\1', ver)
				except: ver = ""
			if not ver: ver = "5.0"
		tmpl = (
"""<?xml version="1.0" encoding="UTF-8" ?>
<teamtalk version="%(ver)s">
    <host>
        <name>%(name)s</name>
        <address>%(hostaddr)s</address>
        <password>%(srvpasswd)s</password>
        <tcpport>%(tcpport)s</tcpport>
        <udpport>%(udpport)s</udpport>
        <encrypted>%(encrypted)s</encrypted>
        <auth>
            <username>%(username)s</username>
            <password>%(password)s</password>
        </auth>
        <join>
            <channel>%(channel)s</channel>
            <password>%(chanpassword)s</password>
        </join>
    </host>
</teamtalk>
""")
		serverInfo = AttrDict(self.info.copy())
		serverInfo["encrypted"] = self.encrypted
		if not userInfo:
			userInfo = {
				"username": "",
				"password": ""
			}
		if cid:
			channel = self.channels[str(cid)]
		else:
			channel = AttrDict({"channel": "", "password": ""})
		ttinfo = {
			"name": self.shortname,
			"hostaddr": self.host,
			"srvpasswd": serverInfo.serverpassword or "",
			"tcpport": serverInfo.tcpport,
			"udpport": serverInfo.udpport,
			"encrypted": str(serverInfo.encrypted).lower(),
			"username": userInfo.username or "",
			"password": userInfo.password or "",
			"channel": channel.channel or "",
			"chanpassword": channel.password or "",
			"ver": ver
		}
		return tmpl % ttinfo

	def includeUpdate(self, lst, name, v1, v2):
		"""Include a change in the named item given old and new values.
		"""
		if v1 == v2: return
		if name == "nickname":
			# The original nickname is already printed, so no need to repeat it.
			lst.append("%s changed to \"%s\"" % (name, v2))
			return
		lst.append("%s changed from \"%s\" to \"%s\"" % (name, v1, v2))

	def doStatus(self, lst, parms, oldParms):
		"""Report user status changes intelligently.
		Helper for updateParms(). Reporting rules:
			- Report only changed status flags.
			- Always report status message if present.
		Formats:
			status active
			status idle (away)
			status message "Busy"  (means only the message changed)
			status message cleared   (means that's all that happened)
		"""
		oldstat,newstat = int(oldParms.get("statusmode", 0)), int(parms.get("statusmode", 0))
		changes = []
		bitsleft = 0xFFFFFFFF
		changes.extend(self.doFlagBits(oldstat, newstat, 3, ["active", "idle", "question", "stat3"]))
		bitsleft ^= 3
		changes.extend(self.doFlagBits(oldstat, newstat, 256, ["male", "female"]))
		bitsleft ^= 256
		changes.extend(self.doFlagBits(oldstat, newstat, 512, ["disabled video", "enabled video"]))
		bitsleft ^= 512
		changes.extend(self.doFlagBits(oldstat, newstat, 2048, ["stopped streaming", "started streaming"]))
		bitsleft ^= 2048
		changes.extend(self.doFlagBits(oldstat, newstat, bitsleft, None))
		buf = ", ".join(changes)
		stat = parms.get("statusmsg")
		oldstat = oldParms.get("statusmsg")
		if stat:
			if buf: buf += " (" +stat +")"
			else: buf = 'message "' +stat +'"'
		elif not buf and oldstat:
			buf = "message cleared"
		if not buf: return
		# Include the time since the last status change.
		self.play("status.wav")
		statusTime = time.time()
		if parms.get("statustime") is None:
			diff = ""
		else:
			diff = self.secsToTime(statusTime -parms["statustime"])
		parms["statustime"] = statusTime
		statbuf = ""
		# Only for non-zero times.
		if diff.replace("0", "").replace(":", ""): statbuf = " after {0}".format(diff)
		if buf: buf = "status {0}{1}".format(buf, statbuf)
		if buf: lst.append(buf)

	@staticmethod
	def secsToTime(secs):
		"""Convert integer or float seconds to hh:mm:ss. Hours are allowed to exceed 24. Float seconds are rounded to the nearest second.
		This function is meant for durations more than clock times.
		"""
		if isinstance(secs, float): secs = int(secs+0.5)
		# Because secs is now int, hh, mm, and ss will become ints.
		mm,ss = divmod(secs, 60)
		hh,mm = divmod(mm, 60)
		return "{0:02d}:{1:02d}:{2:02d}".format(hh, mm, ss)

	# Used by doFlagBits().
	default_bitnames = [("off%d" % (bit+1), "on%d" % (bit+1)) for bit in range(0, 32)]

	def doFlagBits(self, oldval, newval, bits=None, names=None):
		"""Return a list indicating what changed between oldval and newval.
		oldval, newval: Old and new int values to compare.
		bits: An int of the bits to examine, defaults to 0xFFFFFFFF.
		names: A list of names for the bits. Possibilities:
			- None or not passed: Reports each bit as on/off<n>, <n> being the 1-based bit number.
			- List of one name per set bit in bits, lsb first.
			  Missing values here will be handled as for the previous case.
			- List of one name for each combination of bits in bits.
			  Requires len(names) to be one more than the value
			  obtained by collecting all 1 bits in bits at the LSB end.
		Names are usually strings, but when naming bits individually,
		a name can be a two-element list or tuple of (offName, onName).
		Examples for bits=3, oldval=0, and newval=2, then for oldval=2 and newval=0:
		names=["b1","b2"]: "b2", "b1"
		names=["v1","v2","v3","v4"]: "v3", "v1"
		names=None: "on2", "off2"
		"""
		changes = []
		if not names:
			# Name all bits "on" or "off" and their number, 1-based from LSB;
			# see default_bitnames above.
			names = []
		if not bits: # None or 0
			# Use all bits.
			bits = 0xFFFFFFFF
			cnt = 32
		else:
			# Arrange for oldval and newval to contain the wanted bits at the LSB end.
			# bits is also changed to be the mask for the bits at the new position,
			# which makes bits double as a count of possible bit combinations (minus 1).
			# cnt becomes the number of bits being used.
			bits,oldval,newval,cnt = self.collectBits(bits, oldval, newval)
		# bits is non-zero, and all bits are at the LSB end.
		if len(names) == bits+1:
			# Bits named as a unit.
			if oldval & bits != newval & bits:
				changes.append(names[newval])
		else:
			# Bits named individually, some possibly by default naming.
			for i in range(0, cnt):
				if i < len(names):
					name = names[i]
				else:
					name = self.default_bitnames[i]
				if type(name) is str:
					name = ("", name)
				o = oldval & 1
				n = newval & 1
				if n and not o:
					name = name[1]
				elif o and not n:
					name = name[0]
				else:
					name = ""
				if name:
					changes.append(name)
				oldval >>= 1
				newval >>= 1
		return changes

	def collectBits(self, bits0, oldval0, newval0):
		"""Arrange for oldval and newval to contain the wanted bits at the LSB end.
		bits is also changed to be the mask for the bits at the new position,
		which makes bits double as a count of possible bit combinations (minus 1).
		cnt becomes the number of bits being used.
		"""
		bits,oldval,newval,cnt = (0,0,0,0)
		newbit = 1
		while bits0:
			if bits0 & 1:
				bits |= newbit
				if oldval0 & 1: oldval |= newbit
				if newval0 & 1: newval |= newbit
				newbit <<= 1
				cnt += 1
			bits0 >>= 1
			oldval0 >>= 1
			newval0 >>= 1
		return (bits, oldval, newval, cnt)

# Event functions, corresponding to actual event words sent by Teamtalk servers.

	def event__connected_(self, parms):
		"""Internally-generated event signaling connect/welcome to server.
		The "welcome" event will fire at about the same time.
		"""
		self.outputFromEvent("Connected")
		return True

	def event__disconnected_(self, parms):
		"""Internally-generated event signaling disconnect from server.
		"""
		buf = "Disconnected"
		reason = ""
		try: reason = self.conn.disconnectReason
		except: pass
		if reason:
			buf += " (" +reason +")"
		self.outputFromEvent(buf)
		self.clear()
		self._handleRecycling()
		return True

	def event_begin(self, parms):
		"""Sent after a request that includes "id=31" or similar.
		All text from this to the corresponding "end" event are the reply.
		Response collection circumvents this; see _handleCollection().
		"""
		if self.waitID and parms.get("id") == str(self.waitID):
			# Eat this one quietly.
			return True
		# Process this in the default manner.
		return False

	def event_end(self, parms):
		"""Sent after a request that includes "id=31" or similar.
		All text from this back to the corresponding "begin" event are the reply.
		Response collection circumvents this; see _handleCollection().
		"""
		if self.waitID and parms.get("id") == str(self.waitID):
			# Signal the end of the corresponding command's reply.
			self.waitID = 0
			self.ev_idblockDone.set()
			# Then eat this line quietly.
			return True
		# Process this in the default manner.
		return False

	def event_welcome(self, parms):
		"""Sent on successful connection to the server.
		"""
		self.updateParms("Welcome", self.info, parms, silent=True)
		userid = self.info.userid
		self.users.setdefault(userid, AttrDict())
		self.me = self.users[userid]
		self.me["userid"] = userid
		return True

	def event_ok(self, parms):
		"""Sent at the end of a successful login process.
		Also sent on a successful ChangeNick command and at other times.
		There are no parameters for this event.
		"""
		if self.state == "loggingIn":
			self.state = "loggedIn"
			self.play_sounds=1
			self.outputFromEvent("Login successful (server version %s)" % (
				self.info.version[:3]
			))
			self.lastError = None
			self.ev_loggedIn.set()
			self._handleInitChannel(parms)
			return True
		# Handle in the default manner otherwise.
		return False

	def _handleInitChannel(self, parms):
		"""Handle any initial channel joining on login.
		"""
		me = self.me
		loginParms = self.loginParms
		# chanid overrides channel but either is allowed.
		chanid = loginParms.get("chanid")
		channel = loginParms.get("channel")
		if not chanid and channel: 
			if channel == "/": chanid = 1
			else:
				channel = [c for c in self.channels.values()
					if channel == self.channelname(c["channelid"])
				]
				if len(channel) != 1: return
				chanid = channel[0].chanid
		if not chanid: return
		line = "join chanid={0}".format(str(chanid))
		pw = loginParms.get("chanpassword")
		if pw: line += ' password="{0}"'.format(pw)
		self.send(line)

	def event_accepted(self, parms):
		"""Sent when a login is accepted, before user/channel updates.
		Includes info about the just-logged-in user.
		For the signal of successful login completion, see the "ok" event.
		"""
		self.updateParms("Login accepted", self.users[parms['userid']], parms, silent=True)
		udpaddr = list(self.users.values())[0].get("udpaddr")
		if (not udpaddr
		or udpaddr == "[::]:0"
		):
			#self.errorFromEvent("WARNING: null UDP address, XP clients will freeze briefly.")
			pass
		self.reportRightsIssues()
		return True

	def reportRightsIssues(self):
		"""Report user rights values that could compromise use of this program on a server.
		"""
		try: rights = int(self.me["userrights"])
		except KeyError: return
		if not (rights & 0x1):
			self.errorFromEvent("Warning: Multiple logins disallowed")
		if not (rights & 0x2):
			self.errorFromEvent("Warning: Unable to see channel participants")

	def event_loggedin(self, parms):
		"""Sent when a user successfully logs into the server.
		"""
		self.users.setdefault(parms.userid, AttrDict())
		# For when someone pulls a list of users from several servers at once.
		self.play("in.wav")
		self.users[parms['userid']].server = self
		self.updateParms("Logged in", self.users[parms['userid']], parms, silent=True)
		if (self.state != "loggingIn"
		and (self.users[parms.userid].nickname)):
			self.outputFromEvent("%s logged in" %
				(self.nonEmptyNickname(parms.userid, False, True, shortenFacebook=True)
			))
		return True

	def event_serverupdate(self, parms):
		"""Sent when the server info is being updated.
		"""
		self.updateParms("Server update", self.info, parms, silent=(self.state=="loggingIn"))
		return True

	def event_addchannel(self, parms):
		"""Sent when a channel is created and when this user is logging in.
		"""
		self.channels.setdefault(parms.channelid, AttrDict())
		self.updateParms("Add channel", self.channels[parms['channelid']], parms, silent=True)
		# Only show channel creations if we're not logging in right now.
		# Otherwise there's quite a flood of these on some servers.
		if self.state != "loggingIn":
			self.outputFromEvent("New channel %s" % (self.channels[parms.channelid].channel))
		return True

	def event_removechannel(self, parms):
		"""Sent when a channel is removed from the server.
		"""
		self.outputFromEvent("Removed channel %s" % (self.channels[parms.channelid].channel))
		del self.channels[parms['channelid']]
		return True

	def event_updatechannel(self, parms):
		"""Sent when a channel is changed.
		"""
		chan = self.channels[parms.channelid]
		name = chan.channel
		self.updateParms(name, self.channels[parms.channelid], parms, preserve=("parentid", "channel"))
		return True

	def event_adduser(self, parms):
		"""Sent when a user joins a channel and when this user is logging in.
		"""
		try: user = self.users[parms.userid]
		except KeyError:
			# This happens on servers where users are not visible until you join their channel. The loggedin event is not sent for these.
			self.users.setdefault(parms.userid, AttrDict())
			# For when someone pulls a list of users from several servers at once.
			self.users[parms.userid].server = self
			user = self.users[parms.userid]
			self.updateParms("Add user to channel", user, parms, True)
			self.users[parms['userid']].temporary = True
		else:
			self.updateParms("Add user", user, parms, True)
		self.play("join.wav")
		if self.state != "loggingIn":
			issues = ""
			self.outputFromEvent("%s joined %s" % (
				self.nonEmptyNickname(parms.userid, shortenFacebook=True),
				self.channelname(parms.channelid)
			))
		return True

	def event_removeuser(self, parms):
		"""Sent when a user leaves a channel.
		"""
		self.play("leave.wav")
		self.outputFromEvent("%s left %s" % (
			self.nonEmptyNickname(parms.userid, shortenFacebook=True),
			self.channelname(parms.channelid)
		))
		u = self.users[parms['userid']]
		del u['channelid']
		try: del u['channel']
		except KeyError: pass
		if self.users[parms.userid].temporary:
			# This user record sprang up on a channel join,
			# which means this server hides users until you join their channel.
			del self.users[parms.userid]
		return True

	def event_loggedout(self, parms):
		"""Sent when a user logs out of the server.
		"""
		if not parms:
			# This is a logout of this user.
			self.outputFromEvent("You are logged out")
			self.state = "connected"
			self.channels = dict()
			self.users = dict()
			userid = self.info.userid
			self.users.setdefault(userid, AttrDict())
			self.me = self.users[userid]
			self.me["userid"] = userid
			self.ev_loggedIn.clear()
			self.ev_loggedOut.set()
			self._handleRecycling()
			return True
		if self.users[parms.userid].nickname:
			self.play("out.wav")
			self.outputFromEvent("%s logged out" % (self.nonEmptyNickname(self.users[parms.userid], False, True, shortenFacebook=True)))
		del self.users[parms['userid']]
		return True

	def logout(self):
		"""Log out of the server.
		Returns True if logged out on exit and False if not.
		"""
		if not self.ev_loggedIn.isSet():
			return True
		self.ev_loggedOut.clear()
		self.sendWithWait("logout")
		self.ev_loggedOut.wait(10)
		if not self.ev_loggedOut.isSet():
			self.errorFromEvent("Timeout on logging out")
			return False
		if self.ev_loggedIn.isSet():
			self.errorFromEvent("Timeout on logging out (loggedIn flag still set)")
			return False
		return True

	def event_updateuser(self, parms):
		"""Sent when a user's status or other information changes.
		"""
		try: user = self.users[parms.userid]
		except KeyError:
			# This happens on servers where users are not visible until you join their channel. The loggedin event is not sent for these.
			# Admin logins, even predating this instance's login, send this event without a corresponding previous loggedin event.
			self.users.setdefault(parms.userid, AttrDict())
			# For when someone pulls a list of users from several servers at once.
			self.users[parms.userid].server = self
			user = self.users[parms.userid]
			# ToDo: Making this completely silent might not always be best, but the alternative tends to include a lot of unhelpful subscription change information.
			self.updateParms("Add user to server", user, parms, True)
			self.users[parms['userid']].temporary = True
		else:
			name = self.nonEmptyNickname(parms.userid, shortenFacebook=True)
			self.updateParms(name, self.users[parms['userid']], parms)
		return True

	def event_messagedeliver(self, parms):
		"""Sent when a public or private message reaches this user. Message types that can arrive here:
			- Channel messages when this user is in a channel or intercepting someone's channel messages.
			- User messages to this user or to someone for whom this user is intercepting user messages.
			- Broadcast messages to the entire server.
			- Typing indicators from TeamTalk clients that send them.
			- Private user messages from another text client that implements this feature.
		"""
		msg = self.formattedMessage(parms)
		if msg: self.outputFromEvent(msg)
		return True

	def formattedMessage(self, parms):
		"""Return a formatted (for speaking or printing) version of an incoming message.
		Supports user, channel, and broadcast messages, intercepts, and typing indicators.
		"""
		msg = ""
		mtype = parms.type
		content = parms.content.replace(r'\r\n', '\r\n')
		if mtype == "1":
			# User message.
			this = self.me.userid
			if parms.destuserid == this:
				# No need to report the destuserid when it's me.
				self.play("user.wav")
				msg = ("User message from %s:\n%s" % (
					self.nonEmptyNickname(parms.srcuserid, shortenFacebook=True),
					content
				))
			else:
				# This must come from a user message intercept.
				self.play("user.wav")
				msg = ("User message from %s to %s:\n%s" % (
					self.nonEmptyNickname(parms.srcuserid, shortenFacebook=True),
					self.nonEmptyNickname(parms.destuserid, shortenFacebook=True),
					content
				))
		elif mtype == "2":
			# Channel message.
			this = self.me.channelid
			if this and parms.channelid == this:
				# We shouldn't need to report the channel name,
				# because a user can't be in more than one at once anyway.
				self.play("channel.wav")
				msg = ("Channel message from %s:\n%s" % (
					self.nonEmptyNickname(parms.srcuserid, shortenFacebook=True),
					content
				))
			else:
				# This must come from a channel message intercept.
				msg = ("Channel message from %s to %s:\n%s" % (
					self.nonEmptyNickname(parms.srcuserid, shortenFacebook=True),
					parms.channel,
					content
				))
		elif mtype == "3":
			# Broadcast message.
			self.play("broadcast.wav")
			msg = ("*** Broadcast message from %s:\n%s" % (
				self.nonEmptyNickname(parms.srcuserid, shortenFacebook=True),
				content
			))
		elif mtype == "4":
			# User typing start/stop message (TT 4.3+ non-Classic).
			# Format: typing\r\n{0|1}, 1=typing and 0=stopped.
			content = content.replace('\r\n', ' ')
			content = content.replace(r'\r\n', ' ')
			this = self.me.userid
			if parms.destuserid == this:
				# No need to report the destuserid when it's me.
				msg = ("User %s %s" % (
					self.nonEmptyNickname(parms.srcuserid, shortenFacebook=True),
					content
				))
			else:
				# This must come from a user message intercept.
				# In TT 4.3, these seem not to be sent to interceptors,
				# but the code is here in case that's ever supported.
				msg = ("User %s %s to %s" % (
					self.nonEmptyNickname(parms.srcuserid, shortenFacebook=True),
					content,
					self.nonEmptyNickname(parms.destuserid, shortenFacebook=True)
				))
		else:
			# Unknown message type, just dump it all.
			msg = ("messagedeliver %s" % (" ".join(
				[k+"="+v for k,v in parms.items()]
			)))
		return msg

	def event_joined(self, parms):
		"""Sent when this user joins a channel.
		There is a subsequent adduser event for this as well.
		"""
		self.play("join.wav")
		self.outputFromEvent("Joined %s" % (
			self.channelname(parms.channelid)
		))
		return True

	def event_left(self, parms):
		"""Sent when this user leaves a channel.
		There is a subsequent removeuser event for this as well.
		"""
		self.play("leave.wav")
		self.outputFromEvent("Left channel %s" % (
			self.channelname(parms.channelid)
		))
		return True

	def event_addfile(self, parms):
		"""Send when a file is offered in a channel.
		"""
		fid = "{0}:{1}".format(parms.chanid, parms.filename)
		self.play("file.wav")
		self.files.setdefault(fid, AttrDict())
		self.updateParms("Add file", self.files[fid], parms, silent=True)
		if self.state == "loggingIn": return True
		self.outputFromEvent("%s sent to %s file %s (id %s)" % (
			parms.owner,
			self.channelname(parms.chanid),
			parms.filename,
			parms.fileid
		))
		return True

	def event_removefile(self, parms):
		"""Send when a file is removed from a channel's offerings.
		"""
		fid = "{0}:{1}".format(parms.chanid, parms.filename)
		self.play("file.wav")
		self.outputFromEvent("File %s removed from channel %s" % (
			parms.filename,
			self.channelname(parms.chanid)
		))
		del self.files[fid]
		return True

	def event_kicked(self, parms):
		"""Sent when this user is kicked off the server.
		"""
		# This throws an error if the kicker is another instance of this user.
		# Occurs on servers that forbid simultaneous logins to the same account.
		kicker = self.nonEmptyNickname(parms.kickerid)
		self.outputFromEvent("%s has kicked you from the server" % (kicker))
		self.manualCM = (self.autoLogin != 2)
		return True

	def event_stats(self, parms):
		"""Sent in response to a querystats command.
		This command requires admin privileges on the server.
		"""
		buf = ["Server statistics:"]
		for k in parms:
			buf.append("    %s: %s" % (k, parms[k]))
		self.outputFromEvent("\n".join(buf))
		return True

	def event_useraccount(self, parms):
		"""Sent in response to a ListAccounts command.
		One of these is sent for each account defined.
		"""
		parmline = ParmLine("useraccount", parms)
		self.outputFromEvent(parmline)
		return True

	def event_userbanned(self, parms):
		"""Sent in response to a ListBans command.
		One of these is sent for each ban that exists.
		"""
		parmline = ParmLine("userbanned", parms)
		self.outputFromEvent(parmline)
		return True

	def event_pong(self, parms):
		"""Sent in response to a ping command.
		This should only fire if the user sends a ping;
		internally generated pings do not reach this code.
		"""
		# Handle in the default manner.
		return False

	def event_error(self, parms):
		"""Sent when an error occurs.
		"""
		msg = "Error %s: %s" % (parms.number, parms.message)
		# I've never seen more than number and message parms,
		# but I like to be thorough.
		for parm in parms:
			if parm == "number" or parm == "message": continue
			msg += ", %s=%s" % (parm, parms[parm])
		self.outputFromEvent("*** " +msg)
		if not self.ev_loggedIn.isSet():
			self.lastError = msg
		# If this was during login, signal failure.
		if self.state == "loggingIn":
			self.state = "loginError"
			self.ev_loggedIn.set()
		return True

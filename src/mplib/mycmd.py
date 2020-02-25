"""MyCmd, cmd wrapper with more features for console-style applications.
"""
"""
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

# Note: We need to unset the TZ environment variable on Windows which is set
# by Cygwin using the UNIX naming convention (e.g. "Australia/Sydney") as
# this will be interpreted by the underlying Windows system using a
# completely different specification language (e.g. "EST+10:00EDT") thus
# creating timezone havoc. Leaving it empty results in the local timezone
# being used which is what we want. Also note that we need to do this before
# the time or datetime modules have been imported or it has no effect.
# The below snippet is copied from a message archive: http://code.activestate.com/lists/python-win32/12335/
import sys, os
if sys.platform == 'win32':
	_tz = os.getenv('TZ')
	if _tz is not None and '/' in _tz:
		del os.environ['TZ']

# Full locale handling using user defaults.
if sys.platform == 'win32':
	from accessible_output2.outputs import auto
import locale
locale.setlocale(locale.LC_ALL, '')

if sys.platform == 'win32':
	output=auto.Auto()
import subprocess, time, re, shlex, tempfile
from cmd import Cmd
import argparse
import threading
try: from win32api import SetConsoleTitle
except ImportError: pass
import __main__
from mplib import log
from conf import conf

class classproperty(object):
	"""Allows for a class-level property. Example: speakEvents.
	Reference: answer 1 in http://stackoverflow.com/questions/3203286/how-to-create-a-read-only-class-property-in-python, 2017-04-04.
	"""
	def __init__(self, getter):
		self.getter = getter
	def __get__(self, instance, owner):
		return self.getter(owner)

class CommandError(Exception):
	"""An error generated by command-handling code. Output is cleaner for non-Python-savvy users.
	"""
	pass
	#def __repr__(self): return __str__(self)

class ArgumentParser(argparse.ArgumentParser):
	"""An override that blocks program exit on error.
	"""
	def exit(self, status=0, msg=""):
		raise CommandError(msg)

class MyCmd(Cmd):
	"""Custom wrapper for the cmd.Cmd class.
	Includes window title setting under Windows when win32 is available.
	Include a doc string in the main module; it is used as intro and version command text.
	Initialize as for cmd.Cmd(),
	define conf.name and conf.version (requires a conf module),
	optionally call .allowPython(True) to allow bang (!) Python escapes,
	then run with .run().
	Make do_*(self, line) methods to create commands.
	Override emptyline() if it should do more than just print a new prompt.
	The following commands are defined already:
		help, ?: Print list of commands.
		help or ? commandName: Provide help for specific commands from their do_*() method doc strings.
		about: Print the same intro as printed on startup.
		EOF, quit, exit:  Exit the interpreter. Run() returns.
		clear, cls:  Clear the screen if possible.
		errTrace: Print a traceback of the latest error encountered.
	The following static and class methods are defined for easing command implementation:
		getargs: Parse line into args.
		msg: Print all passed arguments through the right output channel.
		msgNoTime: Like msg but avoids printing timestamps if otherwise printing.
		msgFromEvent: Msg version intended for asynchronous event output.
		dequote: Remove starting and ending quotes on a line if present (rarely needed).
		msgError: Msg only intended for error message output.
		confirm: Present a Yes/No confirmation prompt.
		input_withoutHistory: input that tries not to include its input in readline history.
		getMultilineValue:  Get a multiline value ending with "." on its own line.
		linearList:  Print a list value nicely and with a name.
			Items are sorted (case not significant) and wrapped as necessary.
			Null elements are ignored.
			A filter can be passed to avoid nulls and/or rewrite items.
		selectMatch:  Let the user pick an item from a passed list.
			A custom prompt can also be passed,
			as can a translator function for adjusting how list items sort/print.
		callWithRetry:  Wrapper for subprocess launch functions that retries as needed on Cygwin.
	There are also these object methods:
		dispatchSubcommand(prefix, args): Dispatch a subcommand in args[0] by calling "prefix%s" % (args[0]).
			Example: self.dispatchSubcommand("account_", ["list", "all"]) calls self.account_list(["all"])
	"""

	# .ini section name where user-defined aliases are housed.
	AliasSect = "Aliases"

	def allowPython(self, allow=True):
		"""Decide whether or not to allow bang (!) Python escapes, for
		evaluating expressions and executing statements from the command line.
		"""
		if allow:
			self.do_python = self._do_python
		else:
			try: del self.do_python
			except: pass

	def _fixLine(self, line, doHelp):
		"""Helper for precmd that handles both commands and help requests.
		"""
		# Handle aliases and other user-defined command-line substitutions.
		line = self._doSubs(line)
		if not line:
			return line
		elif line[0] == "!" and "do_python" in self.__dict__:
			line = "python " +line[1:]
		cmd,args,line = self.parseline(line)
		if not cmd: return line
		try:
			cmd1 = self._commandMatch(cmd)
		except (CommandError, KeyError, ValueError):
			# No matches found.
			pass
		else:
			line = line.replace(cmd, cmd1, 1)
			cmd = cmd1
		if not doHelp and cmd.lower() in ["?", "help"]:
			line = line.replace(cmd, "", 1).lstrip()
			return "help " +self._fixLine(line, True)
		return line

	def precmd(self, line):
		"""Preprocessor that handles incompletely typed command names.
		Can also handle aliases.
		"""
		return self._fixLine(line, False)

	def _doSubs(self, line):
		"""Handle aliases and any other user-defined command-line substitutions.
		"""
		try: aliases = conf[self.AliasSect]
		except Exception: return line
		for lhs,rhs in aliases.items():
			pass
		return line

	def onecmd(self, line):
		"""Wrapper for Cmd.onecmd() that handles errors.
		Also handles line being a list, to ease execution of a single command from the command line of this app.
		"""
		if isinstance(line, list): line = self.lineFromArgs(line)
		try:
			line = self.precmd(line)
			result = Cmd.onecmd(self, line)
			return result
		except KeyboardInterrupt:
			self.msg("Keyboard interrupt")
		except Exception as e:
			self.msg(err())
			return

	@classmethod
	def lineFromArgs(cls, args):
		"""Build a line from args such that args are properly quoted for parsing back into a list.
		Used to execute a command from the command line of this app.
		"""
		args1 = []
		for arg in args:
			if True: arg = cls._fixParm(arg)
			elif len(arg) == 0 or " " in arg or "\t" in arg or "\r" in arg or "\n" in arg:
				if '"' not in arg: arg = '"' +arg +'"'
				elif "'" not in arg: arg = "'" +arg +"'"
				else: arg = cls._fixParm(arg)
			args1.append(arg)
		line = " ".join(args1)
		return line

	@classmethod
	def _fixParm(cls, parm):
		"""Quote parm if necessary so it can be added to a command line and parsed back into an arg later.
		"""
		if parm is None or parm == "": return '""'
		parm = parm.replace('"', r'\"').replace(r"\'", "'")
		if len(parm) == 0 or " " in parm or "\t" in parm or "\r" in parm or "\n" in parm:
			parm = '"' +parm +'"'
		return parm

	def dispatchSubcommand(self, prefix, args):
		"""Dispatch a subcommand in args[0] by calling "prefix%s" % (args[0]).
		Example: self.dispatchSubcommand("account_", ["list", "all"]) calls self.account_list(["all"])
		"""
		try:
			cmd = args.pop(0)
			cmd = self._commandMatch(cmd, prefix)
		except (IndexError, KeyError, ValueError):
			cmds = self._commands(prefix)
			cmds = [cmds[cmd] for cmd in sorted(cmds.keys())]
			raise CommandError("Subcommand must be one of: {0}".format(", ".join(cmds)))
		func = eval("self.{0}{1}".format(prefix, cmd))
		return func(args)

	def do_quit(self, line):
		"""Exit the program.  EOF, quit, and exit are identical.
		"""
		self.msg("Quit")
		return True

	def do_eof(self, line):
		"""Exit the program.  EOF, quit, and exit are identical.
		"""
		return self.do_quit(line)

	def do_exit(self, line):
		"""Exit the program.  EOF, quit, and exit are identical.
		"""
		return self.do_quit(line)

	def _do_python(self, line):
		"""Evaluate a Python expression or statement.
		Usage: python expr or python statement.
		Shorthand: !expr or !statement.
		Examples:
			!2+4
			!d = dict()
		Statements and expressions are evaluated in the context of __main__.
		"""
		result = None
		import __main__
		try: result = eval(line, __main__.__dict__)
		except SyntaxError:
			exec(line, __main__.__dict__)
			result = None
		self.msg(str(result))

	def do_errTrace(self, e=None):
		"""Provides a very brief traceback for the last-generated error.
		The traceback only shows file names (no paths) and line numbers.
		"""
		if not e: e = None
		print(errTrace(e))

	def emptyline(self):
		"""What happens when the user presses Enter on an empty line.
		This overrides the default behavior of repeating the last-typed command.
		"""
		return False

	def do_help(self, line):
		"""Print help for the program or its commands.
		"""
		if not line.strip() or len(line.split()) != 1:
			return Cmd.do_help(self, line)
		matches = [a for a in dir(self) if a.lower() == "do_"+line.lower().strip()]
		if len(matches) != 1:
			return Cmd.do_help(self, line)
		try: txt = eval("self."+matches[0]).__doc__
		except AttributeError:
			return Cmd.do_help(self, line)
		self.msg(self._formatHelp(txt))

	def _formatHelp(self, txt):
		"""Format help text for output.
		Tabs are translated to three spaces each.
		CR/LF pairs become just Newline.
		An attempt is made to remove indentation caused by Python class structure.
		If there is an initial blank line, it is removed.
		"""
		txt = txt.replace('\13\10', '\n').replace('\t', '   ')
		# Assume doc block starts immediately, not after a Newline.
		firstIndented = False
		if txt.startswith('\n'):
			# But it doesn't.
			txt = txt[1:]
			firstIndented = True
		# Find the smallest indent so we can remove it from all lines.
		# If the first line started without a leading Newline, ignore that one.
		ignoreNext = not firstIndented
		for line in txt.splitlines():
			noMinIndent = 99999
			minIndent = noMinIndent
			if ignoreNext:
				ignoreNext = False
				continue
			lineIndent = len(line) -len(line.lstrip())
			minIndent = min(minIndent, lineIndent)
		# Now apply what we found.
		if minIndent != noMinIndent:
			lines = []
			ignoreNext = not firstIndented
			for line in txt.splitlines():
				if ignoreNext:
					lines.append(line)
					ignoreNext = False
					continue
				line = line[minIndent:]
				lines.append(line)
			txt = "\n".join(lines)
		return format(txt)

	def run(self):
		"""Kick off the command-line interpreter.
		The command line of the program is allowed to consist of one command,
		in which case it is run and the program exits with its return code,
		or with 1 if the command returns a non-empty, non-integer value.
		Otherwise, the normal command loop is started.
		This call returns on quit/exit/EOF,
		or in the one-command-on-app-command-line case, after the command runs.
		The prompt is conf.name
		name is the name for the intro ("type 'help' for help") line.
		sys.argv is processed by this call,
		so if you have something to do with it, do it before calling.
		"""
		try: __main__.__doc__
		except: exit("The main module must define a doc string to be used as the startup screen")
		try: conf.name, conf.version
		except: exit("The main module must set conf.name and conf.version")
		self.plat = self._getPlatform()
		self.prompt = conf.name +"> "
		args = sys.argv[1:]
		if args:
			# Do one command (without intro or prompt) and exit.
			# The command's return value is returned to the OS.
			sys.exit(self.onecmd(args))
		name = '{0}\n\n{1} version {2}, type "help" or "?" for help.'.format(
			__main__.__doc__.strip(),
			conf.name,
			conf.version
		)
		try: SetConsoleTitle(conf.name)
		except: pass
		self.cmdloop(name)

	@staticmethod
	def _getPlatform():
		"""Get a string representing the type of OS platform we're running on.
	"""
		plat = sys.platform.lower()
		if plat[:3] in ["mac", "dar"]:
			return "mac"
		elif "linux" in plat:  # e.g., linux2
			return "linux"
		elif "win" in plat:  # windows, win32, cygwin
			return "windows"
		return plat

	@staticmethod
	def getPlatInfo():
		"""Return some info about the current platform as a string.
		Format for Windows: platform, winverstring maj.min.bld extra
			where "extra" is service packs and such.
			Example: win32, WinXP 5.1.2600 Service Pack 3
		Format for Mac and Linux: platform (may be more to come later).
		"""
		plat = sys.platform
		try:
			maj,min,bld,pl,extra = sys.getwindowsversion()
			try: pl = ["Win32s/Win3.1", "Win9x/Me", "WinNT/2000/XP/x64", "WinCE"][pl]
			except IndexError: pl = "platform " +str(pl)
			pl += " %d.%d.%d %s" % (maj, min, bld, extra)
			plat += ", " +pl
		except AttributeError: pass
		return plat

	@classmethod
	def launchURL(cls, url):
		"""Launch the given URL in a browser.  May not be supported on all platforms."
		Provides some protection against calls with non-URL strings.
		Use a browser= setting in Settings in the config file to select a specific browser.
		Raises a CalledProcessError with a returncode attribute if unsuccessful.
		Raises a RuntimeError if not supported on this platform.
		Returns True on success for compatibility with older code that expected a True/False result instead of exceptions.
		"""
		try: urltype = url.split(":", 1)[0].lower()
		except: urltype = ""
		if len(urltype) < 1 or not re.match(r'^[a-z0-9_]+$', urltype):
			raise ValueError("URL type not recognized")
		url = url.replace(" ", "%20").replace('"', "%22").replace("'", "%27")
		try: browser = conf["Settings"]["browser"]
		except KeyError: browser = None
		plat = sys.platform
		if browser:
			cmds = ([browser, url],)
		elif plat == "cygwin" or plat.startswith("win"):
			# On Cygwin, `cygstart' works but prevents Windows auto-login from working.
			# Running Explorer directly fixes that, at least on Windows XP.
			# [DGL, 2009-03-17]
			# It also works on ActivePython.
			cmds = (["explorer", url],)
		elif "linux" in plat.lower():
			cmds = (["xdg-open", url], ["firefox", url], ["lynx", url], ["links", url], ["w3m", url],)
		elif plat == "darwin":  # MacOS
			cmds = (["open", url],)
		else:
			raise RuntimeError("LaunchURL not supported on this platform")
		cls.msg("Web page launching.")
		for i,cmd in enumerate(cmds):
			try:
				subprocess.check_call(cmd)
				# Quit trying alternatives if that did not throw an exception.
				break
			except Exception as e:
				# Windows Explorer returns 1 on success! [DGL, 2017-09-30, Windows 10]
				if (isinstance(e, subprocess.CalledProcessError)
				and (plat == "cygwin" or plat.startswith("win"))
				and e.returncode == 1):
					# Call that a success by exiting the loop.
					break
				elif i+1 == len(cmds):
					# Fail if this is the last command to try.
					raise
				# Otherwise just try the next command quietly.
		# One of the commands succeeded.
		return True

	def debugging(self):
		"""Return True if debugging is enabled (via a debug key in Settings in the .ini file).
		"""
		try: return bool(conf["Settings"]["debug"])
		except KeyError: return False

	@staticmethod
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

	def do_about(self, line):
		"""
		Report the app name and version info (same as what prints on startup).
		"""
		self.msg(self.intro)

	def do_clear(self, line):
		"""
		Clears the screen.
		"""
		if self.plat == "windows" and sys.platform != "cygwin":
			os.system("cls")
			return ""
		os.system("clear")
		return ""

	def do_cls(self, line):
		"""
		Clears the screen.
		"""
		return self.do_clear(line)

	def _commands(self, prefix="do_"):
		"""Returns all available commands or subcommands as indicated by methods in this class with the given prefix.
		The return value is a dict where keys are lower-case commands and values are mixed-case as appearing in code as function names.
		The default prefix is "do_", which returns a list of commands available to the user.
		"""
		cmds = {}
		for funcname in [f for f in dir(self) if f.startswith(prefix)]:
			cmd = funcname[len(prefix):]
			cmds[cmd.lower()] = cmd
		return cmds

	def _commandMatch(self, cmdWord, prefix="do_"):
		"""
		Returns all available commands or subcommands, and the exact command word indicated by cmdWord.
		Implements command matching when an ambiguous command prefix is typed.
		"""
		cmds = self._commands(prefix)
		# An exact match wins even if there are longer possibilities.
		try: return cmds[cmdWord.lower()]
		except KeyError: pass
		# Get a list of matches, capitalized as they are in the code function names.
		cmdWord = cmdWord.lower()
		matches = [f for f in list(cmds.keys()) if f.startswith(cmdWord)]
		matches = [cmds[cmdKey] for cmdKey in matches]
		if len(matches) == 1: return matches[0]
		elif len(matches) == 0: raise CommandError('No valid command matches "{0}"'.format(cmdWord))
		return self.selectMatch(matches, "Which command did you mean?")

	@classmethod
	def confirm(cls, prompt):
		"""Get permission for an action with a y/n prompt.
		Returns True if "y" is typed and False if "n" is typed.
		Repeats request until one or the other is provided.
		KeyboardInterrupt signals equate to "n"
		"""
		if not prompt.endswith(" "): prompt += " "
		l = ""
		while not l:
			l = cls.input_withoutHistory(prompt)
			l = l.strip()
			l = l.lower()
			if l == "keyboardinterrupt": l = "n"
			if l in ["n", "no"]: return False
			elif l in ["y", "yes"]: return True
			cls.msg("Please enter y or n.")
			l = ""

	@classmethod
	def selectMatch(cls, matches, prompt=None, ftran=None, allowMultiple=False, sort=True, promptOnSingle=False):
		"""
		Return one or more matches from a set.
		matches: The set of matches to consider.
		prompt: The prompt to print above the match list. If none is provided, a reasonable default is used.
		ftran: The function on a match to make it into a string to print. If not provided, no translation is performed and each match must natively be printable.
		allowMultiple: If True, lets the user enter multiple numbers and returns a list of matches. Defaults to False.
			When this is set, the user may also type the word "all" to select the entire list.
			An exclamation (!) before a list of numbers means select all but the listed ones.
		sort: True by default to sort entries, False to leave unsorted.
		promptOnSingle: If True, the user is prompted for a selection even when there is only one option. Defaults to False.
		"""
		if not ftran: ftran = lambda m: m
		mlen = len(matches)
		if mlen == 0: raise CommandError("No matches found")
		if mlen == 1 and not promptOnSingle:
			if allowMultiple: return [matches[0]]
			else: return matches[0]
		if sort: matches = sorted(matches, key=ftran)
		try:
			mlist = [str(i+1) +" " +ftran(match) for i,match in enumerate(matches)]
		except (TypeError, UnicodeDecodeError):
			mlist = [str(i+1) +" " +str(ftran(match)) for i,match in enumerate(matches)]
		if not prompt:
			if allowMultiple: prompt = "Select one or more options:"
			else: prompt = "Select an option:"
		m = prompt +"\n   " +"\n   ".join(mlist)
		cls.msg(m)
		if allowMultiple: prompt = """Selections (or Enter to cancel), ! to negate, "all" for all: """
		else: prompt = "Selection (or Enter to cancel): "
		l = ""
		while not l:
			l = cls.input_withoutHistory(prompt)
			l = l.strip()
			if not l: break
			if allowMultiple:
				try: return cls._collectSelections(matches, l)
				except (IndexError, SyntaxError):
					# Error message printed by _collectSelections().
					l = ""
					continue
			try:
				if l and int(l): return matches[int(l)-1]
			except IndexError:
				cls.msg("Invalid index number")
				l = ""
		raise CommandError("No option selected")

	def help_selection(self):
		"""Help for how to select items via selectMatch().
		"""
		print("""
Handling Selection Lists:

When a numbered list of options appears followed by a prompt to make one or more selections, you can type a number to select just that item. When multiple selections are supported, the following also work:
	- Type numbers separated by spaces and/or commas to select more than one option.
	- Type numbers separated by dashes to select a range; for example, 2-5.
	- Merge these as needed to select complex sets of options; e.g., 2, 4, 6-9, 12.
	- Type a number, list of numbers, range, or combination, all preceded by an exclamation mark (!) to select all but the given option(s). Examples: !5 for all but option 5, and !3 5 6-9 for all but options 3, 5, 6, 7, 8, and 9.
	- Type "all" to select all available options.
""".strip())

	@classmethod
	def _collectSelections(cls, matches, l):
		"""
		Return the matches selected by l. Supported syntax examples (numbers are 1-based):
			9: Just the 9th match.
			2,5 or 2 5 or 2, 5: Matches 2 and 5.
			2-5 or 2..5: Matches 2 3 4 and 5.
			2-5, 9 etc.: Matches 2 through 5 and 9.
			!9: All but the 9th match. Works for all other above ranges as well.
			all: All matches.
		"""
		# First some syntactic simplifications and easy cases.
		l = l.strip()
		if l.lower() == "all":
			return matches
		negating = False
		if l.startswith("!"):
			l = l[1:].lstrip()
			negating = True
		# Comma/space combos become a single comma.
		l = re.sub(r'[ \t,]+', r',', l)
		# .. becomes -
		l = re.sub(r'\.\.', '-', l)
		indices = set()
		# Now make units, each being an index or a range.
		units = l.split(',')
		for unit in units:
			if "-" in unit:
				start,end = unit.split("-")
			else:
				start,end = unit,unit
			start = int(start)
			end = int(end)
			if start < 1 or start > len(matches): 
				m = "%d is not a valid index" % (start)
				raise IndexError(m)
			if end < 1 or end > len(matches): 
				m = "%d is not a valid index" % (end)
				raise IndexError(m)
			indices.update(list(range(start-1, end)))
		if negating: indices = set(range(0, len(matches))) -indices
		return [matches[i] for i in sorted(indices)]

	@classmethod
	def msgNoTime(cls, *args):
		kwargs = {"noTime": True}
		cls.msg(*args, **kwargs)

	@classmethod
	def msgFromEvent(cls, *args):
		kwargs = {"fromEvent": True}
		speakEvents = 0
		try: speakEvents = cls.speakEvents
		except: pass
		if not speakEvents: speakEvents = 0
		if int(speakEvents) != 0:
			mq_vo.extend(args)
		cls.msg(*args, **kwargs)

	@classmethod
	def msg(cls, *args, **kwargs):
		"""
		Arbitor of event output message format:
		"""
		indent1 = kwargs.get("indent1") or None
		indent2 = kwargs.get("indent2") or None
		s = ""
		started = False
		for item in args:
			if item is None: continue
			if started: s += " "
			started = True
			if type(item) is str: s += item
			else:
				s += str(item)
		if not started: return
		s1 = s
		s1 = format(s1, indent1=indent1, indent2=indent2)
		if kwargs.get("fromEvent"):
			mq.append(s1)
		else:
			print(s1)

	@classmethod
	def msgErrOnly(cls, *args, **kwargs):
		"""
		msg() but only for errors.
		"""
		if not args or args[0].startswith("ERROR"):
			cls.msg(*args, **kwargs)
		return

	@classmethod
	def getMultilineValue(cls):
		"""
		Get and return a possibly multiline value.
		The content is prompted for and terminated with a dot on its own line.
		An EOF also ends a value.
		"""
		cls.msg("Enter text, end with a period (.) on a line by itself.")
		content = ""
		while True:
			try:
				line = input("")
			except EOFError:
				line = "."
			line = line.strip()
			if line == ".":
				break
			if content:
				content += "\n"
			content += line
		return content

	@staticmethod
	def linearList(name, l, func=lambda e: str(e)):
		"""
		List l on a (possibly long and wrap-worthy) line.
		The line begins with a header with name and entry count.
		Null elements are removed.  If you don't want this, send in a func that doesn't return null for any entry.
		"""
		l1 = sorted([_f for _f in map(func, l) if _f], key=lambda k: k.lower())
		if len(l) == 0:
			return "%s: 0" % (name)
		return "%s (%0d): %s." % (name, len(l1), ", ".join(l1))

	@staticmethod
	def lineList(name, l, func=lambda e: str(e)):
		"""
		List l one line per entry, indented below a header with name and entry count.
		Null elements are removed.  If you don't want this, send in a func that doesn't return null for any entry.
		"""
		l1 = sorted([_f for _f in map(func, l) if _f], key=lambda k: k.lower())
		if len(l) == 0:
			return "%s: 0" % (name)
		return "%s (%0d):\n    %s." % (name, len(l1), "\n    ".join(l1))

	@staticmethod
	def getargs(line, count=0):
		"""
		Parse the given line into arguments and return them.
		Args are dequoted unless count is non-zero and less than the number of arguments.
		In that case, all but the last arg are dequoted.
		Parsing rules are those used by shlex in Posix mode.
		"""
		# shlex.split dequotes internally.
		if not count or count >= len(line): return shlex.split(line)
		args = shlex.split(line)
		if len(args) < count: return args
		# Dequoted args up to but not including the last.
		args = args[:count]
		# Collect args without dequoting into the last one, then append it.
		tokenizer = shlex.shlex(line)
		[next(tokenizer) for i in range(0, count)]
		lastArg = " ".join([t for t in tokenizer])
		#print("LastArg: " +lastArg)
		args.append(lastArg)
		return args

	@staticmethod
	def dequote(line):
		"""Remove surrounding quotes (if any) from line.
		Also unescapes the quote removed if found inside the remaining string.
		"""
		if not line: return line
		if (line[0] == line[-1] and line[0] in ["'", '"']
		and len(shlex.split(line)) == 1):
			q = line[0]
			line = line[1:-1]
			line = line.replace('\\'+q, q)
		return line

	@staticmethod
	def input_withoutHistory(prompt=None):
		"""
		input() wrapper that keeps its line out of readline history.
		This is to avoid storing question answers like "1."
		"""
		l = input(prompt)
		if len(l) == 0: return l
		try: readline.remove_history_item(readline.get_current_history_length() -1)
		except (NameError, ValueError): pass
		return l

# Input helpers.

def input(prompt=None):
	try:
		return input0(prompt)
	except KeyboardInterrupt:
		return "KeyboardInterrupt"

__builtins__["input0"] = __builtins__["input"]
__builtins__["input"] = input

# Output helpers.

# Formatter for output.
import textwrap
fmt = textwrap.TextWrapper()
def format(text, indent1=None, indent2=None, width=79):
	"""
	Format text for output to screen and/or log file.
	Individual lines are wrapped with indent.
	"""
	if indent1 is None:
		indent1 = ""
		indent2 = "   "
	elif indent2 is None:
		indent2 = indent1 +"   "
	fmt.width = width
	lines = text.splitlines()
	wlines = []
	for line in lines:
		lineIndent = " " * (len(line) -len(line.lstrip()))
		fmt.initial_indent = indent1
		fmt.subsequent_indent = indent2 +lineIndent
		wlines.append("\n".join(fmt.wrap(line)))
	text = "\n".join(wlines)
	return text

class MessageQueue(list):
	def __init__(self, *args, **kwargs):
		self.holdAsyncOutput = False
		speechQueue = False
		if "speechQueue" in kwargs:
			speechQueue = kwargs["speechQueue"]
			del kwargs["speechQueue"]
		self.speechQueue = speechQueue
		list.__init__(self, *args, **kwargs)
		if speechQueue:
			self.thr = threading.Timer(0, self.watch)
			self.thr.setDaemon(True)
			self.thr.start()

	def output(self, nmsgs=0):
		"""
		Output nmsgs messages.
		If nmsgs is not passed, treat as if it were 0.
		If nmsgs is positive, output that many messages.
		If nmsgs is less than 0, output all pending messages.
		If nmsgs is 0:
			- If self.holdAsyncOutput is True, output nothing now.
			- Else, output as if nmsgs were -1.
		"""
		if nmsgs == 0:
			if self.holdAsyncOutput:
				nmsgs = 0
			else:
				nmsgs = -1
		while len(self) and nmsgs != 0:
			s = self.pop(0)
			print(s)
			if nmsgs > 0:
				nmsgs -= 1

	def watch(self):
		while True:
				while len(self):
					m = self.pop(0)
					say(m)
				time.sleep(0.1)

	def append(self, *args, **kwargs):
		list.append(self, *args, **kwargs)
		self.output()

mq = MessageQueue()
mq_vo = MessageQueue(speechQueue=True)

def pendingMessageCount():
	return len(mq)

def flushMessages(nmsgs):
	mq.output(nmsgs)

def err(origin="", exctype=None, value=None, traceback=None):
	"Nice one-line error messages."
	errtype,errval,errtrace = (exctype, value, traceback)
	exctype,value,traceback = sys.exc_info()
	if not errtype: errtype = exctype
	if not errval: errval = value
	if not errtrace: errtrace = traceback
	# Static error trace preservation for errTrace().
	err.val = errval
	err.trace = errtrace
	buf = ""
	if origin: buf += origin +" "
	name = errtype.__name__
	if name == "CommandError": name = ""
	if name: buf += name +": "
	try: buf += str(errval)
	except UnicodeDecodeError: buf += str(errval)
	for i in range(2, len(errval.args)):
		buf += ", " +str(errval.args[i])
	return buf

def errTrace(e=None):
	"""Provides a very brief traceback for the last-generated error.
	The traceback only shows file names (no paths) and line numbers.
	"""
	if e is None:
		try: e = err.trace
		except AttributeError:
			return "No error has been recorded yet."
	trc = []
	while e:
		l = e.tb_lineno
		fname = e.tb_frame.f_code.co_filename
		fname = os.path.basename(fname)
		trc.append("%s %d" % (fname, l))
		e = e.tb_next
	return ", ".join(trc)

def say(*args):
	"""
	On MacOS, speak via the default Mac voice.
	On Windows. speak via SayTools if available.
	"""
	try: s = " ".join(args)
	except TypeError: s = str(args)
	s = re.sub(r'[A-Z_]+', cleanForSpeech, s)
	plat = sys.platform
	if (plat == "cygwin" or plat.startswith("win")):
		if s!="" and s!=" ":
			try: output.speak(s)
			except: print(__main__.err())
	elif plat == "darwin": # MacOS
		cmd = ["say",]
		sprefix = os.environ.get("SAYPREFIX")
		if sprefix: s=sprefix+s
		subprocess.Popen(cmd, stdin=subprocess.PIPE, text=True).communicate(s)
	else:
		sprefix = os.environ.get("SAYPREFIX")
		if sprefix: s=sprefix+" "+s
		subprocess.Popen("spd-say", stdin=subprocess.PIPE, text=True).communicate(s)

def cleanForSpeech(m):
	"""
	Make a few adjustments to a string to make it sound better.
	Based on the Mac default voice (Alex).
	This is called by re.sub from say().
	"""
	s = m.group()
	return s


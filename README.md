#TTCom
This is my fork of the Team Talk Commander originally by Doug lee of dlee.org.
I am internally calling this TTCom 4.0 in order to distinguish this version from Doug's version.

If you just wish to download a binary, a release version is provided.

This version contains 3 things.

1. The ability to use the speak_events functionality on Windows via your screen reader and accessible_output2.

2. The ability to play sounds when teamtalk events occur. In addition, the ability to set server specific sound volume and sound pack.

3. realtime logging of teamtalk events into both a single and individual server log files.



Below is pasted the readme originally provided by Doug Lee himself.

#Readme
This is TTCom, the TeamTalk Commander, also informally referred to as
the TeamTalk Console or the TTCom text client.

This release of TTCom includes source code and a Windows stand-alone executable so that it may be run on Windows without requiring Python to
be installed.

IMPORTANT: As of July, 2019, TTCom requires Python 3.7 or later. The stand-alone Windows executable does not require Python to be installed, however.

Usage:

If running from source on Windows or if your OS does not include Python 3.7 already, install Python 3.7. Possible sources of Python 3.7 include
- http://www.python.org/ (preferred by this author for Python 3)
- http://www.activestate.com/activepython (requires an account and updates Python as a service)

Install this file set somewhere convenient, unzipped.

Copy ttcom_default.conf to ttcom.conf and edit to taste. This is where
servers and per-server behaviors are defined. The autoLogin parameter
determines which servers connect automatically on TTCom startup.

This author recommends setting a nickname for all servers at once by including something like this in ttcom.conf:

[server defaults]
nickname=My Name

If you don't do this, you will be called TTCom User everywhere you go.  Of course, change "My Name" above to what you want as a nickname.

If you want events to print as they occur for only the currently selected server, instead of for all connected servers, include silent=1 in
the above section. See ttcom_default.conf for further ideas.

On Windows, run ttcom.exe. If running from source (on Windows or anywhere else),
run TTCom by running ttcom.py through Python 3.7:

    python3 ttcom.py

	WARNING: Just typing "python ttcom.py" may try to run TTCom through Python 2, which will not work.

	If you get errors, make sure you are running Python 3.7 by typing python -V to check the version number.

You can also specify a server or servers on the command line, by their
shortnames from ttcom.conf, to connect to just those servers:

    python3 ttcom.py simon

To start TTCom without connecting to anything, use -n in place of a server name.

Type "?" or "help" at the TTCom command prompt to learn what is
possible. You can add a command name for help on that command; e.g.,
"?whoIs." Case is not important in command names.


Copyright (C) 2011-2019 Doug Lee

TTCom is released under the GNU Public License (GPL), a copy of which
appears in LICENSE.txt. iniparse, included in its entirety, comes with
its own license (also included).

See http://www.dlee.org/TTCom/ for history, downloads, etc.

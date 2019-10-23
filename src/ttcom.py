#! /usr/bin/env python3

"""TeamTalk Commander (TTCom)
Copyright (c) 2011-2019 Doug Lee.

This program is covered by version 3 of the GNU General Public License.
This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it under
certain conditions.
See the file LICENSE.txt for further information.
The iniparse module is under separate license and copyright;
see that file for details.
"""

import sys, threading
from TTComCmd import TTComCmd
# More for command-line Python support.
import os, time

if __name__ == "__main__":
	from conf import conf
	conf.name = "TTCom"
	conf.version = "4.0.0"
	args = sys.argv[1:]
	# Keep args out of the cmd system.
	del sys.argv[1:]
	noAutoLogins = False
	shortnames = []
	for arg in args:
		if arg == "-n":
			noAutoLogins = True
		else:
			noAutoLogins = True
			shortnames.append(arg)
	app = TTComCmd(noAutoLogins, shortnames)
	app.allowPython()
	if shortnames:
		cur = shortnames[-1]
		app.onecmd("server " +cur)
	app.run()

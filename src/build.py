# -*- coding: utf-8 -*-
# app build tool
# Copyright (C) 2019 Yukio Nozawa <personal@nyanchangames.com>
import os
import platform
import sys
import shutil
import glob
import subprocess


def run(cmd, sh=False):
	"""Runs a specific command."""
	print("Executing: %s" % cmd)
	subprocess.call(cmd.split(),shell=sh)

def mkdir(fld):
	"""Makes a directory. It skips if it already exists. """
	sys.stdout.write("Checking directory: %s ... " % fld)
	if os.path.isdir(fld):
		sys.stdout.write("exists\n")
		return
	else:
		os.mkdir(fld)
		sys.stdout.write("created\n")
def dopackage():
	if win:
		print("Creating installer exe")
		f=open("_build.bat","w")
		f.write("WinRAR a -cfg- -ed -ep1 -k -m5 -r -sfx \"-ztools\\rar_options.txt\" \"%s.exe\" \"%s.dist\\*\"" % (PROJECT_FULL_NAME, PROJECT))
		f.close()
		run("cmd /c _build.bat")
		os.remove("_build.bat")
	if not win:
		print("Creating image dmg")
		os.rename("dist/"+PROJECT+".app","dist/"+PROJECT_FULL_NAME+".app")
		os.remove("dist/%s" % PROJECT)
		run("hdiutil create -volname %s -srcfolder ./dist -ov -format UDZO %s.dmg" % (PROJECT_FULL_NAME, PROJECT_FULL_NAME))

win=True
if platform.system() == 'Darwin':
	win=False

print("win=%s, cwd=%s" % (win, os.getcwd()))
PROJECT = "ttcom"  # Change this line accordingly
PROJECT_FULL_NAME="TTCom"
PYTHON_PATH="c:\python37" #Windows only

#if not os.path.exists("../winfiles"):
#  print("Error: no winfiles folder found.")
#  sys.exit()

print("Building %s. This will take several minutes. Please wait..." % PROJECT)

if "--skip-compile" in sys.argv:
	print("Skipping to packaging")
	dopackage()
	print("Done!")
	sys.exit()

copydir=""

if win:
	cmd="nuitka --follow-imports --standalone %s.py" % (PROJECT)
	copydir="%s.dist" % PROJECT
else:
	cmd="pyinstaller --windowed --onefile --osx-bundle-identifier me.masonasons.%s %s.py" % (PROJECT_FULL_NAME, PROJECT)
	copydir="dist/%s.app/Contents/Resources" % PROJECT

run(cmd, sh=win)#win uses shell=true and mac doesn't
if win:
	print("Copying pythoncom37.dll...")
	shutil.copyfile("%s/Lib/site-packages/pywin32_system32/pythoncom37.dll" % (PYTHON_PATH), "%s.dist/pythoncom37.dll" % PROJECT)

print("Copying sound_lib dlls...")
shutil.copytree("../winfiles", "%s" % copydir)
dopackage()
print("Done!")
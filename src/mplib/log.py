import os
import time
from mplib import logger
path="logs"
def log(name,data):
	if data=="" or data==" ":
		return
	if not os.path.exists("logs"):
		os.makedirs("logs")
	logger.log(path+"/"+name+".log",data+". "+time.strftime("%c, %x")+"\n")
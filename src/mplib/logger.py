def log(worklog, entry):
	f=open(worklog, "a")
	try:
		f.write(entry)
	except:
		pass
	f.close()
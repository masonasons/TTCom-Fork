def log(worklog, entry):
	f=open(worklog, "a")
	f.write(entry)
	f.close()
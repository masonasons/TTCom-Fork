#borrowed and modified heavily from Lucia.
# Originally written by Blastbay
# Python port and increased functionality courtesy of Americranian

from . import sound

class SoundPoolItem:
	def __init__(self, filename, **kwargbs):
		self.handle = sound.Sound()
		self.filename = filename
		self.looping = kwargbs.get("looping", 0)
		self.start_pan = kwargbs.get("start_pan", 0)
		self.start_volume = kwargbs.get("start_volume", 0)
		self.start_pitch = kwargbs.get("start_pitch", 0)
		self.start_offset = kwargbs.get("start_offset", 0)
		self.looping = kwargbs.get("looping", False)
		self.stationary = kwargbs.get("stationary", False)
		self.persistent = kwargbs.get("persistent", False)
		self.paused = kwargbs.get("paused", False)

	def reset(self, pack="sounds/"):
		self.__init__("")


class SoundPool(object):
	def __init__(self):
		self.items = []
		self.clean_frequency = 3

	def play_stationary(self, filename, looping=False, persistent=False):
		return self.play_stationary_extended(filename, looping, 0, 0, 0, 100, persistent)

	def play_stationary_extended(
		self,
		filename,
		looping,
		offset,
		start_pan,
		start_volume,
		start_pitch,
		persistent=False,
	):
		self.clean_frequency -= 1
		if self.clean_frequency <= 0:
			try:
				self.clean_unused()
			except:
				pass
		s = SoundPoolItem(
			filename=filename,
			looping=looping,
			start_offset=offset,
			start_pan=start_pan,
			start_volume=start_volume,
			start_pitch=start_pitch,
			persistent=persistent,
			stationary=True,
		)
		try:
			s.handle.load(filename)
		except:
			s.reset()
			return -1
		if s.start_offset > 0:
			s.handle.position = s.start_offset
		if start_pan != 0.0:
			s.handle.pan = start_pan
		if start_volume < 0.0:
			s.handle.volume = start_volume
		s.handle.pitch = start_pitch
		if looping == True:
			s.handle.play_looped()
		else:
			s.handle.play()
		self.items.append(s)
		return s

	def sound_is_active(self, s):
		if s.looping == False and s.handle == None:
			return False
		if s.looping == False and not s.handle.handle.is_playing:
			return False
		return True

	def sound_is_playing(self, s):
		if not self.sound_is_active(s):
			return False
		return s.handle.handle.is_playing

	def pause_sound(self, s):
		if not self.sound_is_active(s):
			return False
		if s.paused:
			return False
		s.paused = True
		if s.handle.handle.is_playing:
			s.handle.stop()
		return True

	def resume_sound(self, s):
		if not s.paused:
			return False
		s.paused = False
		if s.handle != None and not s.handle.handle.is_playing:
			if s.looping:
				s.handle.play_looped()
			else:
				s.handle.play()
		return True

	def pause_all(self):
		for i in self.items:
			if self.sound_is_playing(i):
				self.pause_sound(i)

	def resume_all(self):
		for i in self.items:
			if i.handle.handle != None:
				self.resume_sound(i)

	def destroy_all(self):
		for i in self.items:
			i.reset()

	def update_sound_start_values(self, s, start_pan, start_volume, start_pitch):
		s.start_pan = start_pan
		s.start_volume = start_volume
		s.start_pitch = start_pitch
		if s.stationary and s.handle != None:
			s.handle.pan = start_pan
			s.handle.volume = start_volume
			s.handle.pitch = start_pitch
			return True
		if s.handle.pitch != start_pitch:
			s.handle.pitch = start_pitch
		return True

	def destroy_sound(self, s):
		s.reset()
		return True

	def clean_unused(self):
		if len(self.items) == 0:
			return
		for i in self.items:
			if i.looping:
				continue
			if i.persistent:
				continue
			if i.handle.handle == None or not i.handle.handle.is_playing and not i.paused:
				self.items.remove(i)
				self.clean_frequency = 3

	def update_audio_system(self):
		self.clean_unused()

	def get_source_object(self, filename):
		if len(self.items) == 0:
			return None
		for i in self.items:
			if i.filename == filename:
				return i
		return None

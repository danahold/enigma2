import os
from enigma import eActionMap, eEPGCache, quitMainloop

from Components.config import config
from Components.TimerSanityCheck import TimerSanityCheck
from Components.Task import Task, Job, job_manager as JobManager

from Screens.MessageBox import MessageBox
import Screens.Standby
from Tools import Directories, Notifications, ASCIItranslit
from Tools.XMLTools import stringToXML

import timer
import xml.etree.cElementTree
import NavigationInstance
from ServiceReference import ServiceReference

from time import localtime, strftime, ctime, time
from bisect import insort
import os

# parses an event, and gives out a (begin, end, name, duration, eit)-tuple.
# begin and end will be corrected
def parseEvent(ev):
	begin = ev.getBeginTime()
	end = begin + ev.getDuration()
	return (begin, end)

class AFTEREVENT:
	NONE = 0
	WAKEUPTOSTANDBY = 1
	STANDBY = 2
	DEEPSTANDBY = 3

class TIMERTYPE:
	NONE = 0
	WAKEUP = 1
	WAKEUPTOSTANDBY = 2
	AUTOSTANDBY = 3
	AUTODEEPSTANDBY = 4
	STANDBY = 5
	DEEPSTANDBY = 6
	REBOOT = 7
	RESTART = 8

# please do not translate log messages
class PowerTimerEntry(timer.TimerEntry, object):
	def __init__(self, begin, end, disabled = False, afterEvent = AFTEREVENT.NONE, timerType = TIMERTYPE.WAKEUP, checkOldTimers = False, autosleepdelay = 60, autosleeprepeat = "once"):
		timer.TimerEntry.__init__(self, int(begin), int(end))
		if checkOldTimers == True:
			if self.begin < time() - 1209600:
				self.begin = int(time())

		if self.end < self.begin:
			self.end = self.begin

		self.dontSave = False
		self.timer = None
		self.__record_service = None
		self.start_prepare = 0
		self.timerType = timerType
		self.afterEvent = afterEvent
		self.autoincrease = False
		self.autoincreasetime = 3600 * 24 # 1 day
		self.autosleepdelay = autosleepdelay
		self.autosleeprepeat = autosleeprepeat

		self.log_entries = []
		self.resetState()

	def __repr__(self):
		timertype = {
			TIMERTYPE.WAKEUP: "wakeup",
			TIMERTYPE.WAKEUPTOSTANDBY: "wakeuptostandby",
			TIMERTYPE.AUTOSTANDBY: "autostandby",
			TIMERTYPE.AUTODEEPSTANDBY: "autodeepstandby",
			TIMERTYPE.STANDBY: "standby",
			TIMERTYPE.DEEPSTANDBY: "deepstandby",
			TIMERTYPE.REBOOT: "reboot",
			TIMERTYPE.RESTART: "restart"
			}[self.timerType]
		return "PowerTimerEntry(type=%s, begin=%s)" % (timertype, ctime(self.begin))

	def log(self, code, msg):
		self.log_entries.append((int(time()), code, msg))
		print "[POWERTIMER]", msg

	def do_backoff(self):
		if self.backoff == 0:
			self.backoff = 5*60
		else:
			self.backoff *= 2
			if self.backoff > 1800:
				self.backoff = 1800
		self.log(10, "backoff: retry in %d minuets" % (int(self.backoff)/60))

	def activate(self):
		next_state = self.state + 1
		self.log(5, "activating state %d" % next_state)

		if next_state == 1 and (self.timerType == TIMERTYPE.AUTOSTANDBY or self.timerType == TIMERTYPE.AUTODEEPSTANDBY):
			eActionMap.getInstance().bindAction('', -0x7FFFFFFF, self.keyPressed)
			self.begin = time() + int(self.autosleepdelay)*60
			if self.end <= self.begin:
				self.end = self.begin

		if next_state == self.StatePrepared:
			self.log(6, "prepare ok, waiting for begin")
			self.next_activation = self.begin
			self.backoff = 0
			return True

		elif next_state == self.StateRunning:
			self.wasPowerTimerWakeup = False
			if os.path.exists("/tmp/was_timer_wakeup"):
				self.wasPowerTimerWakeup = int(open("/tmp/was_powertimer_wakeup", "r").read()) and True or False
				os.remove("/tmp/was_powertimer_wakeup")
			print '!!!!!!!!!!!!!!!!!!!!!!!!wasPowerTimerWakeup:',self.wasPowerTimerWakeup

			print 'TEST01:'
			# if this timer has been cancelled, just go to "end" state.
			if self.cancelled:
				print 'TEST02:'
				return True

			if self.failed:
				print 'TEST03:'
				return True

			if self.timerType == TIMERTYPE.WAKEUP:
				print 'TEST04:'
				if Screens.Standby.inStandby:
					print 'TEST05:'
					Screens.Standby.inStandby.Power()
				return True

			elif self.timerType == TIMERTYPE.WAKEUPTOSTANDBY:
				print 'TEST06:'
				return True

			elif self.timerType == TIMERTYPE.STANDBY:
				print 'TEST07:'
				if not Screens.Standby.inStandby: # not already in standby
					print 'TEST08:'
					Notifications.AddNotificationWithCallback(self.sendStandbyNotification, MessageBox, _("Your STB_BOX wants to set your STB_BOX to standby.\nDo that now?"), timeout = 180)
				return True

			elif self.timerType == TIMERTYPE.AUTOSTANDBY:
				print 'TEST09:'
				if not Screens.Standby.inStandby: # not already in standby
					print 'TEST10:'
					Notifications.AddNotificationWithCallback(self.sendStandbyNotification, MessageBox, _("Your STB_BOX wants to set your STB_BOX to standby.\nDo that now?"), timeout = 180)
					if self.autosleeprepeat == "once":
						print 'TEST11:'
						eActionMap.getInstance().unbindAction('', self.keyPressed)
						return True
					else:
						print 'TEST12:'
						self.begin = time() + int(self.autosleepdelay)*60
						if self.end <= self.begin:
							print 'TEST13:'
							self.end = self.begin
				else:
					print 'TEST14:'
					self.begin = time() + int(self.autosleepdelay)*60
					if self.end <= self.begin:
						print 'TEST15:'
						self.end = self.begin

			elif self.timerType == TIMERTYPE.AUTODEEPSTANDBY:
				print 'TEST16:'
				if NavigationInstance.instance.RecordTimer.isRecording() or abs(NavigationInstance.instance.RecordTimer.getNextRecordingTime() - time()) <= 900 or abs(NavigationInstance.instance.RecordTimer.getNextZapTime() - time()) <= 900:
					print 'TEST17:'
					self.do_backoff()
					# retry
					self.begin = time() + self.backoff
					if self.end <= self.begin:
						print 'TEST18:'
						self.end = self.begin
					return False
				if not Screens.Standby.inTryQuitMainloop: # not a shutdown messagebox is open
					print 'TEST19:'
					if Screens.Standby.inStandby: # in standby
						print 'TEST20:'
						quitMainloop(1)
						return True
					else:
						print 'TEST21:'
						Notifications.AddNotificationWithCallback(self.sendTryQuitMainloopNotification, MessageBox, _("Your STB_BOX wants to shut down your STB_BOX.\nDo that now?"), timeout = 180)
						if self.autosleeprepeat == "once":
							print 'TEST22:'
							eActionMap.getInstance().unbindAction('', self.keyPressed)
							return True
						else:
							print 'TEST23:'
							self.begin = time() + int(self.autosleepdelay)*60
							if self.end <= self.begin:
								print 'TEST24:'
								self.end = self.begin

			elif self.timerType == TIMERTYPE.DEEPSTANDBY and self.wasPowerTimerWakeup:
				print 'TEST25a:'
				return True

			elif self.timerType == TIMERTYPE.DEEPSTANDBY and not self.wasPowerTimerWakeup:
				print 'TEST25b:'
				if NavigationInstance.instance.RecordTimer.isRecording() or abs(NavigationInstance.instance.RecordTimer.getNextRecordingTime() - time()) <= 900 or abs(NavigationInstance.instance.RecordTimer.getNextZapTime() - time()) <= 900:
					print 'TEST26:'
					self.do_backoff()
					# retry
					self.begin = time() + self.backoff
					if self.end <= self.begin:
						print 'TEST27:'
						self.end = self.begin
					return False
				if not Screens.Standby.inTryQuitMainloop: # not a shutdown messagebox is open
					print 'TEST28:'
					if Screens.Standby.inStandby: # in standby
						print 'TEST29:'
						quitMainloop(1)
					else:
						print 'TEST30:'
						Notifications.AddNotificationWithCallback(self.sendTryQuitMainloopNotification, MessageBox, _("Your STB_BOX wants to shut down your STB_BOX.\nDo that now?"), timeout = 180)
				return True

			elif self.timerType == TIMERTYPE.REBOOT:
				print 'TEST31:'
				if NavigationInstance.instance.RecordTimer.isRecording() or abs(NavigationInstance.instance.RecordTimer.getNextRecordingTime() - time()) <= 900 or abs(NavigationInstance.instance.RecordTimer.getNextZapTime() - time()) <= 900:
					print 'TEST32:'
					self.do_backoff()
					# retry
					self.begin = time() + self.backoff
					if self.end <= self.begin:
						print 'TEST33:'
						self.end = self.begin
					return False
				if not Screens.Standby.inTryQuitMainloop: # not a shutdown messagebox is open
					print 'TEST34:'
					if Screens.Standby.inStandby: # in standby
						print 'TEST35:'
						quitMainloop(2)
					else:
						print 'TEST36:'
						Notifications.AddNotificationWithCallback(self.sendTryToRebootNotification, MessageBox, _("Your STB_BOX wants to reboot your STB_BOX.\nDo that now?"), timeout = 180)
				return True

			elif self.timerType == TIMERTYPE.RESTART:
				print 'TEST37:'
				if NavigationInstance.instance.RecordTimer.isRecording() or abs(NavigationInstance.instance.RecordTimer.getNextRecordingTime() - time()) <= 900 or abs(NavigationInstance.instance.RecordTimer.getNextZapTime() - time()) <= 900:
					print 'TEST38:'
					self.do_backoff()
					# retry
					self.begin = time() + self.backoff
					if self.end <= self.begin:
						print 'TEST39:'
						self.end = self.begin
					return False
				if not Screens.Standby.inTryQuitMainloop: # not a shutdown messagebox is open
					print 'TEST40:'
					if Screens.Standby.inStandby: # in standby
						print 'TEST41:'
						quitMainloop(3)
					else:
						print 'TEST42:'
						Notifications.AddNotificationWithCallback(self.sendTryToRestartNotification, MessageBox, _("Your STB_BOX wants to restart the user interface.\nDo that now?"), timeout = 180)
				return True

		elif next_state == self.StateEnded:
			print 'TEST43:'
			old_end = self.end
			if self.afterEvent == AFTEREVENT.STANDBY:
				print 'TEST44:'
				if not Screens.Standby.inStandby: # not already in standby
					print 'TEST45:'
					Notifications.AddNotificationWithCallback(self.sendStandbyNotification, MessageBox, _("A finished powertimer wants to set your\nSTB_BOX to standby. Do that now?"), timeout = 180)
			elif self.afterEvent == AFTEREVENT.DEEPSTANDBY:
				print 'TEST46:'
				if not Screens.Standby.inTryQuitMainloop: # not a shutdown messagebox is open
					print 'TEST47:'
					if Screens.Standby.inStandby: # in standby
						print 'TEST48:'
						quitMainloop(1)
					else:
						print 'TEST49:'
						Notifications.AddNotificationWithCallback(self.sendTryQuitMainloopNotification, MessageBox, _("A finished power timer wants to shut down\nyour STB_BOX. Shutdown now?"), timeout = 180)
			return True

	def setAutoincreaseEnd(self, entry = None):
		if not self.autoincrease:
			return False
		if entry is None:
			new_end =  int(time()) + self.autoincreasetime
		else:
			new_end = entry.begin -30

		dummyentry = PowerTimerEntry(self.begin, new_end, afterEvent = self.afterEvent, timerType = self.timerType)
		dummyentry.disabled = self.disabled
		timersanitycheck = TimerSanityCheck(NavigationInstance.instance.PowerManager.timer_list, dummyentry)
		if not timersanitycheck.check():
			simulTimerList = timersanitycheck.getSimulTimerList()
			if simulTimerList is not None and len(simulTimerList) > 1:
				new_end = simulTimerList[1].begin
				new_end -= 30				# 30 Sekunden Prepare-Zeit lassen
		if new_end <= time():
			return False
		self.end = new_end
		return True

	def sendStandbyNotification(self, answer):
		if answer:
			Notifications.AddNotification(Screens.Standby.Standby)

	def sendTryQuitMainloopNotification(self, answer):
		if answer:
			Notifications.AddNotification(Screens.Standby.TryQuitMainloop, 1)

	def sendTryToRebootNotification(self, answer):
		if answer:
			Notifications.AddNotification(Screens.Standby.TryQuitMainloop, 2)

	def sendTryToRestartNotification(self, answer):
		if answer:
			Notifications.AddNotification(Screens.Standby.TryQuitMainloop, 3)

	def keyPressed(self, key, tag):
		self.begin = time() + int(self.autosleepdelay)*60
		if self.end <= self.begin:
			self.end = self.begin

	def getNextActivation(self):
		if self.state == self.StateEnded or self.state == self.StateFailed:
			return self.end

		next_state = self.state + 1

		return {self.StatePrepared: self.start_prepare,
				self.StateRunning: self.begin,
				self.StateEnded: self.end }[next_state]

	def getNextWakeup(self):
		print 'getNextWakeup'
		if self.state == self.StateEnded or self.state == self.StateFailed:
			print '1'
			return self.end

		if (self.timerType != TIMERTYPE.WAKEUP and self.timerType != TIMERTYPE.WAKEUPTOSTANDBY and not self.afterEvent):
			print '2'
			return -1
		elif (self.timerType != TIMERTYPE.WAKEUP and self.timerType != TIMERTYPE.WAKEUPTOSTANDBY and self.afterEvent):
			return self.end

		next_state = self.state + 1

		print '3', {self.StatePrepared: self.start_prepare,
				self.StateRunning: self.begin,
				self.StateEnded: self.end }[next_state]

		return {self.StatePrepared: self.start_prepare,
				self.StateRunning: self.begin,
				self.StateEnded: self.end }[next_state]

	def failureCB(self, answer):
		if answer == True:
			self.log(13, "ok, zapped away")
			#NavigationInstance.instance.stopUserServices()
			NavigationInstance.instance.playService(self.service_ref.ref)
		else:
			self.log(14, "user didn't want to zap away, record will probably fail")

	def timeChanged(self):
		old_prepare = self.start_prepare
		self.start_prepare = self.begin - self.prepare_time
		self.backoff = 0

		if int(old_prepare) != int(self.start_prepare):
			self.log(15, "time changed, start prepare is now: %s" % ctime(self.start_prepare))

def createTimer(xml):
	timertype = str(xml.get("timertype") or "wakeup")
	timertype = {
		"wakeup": TIMERTYPE.WAKEUP,
		"wakeuptostandby": TIMERTYPE.WAKEUPTOSTANDBY,
		"autostandby": TIMERTYPE.AUTOSTANDBY,
		"autodeepstandby": TIMERTYPE.AUTODEEPSTANDBY,
		"standby": TIMERTYPE.STANDBY,
		"deepstandby": TIMERTYPE.DEEPSTANDBY,
		"reboot": TIMERTYPE.REBOOT,
		"restart": TIMERTYPE.RESTART
		}[timertype]
	begin = int(xml.get("begin"))
	end = int(xml.get("end"))
	repeated = xml.get("repeated").encode("utf-8")
	disabled = long(xml.get("disabled") or "0")
	afterevent = str(xml.get("afterevent") or "nothing")
	afterevent = {
		"nothing": AFTEREVENT.NONE,
		"wakeuptostandby": AFTEREVENT.WAKEUPTOSTANDBY,
		"standby": AFTEREVENT.STANDBY,
		"deepstandby": AFTEREVENT.DEEPSTANDBY
		}[afterevent]
	autosleepdelay = str(xml.get("autosleepdelay") or "0")
	autosleeprepeat = str(xml.get("autosleeprepeat") or "once")

	entry = PowerTimerEntry(begin, end, disabled, afterevent, timertype)
	entry.repeated = int(repeated)
	entry.autosleepdelay = int(autosleepdelay)
	entry.autosleeprepeat = autosleeprepeat

	for l in xml.findall("log"):
		time = int(l.get("time"))
		code = int(l.get("code"))
		msg = l.text.strip().encode("utf-8")
		entry.log_entries.append((time, code, msg))

	return entry

class PowerTimer(timer.Timer):
	def __init__(self):
		timer.Timer.__init__(self)

		self.Filename = Directories.resolveFilename(Directories.SCOPE_CONFIG, "pm_timers.xml")

		try:
			self.loadTimer()
		except IOError:
			print "unable to load timers from file!"

	def doActivate(self, w):
		# when activating a timer which has already passed,
		# simply abort the timer. don't run trough all the stages.
		if w.shouldSkip():
			w.state = PowerTimerEntry.StateEnded
		else:
			# when active returns true, this means "accepted".
			# otherwise, the current state is kept.
			# the timer entry itself will fix up the delay then.
			if w.activate():
				w.state += 1

		try:
			self.timer_list.remove(w)
		except:
			print '[PowerManager]: Remove list failed'

		# did this timer reached the last state?
		if w.state < PowerTimerEntry.StateEnded:
			# no, sort it into active list
			insort(self.timer_list, w)
		else:
			# yes. Process repeated, and re-add.
			if w.repeated:
				w.processRepeated()
				w.state = PowerTimerEntry.StateWaiting
				self.addTimerEntry(w)
			else:
				# Remove old timers as set in config
				self.cleanupDaily(config.recording.keep_timers.getValue())
				insort(self.processed_timers, w)
		self.stateChanged(w)

	def loadTimer(self):
		# TODO: PATH!
		if not Directories.fileExists(self.Filename):
			return
		try:
			doc = xml.etree.cElementTree.parse(self.Filename)
		except SyntaxError:
			from Tools.Notifications import AddPopup
			from Screens.MessageBox import MessageBox

			AddPopup(_("The timer file (pm_timers.xml) is corrupt and could not be loaded."), type = MessageBox.TYPE_ERROR, timeout = 0, id = "TimerLoadFailed")

			print "pm_timers.xml failed to load!"
			try:
				import os
				os.rename(self.Filename, self.Filename + "_old")
			except (IOError, OSError):
				print "renaming broken timer failed"
			return
		except IOError:
			print "pm_timers.xml not found!"
			return

		root = doc.getroot()

		# put out a message when at least one timer overlaps
		checkit = True
		for timer in root.findall("timer"):
			newTimer = createTimer(timer)
			if (self.record(newTimer, True, dosave=False) is not None) and (checkit == True):
				from Tools.Notifications import AddPopup
				from Screens.MessageBox import MessageBox
				AddPopup(_("Timer overlap in pm_timers.xml detected!\nPlease recheck it!"), type = MessageBox.TYPE_ERROR, timeout = 0, id = "TimerLoadFailed")
				checkit = False # at moment it is enough when the message is displayed one time

	def saveTimer(self):
		list = []
		list.append('<?xml version="1.0" ?>\n')
		list.append('<timers>\n')
		for timer in self.timer_list + self.processed_timers:
			if timer.dontSave:
				continue
			list.append('<timer')
			list.append(' timertype="' + str(stringToXML({
				TIMERTYPE.WAKEUP: "wakeup",
				TIMERTYPE.WAKEUPTOSTANDBY: "wakeuptostandby",
				TIMERTYPE.AUTOSTANDBY: "autostandby",
				TIMERTYPE.AUTODEEPSTANDBY: "autodeepstandby",
				TIMERTYPE.STANDBY: "standby",
				TIMERTYPE.DEEPSTANDBY: "deepstandby",
				TIMERTYPE.REBOOT: "reboot",
				TIMERTYPE.RESTART: "restart"
				}[timer.timerType])) + '"')
			list.append(' begin="' + str(int(timer.begin)) + '"')
			list.append(' end="' + str(int(timer.end)) + '"')
			list.append(' repeated="' + str(int(timer.repeated)) + '"')
			list.append(' afterevent="' + str(stringToXML({
				AFTEREVENT.NONE: "nothing",
				AFTEREVENT.WAKEUPTOSTANDBY: "wakeuptostandby",
				AFTEREVENT.STANDBY: "standby",
				AFTEREVENT.DEEPSTANDBY: "deepstandby"
				}[timer.afterEvent])) + '"')
			list.append(' disabled="' + str(int(timer.disabled)) + '"')
			list.append(' autosleepdelay="' + str(timer.autosleepdelay) + '"')
			list.append(' autosleeprepeat="' + str(timer.autosleeprepeat) + '"')
			list.append('>\n')

			if config.recording.debug.getValue():
				for time, code, msg in timer.log_entries:
					list.append('<log')
					list.append(' code="' + str(code) + '"')
					list.append(' time="' + str(time) + '"')
					list.append('>')
					list.append(str(stringToXML(msg)))
					list.append('</log>\n')

			list.append('</timer>\n')

		list.append('</timers>\n')

		file = open(self.Filename + ".writing", "w")
		for x in list:
			file.write(x)
		file.flush()

		import os
		os.fsync(file.fileno())
		file.close()
		os.rename(self.Filename + ".writing", self.Filename)

	def getNextZapTime(self):
		now = time()
		for timer in self.timer_list:
			if timer.begin < now:
				continue
			return timer.begin
		return -1

	def getNextPowerManagerTimeOld(self):
		now = time()
		for timer in self.timer_list:
			if timer.timerType != TIMERTYPE.AUTOSTANDBY and timer.timerType != TIMERTYPE.AUTODEEPSTANDBY:
				next_act = timer.getNextWakeup()
				if next_act < now:
					continue
				return next_act
		return -1

	def getNextPowerManagerTime(self):
		nextrectime = self.getNextPowerManagerTimeOld()
		faketime = time()+300
		if config.timeshift.isRecording.getValue():
			if nextrectime > 0 and nextrectime < faketime:
				return nextrectime
			else:
				return faketime
		else:
			return nextrectime

	def isNextPowerManagerAfterEventActionAuto(self):
		print 'isNextPowerManagerAfterEventActionAuto:'
		now = time()
		t = None
		print 'self.timer_list',self.timer_list
		for timer in self.timer_list:
			print 'POWERTIMER:',timer
			if timer.timerType == TIMERTYPE.WAKEUPTOSTANDBY or timer.afterEvent == AFTEREVENT.WAKEUPTOSTANDBY:
				print 'TRUE'
				return True
		print 'FALSE'
		return False

	def record(self, entry, ignoreTSC=False, dosave=True):		#wird von loadTimer mit dosave=False aufgerufen
		entry.timeChanged()
		print "[POWERTIMER] " + str(entry)
		entry.Timer = self
		self.addTimerEntry(entry)
		if dosave:
			self.saveTimer()
		return None

	def removeEntry(self, entry):
		print "[POWERTIMER] Remove " + str(entry)

		# avoid re-enqueuing
		entry.repeated = False

		# abort timer.
		# this sets the end time to current time, so timer will be stopped.
		entry.autoincrease = False
		entry.abort()

		if entry.state != entry.StateEnded:
			self.timeChanged(entry)

# 		print "state: ", entry.state
# 		print "in processed: ", entry in self.processed_timers
# 		print "in running: ", entry in self.timer_list
		# disable timer first
		if entry.state != 3:
			entry.disable()
		# autoincrease instanttimer if possible
		if not entry.dontSave:
			for x in self.timer_list:
				if x.setAutoincreaseEnd():
					self.timeChanged(x)
		# now the timer should be in the processed_timers list. remove it from there.
		if entry in self.processed_timers:
			self.processed_timers.remove(entry)
		self.saveTimer()

	def shutdown(self):
		self.saveTimer()
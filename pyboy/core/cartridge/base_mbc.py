# -*- coding: utf-8 -*- 
#!/usr/bin/env python
import logging as logger
import array
import os

from .rtc import RTC

class BaseMBC:
	def __init__(self, filename, rombanks, external_ram_count, carttype, sram, battery, rtc_enabled):
		self.filename = filename + ".ram"
		self.rombanks = rombanks
		self.carttype = carttype
		self.battery = battery
		self.rtc_enabled = rtc_enabled
		if self.rtc_enabled:
			self.rtc = RTC()
		else:
			self.rtc = None
		self.rambank_initialized = False
		self.external_rom_count = len(rombanks)
		self.external_ram_count = external_ram_count
		self.init_rambanks(external_ram_count)
		self.gamename = self.getgamename(rombanks)
		self.memorymodel = 0
		self.rambank_enabled = False
		self.rambank_selected = 0
		self.rombank_selected = 1
		self.cgb = bool(self.getitem(0x0143) >> 7)
	
	def stop(self):
		if self.rtc_enabled:
			self.rtc.stop()

	def init_rambanks(self, n):
		self.rambank_initialized = True
		# In real life the values in RAM are scrambled on initialization.
		# Allocating the maximum, as it is easier in Cython. And it's just 128KB...
		self.rambanks = memoryview(array.array("B", [0] * (8*1024*16))).cast("B", shape=(16, 8 * 1024))

	def getgamename(self, rombanks):
		return "".join([chr(rombanks[0, x]) for x in range(0x0134, 0x0142)]).split("\0")[0]

	def setitem(self, address, value):
		raise Exception("Cannot set item in MBC")

	def overrideitem(self, rom_bank, address, value):
		if 0x0000 <= address < 0x4000:
			logger.debug("Performing overwrite on address: 0x%04x:0x%04x. New value: 0x%04x Old value: 0x%04x", rom_bank, address, value, self.rombanks[rom_bank, address])
			self.rombanks[rom_bank, address] = value
		else:
			logger.error("Invalid override address: %0.4x", address)

	def getitem(self, address):
		if 0x0000 <= address < 0x4000:
			return self.rombanks[0, address]
		elif 0x4000 <= address < 0x8000:
			return self.rombanks[self.rombank_selected, address - 0x4000]
		elif 0xA000 <= address < 0xC000:
			# if not self.rambank_initialized:
			#	logger.error("RAM banks not initialized: 0.4x", address)
			if not self.rambank_enabled:
				return 0xFF
			if self.rtc_enabled and 0x08 <= self.rambank_selected <= 0x0C:
				return self.rtc.getregister(self.rambank_selected)
			else:
				return self.rambanks[self.rambank_selected, address - 0xA000]
		# else:
		#	logger.error("Reading address invalid: %0.4x", address)

	def __repr__(self):
		return "\n".join([
			"MBC class: %s" % self.__class__.__name__,
			"Filename: %s" % self.filename,
			"Game name: %s" % self.gamename,
			"GB Color: %s" % str(self.rombanks[0, 0x143] == 0x80),
			"Cartridge type: %s" % hex(self.carttype),
			"Number of ROM banks: %s" % self.external_rom_count,
			"Active ROM bank: %s" % self.rombank_selected,
			# "Memory bank type: %s" % self.ROMBankController,
			"Number of RAM banks: %s" % len(self.rambanks),
			"Active RAM bank: %s" % self.rambank_selected,
			"Battery: %s" % self.battery,
			"RTC: %s" % self.rtc
		])

# 只有ROM的卡带
class ROMOnly(BaseMBC):
	def setitem(self, address, value):
		if 0x2000 <= address < 0x4000:
			if value == 0:
				value = 1
			self.rombank_selected = (value & 0b1)
			logger.debug("Switching bank 0x%0.4x, 0x%0.2x", address, value)
		elif 0xA000 <= address < 0xC000:
			self.rambanks[self.rambank_selected, address - 0xA000] = value
		# else:
		#	logger.debug("Unexpected write to 0x%0.4x, value: 0x%0.2x", address, value)

# -*- coding: utf-8 -*- 
#!/usr/bin/env python
import random
import array

# MEMORY SIZES
INTERNAL_RAM0 = 8 * 1024 # 8KiB
INTERNAL_RAM0_CGB = INTERNAL_RAM0 * 4 # 8 banks of 4KiB
NON_IO_INTERNAL_RAM0 = 0x60
IO_PORTS = 0x4C
NON_IO_INTERNAL_RAM1 = 0x34
INTERNAL_RAM1 = 0x7F

class RAM:
	def __init__(self, cgb, randomize=False):
		self.cgb = cgb
		self.internal_ram0 = array.array("B", [0] * (INTERNAL_RAM0_CGB if cgb else INTERNAL_RAM0))
		self.non_io_internal_ram0 = array.array("B", [0] * (NON_IO_INTERNAL_RAM0))
		self.io_ports = array.array("B", [0] * (IO_PORTS))
		self.internal_ram1 = array.array("B", [0] * (INTERNAL_RAM1))
		self.non_io_internal_ram1 = array.array("B", [0] * (NON_IO_INTERNAL_RAM1))
		if randomize:
			for n in range(INTERNAL_RAM0_CGB if cgb else INTERNAL_RAM0):
				self.internal_ram0[n] = random.getrandbits(8)
			for n in range(NON_IO_INTERNAL_RAM0):
				self.non_io_internal_ram0[n] = random.getrandbits(8)
			for n in range(INTERNAL_RAM1):
				self.internal_ram1[n] = random.getrandbits(8)
			for n in range(NON_IO_INTERNAL_RAM1):
				self.non_io_internal_ram1[n] = random.getrandbits(8)



# -*- coding: utf-8 -*- 
#!/usr/bin/env python
import logging as logger
import array

from pyboy import utils

from . import opcodes

FLAGC, FLAGH, FLAGN, FLAGZ = range(4, 8)
INTR_VBLANK, INTR_LCDC, INTR_TIMER, INTR_SERIAL, INTR_HIGHTOLOW = [1 << x for x in range(5)]

import pyboy

class CPU:
	def set_bc(self, x):
		self.B = x >> 8
		self.C = x & 0x00FF

	def set_de(self, x):
		self.D = x >> 8
		self.E = x & 0x00FF

	def __init__(self, mb):
		self.A = 0
		self.F = 0
		self.B = 0
		self.C = 0
		self.D = 0
		self.E = 0
		self.HL = 0
		self.SP = 0
		self.PC = 0

		self.interrupts_flag_register = 0
		self.interrupts_enabled_register = 0
		self.interrupt_master_enable = False
		self.interrupt_queued = False

		self.mb = mb

		self.halted = False
		self.stopped = False
		self.is_stuck = False

	def set_interruptflag(self, flag):
		self.interrupts_flag_register |= flag

	def tick(self):
		if self.check_interrupts():
			self.halted = False
			# TODO: We return with the cycles it took to handle the interrupt
			return 0

		if self.halted and self.interrupt_queued:
			# GBCPUman.pdf page 20
			# WARNING: The instruction immediately following the HALT instruction is "skipped" when interrupts are
			# disabled (DI) on the GB,GBP, and SGB.
			self.halted = False
			self.PC += 1
			self.PC &= 0xFFFF
		elif self.halted:
			return 4 # TODO: Number of cycles for a HALT in effect?

		old_pc = self.PC # If the PC doesn't change, we're likely stuck
		old_sp = self.SP # Sometimes a RET can go to the same PC, so we check the SP too.
		cycles = self.fetch_and_execute()
		if not self.halted and old_pc == self.PC and old_sp == self.SP and not self.is_stuck and not self.mb.breakpoint_singlestep:
			logger.debug("CPU is stuck: %s", self.dump_state(""))
			self.is_stuck = True
		self.interrupt_queued = False
		return cycles

	def check_interrupts(self):
		if self.interrupt_queued:
			# Interrupt already queued. This happens only when using a debugger.
			return False

		if (self.interrupts_flag_register & 0b11111) & (self.interrupts_enabled_register & 0b11111):
			if self.handle_interrupt(INTR_VBLANK, 0x0040):
				self.interrupt_queued = True
			elif self.handle_interrupt(INTR_LCDC, 0x0048):
				self.interrupt_queued = True
			elif self.handle_interrupt(INTR_TIMER, 0x0050):
				self.interrupt_queued = True
			elif self.handle_interrupt(INTR_SERIAL, 0x0058):
				self.interrupt_queued = True
			elif self.handle_interrupt(INTR_HIGHTOLOW, 0x0060):
				self.interrupt_queued = True
			else:
				logger.error("No interrupt triggered, but it should!")
				self.interrupt_queued = False
			return True
		else:
			self.interrupt_queued = False
		return False

	def handle_interrupt(self, flag, addr):
		if (self.interrupts_enabled_register & flag) and (self.interrupts_flag_register & flag):
			# Clear interrupt flag
			if self.halted:
				self.PC += 1 # Escape HALT on return
				self.PC &= 0xFFFF
			# Handle interrupt vectors
			if self.interrupt_master_enable:
				self.interrupts_flag_register ^= flag # Remove flag
				self.mb.setitem((self.SP - 1) & 0xFFFF, self.PC >> 8) # High
				self.mb.setitem((self.SP - 2) & 0xFFFF, self.PC & 0xFF) # Low
				self.SP -= 2
				self.SP &= 0xFFFF

				self.PC = addr
				self.interrupt_master_enable = False
			return True
		return False

	def fetch_and_execute(self):
		opcode = self.mb.getitem(self.PC)
		if opcode == 0xCB: # Extension code
			opcode = self.mb.getitem(self.PC + 1)
			opcode += 0x100 # Internally shifting look-up table
		return opcodes.execute_opcode(self, opcode)


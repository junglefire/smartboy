#
# License: See LICENSE.md file
# GitHub: https://github.com/Baekalfen/PyBoy
#
"""
The core module of the emulator
"""

import numpy as np
import heapq
import time
import os
import re


from pyboy.api.tilemap import TileMap
from pyboy.logging import get_logger, log_level
from pyboy.plugins.manager import PluginManager, parser_arguments
from pyboy.utils import IntIOWrapper, WindowEvent

from .api import Sprite, Tile, constants
from .core.mb import Motherboard

logger = get_logger(__name__)

SPF = 1 / 60. # inverse FPS (frame-per-second)

defaults = {
	"color_palette": (0xFFFFFF, 0x999999, 0x555555, 0x000000),
	"cgb_color_palette": (
		(0xFFFFFF, 0x7BFF31, 0x0063C5, 0x000000), 
		(0xFFFFFF, 0xFF8484, 0x943A3A, 0x000000), 
		(0xFFFFFF, 0xFF8484, 0x943A3A, 0x000000)
	),
	"scale": 3,
	"window": "SDL2",
	"log_level": "DEBUG",
}

class PyBoy:
	"""
	- * gamerom (str): Filepath to a game-ROM for Game Boy or Game Boy Color.
    - * window (str): "SDL2", "OpenGL", or "null"
    - * scale (int): Window scale factor. Doesn't apply to API.
    - * symbols (str): Filepath to a .sym file to use. If unsure, specify `None`.
    - * bootrom (str): Filepath to a boot-ROM to use. If unsure, specify `None`.
    - * sound (bool): Enable sound emulation and output.
    - * sound_emulated (bool): Enable sound emulation without any output. Used for compatibility.
    - * cgb (bool): Forcing Game Boy Color mode.
    - * color_palette (tuple): Specify the color palette to use for rendering.
    - * cgb_color_palette (list of tuple): Specify the color palette to use for rendering in CGB-mode for non-color games.
    """
	def __init__(self, gamerom, *, window=defaults["window"], scale=defaults["scale"], symbols=None, bootrom=None, sound=False, sound_emulated=False, cgb=None, **kwargs):
		self.initialized = False
		# get command line args
		kwargs["window"] = window
		kwargs["scale"] = scale
		randomize = kwargs.pop("randomize", False) # Undocumented feature
		# get plugins args
		for k, v in defaults.items():
			if k not in kwargs:
				kwargs[k] = kwargs.get(k, defaults[k])
		# setup logging level
		log_level(kwargs.pop("log_level"))
		# check rom file
		if not os.path.isfile(gamerom):
			raise FileNotFoundError(f"ROM file {gamerom} was not found!")
		self.gamerom = gamerom
		# create Motherboard
		self.mb = Motherboard(gamerom, bootrom, kwargs["color_palette"], kwargs["cgb_color_palette"], sound, sound_emulated, cgb, randomize=randomize,)
		# Validate all kwargs
		plugin_manager_keywords = []
		for x in parser_arguments():
			if not x:
				continue
			plugin_manager_keywords.extend(z.strip("-").replace("-", "_") for y in x for z in y[:-1])
		for k, v in kwargs.items():
			if k not in defaults and k not in plugin_manager_keywords:
				logger.error("Unknown keyword argument: %s", k)
				raise KeyError(f"Unknown keyword argument: {k}")
		# Performance measures
		self.avg_pre = 0
		self.avg_tick = 0
		self.avg_post = 0
		# Absolute frame count of the emulation
		self.frame_count = 0
		self.paused = False
		self.events = []
		self.queued_input = []
		self.quitting = False
		self.stopped = False
		self.window_title = "PyBoy"
		###################
		# [alex] API attributes
		# self._hooks = {}
		self._plugin_manager = PluginManager(self, self.mb, kwargs)
		self.initialized = True

	"""
	- Progresses the emulator ahead by `count` frame(s).
	- To run the emulator in real-time, it will need to process 60 frames a second (for example 
	in a while-loop). This function will block for roughly 16,67ms per frame, to not run faster 
	than real-time, unless you specify otherwise with the `PyBoy.set_emulation_speed` method.
	- If you need finer control than 1 frame, have a look at `PyBoy.hook_register` to inject code 
	at a specific point in the game.
	- Setting `render` to `True` will make PyBoy render the screen for *the last frame* of this 
	tick. This can be seen as a type of "frameskipping" optimization.
	- For AI training, it's adviced to use as high a count as practical, as it will otherwise 
	reduce performance substantially. While setting `render` to `False`, you can still access 
	the `PyBoy.game_area` to get a simpler representation of the game. 
	- If `render` was enabled, use `pyboy.api.screen.Screen` to get a NumPy buffer or raw memory buffer.
	"""
	def tick(self, count=1, render=True):
		running = False
		while count != 0:
			_render = render and count == 1 # Only render on last tick to improve performance
			running = self._tick(_render)
			count -= 1
		return running

	def _tick(self, render):
		if self.stopped:
			return False
		t_start = time.perf_counter_ns()
		self._handle_events(self.events)
		t_pre = time.perf_counter_ns()
		if not self.paused:
			self.__rendering(render)
			# Reenter mb.tick until we eventually get a clean exit without breakpoints
			while self.mb.tick():
				pass	
			self.frame_count += 1
		t_tick = time.perf_counter_ns()
		self._post_tick()
		t_post = time.perf_counter_ns()
		# calc performance measures
		nsecs = t_pre - t_start
		self.avg_pre = 0.9 * self.avg_pre + (0.1*nsecs/1_000_000_000)
		nsecs = t_tick - t_pre
		self.avg_tick = 0.9 * self.avg_tick + (0.1*nsecs/1_000_000_000)
		nsecs = t_post - t_tick
		self.avg_post = 0.9 * self.avg_post + (0.1*nsecs/1_000_000_000)
		return not self.quitting

	def _handle_events(self, events):
		# This feeds events into the tick-loop from the window. There might already be events in the list from the API.
		events = self._plugin_manager.handle_events(events)
		for event in events:
			if event == WindowEvent.QUIT:
				self.quitting = True
			elif event == WindowEvent.PASS:
				pass # Used in place of None in Cython, when key isn't mapped to anything
			elif event == WindowEvent.PAUSE_TOGGLE:
				if self.paused:
					self._unpause()
				else:
					self._pause()
			elif event == WindowEvent.PAUSE:
				self._pause()
			elif event == WindowEvent.UNPAUSE:
				self._unpause()
			elif event == WindowEvent._INTERNAL_RENDERER_FLUSH:
				self._plugin_manager._post_tick_windows()
			else:
				self.mb.buttonevent(event)

	def _pause(self):
		if self.paused:
			return
		self.paused = True
		logger.info("Emulation paused!")
		self._update_window_title()

	def _unpause(self):
		if not self.paused:
			return
		self.paused = False
		logger.info("Emulation unpaused!")
		self._update_window_title()

	def _post_tick(self):
		if self.frame_count % 60 == 0:
			self._update_window_title()
		self._plugin_manager.post_tick()
		self._plugin_manager.frame_limiter(1)

		# Prepare an empty list, as the API might be used to send in events between ticks
		self.events = []
		while self.queued_input and self.frame_count == self.queued_input[0][0]:
			_, _event = heapq.heappop(self.queued_input)
			self.events.append(WindowEvent(_event))

	def _update_window_title(self):
		avg_emu = self.avg_pre + self.avg_tick + self.avg_post
		self.window_title = f"CPU/frame: {(self.avg_pre + self.avg_tick) / SPF * 100:0.2f}%"
		self.window_title += f' Emulation: x{(round(SPF / avg_emu) if avg_emu > 0 else "INF")}'
		if self.paused:
			self.window_title += "[PAUSED]"
		self.window_title += self._plugin_manager.window_title()
		self._plugin_manager._set_title()

	def __del__(self):
		self.stop(save=False)

	def __enter__(self):
		return self

	def __exit__(self, type, value, traceback):
		self.stop()

	def stop(self, save=True):
		if self.initialized and not self.stopped:
			logger.info("###########################")
			logger.info("# Emulator is turning off #")
			logger.info("###########################")
			self._plugin_manager.stop()
			self.mb.stop(save)
			self.stopped = True

	def _serial(self):
		return self.mb.getserial()

	def __rendering(self, value):
		self.mb.lcd.disable_renderer = not value

	def _is_cpu_stuck(self):
		return self.mb.cpu.is_stuck




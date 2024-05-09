#
# License: See LICENSE.md file
# GitHub: https://github.com/Baekalfen/PyBoy
#

from pyboy.plugins.base_plugin import PyBoyGameWrapper

from pyboy.plugins.window_sdl2 import WindowSDL2 # isort:skip
from pyboy.plugins.debug import Debug # isort:skip
from pyboy.plugins.disable_input import DisableInput # isort:skip
from pyboy.plugins.auto_pause import AutoPause # isort:skip
from pyboy.plugins.record_replay import RecordReplay # isort:skip
from pyboy.plugins.rewind import Rewind # isort:skip
from pyboy.plugins.debug_prompt import DebugPrompt # isort:skip

def parser_arguments():
    yield WindowSDL2.argv
    pass


class PluginManager:
    def __init__(self, pyboy, mb, pyboy_argv):
        self.pyboy = pyboy
        self.window_sdl2 = WindowSDL2(pyboy, mb, pyboy_argv)
        self.window_sdl2_enabled = self.window_sdl2.enabled()

    def gamewrapper(self):
        pass

    def handle_events(self, events):
        # foreach windows events = [].handle_events(events)
        if self.window_sdl2_enabled:
            events = self.window_sdl2.handle_events(events)
        return events

    def post_tick(self):
        self._post_tick_windows()

    def _set_title(self):
        if self.window_sdl2_enabled:
            self.window_sdl2.set_title(self.pyboy.window_title)
        pass

    def _post_tick_windows(self):
        if self.window_sdl2_enabled:
            self.window_sdl2.post_tick()
        pass

    def frame_limiter(self, speed):
        if speed <= 0:
            return
        # foreach windows done = [].frame_limiter(speed), if done: return
        if self.window_sdl2_enabled:
            done = self.window_sdl2.frame_limiter(speed)
            if done: return

    def window_title(self):
        title = ""
        if self.window_sdl2_enabled:
            title += self.window_sdl2.window_title()
        return title

    def stop(self):
        if self.window_sdl2_enabled:
            self.window_sdl2.stop()

    def handle_breakpoint(self):
        if self.debug_prompt_enabled:
            self.debug_prompt.handle_breakpoint()

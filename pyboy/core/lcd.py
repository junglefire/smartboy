# -*- coding: utf-8 -*- 
#!/usr/bin/env python
import logging as logger
import array

from random import getrandbits
from ctypes import c_void_p
from copy import deepcopy

from .register import *

VIDEO_RAM = 8 * 1024 # 8KB
OBJECT_ATTRIBUTE_MEMORY = 0xA0
INTR_VBLANK, INTR_LCDC, INTR_TIMER, INTR_SERIAL, INTR_HIGHTOLOW = [1 << x for x in range(5)]
ROWS, COLS = 144, 160
TILES = 384

FRAME_CYCLES = 70224

def rgb_to_bgr(color):
    a = 0xFF
    r = (color >> 16) & 0xFF
    g = (color >> 8) & 0xFF
    b = color & 0xFF
    return (a << 24) | (b << 16) | (g << 8) | r

class LCD:
    def __init__(self, cgb, cartridge_cgb, color_palette, cgb_color_palette, randomize=False):
        self.VRAM0 = array.array("B", [0] * VIDEO_RAM)
        self.OAM = array.array("B", [0] * OBJECT_ATTRIBUTE_MEMORY)
        self.disable_renderer = False
        # randmon init
        if randomize:
            for i in range(VIDEO_RAM):
                self.VRAM0[i] = getrandbits(8)
            for i in range(OBJECT_ATTRIBUTE_MEMORY):
                self.OAM[i] = getrandbits(8)
        # register
        self._LCDC = LCDCRegister(0)
        self._STAT = STATRegister() # Bit 7 is always set.
        self.next_stat_mode = 2
        self.SCY = 0x00
        self.SCX = 0x00
        self.LY = 0x00
        self.LYC = 0x00
        # self.DMA = 0x00
        self.BGP = PaletteRegister(0xFC)
        self.OBP0 = PaletteRegister(0xFF)
        self.OBP1 = PaletteRegister(0xFF)
        self.WY = 0x00
        self.WX = 0x00
        self.clock = 0
        self.clock_target = 0
        self.frame_done = False
        self.double_speed = False
        self.cgb = cgb
        if self.cgb:
            if cartridge_cgb:
                logger.debug("Starting CGB renderer")
                self.renderer = CGBRenderer()
            else:
                logger.debug("Starting CGB renderer in DMG-mode")
                # Running DMG ROM on CGB hardware use the default palettes
                bg_pal, obj0_pal, obj1_pal = cgb_color_palette
                self.BGP.palette_mem_rgb = [(rgb_to_bgr(c)) for c in bg_pal]
                self.OBP0.palette_mem_rgb = [(rgb_to_bgr(c)) for c in obj0_pal]
                self.OBP1.palette_mem_rgb = [(rgb_to_bgr(c)) for c in obj1_pal]
                self.renderer = Renderer(False)
        else:
            logger.debug("Starting DMG renderer")
            self.BGP.palette_mem_rgb = [(rgb_to_bgr(c)) for c in color_palette]
            self.OBP0.palette_mem_rgb = [(rgb_to_bgr(c)) for c in color_palette]
            self.OBP1.palette_mem_rgb = [(rgb_to_bgr(c)) for c in color_palette]
            self.renderer = Renderer(False)

    def get_lcdc(self):
        return self._LCDC.value

    def set_lcdc(self, value):
        self._LCDC.set(value)
        if not self._LCDC.lcd_enable:
            # https://www.reddit.com/r/Gameboy/comments/a1c8h0/what_happens_when_a_gameboy_screen_is_disabled/
            # 1. LY (current rendering line) resets to zero. A few games rely on this behavior, namely Mr. Do! When LY
            # is reset to zero, no LYC check is done, so no STAT interrupt happens either.
            # 2. The LCD clock is reset to zero as far as I can tell.
            # 3. I believe the LCD enters Mode 0.
            self.clock = 0
            self.clock_target = FRAME_CYCLES # Doesn't render anything for the first frame
            self._STAT.set_mode(0)
            self.next_stat_mode = 2
            self.LY = 0

    def get_stat(self):
        return self._STAT.value

    def set_stat(self, value):
        self._STAT.set(value)

    def cycles_to_interrupt(self):
        return self.clock_target - self.clock

    def cycles_to_mode0(self):
        multiplier = 2 if self.double_speed else 1
        # Scanline (accessing OAM)
        mode2 = 80 * multiplier
        # Scanline (accessing VRAM) 
        mode3 = 170 * multiplier
        # Vertical blank
        mode1 = 456 * multiplier
        mode = self._STAT._mode
        # Remaining cycles for this already active mode
        remainder = self.clock_target - self.clock

        mode &= 0b11
        if mode == 2:
            return remainder + mode3
        elif mode == 3:
            return remainder
        elif mode == 0:
            return 0
        elif mode == 1:
            remaining_ly = 153 - self.LY
            return remainder + mode1*remaining_ly + mode2 + mode3
        # else:
        #     logger.critical("Unsupported STAT mode: %d", mode)
        #     return 0

    def tick(self, cycles):
        interrupt_flag = 0
        self.clock += cycles

        if self._LCDC.lcd_enable:
            if self.clock >= self.clock_target:
                # Change to next mode
                interrupt_flag |= self._STAT.set_mode(self.next_stat_mode)

                # Pan Docs:
                # The following are typical when the display is enabled:
                #   Mode 2  2_____2_____2_____2_____2_____2___________________2____
                #   Mode 3  _33____33____33____33____33____33__________________3___
                #   Mode 0  ___000___000___000___000___000___000________________000
                #   Mode 1  ____________________________________11111111111111_____
                multiplier = 2 if self.double_speed else 1

                # LCD state machine
                if self._STAT._mode == 2: # Searching OAM
                    if self.LY == 153:
                        self.LY = 0
                        self.clock %= FRAME_CYCLES
                        self.clock_target %= FRAME_CYCLES
                    else:
                        self.LY += 1

                    self.clock_target += 80 * multiplier
                    self.next_stat_mode = 3
                    interrupt_flag |= self._STAT.update_LYC(self.LYC, self.LY)
                elif self._STAT._mode == 3:
                    self.clock_target += 170 * multiplier
                    self.next_stat_mode = 0
                elif self._STAT._mode == 0: # HBLANK
                    self.clock_target += 206 * multiplier

                    self.renderer.scanline(self, self.LY)
                    self.renderer.scanline_sprites(
                        self, self.LY, self.renderer._screenbuffer, self.renderer._screenbuffer_attributes, False
                    )
                    if self.LY < 143:
                        self.next_stat_mode = 2
                    else:
                        self.next_stat_mode = 1
                elif self._STAT._mode == 1: # VBLANK
                    self.clock_target += 456 * multiplier
                    self.next_stat_mode = 1

                    self.LY += 1
                    interrupt_flag |= self._STAT.update_LYC(self.LYC, self.LY)

                    if self.LY == 144:
                        interrupt_flag |= INTR_VBLANK
                        self.frame_done = True

                    if self.LY == 153:
                        # Reset to new frame and start from mode 2
                        self.next_stat_mode = 2
        else:
            # See also `self.set_lcdc`
            if self.clock >= FRAME_CYCLES:
                self.frame_done = True
                self.clock %= FRAME_CYCLES

                # Renderer
                self.renderer.blank_screen(self)

        return interrupt_flag

    def getwindowpos(self):
        return (self.WX - 7, self.WY)

    def getviewport(self):
        return (self.SCX, self.SCY)


COL0_FLAG = 0b01
BG_PRIORITY_FLAG = 0b10

class Renderer:
    def __init__(self, cgb):
        self.cgb = cgb
        self.color_format = "RGBA"

        self.buffer_dims = (ROWS, COLS)

        # self.clearcache = False
        # self.tiles_changed0 = set([])

        # Init buffers as white
        self._screenbuffer_raw = array.array("B", [0x00] * (ROWS*COLS*4))
        self._screenbuffer_attributes_raw = array.array("B", [0x00] * (ROWS*COLS))
        self._tilecache0_raw = array.array("B", [0x00] * (TILES*8*8*4))
        self._spritecache0_raw = array.array("B", [0x00] * (TILES*8*8*4))
        self._spritecache1_raw = array.array("B", [0x00] * (TILES*8*8*4))
        self.sprites_to_render = array.array("i", [0] * 10)

        self._tilecache0_state = array.array("B", [0] * TILES)
        self._spritecache0_state = array.array("B", [0] * TILES)
        self._spritecache1_state = array.array("B", [0] * TILES)
        self.clear_cache()

        self._screenbuffer = memoryview(self._screenbuffer_raw).cast("I", shape=(ROWS, COLS))
        self._screenbuffer_attributes = memoryview(self._screenbuffer_attributes_raw).cast("B", shape=(ROWS, COLS))
        self._tilecache0 = memoryview(self._tilecache0_raw).cast("I", shape=(TILES * 8, 8))
        # OBP0 palette
        self._spritecache0 = memoryview(self._spritecache0_raw).cast("I", shape=(TILES * 8, 8))
        # OBP1 palette
        self._spritecache1 = memoryview(self._spritecache1_raw).cast("I", shape=(TILES * 8, 8))
        self._screenbuffer_ptr = c_void_p(self._screenbuffer_raw.buffer_info()[0])

        self._scanlineparameters = [[0, 0, 0, 0, 0] for _ in range(ROWS)]
        self.ly_window = 0

    def _cgb_get_background_map_attributes(self, lcd, i):
        tile_num = lcd.VRAM1[i]
        palette = tile_num & 0b111
        vbank = (tile_num >> 3) & 1
        horiflip = (tile_num >> 5) & 1
        vertflip = (tile_num >> 6) & 1
        bg_priority = (tile_num >> 7) & 1

        return palette, vbank, horiflip, vertflip, bg_priority

    def scanline(self, lcd, y):
        bx, by = lcd.getviewport()
        wx, wy = lcd.getwindowpos()
        # TODO: Move to lcd class
        self._scanlineparameters[y][0] = bx
        self._scanlineparameters[y][1] = by
        self._scanlineparameters[y][2] = wx
        self._scanlineparameters[y][3] = wy
        self._scanlineparameters[y][4] = lcd._LCDC.tiledata_select

        if lcd.disable_renderer:
            return

        # All VRAM addresses are offset by 0x8000
        # Following addresses are 0x9800 and 0x9C00
        background_offset = 0x1800 if lcd._LCDC.backgroundmap_select == 0 else 0x1C00
        wmap = 0x1800 if lcd._LCDC.windowmap_select == 0 else 0x1C00

        # Used for the half tile at the left side when scrolling
        offset = bx & 0b111

        # Weird behavior, where the window has it's own internal line counter. It's only incremented whenever the
        # window is drawing something on the screen.
        if lcd._LCDC.window_enable and wy <= y and wx < COLS:
            self.ly_window += 1

        for x in range(COLS):
            if lcd._LCDC.window_enable and wy <= y and wx <= x:
                tile_addr = wmap + (self.ly_window) // 8 * 32 % 0x400 + (x-wx) // 8 % 32
                wt = lcd.VRAM0[tile_addr]
                # If using signed tile indices, modify index
                if not lcd._LCDC.tiledata_select:
                    # (x ^ 0x80 - 128) to convert to signed, then
                    # add 256 for offset (reduces to + 128)
                    wt = (wt ^ 0x80) + 128

                bg_priority_apply = 0
                if self.cgb:
                    palette, vbank, horiflip, vertflip, bg_priority = self._cgb_get_background_map_attributes(
                        lcd, tile_addr
                    )
                    if vbank:
                        self.update_tilecache1(lcd, wt, vbank)
                        tilecache = self._tilecache1
                    else:
                        self.update_tilecache0(lcd, wt, vbank)
                        tilecache = self._tilecache0

                    xx = (7 - ((x-wx) % 8)) if horiflip else ((x-wx) % 8)
                    yy = (8*wt + (7 - (self.ly_window) % 8)) if vertflip else (8*wt + (self.ly_window) % 8)

                    pixel = lcd.bcpd.getcolor(palette, tilecache[yy, xx])
                    col0 = (tilecache[yy, xx] == 0) & 1
                    if bg_priority:
                        # We hide extra rendering information in the lower 8 bits (A) of the 32-bit RGBA format
                        bg_priority_apply = BG_PRIORITY_FLAG
                else:
                    self.update_tilecache0(lcd, wt, 0)
                    xx = (x-wx) % 8
                    yy = 8*wt + (self.ly_window) % 8
                    pixel = lcd.BGP.getcolor(self._tilecache0[yy, xx])
                    col0 = (self._tilecache0[yy, xx] == 0) & 1

                self._screenbuffer[y, x] = pixel
                # COL0_FLAG is 1
                self._screenbuffer_attributes[y, x] = bg_priority_apply | col0
                # self._screenbuffer_attributes[y, x] = bg_priority_apply
                # if col0:
                #     self._screenbuffer_attributes[y, x] = self._screenbuffer_attributes[y, x] | col0
            # background_enable doesn't exist for CGB. It works as master priority instead
            elif (not self.cgb and lcd._LCDC.background_enable) or self.cgb:
                tile_addr = background_offset + (y+by) // 8 * 32 % 0x400 + (x+bx) // 8 % 32
                bt = lcd.VRAM0[tile_addr]
                # If using signed tile indices, modify index
                if not lcd._LCDC.tiledata_select:
                    # (x ^ 0x80 - 128) to convert to signed, then
                    # add 256 for offset (reduces to + 128)
                    bt = (bt ^ 0x80) + 128

                bg_priority_apply = 0
                if self.cgb:
                    palette, vbank, horiflip, vertflip, bg_priority = self._cgb_get_background_map_attributes(
                        lcd, tile_addr
                    )

                    if vbank:
                        self.update_tilecache1(lcd, bt, vbank)
                        tilecache = self._tilecache1
                    else:
                        self.update_tilecache0(lcd, bt, vbank)
                        tilecache = self._tilecache0
                    xx = (7 - ((x+offset) % 8)) if horiflip else ((x+offset) % 8)
                    yy = (8*bt + (7 - (y+by) % 8)) if vertflip else (8*bt + (y+by) % 8)

                    pixel = lcd.bcpd.getcolor(palette, tilecache[yy, xx])
                    col0 = (tilecache[yy, xx] == 0) & 1
                    if bg_priority:
                        # We hide extra rendering information in the lower 8 bits (A) of the 32-bit RGBA format
                        bg_priority_apply = BG_PRIORITY_FLAG
                else:
                    self.update_tilecache0(lcd, bt, 0)
                    xx = (x+offset) % 8
                    yy = 8*bt + (y+by) % 8
                    pixel = lcd.BGP.getcolor(self._tilecache0[yy, xx])
                    col0 = (self._tilecache0[yy, xx] == 0) & 1

                self._screenbuffer[y, x] = pixel
                self._screenbuffer_attributes[y, x] = bg_priority_apply | col0
            else:
                # If background is disabled, it becomes white
                self._screenbuffer[y, x] = lcd.BGP.getcolor(0)
                self._screenbuffer_attributes[y, x] = 0

        if y == 143:
            # Reset at the end of a frame. We set it to -1, so it will be 0 after the first increment
            self.ly_window = -1

    def sort_sprites(self, sprite_count):
        # Use insertion sort, as it has O(n) on already sorted arrays. This
        # functions is likely called multiple times with unchanged data.
        # Sort descending because of the sprite priority.

        for i in range(1, sprite_count):
            key = self.sprites_to_render[i] # The current element to be inserted into the sorted portion
            j = i - 1 # Index of the last element in the sorted portion of the array

            # Move elements of the sorted portion greater than the key to the right
            while j >= 0 and key > self.sprites_to_render[j]:
                self.sprites_to_render[j + 1] = self.sprites_to_render[j]
                j -= 1

            # Insert the key into its correct position in the sorted portion
            self.sprites_to_render[j + 1] = key

    def scanline_sprites(self, lcd, ly, buffer, buffer_attributes, ignore_priority):
        if not lcd._LCDC.sprite_enable or lcd.disable_renderer:
            return

        # Find the first 10 sprites in OAM that appears on this scanline.
        # The lowest X-coordinate has priority, when overlapping
        spriteheight = 16 if lcd._LCDC.sprite_height else 8
        sprite_count = 0
        for n in range(0x00, 0xA0, 4):
            y = lcd.OAM[n] - 16 # Documentation states the y coordinate needs to be subtracted by 16
            x = lcd.OAM[n + 1] - 8 # Documentation states the x coordinate needs to be subtracted by 8

            if y <= ly < y + spriteheight:
                # x is used for sorting for priority
                if self.cgb:
                    self.sprites_to_render[sprite_count] = n
                else:
                    self.sprites_to_render[sprite_count] = x << 16 | n
                sprite_count += 1

            if sprite_count == 10:
                break

        # Pan docs:
        # When these 10 sprites overlap, the highest priority one will appear above all others, etc. (Thus, no
        # Z-fighting.) In CGB mode, the first sprite in OAM ($FE00-$FE03) has the highest priority, and so on. In
        # Non-CGB mode, the smaller the X coordinate, the higher the priority. The tie breaker (same X coordinates) is
        # the same priority as in CGB mode.
        self.sort_sprites(sprite_count)

        for _n in self.sprites_to_render[:sprite_count]:
            if self.cgb:
                n = _n
            else:
                n = _n & 0xFF
            # n = self.sprites_to_render_n[_n]
            y = lcd.OAM[n] - 16 # Documentation states the y coordinate needs to be subtracted by 16
            x = lcd.OAM[n + 1] - 8 # Documentation states the x coordinate needs to be subtracted by 8
            tileindex = lcd.OAM[n + 2]
            if spriteheight == 16:
                tileindex &= 0b11111110
            attributes = lcd.OAM[n + 3]
            xflip = attributes & 0b00100000
            yflip = attributes & 0b01000000
            spritepriority = (attributes & 0b10000000) and not ignore_priority
            if self.cgb:
                palette = attributes & 0b111
                if attributes & 0b1000:
                    self.update_spritecache1(lcd, tileindex, 1)
                    if lcd._LCDC.sprite_height:
                        self.update_spritecache1(lcd, tileindex + 1, 1)
                    spritecache = self._spritecache1
                else:
                    self.update_spritecache0(lcd, tileindex, 0)
                    if lcd._LCDC.sprite_height:
                        self.update_spritecache0(lcd, tileindex + 1, 0)
                    spritecache = self._spritecache0
            else:
                # Fake palette index
                palette = 0
                if attributes & 0b10000:
                    self.update_spritecache1(lcd, tileindex, 0)
                    if lcd._LCDC.sprite_height:
                        self.update_spritecache1(lcd, tileindex + 1, 0)
                    spritecache = self._spritecache1
                else:
                    self.update_spritecache0(lcd, tileindex, 0)
                    if lcd._LCDC.sprite_height:
                        self.update_spritecache0(lcd, tileindex + 1, 0)
                    spritecache = self._spritecache0

            dy = ly - y
            yy = spriteheight - dy - 1 if yflip else dy

            for dx in range(8):
                xx = 7 - dx if xflip else dx
                color_code = spritecache[8*tileindex + yy, xx]
                if 0 <= x < COLS and not color_code == 0: # If pixel is not transparent
                    if self.cgb:
                        pixel = lcd.ocpd.getcolor(palette, color_code)
                        bgmappriority = buffer_attributes[ly, x] & BG_PRIORITY_FLAG

                        if lcd._LCDC.cgb_master_priority: # If 0, sprites are always on top, if 1 follow priorities
                            if bgmappriority: # If 0, use spritepriority, if 1 take priority
                                if buffer_attributes[ly, x] & COL0_FLAG:
                                    buffer[ly, x] = pixel
                            elif spritepriority: # If 1, sprite is behind bg/window. Color 0 of window/bg is transparent
                                if buffer_attributes[ly, x] & COL0_FLAG:
                                    buffer[ly, x] = pixel
                            else:
                                buffer[ly, x] = pixel
                        else:
                            buffer[ly, x] = pixel
                    else:
                        # TODO: Unify with CGB
                        if attributes & 0b10000:
                            pixel = lcd.OBP1.getcolor(color_code)
                        else:
                            pixel = lcd.OBP0.getcolor(color_code)

                        if spritepriority: # If 1, sprite is behind bg/window. Color 0 of window/bg is transparent
                            if buffer_attributes[ly, x] & COL0_FLAG: # if BG pixel is transparent
                                buffer[ly, x] = pixel
                        else:
                            buffer[ly, x] = pixel
                x += 1
            x -= 8

    def clear_cache(self):
        self.clear_tilecache0()
        self.clear_spritecache0()
        self.clear_spritecache1()

    def invalidate_tile(self, tile, vbank):
        if vbank and self.cgb:
            self._tilecache0_state[tile] = 0
            self._tilecache1_state[tile] = 0
            self._spritecache0_state[tile] = 0
            self._spritecache1_state[tile] = 0
        else:
            self._tilecache0_state[tile] = 0
            if self.cgb:
                self._tilecache1_state[tile] = 0
            self._spritecache0_state[tile] = 0
            self._spritecache1_state[tile] = 0

    def clear_tilecache0(self):
        for i in range(TILES):
            self._tilecache0_state[i] = 0

    def clear_tilecache1(self):
        pass

    def clear_spritecache0(self):
        for i in range(TILES):
            self._spritecache0_state[i] = 0

    def clear_spritecache1(self):
        for i in range(TILES):
            self._spritecache1_state[i] = 0

    def color_code(self, byte1, byte2, offset):
        """Convert 2 bytes into color code at a given offset.

        The colors are 2 bit and are found like this:

        Color of the first pixel is 0b10
        | Color of the second pixel is 0b01
        v v
        1 0 0 1 0 0 0 1 <- byte1
        0 1 1 1 1 1 0 0 <- byte2
        """
        return (((byte2 >> (offset)) & 0b1) << 1) + ((byte1 >> (offset)) & 0b1)

    def update_tilecache0(self, lcd, t, bank):
        if self._tilecache0_state[t]:
            return
        # for t in self.tiles_changed0:
        for k in range(0, 16, 2): # 2 bytes for each line
            byte1 = lcd.VRAM0[t*16 + k]
            byte2 = lcd.VRAM0[t*16 + k + 1]
            y = (t*16 + k) // 2

            for x in range(8):
                colorcode = self.color_code(byte1, byte2, 7 - x)
                self._tilecache0[y, x] = colorcode

        self._tilecache0_state[t] = 1

    def update_tilecache1(self, lcd, t, bank):
        pass

    def update_spritecache0(self, lcd, t, bank):
        if self._spritecache0_state[t]:
            return
        # for t in self.tiles_changed0:
        for k in range(0, 16, 2): # 2 bytes for each line
            byte1 = lcd.VRAM0[t*16 + k]
            byte2 = lcd.VRAM0[t*16 + k + 1]
            y = (t*16 + k) // 2

            for x in range(8):
                colorcode = self.color_code(byte1, byte2, 7 - x)
                self._spritecache0[y, x] = colorcode

        self._spritecache0_state[t] = 1

    def update_spritecache1(self, lcd, t, bank):
        if self._spritecache1_state[t]:
            return
        # for t in self.tiles_changed0:
        for k in range(0, 16, 2): # 2 bytes for each line
            byte1 = lcd.VRAM0[t*16 + k]
            byte2 = lcd.VRAM0[t*16 + k + 1]
            y = (t*16 + k) // 2

            for x in range(8):
                colorcode = self.color_code(byte1, byte2, 7 - x)
                self._spritecache1[y, x] = colorcode

        self._spritecache1_state[t] = 1

    def blank_screen(self, lcd):
        # If the screen is off, fill it with a color.
        for y in range(ROWS):
            for x in range(COLS):
                self._screenbuffer[y, x] = lcd.BGP.getcolor(0)
                self._screenbuffer_attributes[y, x] = 0


####################################
#
#  ██████   ██████   ██████
# ██       ██        ██   ██
# ██       ██   ███  ██████
# ██       ██    ██  ██   ██
#  ██████   ██████   ██████
#


class CGBLCD(LCD):
    def __init__(self, cgb, cartridge_cgb, color_palette, cgb_color_palette, randomize=False):
        LCD.__init__(self, cgb, cartridge_cgb, color_palette, cgb_color_palette, randomize=False)
        self.VRAM1 = array.array("B", [0] * VIDEO_RAM)

        self.vbk = VBKregister()
        self.bcps = PaletteIndexRegister()
        self.bcpd = PaletteColorRegister(self.bcps)
        self.ocps = PaletteIndexRegister()
        self.ocpd = PaletteColorRegister(self.ocps)


class CGBRenderer(Renderer):
    def __init__(self):
        self._tilecache1_state = array.array("B", [0] * TILES)
        Renderer.__init__(self, True)

        self._tilecache1_raw = array.array("B", [0xFF] * (TILES*8*8*4))

        self._tilecache1 = memoryview(self._tilecache1_raw).cast("I", shape=(TILES * 8, 8))
        self._tilecache1_state = array.array("B", [0] * TILES)
        self.clear_cache()

    def clear_cache(self):
        self.clear_tilecache0()
        self.clear_tilecache1()
        self.clear_spritecache0()
        self.clear_spritecache1()

    def clear_tilecache1(self):
        for i in range(TILES):
            self._tilecache1_state[i] = 0

    def update_tilecache0(self, lcd, t, bank):
        if self._tilecache0_state[t]:
            return

        if bank:
            vram_bank = lcd.VRAM1
        else:
            vram_bank = lcd.VRAM0

        # for t in self.tiles_changed0:
        for k in range(0, 16, 2): # 2 bytes for each line
            byte1 = vram_bank[t*16 + k]
            byte2 = vram_bank[t*16 + k + 1]
            y = (t*16 + k) // 2

            for x in range(8):
                self._tilecache0[y, x] = self.color_code(byte1, byte2, 7 - x)

        self._tilecache0_state[t] = 1

    def update_tilecache1(self, lcd, t, bank):
        if self._tilecache1_state[t]:
            return
        if bank:
            vram_bank = lcd.VRAM1
        else:
            vram_bank = lcd.VRAM0
        # for t in self.tiles_changed0:
        for k in range(0, 16, 2): # 2 bytes for each line
            byte1 = vram_bank[t*16 + k]
            byte2 = vram_bank[t*16 + k + 1]
            y = (t*16 + k) // 2

            for x in range(8):
                self._tilecache1[y, x] = self.color_code(byte1, byte2, 7 - x)

        self._tilecache1_state[t] = 1

    def update_spritecache0(self, lcd, t, bank):
        if self._spritecache0_state[t]:
            return
        if bank:
            vram_bank = lcd.VRAM1
        else:
            vram_bank = lcd.VRAM0
        # for t in self.tiles_changed0:
        for k in range(0, 16, 2): # 2 bytes for each line
            byte1 = vram_bank[t*16 + k]
            byte2 = vram_bank[t*16 + k + 1]
            y = (t*16 + k) // 2

            for x in range(8):
                self._spritecache0[y, x] = self.color_code(byte1, byte2, 7 - x)

        self._spritecache0_state[t] = 1

    def update_spritecache1(self, lcd, t, bank):
        if self._spritecache1_state[t]:
            return
        if bank:
            vram_bank = lcd.VRAM1
        else:
            vram_bank = lcd.VRAM0
        # for t in self.tiles_changed0:
        for k in range(0, 16, 2): # 2 bytes for each line
            byte1 = vram_bank[t*16 + k]
            byte2 = vram_bank[t*16 + k + 1]
            y = (t*16 + k) // 2

            for x in range(8):
                self._spritecache1[y, x] = self.color_code(byte1, byte2, 7 - x)

        self._spritecache1_state[t] = 1


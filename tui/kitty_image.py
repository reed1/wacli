"""Inline image display using the Kitty graphics protocol.

Images are sent as virtual placements and drawn through Unicode placeholder cells rather
than placed directly at a screen position, so Textual's compositor keeps full control of
the region: the image scrolls, clips and gets overdrawn like ordinary text.
"""

import base64
import fcntl
import io
import os
import struct
import sys
import termios

from itertools import count
from math import ceil, floor
from pathlib import Path
from random import randint

from PIL import Image as PILImage
from PIL import ImageOps
from rich.color import Color
from rich.console import Console, ConsoleOptions, RenderResult
from rich.segment import Segment
from rich.style import Style
from textual.events import Resize
from textual.widget import Widget

from tui.utils import log

PLACEHOLDER = chr(0x10EEEE)
_ESCAPE_START = "\x1b_G"
_ESCAPE_END = "\x1b\\"
_CHUNK_SIZE = 4096
_FALLBACK_CELL_SIZE = (10, 20)

# Kitty encodes the row/column of each placeholder cell as combining marks drawn from this
# fixed table; index i means "row/column i".
# fmt: off
NUMBER_TO_DIACRITIC = [
    0x00305, 0x0030d, 0x0030e, 0x00310, 0x00312, 0x0033d, 0x0033e, 0x0033f, 0x00346, 0x0034a, 0x0034b, 0x0034c,
    0x00350, 0x00351, 0x00352, 0x00357, 0x0035b, 0x00363, 0x00364, 0x00365, 0x00366, 0x00367, 0x00368, 0x00369,
    0x0036a, 0x0036b, 0x0036c, 0x0036d, 0x0036e, 0x0036f, 0x00483, 0x00484, 0x00485, 0x00486, 0x00487, 0x00592,
    0x00593, 0x00594, 0x00595, 0x00597, 0x00598, 0x00599, 0x0059c, 0x0059d, 0x0059e, 0x0059f, 0x005a0, 0x005a1,
    0x005a8, 0x005a9, 0x005ab, 0x005ac, 0x005af, 0x005c4, 0x00610, 0x00611, 0x00612, 0x00613, 0x00614, 0x00615,
    0x00616, 0x00617, 0x00657, 0x00658, 0x00659, 0x0065a, 0x0065b, 0x0065d, 0x0065e, 0x006d6, 0x006d7, 0x006d8,
    0x006d9, 0x006da, 0x006db, 0x006dc, 0x006df, 0x006e0, 0x006e1, 0x006e2, 0x006e4, 0x006e7, 0x006e8, 0x006eb,
    0x006ec, 0x00730, 0x00732, 0x00733, 0x00735, 0x00736, 0x0073a, 0x0073d, 0x0073f, 0x00740, 0x00741, 0x00743,
    0x00745, 0x00747, 0x00749, 0x0074a, 0x007eb, 0x007ec, 0x007ed, 0x007ee, 0x007ef, 0x007f0, 0x007f1, 0x007f3,
    0x00816, 0x00817, 0x00818, 0x00819, 0x0081b, 0x0081c, 0x0081d, 0x0081e, 0x0081f, 0x00820, 0x00821, 0x00822,
    0x00823, 0x00825, 0x00826, 0x00827, 0x00829, 0x0082a, 0x0082b, 0x0082c, 0x0082d, 0x00951, 0x00953, 0x00954,
    0x00f82, 0x00f83, 0x00f86, 0x00f87, 0x0135d, 0x0135e, 0x0135f, 0x017dd, 0x0193a, 0x01a17, 0x01a75, 0x01a76,
    0x01a77, 0x01a78, 0x01a79, 0x01a7a, 0x01a7b, 0x01a7c, 0x01b6b, 0x01b6d, 0x01b6e, 0x01b6f, 0x01b70, 0x01b71,
    0x01b72, 0x01b73, 0x01cd0, 0x01cd1, 0x01cd2, 0x01cda, 0x01cdb, 0x01ce0, 0x01dc0, 0x01dc1, 0x01dc3, 0x01dc4,
    0x01dc5, 0x01dc6, 0x01dc7, 0x01dc8, 0x01dc9, 0x01dcb, 0x01dcc, 0x01dd1, 0x01dd2, 0x01dd3, 0x01dd4, 0x01dd5,
    0x01dd6, 0x01dd7, 0x01dd8, 0x01dd9, 0x01dda, 0x01ddb, 0x01ddc, 0x01ddd, 0x01dde, 0x01ddf, 0x01de0, 0x01de1,
    0x01de2, 0x01de3, 0x01de4, 0x01de5, 0x01de6, 0x01dfe, 0x020d0, 0x020d1, 0x020d4, 0x020d5, 0x020d6, 0x020d7,
    0x020db, 0x020dc, 0x020e1, 0x020e7, 0x020e9, 0x020f0, 0x02cef, 0x02cf0, 0x02cf1, 0x02de0, 0x02de1, 0x02de2,
    0x02de3, 0x02de4, 0x02de5, 0x02de6, 0x02de7, 0x02de8, 0x02de9, 0x02dea, 0x02deb, 0x02dec, 0x02ded, 0x02dee,
    0x02def, 0x02df0, 0x02df1, 0x02df2, 0x02df3, 0x02df4, 0x02df5, 0x02df6, 0x02df7, 0x02df8, 0x02df9, 0x02dfa,
    0x02dfb, 0x02dfc, 0x02dfd, 0x02dfe, 0x02dff, 0x0a66f, 0x0a67c, 0x0a67d, 0x0a6f0, 0x0a6f1, 0x0a8e0, 0x0a8e1,
    0x0a8e2, 0x0a8e3, 0x0a8e4, 0x0a8e5, 0x0a8e6, 0x0a8e7, 0x0a8e8, 0x0a8e9, 0x0a8ea, 0x0a8eb, 0x0a8ec, 0x0a8ed,
    0x0a8ee, 0x0a8ef, 0x0a8f0, 0x0a8f1, 0x0aab0, 0x0aab2, 0x0aab3, 0x0aab7, 0x0aab8, 0x0aabe, 0x0aabf, 0x0aac1,
    0x0fe20, 0x0fe21, 0x0fe22, 0x0fe23, 0x0fe24, 0x0fe25, 0x0fe26, 0x10a0f, 0x10a38, 0x1d185, 0x1d186, 0x1d187,
    0x1d188, 0x1d189, 0x1d1aa, 0x1d1ab, 0x1d1ac, 0x1d1ad, 0x1d242, 0x1d243, 0x1d244,
]
# fmt: on

MAX_CELLS = len(NUMBER_TO_DIACRITIC)

_image_ids = count(randint(1, 2**24))


def terminal_is_kitty() -> bool:
    return bool(os.environ.get("KITTY_WINDOW_ID")) or os.environ.get("TERM", "").endswith("kitty")


def cell_size() -> tuple[int, int]:
    """Pixel size of a single terminal cell."""
    try:
        packed = fcntl.ioctl(sys.__stderr__.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
    except OSError:
        return _FALLBACK_CELL_SIZE
    rows, columns, x_pixels, y_pixels = struct.unpack("HHHH", packed)
    if not all((rows, columns, x_pixels, y_pixels)):
        return _FALLBACK_CELL_SIZE
    return x_pixels // columns, y_pixels // rows


class _Placement:
    """A transmitted image plus the placeholder grid that reveals it."""

    def __init__(self, image_id: int, columns: int, rows: int) -> None:
        self.image_id = image_id
        self.columns = columns
        self.rows = rows
        self.left_pad = 0
        self.top_pad = 0

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        # The image id reaches the terminal as the cell's foreground colour, with the
        # highest byte carried by a third combining mark.
        style = Style.from_color(
            Color.from_rgb(
                (self.image_id >> 16) & 0xFF, (self.image_id >> 8) & 0xFF, self.image_id & 0xFF
            )
        )
        id_mark = chr(NUMBER_TO_DIACRITIC[(self.image_id >> 24) & 0xFF])
        padding = " " * self.left_pad
        for _ in range(self.top_pad):
            yield Segment("\n")
        for row in range(self.rows):
            row_mark = chr(NUMBER_TO_DIACRITIC[row])
            cells = "".join(
                f"{PLACEHOLDER}{row_mark}{chr(NUMBER_TO_DIACRITIC[column])}{id_mark}"
                for column in range(self.columns)
            )
            yield Segment(padding)
            yield Segment(cells, style)
            yield Segment("\n")


class KittyImage(Widget):
    """Draws an image scaled to fit the widget's box, preserving aspect ratio.

    Falls back to a one-line description wherever the graphics protocol is unusable
    (non-kitty terminal, headless test driver).
    """

    DEFAULT_CSS = """
    KittyImage {
        width: 1fr;
        height: 1fr;
    }
    """

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self._source = PILImage.open(path)
        self._placement: _Placement | None = None

    @property
    def graphics_enabled(self) -> bool:
        driver = self.app._driver
        return terminal_is_kitty() and driver is not None and not driver.is_headless

    def on_resize(self, event: Resize) -> None:
        if self.graphics_enabled:
            self._place(event.size.width, event.size.height)

    def on_unmount(self) -> None:
        self._release()

    def render(self) -> RenderResult:
        if self._placement is not None:
            return self._placement
        return f"[dim]🖼 {self.path.name} ({self._source.width}×{self._source.height})[/]"

    def _fit(self, columns: int, rows: int) -> tuple[int, int]:
        """Largest cell box within columns×rows that keeps the image's aspect ratio."""
        cell_width, cell_height = cell_size()
        aspect = self._source.height / self._source.width
        fitted_rows = ceil(columns * cell_width * aspect / cell_height)
        if fitted_rows <= rows:
            return columns, max(1, fitted_rows)
        fitted_columns = floor(rows * cell_height / (cell_width * aspect))
        return max(1, fitted_columns), rows

    def _place(self, box_columns: int, box_rows: int) -> None:
        if box_columns < 1 or box_rows < 1:
            return
        box_columns, box_rows = min(box_columns, MAX_CELLS), min(box_rows, MAX_CELLS)
        columns, rows = self._fit(box_columns, box_rows)
        if self._placement and (self._placement.columns, self._placement.rows) == (columns, rows):
            return

        self._release()
        cell_width, cell_height = cell_size()
        # Cell boxes are coarse, so letterbox onto a transparent canvas of exactly the
        # placement size instead of stretching the image to the nearest whole cell.
        target = (columns * cell_width, rows * cell_height)
        fitted = ImageOps.contain(self._source.convert("RGBA"), target, PILImage.LANCZOS)
        canvas = PILImage.new("RGBA", target, (0, 0, 0, 0))
        canvas.paste(fitted, ((target[0] - fitted.width) // 2, (target[1] - fitted.height) // 2))

        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode()

        image_id = next(_image_ids)
        while encoded:
            chunk, encoded = encoded[:_CHUNK_SIZE], encoded[_CHUNK_SIZE:]
            self._send(chunk, i=image_id, f=100, m=1 if encoded else 0, q=2)
        self._send(a="p", i=image_id, c=columns, r=rows, U=1, q=2)

        placement = _Placement(image_id, columns, rows)
        placement.left_pad = (box_columns - columns) // 2
        placement.top_pad = (box_rows - rows) // 2
        self._placement = placement
        log(f"kitty_image: {self.path.name} {columns}x{rows} cells in {box_columns}x{box_rows}")
        self.refresh()

    def _release(self) -> None:
        if self._placement is not None:
            self._send(a="d", d="I", i=self._placement.image_id, q=2)
            self._placement = None

    def _send(self, payload: str = "", **keys: int | str) -> None:
        # Routed through the driver so graphics commands stay ordered with the frames that
        # reference them; writing to the tty directly would race Textual's writer thread.
        control = ",".join(f"{key}={value}" for key, value in keys.items())
        self.app._driver.write(
            f"{_ESCAPE_START}{control}{f';{payload}' if payload else ''}{_ESCAPE_END}"
        )

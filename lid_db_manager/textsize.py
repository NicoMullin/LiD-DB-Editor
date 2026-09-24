"""How big the text is, as a percentage.

Its own module because two sides need it and neither should depend on the other:
``state`` remembers the number between sessions and must stay free of Qt, so that
the command line and the tests still run where PySide6 is not installed, while
``ui.theme`` turns it into pixels.

Nothing here knows about pixels or fonts - only what counts as a number this
build will accept.
"""

from __future__ import annotations

# 70% is small but still legible on a 4K screen. Past 200% the window stops
# being able to fit its own controls, so that is where it stops rather than
# letting somebody scale the buttons off the edge.
MIN_SCALE = 70
MAX_SCALE = 200
DEFAULT_SCALE = 100

# What one press of "bigger"/"smaller" moves.
SCALE_STEP = 10

# What the View menu offers directly, so the common sizes are one click.
SCALE_PRESETS = (80, 90, 100, 110, 125, 150, 175, 200)


def clamp_scale(value) -> int:
    """A text scale this build will accept, whatever was asked for.

    Anything unreadable - a string, None, a state.json edited by hand - reads as
    the default rather than raising, because the alternative is a window that
    will not open.
    """
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return DEFAULT_SCALE
    return max(MIN_SCALE, min(MAX_SCALE, number))


def stepped(scale, *, up: bool) -> int:
    """The next size in that direction, landing on multiples of SCALE_STEP.

    A scale arrived at from a preset - 125 - steps to 130 and then 140 rather
    than staying off the grid forever.
    """
    current = clamp_scale(scale)
    if up:
        return clamp_scale((current // SCALE_STEP + 1) * SCALE_STEP)
    if current % SCALE_STEP:
        return clamp_scale(current - current % SCALE_STEP)
    return clamp_scale(current - SCALE_STEP)

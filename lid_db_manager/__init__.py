"""LET IT DIE DB Mod Manager.

A small offline tool that applies JSON- or SQL-defined SQLite patches to
LET IT DIE's ``masters.db``, snapshots the pre-state for revert, and
re-applies the enabled mod list when the game replaces the database.
"""

__version__ = "0.1.0"

APP_NAME = "LET IT DIE DB Mod Manager"
GAME_NAME = "LET IT DIE"
DB_FILENAME = "masters.db"

# Best-guess default the file picker opens on. No auto-detection (A3.5/A6.1).
DEFAULT_DB_HINT = r"C:\Program Files (x86)\Steam\steamapps\common\LET IT DIE\BrgGame\Content\masters.db"

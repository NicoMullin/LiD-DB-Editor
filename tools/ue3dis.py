"""The UnrealScript bytecode reader, for the tools that change BrgGame.upk.

It lives in the manager now (lid_db_manager/upk/ue3script.py), which merges two
mods' changes to one function with it; the tools put the manager on the path
before importing this.
"""
from lid_db_manager.upk.ue3script import OBJ_DISK, OBJ_MEM, Dis  # noqa: F401

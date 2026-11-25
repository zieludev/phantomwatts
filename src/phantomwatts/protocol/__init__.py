"""Byte-exact codecs for the GATT characteristics a smart trainer exposes.

Pure functions and frozen dataclasses: no Bluetooth stack, no I/O. Values are in the unit the
field name says (watts, km/h, percent); the wire resolution is handled inside the codec, so a
value that sits on the wire grid round-trips exactly.
"""

import os

# Must be set before any Qt import so tests never open real windows.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

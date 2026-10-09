"""Data package holding the Manim scene runtime that is injected into every sandboxed script.

``scene_runtime.py`` is read as TEXT by :mod:`aadhi.manim.aadhi_scene` and executed only inside the
sandbox. Never import it in the application: it patches Manim's TeX handling process-wide. This
``__init__`` exists so packaging (``setuptools.packages.find``) ships the file with the wheel.
"""

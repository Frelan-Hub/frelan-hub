"""Presentation / control-plane layer for the AI-Conductor B Runtime.

This package is a *front end over the runtime*, in the same sense that
``main.py`` is a front end over it: it wires the existing Conductor together and
decides where its own files go. It never reaches inside the Mission,
Interpretation or Transport layers, and it changes no contract — a run started
from the dashboard is the same ``main.py`` subprocess a Founder would start from
a terminal, given an explicit ``-o``.

Modules, inner to outer:

- :mod:`ui.runs`       — run registry, ledger reading, artifact discovery. Pure;
                         imports no Streamlit, so it is cheap to test.
- :mod:`ui.library`    — meeting-type menu and contract briefs. Also pure.
- :mod:`ui.state`      — session state, config persistence, console-log drain.
- :mod:`ui.theme`      — CSS, header bar, status pill, section furniture.
- :mod:`ui.launcher`   — starting and stopping the Conductor subprocess.
- :mod:`ui.components` — shared renderers used by more than one view.
- :mod:`ui.views`      — the nine control-plane views.

``streamlit_app.py`` wires them and is the only module that names a view.
"""

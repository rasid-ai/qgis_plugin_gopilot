"""Run the plugin's unit tests against a QGIS Python.

The Makefile's ``make test`` target drives nosetests, which has not run on
Python 3.10+ (it imports ``collections.Callable``). This runner does the same
three things that target did, with the standard library:

1. puts the plugin root AND ``test/`` on ``sys.path``;
2. puts the plugin's bundled site-packages on ``sys.path``, so ``markdown``
   imports the way it does inside QGIS;
3. runs the tests.

Usage, from the plugin root::

    "C:/Program Files/QGIS 3.36.3/bin/python-qgis.bat" scripts/run_tests.py
    "C:/Program Files/QGIS 3.36.3/bin/python-qgis.bat" scripts/run_tests.py test_gopilot_dock

With no arguments it runs every ``test_*.py`` in ``test/``.
"""

import os
import sys
import unittest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DIR = os.path.join(PLUGIN_DIR, "test")

for path in (TEST_DIR, PLUGIN_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

# Native platform by default: the offscreen plugin has no font database on
# Windows, and a few of these tests measure laid-out widgets.
if not os.environ.get("QT_QPA_PLATFORM") and not sys.platform.startswith("win"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"


def main():
    from rasid_components.dependencies import venv_manager
    if not venv_manager.ensure_venv_packages_available():
        missing = ", ".join(venv_manager.get_missing_packages()) or "unknown"
        print(f"warning: missing bundled packages ({missing}); "
              f"expected in {venv_manager.get_venv_dir()}")

    loader = unittest.TestLoader()
    names = sys.argv[1:]
    if names:
        suite = loader.loadTestsFromNames(names)
    else:
        # top_level_dir is the PLUGIN root, not test/, so the modules load as
        # ``test.test_*`` — a package. test_qgis_environment and
        # test_translations say ``from .utilities import get_qgis_app``, and a
        # relative import needs a parent package; discovered from test/ as
        # top-level they have none, and both failed to import at collection
        # time. That is why the suite reported two errors that no test in it
        # was responsible for.
        suite = loader.discover(TEST_DIR, pattern="test_*.py",
                                top_level_dir=PLUGIN_DIR)

    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

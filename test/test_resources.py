# coding=utf-8
"""Resources test.

.. note:: This program is free software; you can redistribute it and/or modify
     it under the terms of the GNU General Public License as published by
     the Free Software Foundation; either version 2 of the License, or
     (at your option) any later version.

"""

__author__ = 'info@rasid.ai'
__date__ = '2026-03-20'
__copyright__ = 'Copyright 2026, RASID '

import unittest

from qgis.PyQt.QtGui import QIcon



class RasidPluginDialogTest(unittest.TestCase):
    """Test rerources work."""

    def setUp(self):
        """Register the compiled Qt resources this test looks things up in.

        The import is the whole point: the resource path below only resolves
        once ``resources.py`` has run, and this test never imported it. It
        therefore passed only when some EARLIER test in the run happened to
        import it first — which is why it was green on Qt5 and red on Qt6,
        where the tests that used to do so are gone.
        """
        import resources                              # noqa: F401

    def tearDown(self):
        """Runs after each test."""
        pass

    def test_icon_png(self):
        """The compiled .qrc registers the prefix it declares.

        NOTE: nothing in the plugin reads this resource. The toolbar icon is
        loaded from the filesystem (``rasid_plugin.py`` joins the plugin dir
        with ``gopilot-mark.svg``), so ``resources.py`` is imported for its
        side effect and never used. This test is kept only because it is the
        one thing that would notice the .qrc failing to compile.

        The path was ``/plugins/RasidPlugin/`` — Plugin Builder's original
        camel-case name, which the .qrc has never used. It declares
        ``/plugins/rasid_plugin``, so that was asserting a resource that has
        never existed in this repo.
        """
        path = ':/plugins/rasid_plugin/icon.png'
        icon = QIcon(path)
        self.assertFalse(icon.isNull())

if __name__ == "__main__":
    suite = unittest.makeSuite(RasidPluginResourcesTest)
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)




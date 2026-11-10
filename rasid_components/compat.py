"""Qt5/Qt6 Compatibility Layer for QGIS 3.x and 4.x

This module provides version-safe Qt constants that work across both:
- QGIS 3.x (Qt5/PyQt5)
- QGIS 4.x (Qt6/PyQt6)

WHY THIS FILE EXISTS
====================
In Qt6, all enum constants were moved under their enum class names (scoped
enums). The old flat syntax (``Qt.AlignCenter``) was removed in Qt6 and raises
``AttributeError`` there. The scoped syntax (``Qt.AlignmentFlag.AlignCenter``)
is understood by BOTH PyQt5 (5.15+, which every supported QGIS build ships) and
PyQt6, so this module uses the scoped form exclusively.

Using the scoped form everywhere has two benefits:
1. One code path — no runtime ``QT_VERSION`` branching for enums, so there is
   nothing to keep in sync between a Qt5 and a Qt6 branch.
2. The QGIS plugin repository's static Qt6-compatibility checker scans source
   text and flags flat enums even when they sit inside a Qt5-only runtime
   branch (it cannot see the guard). Scoped enums pass that check cleanly.

HOW THIS WORKS
==============
1. Export version-safe constants with a ``Qt_`` (or ``QWidget_``) prefix using
   scoped enum syntax.
2. All plugin code imports from this file instead of using raw ``Qt.*``
   constants.

Example usage in your code:
    from .compat import Qt_AlignCenter, Qt_WindowMinimizeButtonHint

    label.setAlignment(Qt_AlignCenter)  # Works on both Qt5 and Qt6
    self.setWindowFlags(self.windowFlags() | Qt_WindowMinimizeButtonHint)

SUPPORTED VERSIONS
==================
This compatibility layer enables the plugin to run on:
- QGIS 3.0 - 3.99 (Qt5 / PyQt5 5.15+)
- QGIS 4.0 - 4.99 (Qt6 / PyQt6)

IMPORTANT RULES
===============
1. ALWAYS use qgis.PyQt imports, NEVER PyQt5 or PyQt6 directly.
2. NEVER use raw ``Qt.Something`` flat constants anywhere (including this file).
3. If you need a new Qt constant, add ONE line here using scoped syntax:
       Qt_ConstantName = Qt.EnumClass.ConstantName
   then import it: ``from .compat import Qt_ConstantName``.
4. ``exec_dialog()`` below still branches on version because the method name
   itself differs (Qt5 ``exec_()`` vs Qt6 ``exec()``); that is not an enum.
"""
from qgis.PyQt.QtCore import QT_VERSION, QEvent, Qt, QEasingCurve
from qgis.PyQt.QtGui import QColor, QIcon, QPainter
from qgis.PyQt.QtWidgets import (
    QDialog, QDockWidget, QFrame, QLineEdit, QMessageBox, QSizePolicy,
)

QT6 = QT_VERSION >= 0x060000


def exec_dialog(dialog):
    """Execute a dialog modally.

    Qt6 renamed the old ``exec_()`` (a Python 2-era workaround, since ``exec``
    was a keyword) back to ``exec()``. PyQt5 5.15+ — the oldest binding any
    supported QGIS build ships — also provides ``exec()``, so calling it
    directly works on both and needs no version branch.

    Usage:
        from .compat import exec_dialog
        result = exec_dialog(my_dialog)
    """
    return dialog.exec()


# Scoped enum syntax below works on both PyQt5 (5.15+) and PyQt6.

# Window flags
Qt_Window                    = Qt.WindowType.Window
Qt_Dialog                    = Qt.WindowType.Dialog
Qt_WindowMinimizeButtonHint  = Qt.WindowType.WindowMinimizeButtonHint
Qt_WindowMaximizeButtonHint  = Qt.WindowType.WindowMaximizeButtonHint
Qt_WindowCloseButtonHint     = Qt.WindowType.WindowCloseButtonHint
Qt_WindowStaysOnTopHint      = Qt.WindowType.WindowStaysOnTopHint

# Alignment
Qt_AlignCenter               = Qt.AlignmentFlag.AlignCenter
Qt_AlignLeft                 = Qt.AlignmentFlag.AlignLeft
Qt_AlignRight                = Qt.AlignmentFlag.AlignRight
Qt_AlignTop                  = Qt.AlignmentFlag.AlignTop
Qt_AlignBottom               = Qt.AlignmentFlag.AlignBottom
Qt_AlignVCenter              = Qt.AlignmentFlag.AlignVCenter
Qt_AlignHCenter              = Qt.AlignmentFlag.AlignHCenter

# Orientation
Qt_Horizontal                = Qt.Orientation.Horizontal
Qt_Vertical                  = Qt.Orientation.Vertical

# Item roles / flags
Qt_UserRole                  = Qt.ItemDataRole.UserRole
Qt_DisplayRole               = Qt.ItemDataRole.DisplayRole
Qt_CheckStateRole            = Qt.ItemDataRole.CheckStateRole
Qt_ItemIsEditable            = Qt.ItemFlag.ItemIsEditable
Qt_ItemIsEnabled             = Qt.ItemFlag.ItemIsEnabled
Qt_ItemIsSelectable          = Qt.ItemFlag.ItemIsSelectable

# Mouse buttons
Qt_LeftButton                = Qt.MouseButton.LeftButton
Qt_RightButton               = Qt.MouseButton.RightButton
Qt_MiddleButton              = Qt.MouseButton.MiddleButton

# QFrame shapes
QFrame_NoFrame               = QFrame.Shape.NoFrame
QFrame_HLine                 = QFrame.Shape.HLine
QFrame_VLine                 = QFrame.Shape.VLine
QFrame_StyledPanel           = QFrame.Shape.StyledPanel
QFrame_Panel                 = QFrame.Shape.Panel
QFrame_Box                   = QFrame.Shape.Box

# QLineEdit echo modes
QLineEdit_Normal             = QLineEdit.EchoMode.Normal
QLineEdit_Password           = QLineEdit.EchoMode.Password
QLineEdit_NoEcho             = QLineEdit.EchoMode.NoEcho
QLineEdit_PasswordEchoOnEdit = QLineEdit.EchoMode.PasswordEchoOnEdit

# QPainter render hints
QPainter_Antialiasing        = QPainter.RenderHint.Antialiasing
QPainter_TextAntialiasing    = QPainter.RenderHint.TextAntialiasing
QPainter_SmoothPixmapTransform = QPainter.RenderHint.SmoothPixmapTransform

# QMessageBox
QMessageBox_Information      = QMessageBox.Icon.Information
QMessageBox_Question         = QMessageBox.Icon.Question
QMessageBox_Warning          = QMessageBox.Icon.Warning
QMessageBox_Critical         = QMessageBox.Icon.Critical
QMessageBox_Yes              = QMessageBox.StandardButton.Yes
QMessageBox_No               = QMessageBox.StandardButton.No
QMessageBox_Ok               = QMessageBox.StandardButton.Ok
QMessageBox_Cancel           = QMessageBox.StandardButton.Cancel

# QDialog
QDialog_Accepted             = QDialog.DialogCode.Accepted
QDialog_Rejected             = QDialog.DialogCode.Rejected

# Window types / widget attributes
Qt_Tool                      = Qt.WindowType.Tool
Qt_FramelessWindowHint       = Qt.WindowType.FramelessWindowHint
Qt_WA_TranslucentBackground  = Qt.WidgetAttribute.WA_TranslucentBackground
# Without this, a stylesheet `background` on a plain QWidget SUBCLASS is
# silently ignored — Qt only honours it for QFrame-derived widgets or when the
# attribute is set explicitly. See widgets.paint_background.
Qt_WA_StyledBackground       = Qt.WidgetAttribute.WA_StyledBackground

# Pen styles
Qt_SolidLine                 = Qt.PenStyle.SolidLine

# Keyboard modifiers — the composer distinguishes Enter, Shift+Enter and
# Ctrl+Enter, which decide between sending and inserting a newline.
Qt_ShiftModifier             = Qt.KeyboardModifier.ShiftModifier
Qt_ControlModifier           = Qt.KeyboardModifier.ControlModifier
Qt_AltModifier               = Qt.KeyboardModifier.AltModifier
Qt_NoModifier                = Qt.KeyboardModifier.NoModifier

# Keyboard keys
Qt_Key_Escape                = Qt.Key.Key_Escape
Qt_Key_Backspace             = Qt.Key.Key_Backspace
Qt_Key_Delete                = Qt.Key.Key_Delete
Qt_Key_Return                = Qt.Key.Key_Return
Qt_Key_Enter                 = Qt.Key.Key_Enter

# QSizePolicy
QSizePolicy_Expanding        = QSizePolicy.Policy.Expanding
QSizePolicy_Preferred        = QSizePolicy.Policy.Preferred
QSizePolicy_Fixed            = QSizePolicy.Policy.Fixed
QSizePolicy_Minimum          = QSizePolicy.Policy.Minimum
QSizePolicy_Maximum          = QSizePolicy.Policy.Maximum
# Ignored lets a label shrink below its own text width, which is what allows
# an elided title to narrow with the dock instead of pinning it open.
QSizePolicy_Ignored          = QSizePolicy.Policy.Ignored
QSizePolicy_MinimumExpanding = QSizePolicy.Policy.MinimumExpanding

# Pen caps / joins — the icon set strokes every path with round ends, which is
# what gives it its family resemblance to the SaaS's lucide icons.
Qt_RoundCap                  = Qt.PenCapStyle.RoundCap
Qt_FlatCap                   = Qt.PenCapStyle.FlatCap
Qt_RoundJoin                 = Qt.PenJoinStyle.RoundJoin
Qt_NoPen                     = Qt.PenStyle.NoPen
Qt_NoBrush                   = Qt.BrushStyle.NoBrush
Qt_SolidPattern              = Qt.BrushStyle.SolidPattern

# QIcon modes/states — used to register the disabled and active tints of a
# painted icon so a button dims and highlights without the caller swapping it.
QIcon_Normal                 = QIcon.Mode.Normal
QIcon_Disabled               = QIcon.Mode.Disabled
QIcon_Active                 = QIcon.Mode.Active
QIcon_Selected               = QIcon.Mode.Selected
QIcon_Off                    = QIcon.State.Off
QIcon_On                     = QIcon.State.On

# Easing — the toggle switch's knob travel.
QEasingCurve_OutCubic        = QEasingCurve.Type.OutCubic

# Dock widget areas and features. The GoPilot panel docks right by default and
# must stay dockable/floatable/closable, which is the default set minus the
# vertical-title option.
Qt_RightDockWidgetArea       = Qt.DockWidgetArea.RightDockWidgetArea
Qt_LeftDockWidgetArea        = Qt.DockWidgetArea.LeftDockWidgetArea
Qt_BottomDockWidgetArea      = Qt.DockWidgetArea.BottomDockWidgetArea
Qt_AllDockWidgetAreas        = Qt.DockWidgetArea.AllDockWidgetAreas
QDockWidget_Movable          = QDockWidget.DockWidgetFeature.DockWidgetMovable
QDockWidget_Floatable        = QDockWidget.DockWidgetFeature.DockWidgetFloatable
QDockWidget_Closable         = QDockWidget.DockWidgetFeature.DockWidgetClosable

# Text elide. Right for a chat title; Middle for a filesystem path, where the
# drive and the leaf folder are the informative ends and the middle is not.
Qt_ElideRight                = Qt.TextElideMode.ElideRight
Qt_ElideMiddle               = Qt.TextElideMode.ElideMiddle
Qt_ElideLeft                 = Qt.TextElideMode.ElideLeft

# Misc
Qt_KeepAspectRatio           = Qt.AspectRatioMode.KeepAspectRatio
# "Cover" rather than "contain": scale until the box is filled on BOTH axes and
# let the overflow be cropped. See image_loader.load_image.
Qt_KeepAspectRatioByExpanding = Qt.AspectRatioMode.KeepAspectRatioByExpanding
Qt_IgnoreAspectRatio         = Qt.AspectRatioMode.IgnoreAspectRatio
Qt_SmoothTransformation      = Qt.TransformationMode.SmoothTransformation
Qt_ScrollBarAlwaysOff        = Qt.ScrollBarPolicy.ScrollBarAlwaysOff
Qt_ScrollBarAlwaysOn         = Qt.ScrollBarPolicy.ScrollBarAlwaysOn
Qt_ScrollBarAsNeeded         = Qt.ScrollBarPolicy.ScrollBarAsNeeded
Qt_WaitCursor                = Qt.CursorShape.WaitCursor
Qt_ArrowCursor               = Qt.CursorShape.ArrowCursor
Qt_PointingHandCursor        = Qt.CursorShape.PointingHandCursor
Qt_Checked                   = Qt.CheckState.Checked
Qt_Unchecked                 = Qt.CheckState.Unchecked
Qt_RichText                  = Qt.TextFormat.RichText
Qt_PlainText                 = Qt.TextFormat.PlainText
Qt_TextBrowserInteraction    = Qt.TextInteractionFlag.TextBrowserInteraction
Qt_transparent               = Qt.GlobalColor.transparent
Qt_SizeAllCursor             = Qt.CursorShape.SizeAllCursor
# Edge and corner cursors for the frameless panel's own resize zones.
Qt_SizeHorCursor             = Qt.CursorShape.SizeHorCursor
Qt_SizeVerCursor             = Qt.CursorShape.SizeVerCursor
Qt_SizeBDiagCursor           = Qt.CursorShape.SizeBDiagCursor
Qt_SizeFDiagCursor           = Qt.CursorShape.SizeFDiagCursor

# Event types, for the drag filter in window_drag.py
QEvent_MouseButtonPress      = QEvent.Type.MouseButtonPress
QEvent_MouseButtonRelease    = QEvent.Type.MouseButtonRelease
QEvent_MouseButtonDblClick   = QEvent.Type.MouseButtonDblClick
QEvent_MouseMove             = QEvent.Type.MouseMove
QEvent_Enter                 = QEvent.Type.Enter
QEvent_Leave                 = QEvent.Type.Leave

# Keeps the OS resize border while dropping the caption; see
# gopilot_dock._apply_floating_chrome for the measurements behind that.
Qt_CustomizeWindowHint       = Qt.WindowType.CustomizeWindowHint


def mouse_global_pos(event):
    """The screen position of a mouse event, as a ``QPoint``.

    Another renamed method rather than a moved enum, so it needs a branch like
    ``exec_dialog``: Qt5's ``globalPos()`` was replaced in Qt6 by
    ``globalPosition()``, which returns a ``QPointF``. Qt6 keeps ``globalPos()``
    only as a deprecated alias on some event classes, so prefer the new name
    and fall back.

    Used by the frameless floating panel to drag its own window — with no OS
    caption there is nothing for Windows to drag by, so the move arithmetic is
    ours and it has to be done in screen coordinates.
    """
    getter = getattr(event, "globalPosition", None)
    if getter is not None:
        return getter().toPoint()
    return event.globalPos()
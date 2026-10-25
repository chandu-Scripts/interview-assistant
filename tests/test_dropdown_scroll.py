"""Wheel navigation must not silently change form values or model selection."""

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea, QVBoxLayout, QWidget

from interview_assistant.ui.controls import ChoiceComboBox
from interview_assistant.ui.provider_picker import ModelSelector


def wheel(widget, *, angle=-120, pixels=0):
    position = widget.rect().center()
    event = QWheelEvent(
        QPointF(position),
        QPointF(widget.mapToGlobal(position)),
        QPoint(0, pixels),
        QPoint(0, angle),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(widget, event)


@pytest.mark.parametrize("focused", [False, True])
@pytest.mark.parametrize("editable", [False, True])
@pytest.mark.parametrize("angle,pixels", [(-120, 0), (120, 0), (0, -30), (0, 30)])
def test_closed_dropdown_wheel_preserves_choice(qtbot, focused, editable, angle, pixels):
    page = QWidget()
    qtbot.addWidget(page)
    layout = QVBoxLayout(page)
    combo = ChoiceComboBox()
    combo.setEditable(editable)
    combo.addItems(["First", "Chosen", "Last"])
    combo.setCurrentIndex(1)
    other = QPushButton("Focus elsewhere")
    layout.addWidget(combo)
    layout.addWidget(other)
    page.show()
    (combo if focused else other).setFocus()
    changes = []
    combo.currentIndexChanged.connect(changes.append)
    wheel(combo.lineEdit() if editable else combo, angle=angle, pixels=pixels)
    assert combo.currentText() == "Chosen" and not changes


@pytest.mark.parametrize("editable", [False, True])
def test_wheel_over_focused_dropdown_scrolls_parent_page(qtbot, editable):
    scroll = QScrollArea()
    qtbot.addWidget(scroll)
    scroll.setWidgetResizable(True)
    content = QWidget()
    layout = QVBoxLayout(content)
    combo = ChoiceComboBox()
    combo.setEditable(editable)
    combo.addItems(["First", "Chosen", "Last"])
    combo.setCurrentIndex(1)
    layout.addWidget(combo)
    spacer = QWidget()
    spacer.setMinimumHeight(1500)
    layout.addWidget(spacer)
    scroll.setWidget(content)
    scroll.resize(400, 250)
    scroll.show()
    combo.setFocus()
    QApplication.processEvents()
    assert scroll.verticalScrollBar().maximum() > 0
    wheel(combo.lineEdit() if editable else combo)
    assert scroll.verticalScrollBar().value() > 0
    assert combo.currentIndex() == 1
    wheel(combo.lineEdit() if editable else combo, angle=120)
    assert scroll.verticalScrollBar().value() == 0
    assert combo.currentIndex() == 1


def test_open_dropdown_scrolls_list_then_click_commits(qtbot, qapp):
    combo = ChoiceComboBox()
    qtbot.addWidget(combo)
    combo.setStyle(qapp.style())
    combo.addItems([f"Option {i}" for i in range(100)])
    combo.setMaxVisibleItems(6)
    combo.resize(240, 36)
    combo.show()
    qtbot.mouseClick(combo, Qt.MouseButton.LeftButton, pos=QPoint(combo.width() - 12, 18))
    view = combo.view()
    qtbot.waitUntil(view.isVisible)
    before = view.verticalScrollBar().value()
    wheel(view.viewport(), angle=-360)
    assert view.verticalScrollBar().value() > before
    assert combo.currentIndex() == 0
    index = view.indexAt(view.viewport().rect().center())
    assert index.isValid() and index.row() != 0
    # Qt briefly suppresses popup mouse release after opening to avoid accidental picks.
    qtbot.wait(qapp.doubleClickInterval() + 50)
    qtbot.mouseMove(view.viewport(), pos=view.visualRect(index).center())
    qtbot.mouseClick(
        view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(index).center()
    )
    assert combo.currentIndex() == index.row()
    assert not view.isVisible()
    wheel(combo)
    assert combo.currentIndex() == index.row()


def test_preferred_model_selector_does_not_change_on_wheel(qtbot):
    combo = ModelSelector(["openai:gpt-5.4-mini", "openai:gpt-5.4"])
    qtbot.addWidget(combo)
    combo.setCurrentIndex(1)
    combo.show()
    combo.setFocus()
    wheel(combo)
    assert combo.currentData() == "openai:gpt-5.4-mini"


def test_explicit_keyboard_navigation_still_works(qtbot):
    combo = ChoiceComboBox()
    qtbot.addWidget(combo)
    combo.addItems(["First", "Second"])
    combo.show()
    combo.setFocus()
    qtbot.keyClick(combo, Qt.Key.Key_Down)
    assert combo.currentIndex() == 1

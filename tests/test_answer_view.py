from PySide6.QtWidgets import QApplication

from interview_assistant.ui.answer_view import AnswerView, split_blocks


def test_paragraph_copy_is_separate_from_code_copy(qtbot):
    view = AnswerView()
    qtbot.addWidget(view)
    view.resize(600, 350)
    view.show()
    view.setMarkdown(
        "```text\nFirst paragraph.\n\nSecond paragraph.\n```\n\n```python\nx = 1\n\nprint(x)\n```"
    )
    assert len(view.cards) == 3
    for card, expected in zip(
        view.cards, ["First paragraph.", "Second paragraph.", "x = 1\n\nprint(x)"], strict=True
    ):
        card.copy_btn.click()
        assert QApplication.clipboard().text() == expected
    assert "⧉" not in view.toPlainText() and "✓" not in view.toPlainText()


def test_streaming_updates_cards_and_clears_old_content(qtbot):
    view = AnswerView()
    qtbot.addWidget(view)
    view.setMarkdown("```text\nFirst")
    first = view.cards[0]
    view.setMarkdown("```text\nFirst answer.\n```\n\n```text\nSecond")
    assert len(view.cards) == 2 and view.cards[0] is first
    view.cards[1].copy_btn.click()
    assert QApplication.clipboard().text() == "Second"
    view.setPlainText("Saved <b>literal</b>\n\nAnother paragraph")
    assert view.cards[0].label.text() == "Saved <b>literal</b>"
    view.clear()
    assert not view.cards and view.toPlainText() == ""


def test_unlabelled_code_keeps_blank_lines_and_no_fences_in_copy(qapp):
    blocks = split_blocks("```\ndef f():\n    x = 1\n\n    return x\n```", True)
    assert len(blocks) == 1 and blocks[0].code
    assert blocks[0].text == "def f():\n    x = 1\n\n    return x"

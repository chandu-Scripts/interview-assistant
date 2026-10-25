"""Render single-window navigation with fake providers and isolated, fictional data."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtTest import (
    QSignalSpy,  # noqa: E402
    QTest,  # noqa: E402
)
from PySide6.QtWidgets import QApplication  # noqa: E402

from interview_assistant.app import composition  # noqa: E402
from interview_assistant.config.keys import MemoryKeyStore  # noqa: E402
from interview_assistant.config.relay import MemoryRelayTokenStore  # noqa: E402
from interview_assistant.config.settings import Settings  # noqa: E402
from interview_assistant.config.user_settings import UserSettingsStore  # noqa: E402
from interview_assistant.ui.settings_dialog import SettingsDialog  # noqa: E402
from interview_assistant.ui.theme import apply_theme  # noqa: E402


class FakeSTT:
    name = "preview"
    metered = False

    def warmup(self):
        pass

    def transcribe(self, pcm):
        return ""


def main() -> None:
    root = Path.cwd()
    out = root / "tmp" / "workspace-preview"
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    apply_theme(app)
    for name in ("segoeui.ttf", "segoeuib.ttf"):
        QFontDatabase.addApplicationFont(str(Path("C:/Windows/Fonts") / name))
    with TemporaryDirectory(dir=out) as temp:
        os.environ["DATA_DIR"] = temp
        os.chdir(temp)
        settings = Settings(
            _env_file=None,
            openai_api_key=None,
            anthropic_api_key=None,
            gemini_api_key=None,
            relay_token=None,
            relay_url=None,
            usage_log=False,
        )
        composition.build_provider = lambda _: FakeSTT()
        composition.SettingsDialog = lambda store, keys: SettingsDialog(
            store, keys, raw_settings=lambda: settings, relay_store=MemoryRelayTokenStore()
        )
        parts = composition.build_app(
            app,
            settings,
            MemoryKeyStore(),
            QSettings(str(Path(temp) / "app.ini"), QSettings.Format.IniFormat),
            lambda _: False,
            lambda _: False,
            UserSettingsStore(Path(temp) / "settings.json"),
        )
        screen = parts.start_screen
        try:
            screen.resume_page.library.add(
                "Data Engineer.pdf", "Fictional data engineering resume."
            )
            screen.resume_page.library.add("Platform Engineer.docx", "Fictional platform resume.")
            parts.history.create_interview_session(company="Northwind", role_title="Data Engineer")
            screen.resize(960, 680)
            practice = screen._workspace_widgets["practice"]
            practice._examples()
            practice.entries.setCurrentRow(0)
            screen.show()
            for name, action in (
                ("sessions", screen._show_list),
                ("resumes", screen._show_resumes),
                ("practice", screen.extra_nav["practice"].click),
                ("practice-answer", practice._select),
                ("practice-edit", practice._edit),
                ("practice-back", practice._cancel),
                ("practice-json", practice._open_import),
                ("settings", parts.window.show_settings),
                ("privacy", parts.window.show_privacy),
                ("history", parts.window.show_history),
                ("audio", parts.window.show_controls),
            ):
                action()
                app.processEvents()
                QTest.qWait(180)
                assert screen.size().width() == 960 and screen.size().height() == 680
                visible = [w for w in app.topLevelWidgets() if w.isVisible()]
                if name == "practice":
                    assert not practice.detail_answer.isVisible()
                    assert not practice.answer.isVisible()
                if name == "practice-edit":
                    assert practice.save_btn.visibleRegion().boundingRect().height() > 0
                if name == "practice-json":
                    assert practice.json_view.import_btn.visibleRegion().boundingRect().height() > 0
                assert visible == [screen], [w.windowTitle() for w in visible]
                screen.grab().save(str(out / f"{name}.png"))
                print(f"{name}: one visible window, 960x680")
            screen.hide()
            parts.overlay.show()
            question = "How many catalogs did you use?"
            finished = QSignalSpy(parts.controller.answer_finished)
            parts.overlay.input.setText(question)
            parts.overlay.ask_btn.click()
            for _ in range(500):
                QTest.qWait(10)
                if finished.count() and parts.overlay.origin_label.text() == "Saved fictional Q&A":
                    break
            assert finished.count() == 1
            assert parts.overlay.origin_label.text() == "Saved fictional Q&A", (
                parts.overlay.origin_label.text(),
                parts.overlay.chip.text(),
                finished.at(0),
                parts.overlay.answer.toPlainText(),
            )
            expected = next(e.answer for e in practice.library.entries() if e.question == question)
            assert parts.overlay.answer.toPlainText() == expected
            parts.overlay.grab().save(str(out / "saved-answer.png"))
            print("saved answer: verbatim text, fictional source label, no configured AI providers")
            parts.controller.ask("How many ADF pipelines did you build?")
            for _ in range(500):
                QTest.qWait(10)
                if (
                    finished.count() == 2
                    and "maintained about 20" in parts.overlay.answer.toPlainText()
                ):
                    break
            assert finished.count() == 2
            assert "maintained about 20" in parts.overlay.answer.toPlainText()
            parts.overlay.grab().save(str(out / "saved-paraphrase.png"))
            print("Paraphrased question: saved answer displayed without any selection")
            parts.overlay.input.setText("What was your catalog count?")
            parts.overlay.ask_btn.click()
            for _ in range(500):
                QTest.qWait(10)
                if finished.count() == 3:
                    break
            assert finished.count() == 3
            assert "three catalogs" in parts.overlay.answer.toPlainText()
            assert parts.overlay.origin_label.text() == "Saved fictional Q&A"
            parts.overlay.grab().save(str(out / "saved-semantic-match.png"))
            print("Semantic paraphrase: saved catalog answer, no configured AI providers")
            parts.controller.stop()
            parts.overlay.on_question(10000, "Format preview", "typed")
            parts.overlay.on_delta(10000, "Direct answer first.\n\nBrief supporting detail.")
            parts.overlay.on_finished(10000, "preview:test-provider", 0.01, 0.02)
            parts.overlay.input.setText("fix format")
            parts.overlay.ask_btn.click()
            app.processEvents()
            assert parts.overlay._text.startswith("```text")
            assert parts.overlay._text.endswith("```")
            assert len(parts.overlay.answer.cards) == 2
            parts.overlay.answer.cards[1].copy_btn.click()
            assert app.clipboard().text() == "Brief supporting detail."
            QTest.qWait(50)
            parts.overlay.grab().save(str(out / "answer-block-format.png"))
            print("format repair: closed paragraph blocks, no provider request")
        finally:
            parts.window.close()
            screen.close()
            parts.history.close()
            os.chdir(root)


if __name__ == "__main__":
    main()

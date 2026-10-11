"""Local resume management. File extraction runs off the UI thread."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.app.attachments import ATTACHMENT_FILTER, AttachmentError, read_attachment
from interview_assistant.app.resume_library import ResumeLibrary


class ResumeImport(QThread):
    loaded = Signal(str, str)
    failed = Signal(str)

    def __init__(self, path: Path, parent: QWidget) -> None:
        super().__init__(parent)
        self.path = path

    def run(self) -> None:
        try:
            text = read_attachment(self.path)
        except AttachmentError as e:
            self.failed.emit(str(e))
        else:
            self.loaded.emit(self.path.name, text)


class ResumePage(QWidget):
    changed = Signal()

    def __init__(
        self, library: ResumeLibrary, confirm_delete: Callable[[str], bool] | None = None
    ) -> None:
        super().__init__()
        self.library = library
        self._confirm_delete = confirm_delete or self._confirm
        self._worker: ResumeImport | None = None
        self._texts: dict[str, str] = {}
        self.title = QLabel("Resumes")
        self.title.setObjectName("title")
        hint = QLabel("Manage the resume text used to personalize your interview answers.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self.import_btn = QPushButton("Import Resume")
        self.import_btn.setObjectName("create")
        self.rename_btn = QPushButton("Rename")
        self.delete_btn = QPushButton("Delete")
        top = QHBoxLayout()
        top.addWidget(self.title, 1)
        top.addWidget(self.import_btn)
        self.resumes = QListWidget()
        self.resumes.setMinimumWidth(200)
        self.resumes.setAccessibleName("Saved resumes")
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("Extracted resume text")
        self.preview.setPlaceholderText("Import a resume to preview the text used for answers.")
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.resumes)
        split.addWidget(self.preview)
        split.setStretchFactor(1, 1)
        split.setSizes([250, 650])
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        note = QLabel(
            "Saved on this device. Only extracted text is kept (up to 12,000 characters). "
            "Deleting here leaves your original file and copies in existing sessions unchanged."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        actions = QHBoxLayout()
        actions.addWidget(self.message, 1)
        actions.addWidget(self.rename_btn)
        actions.addWidget(self.delete_btn)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(16)
        layout.addLayout(top)
        layout.addWidget(hint)
        layout.addWidget(split, 1)
        layout.addLayout(actions)
        layout.addWidget(note)
        self.resumes.currentItemChanged.connect(self._selection_changed)
        self.import_btn.clicked.connect(self._pick_import)
        self.rename_btn.clicked.connect(self._rename)
        self.delete_btn.clicked.connect(self._delete)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.wait_for_import)
        self.refresh()

    def selected_name(self) -> str:
        item = self.resumes.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else ""

    def refresh(self, select: str | None = None) -> None:
        keep = self.selected_name() if select is None else select
        resumes = self.library.list()
        self._texts = {r.name: r.text for r in resumes}
        self.resumes.clear()
        for r in resumes:
            item = QListWidgetItem(r.name)
            item.setData(Qt.ItemDataRole.UserRole, r.name)
            self.resumes.addItem(item)
            if r.name == keep:
                self.resumes.setCurrentItem(item)
        if self.resumes.currentRow() < 0 and resumes:
            self.resumes.setCurrentRow(0)
        self.message.setText(self.library.error or f"{len(resumes)} saved resume(s)")
        self._selection_changed()

    def _selection_changed(self) -> None:
        name = self.selected_name()
        self.preview.setPlainText(self._texts.get(name, ""))
        for button in (self.rename_btn, self.delete_btn):
            button.setEnabled(bool(name) and self._worker is None)

    def _pick_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import resume", "", ATTACHMENT_FILTER)
        if path:
            self.import_file(Path(path))

    def import_file(self, path: Path) -> None:
        if self._worker is not None:
            return
        self.message.setText("Reading resume…")
        self.import_btn.setEnabled(False)
        self._worker = ResumeImport(path, self)
        self._worker.loaded.connect(self._save_import)
        self._worker.failed.connect(self.message.setText)
        self._worker.finished.connect(self._import_finished)
        self._selection_changed()
        self._worker.start()

    def _save_import(self, name: str, text: str) -> None:
        try:
            if any(r.name.casefold() == name.casefold() for r in self.library.list()):
                raise ValueError(
                    "A resume with that name already exists. "
                    "Rename it before importing another copy."
                )
            self.library.add(name, text)
        except (OSError, ValueError) as e:
            self.message.setText(str(e))
            return
        self.refresh(name)
        self.changed.emit()

    def _import_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        self.import_btn.setEnabled(True)
        self._selection_changed()

    def wait_for_import(self) -> None:
        if self._worker is not None:
            self._worker.wait()

    def _rename(self) -> None:
        name = self.selected_name()
        if not name:
            return
        new_name, accepted = QInputDialog.getText(self, "Rename resume", "Resume name", text=name)
        if not accepted:
            return
        try:
            self.library.rename(name, new_name)
        except (OSError, ValueError) as e:
            self.message.setText(str(e))
            return
        self.refresh(new_name.strip())
        self.changed.emit()

    def _confirm(self, name: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("Delete saved resume")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(
            f'Delete "{name}" from the resume library? '
            "Your original file and existing session copies will remain."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _delete(self) -> None:
        name = self.selected_name()
        if not name or not self._confirm_delete(name):
            return
        try:
            self.library.delete(name)
        except (OSError, ValueError) as e:
            self.message.setText(str(e))
            return
        self.refresh()
        self.changed.emit()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.wait_for_import()
        super().closeEvent(event)

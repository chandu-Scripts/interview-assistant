# Explicit screen-share privacy

## Decision

The user explicitly requested a separate screen-share privacy feature on 7 November 2026,
overriding the earlier project-wide ban on display affinity for this feature only.
Presentation mode remains a separate local hide-and-pause action.

Codex and Claude independently reviewed the approach before implementation. Use the supported
Windows display-affinity API behind one native adapter, with a session-only toggle in Privacy.
The app remains visible locally, and audio capture and answers are not paused by this toggle.
It starts Off on every launch and is never persisted or automatically enabled.

## Boundaries and failure handling

The adapter requires Windows 10 version 2004 (build 19041) or later and DWM composition.
Earlier Windows treats the exclude value as monitor-only and can produce black rectangles,
so it is rejected rather than treated as supported. The adapter touches only current-process
root windows and checks ownership before every operation.

On enable, set `WDA_EXCLUDEFROMCAPTURE` and read it back for visible application windows.
Qt top-level Show and handle-change events apply the setting synchronously. A 500 ms audit,
running only while enabled, covers native dialogs and menus not represented by Qt widgets and
rechecks settings. Native popups may expose an initial frame before that audit. This approach
preserves native file-dialog behavior instead of replacing every dialog in the product.

If any application window fails application/readback, stop claiming On, attempt to reset every
touched window, and show Unavailable. Failed resets remain Unavailable with an explicit retry
button; never display Off until reset is verified or the original window no longer exists.
Revalidate process, thread and class identity before resetting previously tracked handles.
Disable, shutdown and account logout remove settings and release the event filter/timer.

The Privacy page, workspace notice and compact overlay label show the state locally.
These labels describe the OS setting, not proof that a sharing tool respects it. No process
disguising, monitoring bypass, global screenshot blocking or other-process changes are added.

## Verification and limits

Synthetic backend tests exercise Off startup, support failure, partial enable, readback
mismatch, rollback failure/retry, new windows, handle reuse, shutdown and control wiring.
The explicit native test uses synthetic windows/menu content and verifies API readback on
enable, disable and release. It does not start audio, read private data or use credentials.

Run native API smoke with `QT_QPA_PLATFORM=windows` and `CHANDOO_NATIVE_AFFINITY_TEST=1`:

```powershell
python -m pytest tests/test_capture_visibility.py -k native_windows -q
```

Receiver-side Teams/Zoom/Meet sharing, specific capture backends, remote desktop and screen
recorders remain separate manual acceptance checks. A successful API readback is not a
universal capture exclusion guarantee, nor a security boundary. Native popup first-frame
coverage is explicitly not guaranteed. User must verify the receiver view before relying on it.

Rollback: disable the feature and verify reset, then revert this isolated branch. Existing
Presentation and privacy storage settings are unchanged.

## Primary sources

- [Microsoft SetWindowDisplayAffinity](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity)
- [Microsoft GetWindowDisplayAffinity](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowdisplayaffinity)

# Screen-share privacy validation

Validated on Windows, 7 November 2026, feature branch `feature/screen-share-privacy`.
This report records API state and synthetic behavior, not receiver-side capture results.

## Results

- Final isolated privacy, Presentation and security suite: **78 passed, 2 skipped** in 8.22 s.
  The two skipped tests are the explicitly opt-in native smoke cases.
- Explicit native smoke: **2 passed** in 10.67 s. Both plain and translucent QWidget windows
  stayed locally visible. The test verified exclude affinity after enabling, after changing
  window flags/handle recreation, and on a newly opened QMenu. Disabling and release read back
  unrestricted affinity on the test window and menu. No screen image was captured.
- Broader composition run: 101 passed, 4 failed, 1 skipped. Three failures were missing local
  semantic-model assets in the new worktree. After copying the already-installed model and
  tokenizer, all three passed on targeted rerun (18 passed including privacy tests, 2 skips).
- The remaining composition failure is pre-existing: `test_composition_shares_verified_catalog_with_wizard`
  expects `gpt-5.4-mini` at tests/test_composition.py:218, but main now restricts the picker to
  four newer user-selected IDs. Reproduced independently on unchanged root main: 1 failed in
  9.99 s. This feature does not change that unrelated fixture or model-selection behavior.
- Ruff passed for every changed Python file. Mypy passed for the new native manager and
  modified privacy UI. `git diff --check` passed.

The synthetic composition test uses FakeSource audio, not a microphone. It verifies recording
and answer-controller pause state do not change when screen-share privacy changes, that the
overlay still fits 640 logical pixels, and that bubble mode remains 48 pixels and restores its
status correctly. Existing Presentation still pauses capture/answers. Shutdown resets privacy
and prevents subsequent enabling.

Failure coverage includes unsupported OS/backend, pre-19041 rejection before loading native
APIs, discovery exceptions, no visible windows, partial enable, readback mismatch, reset failure
and explicit retry, foreign/reused handles, new native windows, Qt Show events, stale-handle
pruning, and release. Off startup performs no native operations or polling.

## Reproduce

From this worktree, with the root virtual environment available:

```powershell
$env:PYTHONPATH = "$PWD/src"
$env:QT_QPA_PLATFORM = 'offscreen'
C:/Users/tcs91/Desktop/KCPD/.venv/Scripts/python.exe -m pytest tests/test_capture_visibility.py tests/test_privacy.py tests/test_security_hygiene.py -q
```

Native API/readback smoke (opens only synthetic test windows):

```powershell
$env:PYTHONPATH = "$PWD/src"
$env:QT_QPA_PLATFORM = 'windows'
$env:CHANDOO_NATIVE_AFFINITY_TEST = '1'
C:/Users/tcs91/Desktop/KCPD/.venv/Scripts/python.exe -m pytest tests/test_capture_visibility.py -k native_windows -q
```

## Unverified and limits

Receiver-side Teams, Zoom, Meet and other capture tools remain unverified. Windows API success
does not prove that every sharing/capture path respects the setting. Native dialogs outside
Qt's synchronous Show path can expose initial frames before the 500 ms enabled-only audit.
No zero-frame-leak guarantee is made. Real sharing recipients must verify the result.
Independent integration review applied this branch's patch to a detached liquid-glass
checkout without merging either branch: **94 passed, 2 skipped**, followed by **2 native
passes**. The patch applied without conflicts. Root also independently repeated the isolated
privacy suite (**78 passed, 2 skipped**) and native suite (**2 passed**).
The matching branch has not been included in that integration check. None of these branches
has been merged into main or pushed.

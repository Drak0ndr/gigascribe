"""Regression: a native Window on public js_api attributes freezes WebView2.

See pywebview issue #1815 / #1528. The bridge must keep only callable,
small public members; window/native references must stay private.
"""
from __future__ import annotations

import desktop


def test_native_window_is_private_to_js_api():
    bridge = desktop.WindowAPI()
    native = object()
    bridge._window = native
    assert bridge._window is native

    # This is how pywebview enumerates the js_api object: public attributes
    # are inspected recursively; exposing a Window reaches native WinForms.
    public = {name: getattr(bridge, name) for name in dir(bridge) if not name.startswith('_')}
    assert set(public) == {'minimize', 'toggle_maximize', 'close', 'save_text'}
    assert all(callable(value) for value in public.values())
    assert not hasattr(bridge, 'window')
    desktop.verify_js_api_surface()  # also runs in packaged EXE --self-check


def test_js_bridge_window_actions_still_work():
    events: list[str] = []

    class FakeWindow:
        def minimize(self): events.append('minimize')
        def maximize(self): events.append('maximize')
        def restore(self): events.append('restore')
        def destroy(self): events.append('destroy')

    bridge = desktop.WindowAPI(native_frame=True)
    bridge._window = FakeWindow()
    bridge.minimize()
    bridge.toggle_maximize()
    bridge.toggle_maximize()
    bridge.close()
    assert events == ['minimize', 'maximize', 'restore', 'destroy']


def test_launcher_passes_private_js_bridge_to_webview():
    """Prevent regressions in main(): no bridge.window=... assignment."""
    from pathlib import Path
    source_text = (Path(desktop.__file__)).read_text(encoding='utf-8')
    assert 'bridge._window = webview.create_window(' in source_text
    assert 'js_api=bridge' in source_text
    assert 'bridge.window' not in source_text
    assert 'self.window' not in source_text

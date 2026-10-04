"""Native GTK WebKit application window host for Linux desktop environments."""

from __future__ import annotations

import os
import sys
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Optional


def _is_local_url(url_str: str) -> bool:
    """Check if the target URL is on local loopback."""
    try:
        parsed = urllib.parse.urlparse(url_str)
        host = (parsed.hostname or "").lower()
        return host in ("127.0.0.1", "localhost", "::1")
    except Exception:
        return False


def _get_icon_path() -> Optional[Path]:
    """Find native app icon for window decoration."""
    desktop_dir = Path(__file__).resolve().parent
    png_path = desktop_dir / "antiagent.png"
    if png_path.is_file():
        return png_path
    svg_path = desktop_dir / "antiagent.svg"
    if svg_path.is_file():
        return svg_path
    return None


def run_gtk4_window(url: str = "http://127.0.0.1:4242") -> int:
    """Launch native window using GTK 4 and WebKit 6."""
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("WebKit", "6.0")
    from gi.repository import Gtk, WebKit, Gdk

    # Apply dark background styling
    css = b"window { background-color: #0d1117; }"
    provider = Gtk.CssProvider()
    provider.load_from_data(css)
    display = Gdk.Display.get_default()
    if display:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    app = Gtk.Application(application_id="com.antiagent.desktop")

    def on_activate(application):
        win = Gtk.ApplicationWindow(application=application, title="AntiAgent Guard")
        win.set_default_size(1160, 800)
        win.set_size_request(800, 600)

        icon_path = _get_icon_path()
        if icon_path and hasattr(win, "set_icon_name"):
            win.set_icon_name("antiagent")

        webview = WebKit.WebView()
        win.set_child(webview)

        def on_decide_policy(view, decision, decision_type):
            if decision_type == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
                nav_action = decision.get_navigation_action()
                req = nav_action.get_request()
                uri = req.get_uri() if req else ""
                if uri and not _is_local_url(uri):
                    decision.ignore()
                    webbrowser.open(uri)
                    return True
            decision.use()
            return True

        webview.connect("decide-policy", on_decide_policy)
        webview.load_uri(url)
        win.present()

    app.connect("activate", on_activate)
    return app.run(None)


def run_gtk3_window(url: str = "http://127.0.0.1:4242") -> int:
    """Launch native window using GTK 3 and WebKit2."""
    import gi
    gi.require_version("Gtk", "3.0")
    try:
        gi.require_version("WebKit2", "4.1")
    except (ValueError, AttributeError):
        gi.require_version("WebKit2", "4.0")
    from gi.repository import Gtk, WebKit2, Gdk

    # Apply dark background styling
    css = b"window { background-color: #0d1117; }"
    provider = Gtk.CssProvider()
    provider.load_from_data(css)
    screen = Gdk.Screen.get_default()
    if screen:
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    win = Gtk.Window(title="AntiAgent Guard")
    win.set_default_size(1160, 800)
    win.set_size_request(800, 600)
    win.set_position(Gtk.WindowPosition.CENTER)

    icon_path = _get_icon_path()
    if icon_path and icon_path.is_file():
        try:
            win.set_icon_from_file(str(icon_path))
        except Exception:
            pass

    webview = WebKit2.WebView()
    win.add(webview)

    def on_decide_policy(view, decision, decision_type):
        if decision_type == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            nav_action = decision.get_navigation_action()
            req = nav_action.get_request()
            uri = req.get_uri() if req else ""
            if uri and not _is_local_url(uri):
                decision.ignore()
                webbrowser.open(uri)
                return True
        decision.use()
        return True

    webview.connect("decide-policy", on_decide_policy)
    webview.load_uri(url)

    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
    return 0


def run_native_window(url: str = "http://127.0.0.1:4242") -> int:
    """Attempt GTK4/WebKit6 first, then fallback to GTK3/WebKit2."""
    errors = []
    try:
        return run_gtk4_window(url)
    except Exception as e:
        errors.append(f"GTK4/WebKit6 failed: {e}")

    try:
        return run_gtk3_window(url)
    except Exception as e:
        errors.append(f"GTK3/WebKit2 failed: {e}")

    raise RuntimeError("No supported GTK WebKit runtime found on this Linux system.\n" + "\n".join(errors))


if __name__ == "__main__":
    target_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:4242"
    try:
        code = run_native_window(target_url)
        sys.exit(code)
    except Exception as err:
        sys.stderr.write(f"[AntiAgent] Linux native window error: {err}\n")
        sys.exit(2)

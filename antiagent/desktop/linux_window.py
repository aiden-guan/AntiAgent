"""Native GTK WebKit application window host for Linux desktop environments."""

from __future__ import annotations

import os
import sys
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Optional

# Ubuntu 24.04+ AppArmor restricts unprivileged user namespaces by default
# (kernel.apparmor_restrict_unprivileged_userns = 1), causing WebKit's
# bubblewrap sandbox to fail on startup unless disabled or profiled.
if sys.platform.startswith("linux") and "WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS" not in os.environ:
    os.environ["WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS"] = "1"



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


def _detect_system_is_dark() -> bool:
    """Detect if the system theme preference is dark mode across Linux desktops."""
    # 1. Try FreeDesktop XDG Settings Portal via Gio DBus (GNOME, KDE Plasma, wlroots)
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        if bus:
            val = bus.call_sync(
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.Settings",
                "Read",
                GLib.Variant("(ss)", ("org.freedesktop.appearance", "color-scheme")),
                GLib.VariantType("(v)"),
                Gio.DBusCallFlags.NONE,
                800,
                None,
            )
            if val:
                portal_val = val.get_child_value(0).get_variant().unpack()
                # 1 = prefer-dark, 2 = prefer-light, 0 = no preference
                if portal_val == 1:
                    return True
                elif portal_val == 2:
                    return False
    except Exception:
        pass

    # 2. Try GNOME GSettings
    try:
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        if source and source.lookup("org.gnome.desktop.interface", True):
            gsettings = Gio.Settings.new("org.gnome.desktop.interface")
            keys = gsettings.list_keys() if hasattr(gsettings, "list_keys") else []
            if not keys or "color-scheme" in keys:
                try:
                    scheme = gsettings.get_string("color-scheme").lower()
                    if "prefer-dark" in scheme:
                        return True
                    if "prefer-light" in scheme:
                        return False
                except Exception:
                    pass
            if not keys or "gtk-theme" in keys:
                try:
                    theme = gsettings.get_string("gtk-theme").lower()
                    if "dark" in theme:
                        return True
                except Exception:
                    pass
    except Exception:
        pass

    # 3. Try environment variable
    gtk_theme = os.environ.get("GTK_THEME", "").lower()
    if "dark" in gtk_theme:
        return True

    # 4. Try KDE globals configuration (~/.config/kdeglobals)
    try:
        kde_cfg = Path.home() / ".config" / "kdeglobals"
        if kde_cfg.is_file():
            content = kde_cfg.read_text(encoding="utf-8", errors="ignore").lower()
            if "color-scheme=dark" in content or "colorscheme=dark" in content or "breezedark" in content:
                return True
            if "color-scheme=light" in content or "colorscheme=light" in content:
                return False
    except Exception:
        pass

    return True


def _setup_theme_listener(on_theme_changed: callable) -> list:
    """Setup listeners for system dark/light theme changes at runtime.

    Returns a list of subscription handles/objects to keep alive.
    """
    handles = []

    # 1. Listen to FreeDesktop Portal DBus SettingChanged signal
    try:
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        if bus:
            def on_portal_setting_changed(connection, sender, path, iface, signal, params, user_data=None):
                try:
                    if params and len(params) >= 3:
                        ns = params[0]
                        key = params[1]
                        if ns == "org.freedesktop.appearance" and key == "color-scheme":
                            val = params[2].unpack()
                            if val == 1:
                                on_theme_changed(True)
                            elif val == 2:
                                on_theme_changed(False)
                            else:
                                on_theme_changed(_detect_system_is_dark())
                except Exception:
                    pass

            sub_id = bus.signal_subscribe(
                "org.freedesktop.portal.Desktop",
                "org.freedesktop.portal.Settings",
                "SettingChanged",
                "/org/freedesktop/portal/desktop",
                None,
                Gio.DBusSignalFlags.NONE,
                on_portal_setting_changed,
                None,
            )
            handles.append((bus, sub_id))
    except Exception:
        pass

    # 2. Listen to GNOME GSettings changed signals
    try:
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        if source and source.lookup("org.gnome.desktop.interface", True):
            gsettings = Gio.Settings.new("org.gnome.desktop.interface")

            def on_gsettings_changed(settings, key, user_data=None):
                if key in ("color-scheme", "gtk-theme"):
                    on_theme_changed(_detect_system_is_dark())

            gsettings.connect("changed::color-scheme", on_gsettings_changed)
            gsettings.connect("changed::gtk-theme", on_gsettings_changed)
            handles.append(gsettings)
    except Exception:
        pass

    return handles


def run_gtk4_window(url: str = "http://127.0.0.1:4242") -> int:
    """Launch native window using GTK 4 and WebKit 6."""
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("WebKit", "6.0")

    has_adw = False
    try:
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        has_adw = True
    except Exception:
        pass

    from gi.repository import Gtk, WebKit, Gdk, GLib

    GLib.set_prgname("com.antiagent.desktop")
    GLib.set_application_name("AntiAgent Guard")

    initial_dark = _detect_system_is_dark()
    if has_adw:
        try:
            style_mgr = Adw.StyleManager.get_default()
            if hasattr(style_mgr, "get_system_supports_color_schemes") and style_mgr.get_system_supports_color_schemes():
                style_mgr.set_color_scheme(Adw.ColorScheme.DEFAULT)
            else:
                style_mgr.set_color_scheme(Adw.ColorScheme.FORCE_DARK if initial_dark else Adw.ColorScheme.FORCE_LIGHT)
        except Exception:
            pass
    else:
        settings = Gtk.Settings.get_default()
        if settings:
            try:
                settings.set_property("gtk-application-prefer-dark-theme", initial_dark)
            except Exception:
                pass

    def apply_theme(is_dark: bool):
        if has_adw:
            try:
                sm = Adw.StyleManager.get_default()
                if hasattr(sm, "get_system_supports_color_schemes") and sm.get_system_supports_color_schemes():
                    sm.set_color_scheme(Adw.ColorScheme.DEFAULT)
                else:
                    sm.set_color_scheme(Adw.ColorScheme.FORCE_DARK if is_dark else Adw.ColorScheme.FORCE_LIGHT)
            except Exception:
                pass
        else:
            s = Gtk.Settings.get_default()
            if s:
                try:
                    s.set_property("gtk-application-prefer-dark-theme", is_dark)
                except Exception:
                    pass

    theme_handles = _setup_theme_listener(apply_theme)

    # Apply dark background styling
    css = b"window { background-color: #0d1117; }"
    provider = Gtk.CssProvider()
    provider.load_from_data(css)
    display = Gdk.Display.get_default()
    if display:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        try:
            icon_theme = Gtk.IconTheme.get_for_display(display)
            desktop_dir = Path(__file__).resolve().parent
            icon_theme.add_search_path(str(desktop_dir))
        except Exception:
            pass

    try:
        Gtk.Window.set_default_icon_name("antiagent")
    except Exception:
        pass

    app = Gtk.Application(application_id="com.antiagent.desktop")

    def on_activate(application):
        win = Gtk.ApplicationWindow(application=application, title="AntiAgent Guard")
        win.set_default_size(1160, 800)
        win.set_size_request(800, 600)

        icon_path = _get_icon_path()
        if hasattr(win, "set_icon_name"):
            try:
                win.set_icon_name("antiagent")
            except Exception:
                pass

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
    from gi.repository import Gtk, WebKit2, Gdk, GLib

    GLib.set_prgname("com.antiagent.desktop")
    GLib.set_application_name("AntiAgent Guard")

    initial_dark = _detect_system_is_dark()
    settings = Gtk.Settings.get_default()
    if settings:
        try:
            settings.set_property("gtk-application-prefer-dark-theme", initial_dark)
        except Exception:
            pass

    def apply_theme(is_dark: bool):
        s = Gtk.Settings.get_default()
        if s:
            try:
                s.set_property("gtk-application-prefer-dark-theme", is_dark)
            except Exception:
                pass

    theme_handles = _setup_theme_listener(apply_theme)

    desktop_dir = Path(__file__).resolve().parent
    try:
        icon_theme = Gtk.IconTheme.get_default()
        if icon_theme:
            icon_theme.append_search_path(str(desktop_dir))
    except Exception:
        pass

    try:
        Gtk.Window.set_default_icon_name("antiagent")
    except Exception:
        pass

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
    header = Gtk.HeaderBar()
    header.set_show_close_button(True)
    header.set_title("AntiAgent Guard")
    win.set_titlebar(header)

    win.set_default_size(1160, 800)
    win.set_size_request(800, 600)
    win.set_position(Gtk.WindowPosition.CENTER)
    try:
        win.set_wmclass("com.antiagent.desktop", "com.antiagent.desktop")
    except Exception:
        pass

    icon_path = _get_icon_path()
    if icon_path and icon_path.is_file():
        try:
            win.set_icon_from_file(str(icon_path))
        except Exception:
            pass
    if hasattr(win, "set_icon_name"):
        try:
            win.set_icon_name("antiagent")
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

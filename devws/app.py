"""Native desktop app shell.

Runs the workspace manager in its own GTK4 window (via system PyGObject +
WebKitGTK — no pip packages). The HTTP server stays a private implementation
detail: it binds to 127.0.0.1 on an ephemeral port that only this window
talks to. Falls back to the default browser when GTK/WebKit is missing.
"""

from __future__ import annotations

import threading

from .server import make_server

APP_ID = "io.github.MrDonLeo.DevWorkspaceManager"
APP_TITLE = "Developer Workspaces"


def gui_backend() -> str:
    """'gtk' when GTK4 + WebKitGTK are importable, else 'browser'."""
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("WebKit", "6.0")
        from gi.repository import Gtk, WebKit  # noqa: F401
        return "gtk"
    except (ImportError, ValueError):
        return "browser"


def _serve_in_background(host: str, port: int, config_path: str | None):
    httpd, app = make_server(host, port, config_path)
    actual_port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, app, actual_port


def run_window(config_path: str | None = None) -> int:
    """Launch the native window; returns the process exit code."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("WebKit", "6.0")
    from gi.repository import Gio, Gtk, WebKit

    # ephemeral port: nothing else can guess-collide, nothing is "hosted"
    httpd, app, port = _serve_in_background("127.0.0.1", 0, config_path)
    url = f"http://127.0.0.1:{port}/"

    gtk_app = Gtk.Application(application_id=APP_ID)

    def open_externally(uri: str) -> None:
        Gio.AppInfo.launch_default_for_uri(uri, None)

    def on_create(webview, nav_action):
        # window.open() (e.g. clicking a port chip) goes to the user's browser
        uri = nav_action.get_request().get_uri()
        if uri:
            open_externally(uri)
        return None

    def on_activate(gapp):
        win = Gtk.ApplicationWindow(application=gapp, title=APP_TITLE)
        win.set_default_size(1240, 840)
        webview = WebKit.WebView()
        webview.connect("create", on_create)
        settings = webview.get_settings()
        settings.set_enable_developer_extras(True)
        webview.load_uri(url)
        win.set_child(webview)
        win.present()

    gtk_app.connect("activate", on_activate)
    try:
        code = gtk_app.run(None)
    finally:
        httpd.shutdown()
        app.orchestrator.shutdown()
    return code


def run_browser_fallback(host: str, port: int, config_path: str | None) -> int:
    """No GTK available: serve normally and open the default browser."""
    import webbrowser

    httpd, app, actual_port = _serve_in_background(host, port, config_path)
    url = f"http://{host}:{actual_port}/"
    print(f"GTK not available — opening {url} in your browser (Ctrl+C to quit)")
    webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        app.orchestrator.shutdown()
    return 0


def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Developer Workspace Manager")
    parser.add_argument("--serve", action="store_true",
                        help="run the HTTP server only (no window)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address for --serve mode")
    parser.add_argument("--port", type=int, default=8765,
                        help="port for --serve mode (window mode picks its own)")
    parser.add_argument("--config", default=None, help="path to config.json")
    args = parser.parse_args()

    if args.serve:
        from .server import serve_forever
        serve_forever(args.host, args.port, args.config)
        return

    if gui_backend() == "gtk":
        sys.exit(run_window(args.config))
    sys.exit(run_browser_fallback("127.0.0.1", args.port, args.config))


if __name__ == "__main__":
    main()

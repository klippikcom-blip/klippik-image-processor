"""
KlippiK Image Processor — Windows Launcher
===========================================
Starts the Streamlit server and opens the browser automatically.

Works in two modes:
  • Script mode  — python launcher_windows.py
  • Frozen mode  — KlippiK_Image_Processor.exe  (built via PyInstaller)
"""

import os
import subprocess
import sys
import threading
import time
import webbrowser

APP_PORT = 8501
APP_URL  = f"http://localhost:{APP_PORT}"


def get_base_dir() -> str:
    """
    When frozen (PyInstaller EXE), files are extracted to sys._MEIPASS.
    When running as a plain script, use the script's own directory.
    """
    if getattr(sys, "frozen", False):
        return sys._MEIPASS          # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


def set_streamlit_env(base_dir: str):
    """Set environment variables Streamlit needs when running inside a frozen EXE."""
    os.environ["STREAMLIT_SERVER_PORT"]          = str(APP_PORT)
    os.environ["STREAMLIT_SERVER_HEADLESS"]      = "true"
    os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    os.environ["STREAMLIT_SERVER_ENABLE_CORS"]   = "false"

    # Tell Streamlit where its own static files are (bundled by PyInstaller)
    streamlit_static = os.path.join(base_dir, "streamlit", "static")
    if os.path.isdir(streamlit_static):
        os.environ["STREAMLIT_STATIC_PATH"] = streamlit_static


def open_browser():
    """Wait a few seconds for the server to start, then open the browser."""
    time.sleep(4)
    webbrowser.open(APP_URL)


def main():
    base_dir = get_base_dir()
    app_py   = os.path.join(base_dir, "app.py")

    if not os.path.exists(app_py):
        print(f"ERROR: app.py not found at {app_py}")
        input("Press Enter to exit.")
        sys.exit(1)

    set_streamlit_env(base_dir)

    print("=" * 52)
    print("  KlippiK Image Processor")
    print(f"  Starting at {APP_URL}")
    print("  Browser will open automatically in a few seconds.")
    print("  Close this window to stop the tool.")
    print("=" * 52)
    print()

    # Open browser in background thread
    threading.Thread(target=open_browser, daemon=True).start()

    # Run Streamlit
    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit", "run", app_py,
        "--server.port",               str(APP_PORT),
        "--server.headless",           "true",
        "--browser.gatherUsageStats",  "false",
        "--server.enableCORS",         "false",
        "--server.enableXsrfProtection", "true",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()

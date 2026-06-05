"""
KlippiK Image Processor — Windows Launcher v2
Uses streamlit.web.bootstrap.run() which is more reliable in frozen EXE mode.
"""

import os
import sys
import threading
import time
import webbrowser

APP_PORT = 8501
APP_URL  = f"http://localhost:{APP_PORT}"


def get_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return sys._MEIPASS   # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


def open_browser():
    time.sleep(5)
    print(f"Opening browser at {APP_URL} ...")
    webbrowser.open(APP_URL)


def main():
    base_dir = get_base_dir()
    app_py   = os.path.join(base_dir, "app.py")

    print("=" * 55)
    print("  KlippiK Image Processor")
    print(f"  Port:    {APP_PORT}")
    print(f"  App:     {app_py}")
    print(f"  BaseDir: {base_dir}")
    print("  Close this window to stop the tool.")
    print("=" * 55)

    if not os.path.exists(app_py):
        print(f"\nERROR: app.py not found at {app_py}")
        input("Press Enter to exit.")
        sys.exit(1)

    # Environment variables Streamlit needs
    os.environ["STREAMLIT_SERVER_PORT"]                  = str(APP_PORT)
    os.environ["STREAMLIT_SERVER_HEADLESS"]              = "true"
    os.environ["STREAMLIT_SERVER_ENABLE_CORS"]           = "false"
    os.environ["STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION"] = "true"
    os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"]   = "false"
    os.environ["STREAMLIT_SERVER_MAX_UPLOAD_SIZE"]       = "200"

    # Open browser after delay
    threading.Thread(target=open_browser, daemon=True).start()

    print("\nStarting Streamlit server...")

    # Primary approach: bootstrap.run (bypasses CLI arg parsing)
    try:
        from streamlit.web import bootstrap
        print("bootstrap imported OK")
        bootstrap.run(app_py, False, [], {})
        return
    except TypeError:
        # Some Streamlit versions have a different signature
        try:
            from streamlit.web import bootstrap
            bootstrap.run(app_py, "", [], {})
            return
        except Exception as e:
            print(f"bootstrap.run failed: {e}")

    # Fallback: CLI approach
    try:
        print("Trying CLI fallback...")
        from streamlit.web import cli as stcli
        sys.argv = [
            "streamlit", "run", app_py,
            f"--server.port={APP_PORT}",
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
            "--server.enableCORS=false",
        ]
        sys.exit(stcli.main())
    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        input("\nPress Enter to exit.")
        sys.exit(1)


if __name__ == "__main__":
    main()

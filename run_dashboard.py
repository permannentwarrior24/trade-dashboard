"""Run the dashboard on Linux, macOS, or Windows and open it in a browser."""

import argparse
import threading
import time
import urllib.error
import urllib.request
import webbrowser


def open_browser_when_ready(url: str) -> None:
    """Wait until Uvicorn responds, then open the user's default browser."""
    for _ in range(60):
        try:
            with urllib.request.urlopen(url, timeout=1):
                webbrowser.open(url)
                return
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start Trade Dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--reload", action="store_true", help="reload after code changes")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "Python dependencies are missing. Run: python -m pip install -r requirements.txt"
        ) from exc

    browser_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    url = f"http://{browser_host}:{args.port}"
    if not args.no_browser:
        threading.Thread(
            target=open_browser_when_ready, args=(url,), daemon=True
        ).start()

    print(f"Starting Trade Dashboard on {url}")
    uvicorn.run("server:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()

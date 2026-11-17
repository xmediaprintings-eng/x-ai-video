"""Start the server and print addresses for this PC and for phones on the same Wi-Fi."""
from __future__ import annotations

import os
import socket
import threading
import webbrowser


def lan_ip() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    ip = lan_ip()
    print("\n" + "=" * 56)
    print("  X Media AI Video is running")
    print(f"  On this computer : http://localhost:{port}")
    if ip:
        print(f"  On your phone    : http://{ip}:{port}   (same Wi-Fi)")
    print("  Close this window to stop.")
    print("=" * 56 + "\n")
    if os.environ.get("XAV_NO_BROWSER") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()

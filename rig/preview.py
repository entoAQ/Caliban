#!/usr/bin/env python3
"""Live MJPEG preview for framing and focusing the bench rig.

Run it on the Pi, open http://<pi-address>:8000/ on a laptop, and you get a
live view to frame the dish and turn the focus ring against. Ctrl+C to stop.

This exists because the alternatives are worse. `rpicam-hello -t 0` needs a
desktop session, so over SSH it just reports "Preview window unavailable";
running it through screen sharing works but adds enough lag that hunting for
peak focus becomes guesswork. A raw MJPEG stream straight to the browser is
about as direct as it gets.

Deliberately runs at preview resolution rather than the full 2592x1944 the
captures use. Framing and focus don't need the pixels, and the lower rate
keeps latency low enough that turning the lens feels connected to what you
see on screen -- which is the whole point.

Auto-exposure is left ON here, unlike capture.py. This is a viewfinder, not a
measurement: you want to see the dish clearly while you position it, whatever
the lighting is doing. Locking exposure is capture.py's job.
"""

import io
import socketserver
from http import server
from threading import Condition

from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput

PORT = 8000
PREVIEW_SIZE = (1296, 972)

PAGE = """<!doctype html>
<html>
<head><title>Rig preview</title></head>
<body style="margin:0;background:#111;text-align:center">
<img src="stream.mjpg" style="max-width:100%;height:auto">
</body>
</html>
"""


class StreamBuffer(io.BufferedIOBase):
    """Holds the most recent frame and wakes waiting clients when it changes.

    Only the latest frame is kept. A viewer that falls behind should skip
    ahead to the present rather than work through a backlog -- stale frames
    are worse than useless when you are adjusting a lens in real time.
    """

    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


class StreamHandler(server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            content = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/stream.mjpg":
            self.send_response(200)
            self.send_header("Age", 0)
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header(
                "Content-Type", "multipart/x-mixed-replace; boundary=FRAME"
            )
            self.end_headers()
            try:
                while True:
                    with buffer.condition:
                        buffer.condition.wait()
                        frame = buffer.frame
                    self.wfile.write(b"--FRAME\r\n")
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
            except Exception:
                # Browser closed the tab, navigated away, or the network
                # blipped. Nothing to do and nothing worth logging.
                pass
        else:
            self.send_error(404)
            self.end_headers()

    def log_message(self, *args):
        # Silence the per-request logging; an MJPEG stream would bury the
        # terminal in noise.
        pass


class StreamServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    buffer = StreamBuffer()
    picam2 = Picamera2()
    picam2.configure(picam2.create_video_configuration(main={"size": PREVIEW_SIZE}))
    picam2.start_recording(MJPEGEncoder(), FileOutput(buffer))

    print(f"Preview at http://<pi-address>:{PORT}/   (Ctrl+C to stop)")
    try:
        StreamServer(("", PORT), StreamHandler).serve_forever()
    finally:
        picam2.stop_recording()

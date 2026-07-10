#!/usr/bin/env python3

import json
import os
import re
import signal
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = 8765

# Set this to the private IP of the reverse proxy server.
# Only used when HOST allows remote connections.
ALLOWED_PROXY_IP = "192.168.0.54"
REQUIRE_REVERSE_PROXY_HEADERS = True

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = os.path.join(BASE_DIR, "index.html")

UPDATE_LOG_FILE = "/var/log/ottobot-update.log"
UPDATE_LOG_SERVICE_VALUE = "__ottobot_update_log__"

# Compose service names: alphanumeric start, then [a-zA-Z0-9._-]. Anything
# else (in particular a leading "-") must not reach the docker argv below.
SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not self.is_allowed_request():
            self.send_error(403, "Forbidden")
            return

        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/":
            self.handle_index()
            return

        if parsed.path in ("/favicon.ico", "/favicon.svg"):
            self.handle_favicon()
            return

        if parsed.path == "/services":
            self.handle_services()
            return

        if parsed.path == "/events":
            query = urllib.parse.parse_qs(parsed.query)
            service = query.get("service", [""])[0].strip()
            self.handle_events(service)
            return

        self.send_error(404)

    def handle_index(self):
        try:
            with open(HTML_FILE, "rb") as file:
                body = file.read()
        except OSError:
            self.send_error(500, "index.html not found")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def handle_favicon(self):
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(FAVICON_SVG)

    def handle_services(self):
        try:
            result = subprocess.run(
                ["docker", "compose", "config", "--services"],
                cwd=BASE_DIR,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=True,
            )

            services = [
                line.strip() for line in result.stdout.splitlines() if line.strip()
            ]
        except subprocess.SubprocessError:
            services = []

        body = json.dumps(services).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def handle_events(self, service):
        if service == UPDATE_LOG_SERVICE_VALUE:
            cmd = [
                "tail",
                "-n",
                "200",
                "-F",
                UPDATE_LOG_FILE,
            ]
        else:
            if service and not SERVICE_NAME_RE.match(service):
                self.send_error(400, "Invalid service name")
                return

            cmd = [
                "docker",
                "compose",
                "logs",
                "-f",
                "--tail=200",
            ]

            if service:
                cmd.append(service)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        process = subprocess.Popen(
            cmd,
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            errors="replace",
            start_new_session=True,
        )

        stdout = process.stdout

        if stdout is None:
            process.terminate()
            raise RuntimeError("Failed to open process stdout")

        try:
            for line in stdout:
                line = line.rstrip("\r\n")
                message = f"data: {line}\n\n"

                self.wfile.write(message.encode("utf-8"))
                self.wfile.flush()
        except BrokenPipeError:
            pass
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def is_allowed_request(self):
        if host_is_local_only():
            return True

        remote_ip = self.client_address[0]

        if remote_ip != ALLOWED_PROXY_IP:
            return False

        if REQUIRE_REVERSE_PROXY_HEADERS:
            if not self.headers.get("X-Forwarded-For"):
                return False

            if not self.headers.get("X-Forwarded-Proto"):
                return False

            if not self.headers.get("X-Forwarded-Host"):
                return False

        return True

    def log_message(self, format, *args):
        return


# Icon: "Radio Signal 1026" from SVG Repo, licensed under CC0 / public domain.
FAVICON_SVG = b"""<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!-- Uploaded to: SVG Repo, www.svgrepo.com, Generator: SVG Repo Mixer Tools -->
<svg width="800px" height="800px" viewBox="0 -3 20 20" version="1.1" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">

    <title>radio_signal [#1026]</title>
    <desc>Created with Sketch.</desc>
    <defs></defs>
    <g id="Page-1" stroke="none" stroke-width="1" fill="none" fill-rule="evenodd">
        <g id="Dribbble-Light-Preview" transform="translate(-140.000000, -3642.000000)" fill="#000000">
            <g id="icons" transform="translate(56.000000, 160.000000)">
                <path d="M95.4324393,3487.63833 C95.4094393,3487.61278 95.3924393,3487.58428 95.3674393,3487.55971 L95.3634393,3487.56265 C94.5824393,3486.79509 93.2434393,3486.86683 92.5364393,3487.56167 C91.8294393,3488.25651 91.8314393,3489.64423 92.5384393,3490.33907 C92.5474393,3490.34791 92.5584393,3490.35381 92.5674393,3490.36265 C92.5914393,3490.38722 92.6084393,3490.41671 92.6334393,3490.44128 L92.6364393,3490.43735 C93.4174393,3491.20491 94.7564393,3491.13415 95.4634393,3490.43833 C96.1704393,3489.74349 96.1684393,3488.35577 95.4614393,3487.66093 C95.4524393,3487.65209 95.4414393,3487.64717 95.4324393,3487.63833 M91.1194393,3486.17002 L89.7054393,3484.77936 C87.5844393,3486.86486 87.6834393,3491.13612 89.8044393,3493.22064 L91.2184393,3491.83096 C89.8044393,3490.44128 89.7054393,3487.55971 91.1194393,3486.17002 M88.2914393,3483.38968 L86.8774393,3482 C82.6344393,3486.17002 83.4404393,3492.5258 86.9764393,3496 L88.3904393,3494.61032 C85.5624393,3491.83096 85.4634393,3486.17002 88.2914393,3483.38968 M98.1954393,3484.77936 L96.7814393,3486.17002 C98.1954393,3487.55971 98.2944393,3490.44128 96.8804393,3491.83096 L98.2944393,3493.22064 C100.415439,3491.13612 100.316439,3486.86486 98.1954393,3484.77936 M101.122439,3496 L99.7084393,3494.61032 C102.537439,3491.83096 102.438439,3486.17002 99.6094393,3483.38968 L101.023439,3482 C104.559439,3485.47518 105.365439,3491.83096 101.122439,3496" id="radio_signal-[#1026]"></path>
            </g>
        </g>
    </g>
</svg>"""


def host_is_local_only():
    return HOST in ("127.0.0.1", "localhost", "::1")


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Serving Docker Compose logs on http://{HOST}:{PORT}")
    server.serve_forever()

#!/usr/bin/env python3
"""Check installed wheels outside the checkout, without providers or a browser."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    python = str(parser.parse_args().python.absolute())
    with tempfile.TemporaryDirectory(prefix="sciver-installed-") as temp:
        root = Path(temp)
        workspace = root / "workspace"
        subprocess.run([python, "-I", "-m", "theory_master", "quickstart", "--workspace", str(workspace), "--no-web"], cwd=root, check=True)
        with (root / "server.log").open("w+") as log:
            server = subprocess.Popen([python, "-I", "-m", "tm_observer", "serve", "--search-root", str(workspace), "--state-dir", str(root / "observer"), "--port", "0"], cwd=root, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    log.seek(0)
                    output = log.read()
                    match = re.search(r"Sciver Web UI: (http://[^\s]+)", output)
                    if match:
                        url = match[1]
                        break
                    if server.poll() is not None:
                        raise RuntimeError(output)
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Installed Web server did not become ready: " + output)
                with urlopen(url + "healthz", timeout=5) as response:
                    assert json.load(response)["ok"]
                with urlopen(url, timeout=5) as response:
                    assert response.url.endswith('/research-chat/')
                    assert 'data-setup-welcome' in response.read().decode('utf-8')
                with urlopen(url + "observer_state.json", timeout=10) as response:
                    assert any("test0" in str(run) for run in json.load(response)["runs"])
                with urlopen(url + "research-chat/assets/research-chat.js", timeout=5) as response:
                    assert response.status == 200 and len(response.read()) > 100
                print("PASS: installed Test0, task discovery, HTTP server and frontend assets")
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == "__main__":
    main()

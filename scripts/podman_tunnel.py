"""Expose this project's Podman VM ports when macOS port forwarding is unavailable.

Run in a separate terminal after `podman compose up -d`. Ctrl-C closes the tunnel.
"""

from __future__ import annotations

import json
import os
import subprocess


machine = json.loads(subprocess.check_output(["podman", "machine", "inspect"], text=True))[0]
ssh = machine["SSHConfig"]
command = [
    "ssh", "-N", "-o", "BatchMode=yes", "-i", ssh["IdentityPath"],
    "-p", str(ssh["Port"]),
    "-L", "127.0.0.1:18080:127.0.0.1:18080",
    "-L", "127.0.0.1:18000:127.0.0.1:18000",
    f"{ssh['RemoteUsername']}@127.0.0.1",
]
os.execvp("ssh", command)

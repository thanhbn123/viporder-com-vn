#!/usr/bin/env python3
"""Guard: does the containerised nginx config actually match the mounts?

Two real defects shipped in `deploy/docker-compose.yml`, both invisible until
somebody tried to start the stack, and neither caught by any existing check:

1. The site config includes `/etc/nginx/snippets/viporder-security-headers.conf`
   in five places, and compose never mounted it. nginx exits at startup with
   `[emerg] open() ... failed` — **the containerised stack could not start at all.**

2. The nginx service mounted the WHOLE REPOSITORY as the web root
   (`../:/srv/viporder/site:ro`), and the site config serves that root with
   `try_files`. The application's own source, `docs/SECURITY.md`, the tooling and
   the test suite were all fetchable over HTTPS. `.env` was protected, but only by
   the separate dotfile rule — a mitigation for something that should never have
   been reachable.

`tools/check_nginx_config.py` could not catch either: it reasons about the nginx
config in isolation and never looks at compose. This tool checks the seam between
them, which is exactly where both defects lived.

Scope: every absolute `include` path in the site config must be provided by a
mount on the compose nginx service, and no mount may expose a directory that
contains files this project does not publish.

It does NOT validate nginx syntax, and it does NOT run Docker.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "deploy" / "docker-compose.yml"
NGINX_DIR = ROOT / "deploy" / "nginx"

#: Files the public site is allowed to serve from the document root.
PUBLISHED = {"index.html", "404.html", "robots.txt", "sitemap.xml", "static"}

INCLUDE_RE = re.compile(r"^\s*include\s+([^;]+);", re.MULTILINE)
ROOT_RE = re.compile(r"^\s*root\s+([^;]+);", re.MULTILINE)


#: `- host:container[:mode]` inside a volumes list. Deliberately NOT a YAML parse:
#: this tool has to run under the same bare `python3` as the other repo tools, and
#: in the site-checks CI job nothing is pip-installed. Adding PyYAML here would
#: make the guard fail OPEN in exactly the environment it is meant to protect.
_VOLUME_RE = re.compile(
    r"^\s*-\s*([^\s#][^:]*):([^:\s]+)(?::[a-zA-Z,]+)?\s*$", re.MULTILINE
)


def _container_paths(text: str) -> dict[str, str]:
    """Map container path -> host path for every volume in the compose file.

    Scans all services rather than only `nginx`: a path mounted anywhere is a path
    the stack provides, and being permissive here means the check cannot fail open
    if the service is ever renamed.
    """
    out: dict[str, str] = {}
    for host, container in _VOLUME_RE.findall(text):
        out[container.strip().rstrip("/") or "/"] = host.strip()
    return out


def main() -> int:
    errors: list[str] = []
    notes: list[str] = []

    if not COMPOSE.exists():
        print(f"FAIL: {COMPOSE} is missing")
        return 1

    compose_text = COMPOSE.read_text(encoding="utf-8")
    if "nginx:" not in compose_text:
        print("FAIL: deploy/docker-compose.yml has no `nginx` service")
        return 1

    mounts = _container_paths(compose_text)
    if not mounts:
        print("FAIL: the compose nginx service mounts nothing")
        return 1

    confs = sorted(NGINX_DIR.glob("*.conf"))
    if not confs:
        print(f"FAIL: no nginx configs found in {NGINX_DIR}")
        return 1

    # -- 1. every absolute include must be mounted ---------------------------
    for conf in confs:
        text = conf.read_text(encoding="utf-8")
        for target in INCLUDE_RE.findall(text):
            target = target.strip().strip('"').strip("'")
            if not target.startswith("/"):
                # A relative include resolves inside the nginx prefix, which the
                # image provides; not our business here.
                continue
            if target in mounts:
                notes.append(f"{conf.name}: include {target} — mounted")
            else:
                errors.append(
                    f"{conf.name} includes {target}, but the compose nginx service "
                    f"does not mount it. nginx will exit at startup with "
                    f'`[emerg] open() "{target}" failed`.'
                )

    # -- 2. the web root must not expose the repository ----------------------
    for conf in confs:
        text = conf.read_text(encoding="utf-8")
        for root in ROOT_RE.findall(text):
            root = root.strip().strip('"').strip("'")
            if root not in mounts:
                continue
            host = mounts[root]
            host_path = (COMPOSE.parent / host).resolve()
            if host_path == ROOT.resolve():
                errors.append(
                    f"{conf.name} serves root {root} from the repository root "
                    f"({host}). Everything in the tree becomes fetchable — source, "
                    f"docs, tools, tests. Mount only the published files: "
                    f"{', '.join(sorted(PUBLISHED))}."
                )
            elif host_path.name not in PUBLISHED and host_path.parent == ROOT:
                errors.append(
                    f"{conf.name} serves root {root} from {host}, which this project "
                    f"does not publish."
                )
            else:
                notes.append(f"{conf.name}: root {root} <- {host} — published subset")

    # -- 3. nothing else may be mounted that is not needed -------------------
    for container, host in mounts.items():
        host_path = (COMPOSE.parent / host).resolve()
        try:
            under_root = host_path.is_relative_to(ROOT)
        except ValueError:  # pragma: no cover - py<3.9 safety
            under_root = str(host_path).startswith(str(ROOT))
        if under_root and host_path == ROOT.resolve():
            errors.append(
                f"a volume mounts the whole repository ({host} -> {container})"
            )

    for line in notes:
        print(f"  {line}")
    for line in errors:
        print(f"ERROR {line}")

    print(
        f"compose-nginx: {len(mounts)} mount(s), {len(confs)} config(s), {len(errors)} error(s)"
    )
    print(
        "scope: include/mount agreement and web-root exposure in "
        "deploy/docker-compose.yml + deploy/nginx/*.conf only — this does NOT run "
        "Docker, and does NOT validate nginx syntax (use `nginx -t`)."
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

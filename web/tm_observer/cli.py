from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .archify_views import (
    bootstrap_archify,
    build_architecture_delta,
    build_static_views,
    default_archify_root,
    default_state_dir,
)
from .auth import default_auth_file, ensure_auth_file
from .collector import collect_state
from .al_public import install_tunnel_service, render_tunnel_service, uninstall_tunnel_service
from .service import install_user_service, render_user_service, uninstall_user_service
from .web import serve


def _default_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return Path.cwd().resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tm-observer", description="Sciver research observability and operator controls")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="collect one normalized observer_state.json")
    snap.add_argument("--search-root", type=Path, default=_default_root())
    snap.add_argument("--output", type=Path, required=True)

    bootstrap = sub.add_parser("archify-bootstrap", help="install the pinned Archify renderer outside the repository")
    bootstrap.add_argument("--target", type=Path, default=default_archify_root())

    delta = sub.add_parser("build-delta", help="compare previous validated Architecture snapshot with current HEAD")
    delta.add_argument("--search-root", type=Path, default=_default_root())
    delta.add_argument("--state-dir", type=Path, default=default_state_dir())
    delta.add_argument("--archify-root", type=Path, default=default_archify_root())
    delta.add_argument("--force", action="store_true")

    static = sub.add_parser("build-static", help="validate and render Architecture/Workflow/Lifecycle views")
    static.add_argument("--search-root", type=Path, default=_default_root())
    static.add_argument("--state-dir", type=Path, default=default_state_dir())
    static.add_argument("--archify-root", type=Path, default=default_archify_root())
    static.add_argument("--force", action="store_true")

    auth = sub.add_parser("auth-init", help="create a private Basic Auth credential file if missing")
    auth.add_argument("--path", type=Path, default=default_auth_file())
    auth.add_argument("--username", default="tmobserver")

    unit = sub.add_parser("service-unit", help="print the localhost-only systemd user service unit")
    unit.add_argument("--search-root", type=Path, default=_default_root())
    unit.add_argument("--state-dir", type=Path, default=default_state_dir())
    unit.add_argument("--archify-root", type=Path, default=default_archify_root())
    unit.add_argument("--port", type=int, default=8787)
    unit.add_argument("--basic-auth-file", type=Path)

    install = sub.add_parser("service-install", help="install and start the systemd user service")
    install.add_argument("--search-root", type=Path, default=_default_root())
    install.add_argument("--state-dir", type=Path, default=default_state_dir())
    install.add_argument("--archify-root", type=Path, default=default_archify_root())
    install.add_argument("--port", type=int, default=8787)
    install.add_argument("--basic-auth-file", type=Path)
    install.add_argument("--no-start", action="store_true")

    sub.add_parser("service-uninstall", help="disable and remove the systemd user service")

    tunnel_unit = sub.add_parser("al-tunnel-unit", help="print the dedicated AL reverse-tunnel systemd unit")
    tunnel_unit.add_argument("--key", type=Path)
    tunnel = sub.add_parser("al-tunnel-install", help="install and start the dedicated AL reverse tunnel")
    tunnel.add_argument("--key", type=Path)
    tunnel.add_argument("--no-start", action="store_true")
    sub.add_parser("al-tunnel-uninstall", help="disable and remove the dedicated AL reverse tunnel")

    web = sub.add_parser("serve", help="serve the Sciver Web UI")
    web.add_argument("--search-root", type=Path, default=_default_root())
    web.add_argument("--open", action="store_true", help="open the browser after the server is ready")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8787)
    web.add_argument("--cache-ttl", type=float, default=5.0)
    web.add_argument("--state-dir", type=Path, default=default_state_dir())
    web.add_argument("--archify-root", type=Path, default=default_archify_root())
    web.add_argument("--static-refresh", type=float, default=30.0)
    web.add_argument("--basic-auth-file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "snapshot":
        state = collect_state(args.search_root)
        args.output = args.output.expanduser().resolve()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(args.output)
        return 0
    if args.command == "archify-bootstrap":
        print(bootstrap_archify(args.target))
        return 0
    if args.command == "build-delta":
        result = build_architecture_delta(
            args.search_root,
            args.state_dir,
            archify_root=args.archify_root,
            force=args.force,
        )
        if not result.ok:
            print(result.warning or "delta build failed")
            return 2
        if result.warning:
            print(result.warning)
        else:
            print(result.output_path)
        return 0
    if args.command == "build-static":
        result = build_static_views(
            args.search_root,
            args.state_dir,
            archify_root=args.archify_root,
            force=args.force,
        )
        if not result.ok:
            print(result.warning or "static build failed")
            return 2
        print(result.output_dir)
        return 0
    if args.command == "auth-init":
        print(ensure_auth_file(args.path, username=args.username))
        return 0
    if args.command == "service-unit":
        print(
            render_user_service(
                args.search_root,
                python_executable=Path(sys.executable),
                state_dir=args.state_dir,
                archify_root=args.archify_root,
                auth_file=args.basic_auth_file,
                port=args.port,
            ),
            end="",
        )
        return 0
    if args.command == "service-install":
        result = install_user_service(
            args.search_root,
            python_executable=Path(sys.executable),
            state_dir=args.state_dir,
            archify_root=args.archify_root,
            auth_file=args.basic_auth_file,
            port=args.port,
            start=not args.no_start,
        )
        print(result.unit_path)
        return 0
    if args.command == "service-uninstall":
        print(uninstall_user_service())
        return 0
    if args.command == "al-tunnel-unit":
        print(render_tunnel_service(key_path=args.key), end="")
        return 0
    if args.command == "al-tunnel-install":
        result = install_tunnel_service(key_path=args.key, start=not args.no_start)
        print(result.unit_path)
        return 0
    if args.command == "al-tunnel-uninstall":
        print(uninstall_tunnel_service())
        return 0
    if args.command == "serve":
        serve(
            args.search_root,
            args.host,
            args.port,
            args.cache_ttl,
            state_dir=args.state_dir,
            archify_root=args.archify_root,
            static_refresh=args.static_refresh,
            auth_file=args.basic_auth_file,
            open_browser=args.open,
        )
        return 0
    raise AssertionError(args.command)

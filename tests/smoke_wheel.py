"""Build the checkout's wheel and exercise its console entry point in isolation."""

import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


def main():
    repo = Path(__file__).resolve().parents[1]
    version = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    with tempfile.TemporaryDirectory(prefix="sqlwhy-wheel-") as tmp:
        root = Path(tmp)
        wheelhouse = root / "wheelhouse"
        subprocess.run(
            [sys.executable, "-m", "pip", "--disable-pip-version-check", "wheel", "--no-deps", "--wheel-dir",
             str(wheelhouse), "."], cwd=repo, check=True, timeout=180,
        )
        wheel, = wheelhouse.glob("sqlwhy-*.whl")
        cwd = root / "empty directory"
        cwd.mkdir()
        cmd = ["uvx", "--isolated", "--no-config", "--no-python-downloads", "--quiet", "--python", sys.executable,
               "--cache-dir", str(root / "cache"), "--from", wheel.as_uri(), "sqlwhy"]
        env = {k: v for k, v in os.environ.items() if k.upper() not in {"PYTHONHOME", "PYTHONPATH"}}

        def cli(code, *args):
            p = subprocess.run([*cmd, *args], cwd=cwd, env=env, capture_output=True, encoding="utf-8", timeout=120)
            assert p.returncode == code and not p.stderr, (
                f"{args} exited {p.returncode}, expected {code} with empty stderr\n{p.stdout}\n{p.stderr}"
            )
            return p.stdout

        demo = json.loads(cli(1, "demo", "cte-audit", "--json"))
        fields = ("key", "expected_rows", "source_rows", "source_keys", "report_rows", "violation_count")
        assert tuple(demo[field] for field in fields) == ("o.id", 1, 2, 2, 2, 2), demo
        examples = [(ex["target"], ex["status"], ex["output_rows"]) for ex in demo["examples"]]
        assert examples == [({"key": "o.id", "value": 41}, "present", 2),
                            ({"key": "o.id", "value": 42}, "removed", 0)], examples
        changes = [
            (ex["target"]["value"], s["cte"], s["stage"], s["before"], s["after"], s.get("truth_counts"))
            for ex in demo["examples"] for s in ex["stages"] if s["before"] is not None and s["before"] != s["after"]
        ]
        assert changes == [
            (41, "orders_with_payments", "join", 1, 2, None),
            (42, "settled_orders", "where", 1, 0, {"TRUE": 0, "FALSE": 0, "UNKNOWN": 1}),
        ], changes
        installed = cli(0, "--version").strip()
        assert installed == f"sqlwhy {version}", installed
        success = json.loads(cli(0, "demo", "left-join-null", "--expect", "removed", "--expect-rows", "0", "--json"))
        assert (success["status"], success["output_rows"]) == ("removed", 0), success
        print(f"Verified {installed} from its wheel with both audit and trace demos.")


if __name__ == "__main__":
    main()

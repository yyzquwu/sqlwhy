"""Verify the recorded dbt artifacts against fresh compilations in a temporary directory."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


FIXTURES = Path(__file__).resolve().parent
CASES = {
    "stg_orders": ("dbt-labs/jaffle-shop-classic", "fd7bfacae4f497ff044a6a0275268676bf1b64c3", "stg_orders"),
    "a1": ("fivetran/dbt_quickbooks", "505a2f48c0f07f10d6ef0ef516b84e1a7800c48f", "int_quickbooks__invoice_join"),
    "a2": ("fivetran/dbt_quickbooks", "74da53b53cc1acf0cd29275744263dd4757ce589", "int_quickbooks__bill_payment_double_entry"),
    "a3": ("fivetran/dbt_shopify", "304c7ee7f4c1d229981b491c9bf2b60a7411f73b", "int_shopify__discounts__abandoned_checkouts"),
    "a5": ("fivetran/dbt_zendesk", "1ad0a86859f8c589889e7a736130eadb04a4d6d2", "int_zendesk__ticket_resolution_times_calendar"),
}
PROFILE = """local_duckdb:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: evidence.duckdb
      schema: main
      threads: 1
"""


def run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True)


def check_compilation(root):
    dbt = Path(sys.executable).with_name("dbt.exe" if os.name == "nt" else "dbt")
    options = ("--profile", "local_duckdb", "--profiles-dir", str(root), "--no-use-colors")
    (root / "profiles.yml").write_text(PROFILE, encoding="utf-8")
    for case, (repo, revision, model) in CASES.items():
        project = root / case
        project.mkdir()
        run("git", "init", "-q", cwd=project)
        run("git", "fetch", "-q", "--depth", "1", f"https://github.com/{repo}.git", revision, cwd=project)
        run("git", "-c", "core.autocrlf=false", "checkout", "-q", "--detach", "FETCH_HEAD", cwd=project)
        if case == "stg_orders":
            if (project / "seeds/raw_orders.csv").read_bytes() != (FIXTURES / "raw_orders.csv").read_bytes():
                raise SystemExit("Pinned seed bytes differ.")
            run(dbt, "seed", *options, "--select", "raw_orders", cwd=project)
        else:
            if case == "a3":
                path = project / "packages.yml"
                packages = path.read_text(encoding="utf-8").replace(
                    "revision: feature/revamp/add-tax-line", "revision: dc5b7a64ccb979bdf55ac29e0c8ac488058cd376"
                )
                path.write_text(packages, encoding="utf-8")
            lock = FIXTURES / "locks" / f"{case}.yml"
            shutil.copyfile(lock, project / "package-lock.yml")
            run(dbt, "deps", *options, cwd=project)
            if lock.read_text(encoding="utf-8") != (project / "package-lock.yml").read_text(encoding="utf-8"):
                raise SystemExit(f"{case}: dependency lock changed.")
        run(dbt, "compile", *options, "--select", model, cwd=project)
        compiled, = (project / "target/compiled").rglob(f"{model}.sql")
        recorded = FIXTURES / "compiled" / f"{case}.sql"
        if compiled.read_text(encoding="utf-8") != recorded.read_text(encoding="utf-8"):
            raise SystemExit(f"{case}: freshly compiled SQL differs from the recorded SQL.")
        print(f"{case}: verified {recorded.name}", flush=True)


if __name__ == "__main__":
    os.environ["DBT_SEND_ANONYMOUS_USAGE_STATS"] = "false"
    with TemporaryDirectory(prefix="sqlwhy-dbt-") as temp:
        check_compilation(Path(temp))

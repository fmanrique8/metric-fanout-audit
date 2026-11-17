import argparse
import json
import sys
from pathlib import Path

from .audit import audit, read_scenario


def signed(amount):
    return f"+{amount}" if amount != "0.00" and not amount.startswith("-") else amount


def render(report):
    fact = report["fact"]
    metric = report["metric"]
    base = report["base"]
    final = report["final"]
    first = report["first_fanout"]
    lines = [f"FAIL  first fanout: {fact} -> {first}" if first else "PASS  fact grain preserved"]
    lines.append(f"{metric}: {base['total']} -> {final['total']} (delta {signed(report['delta'])})")
    lines.append(f"rows: {base['rows']} -> {final['rows']}")
    for stage in report["stages"]:
        status = "FANOUT" if stage["affected"] else "OK"
        lines.append(
            f"{status}  {fact} -> {stage['join']}  "
            f"rows {stage['rows_before']} -> {stage['rows_after']}  "
            f"{metric} {stage['total_before']} -> {stage['total_after']}"
        )
        for row in stage["affected"]:
            lines.append(
                f"  {report['key']}={row['key']!r}: {row['right_matches']} right matches, "
                f"rows {row['rows_before']} -> {row['rows_after']}, delta {signed(row['delta'])}"
            )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit additive fact metrics for join fanout.")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="check a TOML scenario with local CSV inputs")
    check.add_argument("scenario", type=Path)
    check.add_argument("--json", action="store_true", help="print a machine-readable report")
    arguments = parser.parse_args(argv)
    try:
        report = audit(read_scenario(arguments.scenario))
    except (OSError, ValueError) as error:
        print(f"ERROR  {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if arguments.json else render(report))
    return 1 if report["first_fanout"] is not None else 0

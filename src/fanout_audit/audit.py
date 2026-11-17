import csv
import re
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Join:
    name: str
    csv_path: Path
    fact_column: str
    right_column: str


@dataclass(frozen=True)
class Scenario:
    fact_name: str
    fact_csv: Path
    key: str
    metric: str
    joins: list[Join]


def read_scenario(path: Path) -> Scenario:
    with path.open("rb") as file:
        config = tomllib.load(file)

    fact = config.get("fact")
    joins = config.get("joins")
    if set(config) != {"fact", "joins"} or not isinstance(joins, list) or not joins:
        raise ValueError("scenario requires [fact] and at least one [[joins]]")
    fact_name, fact_csv, key, metric = read_fields(fact, "fact", "name", "csv", "key", "metric")

    steps = []
    names = {fact_name}
    for index, join in enumerate(joins, start=1):
        label = f"joins[{index}]"
        name, csv_path, fact_column, right_column = read_fields(
            join, label, "name", "csv", "fact_column", "right_column"
        )
        if name in names:
            raise ValueError(f"{label}: table name {name!r} is already in use")
        names.add(name)
        steps.append(Join(name, path.parent / csv_path, fact_column, right_column))
    return Scenario(fact_name, path.parent / fact_csv, key, metric, steps)


def read_fields(table, label, *fields):
    if not isinstance(table, dict) or set(table) != set(fields):
        raise ValueError(f"{label} requires exactly these fields: {', '.join(fields)}")
    values = tuple(table[field] for field in fields)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{label}: all fields must be nonempty strings")
    return values


def read_csv(path, columns):
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file, strict=True)
        try:
            headers = next(reader, [])
            if not headers or "" in headers or len(set(headers)) != len(headers):
                raise ValueError(f"{path}: expected a header with unique, nonempty column names")
            missing = set(columns) - set(headers)
            if missing:
                raise ValueError(f"{path}: missing columns {', '.join(sorted(missing))}")
            positions = {column: headers.index(column) for column in columns}
            for row in reader:
                if len(row) != len(headers):
                    raise ValueError(f"{path}:{reader.line_num}: row has the wrong number of fields")
                yield reader.line_num, {column: row[index] for column, index in positions.items()}
        except csv.Error as error:
            raise ValueError(f"{path}:{reader.line_num}: {error}") from error


AMOUNT = re.compile(r"([+-]?)([0-9]{1,18})(?:\.([0-9]{1,6}))?")
SCALE = 10**6


def parse_amount(value):
    match = AMOUNT.fullmatch(value)
    if not match:
        raise ValueError("expected a decimal with up to 18 integer and 6 fractional digits")
    sign, whole, fraction = match.groups()
    units = int(whole) * SCALE + int((fraction or "").ljust(6, "0"))
    return -units if sign == "-" else units


def format_amount(units):
    whole, fraction = divmod(abs(units), SCALE)
    digits = f"{fraction:06d}".rstrip("0").ljust(2, "0")
    sign = "-" if units < 0 else ""
    return f"{sign}{whole}.{digits}"


def audit(scenario: Scenario) -> dict:
    columns = {scenario.key, scenario.metric, *(join.fact_column for join in scenario.joins)}
    facts = {}
    amounts = {}
    for line, row in read_csv(scenario.fact_csv, columns):
        key = row[scenario.key]
        if not key.strip() or key in facts:
            raise ValueError(f"{scenario.fact_csv}:{line}: empty or duplicate fact key {key!r}")
        try:
            amounts[key] = parse_amount(row[scenario.metric])
        except ValueError as error:
            raise ValueError(f"{scenario.fact_csv}:{line}: {scenario.metric}: {error}") from error
        facts[key] = row

    base_total = sum(amounts.values())
    total = base_total
    row_count = len(facts)
    base = {"rows": row_count, "total": format_amount(total)}
    multiplicity = dict.fromkeys(facts, 1)
    first_fanout = None
    stages = []
    for join in scenario.joins:
        counts = Counter(
            row[join.right_column]
            for _, row in read_csv(join.csv_path, [join.right_column])
            if row[join.right_column] != ""
        )
        affected = []
        previous_total = total
        previous_rows = row_count
        for key, row in facts.items():
            join_value = row[join.fact_column]
            matches = counts.get(join_value, 0)
            if matches <= 1:
                continue  # A left join keeps one row even when there are no matches.
            before = multiplicity[key]
            after = before * matches
            multiplicity[key] = after
            added = after - before
            delta = amounts[key] * added
            row_count += added
            total += delta
            affected.append({
                "key": key, "join_value": join_value, "right_matches": matches,
                "rows_before": before, "rows_after": after, "delta": format_amount(delta),
            })
        if affected and first_fanout is None:
            first_fanout = join.name
        stages.append({
            "join": join.name, "rows_before": previous_rows, "rows_after": row_count,
            "total_before": format_amount(previous_total), "total_after": format_amount(total),
            "delta": format_amount(total - previous_total), "affected": affected,
        })

    return {
        "fact": scenario.fact_name, "key": scenario.key, "metric": scenario.metric,
        "base": base, "first_fanout": first_fanout, "stages": stages,
        "final": {"rows": row_count, "total": format_amount(total)},
        "delta": format_amount(total - base_total),
    }

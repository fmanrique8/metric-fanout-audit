# Metric Fanout Audit

Two orders total **$120.00**. A left join to refund events makes the same order amounts sum to **$220.00**. Order `101` had two matching refund rows, so its `$100.00` amount was counted twice.

Metric Fanout Audit checks one additive fact metric against an ordered set of left joins. It reports the first join that multiplies fact rows, the affected keys, and the exact change in the metric.

## Run the demo

From the repository root with Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
uv run --locked fanout-audit check examples/orders-refunds/fanout.toml
```

```text
FAIL  first fanout: orders -> refunds
order_total: 120.00 -> 220.00 (delta +100.00)
rows: 2 -> 3
FANOUT  orders -> refunds  rows 2 -> 3  order_total 120.00 -> 220.00
  order_id='101': 2 right matches, rows 1 -> 2, delta +100.00
```

This run exits `1`. Open [orders.csv](examples/orders-refunds/orders.csv) and [refunds.csv](examples/orders-refunds/refunds.csv) to see the two matches for `101`. The [repaired fixture](examples/orders-refunds/refunds-by-order.csv) groups refunds by `order_id` before joining:

```sh
uv run --locked fanout-audit check examples/orders-refunds/repaired.toml
```

```text
PASS  fact grain preserved
order_total: 120.00 -> 120.00 (delta 0.00)
rows: 2 -> 2
OK  orders -> refunds_by_order  rows 2 -> 2  order_total 120.00 -> 120.00
```

This run exits `0`. Add `--json` to either command for CI output. Invalid configuration, missing columns, duplicate or empty fact keys, and invalid metric values exit `2` with a diagnostic on stderr.

## Audit another join

Copy the [example TOML](examples/orders-refunds/fanout.toml). Set the fact CSV, its unique key, and a numeric column whose values have meaning when summed once per fact row. For each `[[joins]]`, name the right CSV and declare the two columns used in the equality predicate. Relative paths resolve from the TOML file. Join keys are case-sensitive text, so `001` and `1` are separate values. An empty join key has SQL `NULL` behavior. Amounts are fixed-point decimals with up to 18 integer digits and 6 fractional digits.

Each join is a left equality join from the original fact table to a right table. The tool counts matches by right key, then multiplies each fact row's current multiplicity by at least one. This produces the same row counts and sums as the declared joins without constructing the expanded table. Fanout still fails the check when signed amounts cancel and the total stays the same.

The audit covers single-column keys, local CSV snapshots, and one additive metric at the fact grain. Use representative exports for the joins you want to check. Queries with filters, joins between right tables, and metrics defined at a different grain fall outside this model.

## Verify correctness

```sh
uv run --locked python -m unittest discover -s tests -v
```

The tests compare the two demo totals against actual SQLite joins. They also check cumulative fanout, unmatched and empty keys, leading zeros, decimal precision, and offsetting amounts.

import csv
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from fanout_audit.audit import Join, Scenario, audit, read_scenario
from fanout_audit.cli import main


DEMO = Path(__file__).resolve().parents[1] / "examples" / "orders-refunds"


class DemoTests(unittest.TestCase):
    def test_failing_and_repaired_reports_match_sql(self):
        with sqlite3.connect(":memory:") as db:
            db.executescript("""
                create table orders (order_id text primary key, amount_cents integer);
                create table refunds (order_id text);
                create view refunds_by_order as
                    select order_id, count(*) as refund_count from refunds group by order_id;
            """)
            with (DEMO / "orders.csv").open(newline="") as orders:
                db.executemany("insert into orders values (?, ?)", (
                    (row["order_id"], int(row["order_total"].replace(".", "")))
                    for row in csv.DictReader(orders)
                ))
            with (DEMO / "refunds.csv").open(newline="") as refunds:
                db.executemany("insert into refunds values (?)", (
                    (row["order_id"],) for row in csv.DictReader(refunds)
                ))
            with (DEMO / "refunds-by-order.csv").open(newline="") as repaired:
                self.assertEqual(list(db.execute("select * from refunds_by_order order by order_id")), [
                    (row["order_id"], int(row["refund_count"])) for row in csv.DictReader(repaired)
                ])
            for scenario, table, exit_code, total in [
                ("fanout.toml", "refunds", 1, "220.00"),
                ("repaired.toml", "refunds_by_order", 0, "120.00"),
            ]:
                with self.subTest(scenario=scenario), redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(["check", str(DEMO / scenario), "--json"]), exit_code)
                    report = json.loads(output.getvalue())
                    rows, cents = db.execute(f"""
                        select count(*), sum(amount_cents)
                        from orders left join {table} using (order_id)
                    """).fetchone()
                    self.assertEqual(report["final"], {"rows": rows, "total": total})
                    self.assertEqual(int(total.replace(".", "")), cents)
                    self.assertEqual(report["first_fanout"], table if exit_code else None)

        report = audit(read_scenario(DEMO / "fanout.toml"))
        self.assertEqual(report["stages"][0]["affected"], [
            {"key": "101", "join_value": "101", "right_matches": 2,
             "rows_before": 1, "rows_after": 2, "delta": "100.00"}
        ])


class EdgeCaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.facts = self.root / "facts.csv"

    def make_join(self, name, contents):
        path = self.root / f"{name}.csv"
        path.write_text(contents, encoding="utf-8")
        return Join(name, path, "link", "link")

    def test_first_fanout_and_cumulative_multiplicity(self):
        self.facts.write_text("id,link,metric\n001,A,0.000001\n002,,20\n003,B,5\n", encoding="utf-8")
        joins = [
            self.make_join("dimension", "link\nA\nB\n"),
            self.make_join("two", 'link\nA\nA\n""\n""\n'),
            self.make_join("three", "link\nA\nA\nA\nextra\nextra\n"),
        ]
        result = audit(Scenario("facts", self.facts, "id", "metric", joins))
        self.assertEqual(result["first_fanout"], "two")
        self.assertEqual([stage["rows_after"] for stage in result["stages"]], [3, 4, 8])
        self.assertEqual(result["final"], {"rows": 8, "total": "25.000006"})
        self.assertEqual(result["stages"][2]["affected"][0]["key"], "001")
        self.assertEqual(result["stages"][2]["delta"], "0.000004")

    def test_fanout_even_when_signed_amounts_cancel(self):
        self.facts.write_text("id,link,metric\n1,A,10.00\n2,A,-10.00\n", encoding="utf-8")
        join = self.make_join("right", "link\nA\nA\n")
        result = audit(Scenario("facts", self.facts, "id", "metric", [join]))
        self.assertEqual(result["first_fanout"], "right")
        self.assertEqual(result["delta"], "0.00")
        self.assertEqual(result["final"]["rows"], 4)

    def test_bad_input_reports_error_instead_of_verdict(self):
        self.facts.write_text("order_id,order_total\n101,10.00\n101,5.00\n", encoding="utf-8")
        scenario = self.root / "scenario.toml"
        config = (DEMO / "fanout.toml").read_text(encoding="utf-8")
        scenario.write_text(config.replace("orders.csv", "facts.csv"), encoding="utf-8")
        with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(["check", str(scenario)]), 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("empty or duplicate fact key '101'", error.getvalue())

    def test_rejects_missing_columns_and_overprecise_amounts(self):
        join = self.make_join("right", "link\nA\n")
        scenario = Scenario("facts", self.facts, "id", "metric", [join])
        for contents, message in [
            ("id,link\n1,A\n", "missing columns metric"),
            ("id,link,metric\n1,A,0.0000001\n", "6 fractional digits"),
            ("id,link,metric\n,A,10\n", "empty or duplicate fact key"),
        ]:
            with self.subTest(contents=contents):
                self.facts.write_text(contents, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    audit(scenario)

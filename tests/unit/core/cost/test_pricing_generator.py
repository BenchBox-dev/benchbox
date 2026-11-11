from __future__ import annotations

import io
import json
import shutil
import sys
import urllib.error
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.message import Message
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
_SCRIPTS_DIR = str(REPO_ROOT / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import generate_pricing_data as generator

pytestmark = [
    pytest.mark.unit,
    pytest.mark.fast,
]

EVIDENCE_PATH = REPO_ROOT / "benchbox" / "core" / "cost" / "pricing_vendor_evidence.yaml"
PRICING_PATH = REPO_ROOT / "benchbox" / "core" / "cost" / "pricing_data.yaml"

GENERATED_SECTIONS = [
    "redshift_node_prices",
    "athena_price_per_tb",
    "synapse_dedicated_dwu_prices",
    "synapse_serverless_price_per_tb",
    "fabric_cu_prices",
    "databricks.azure",
    "provenance.redshift_node_prices",
    "provenance.athena_price_per_tb",
    "provenance.synapse_dedicated_dwu_prices",
    "provenance.synapse_serverless_price_per_tb",
    "provenance.fabric_cu_prices",
]

MANUAL_TABLES = [
    "snowflake_credit_prices",
    "bigquery_on_demand_prices",
    "firebolt_node_fbu_rates",
    "firebolt_fbu_price",
    "databricks_dbu_prices",
]


@pytest.fixture()
def scratch_copy(tmp_path):
    evidence = tmp_path / "pricing_vendor_evidence.yaml"
    pricing = tmp_path / "pricing_data.yaml"
    shutil.copy(EVIDENCE_PATH, evidence)
    shutil.copy(PRICING_PATH, pricing)
    return evidence, pricing


def test_check_passes_on_committed_data():
    assert generator.main(["--check"]) == 0


def test_regeneration_is_byte_stable(scratch_copy):
    evidence, pricing = scratch_copy
    before = pricing.read_bytes()
    assert generator.run_regenerate(evidence, pricing) == 0
    assert pricing.read_bytes() == before


def test_check_fails_on_edited_value(scratch_copy, capsys):
    evidence, pricing = scratch_copy
    text = pricing.read_text(encoding="utf-8")
    assert "  dw100c:\n    us: 1.51" in text
    pricing.write_text(text.replace("  dw100c:\n    us: 1.51", "  dw100c:\n    us: 1.52", 1), encoding="utf-8")
    assert generator.run_check(evidence, pricing) == 1
    assert "dw100c" in capsys.readouterr().err


def test_check_fails_when_manual_review_due_missing(scratch_copy):
    evidence, pricing = scratch_copy
    text = pricing.read_text(encoding="utf-8")
    due_line = "    manual_review_due: '2026-12-17'\n"
    assert text.count(due_line) == len(MANUAL_TABLES)
    pricing.write_text(text.replace(due_line, "", 1), encoding="utf-8")
    with pytest.raises(generator.PricingGeneratorError, match="manual_review_due"):
        generator.run_check(evidence, pricing)


def test_manual_tables_carry_review_due_dates():
    payload = yaml.safe_load(PRICING_PATH.read_text(encoding="utf-8"))
    for table in MANUAL_TABLES:
        due = payload["provenance"][table]["manual_review_due"]
        assert date.fromisoformat(due) > date(2026, 9, 18)


def test_evidence_regions_match_runtime_tables():
    from benchbox.core.cost import pricing

    evidence = generator.load_evidence()
    for table, runtime in (
        ("athena_price_per_tb", pricing.ATHENA_PRICE_PER_TB),
        ("synapse_serverless_price_per_tb", pricing.SYNAPSE_SERVERLESS_PRICE_PER_TB),
    ):
        regions = evidence[table]["regions"]
        assert set(regions) == set(runtime), table
        for region, price in regions.items():
            assert Decimal(str(runtime[region])) == Decimal(price), (table, region)


def test_region_tables_render_every_observed_region():
    evidence = generator.load_evidence()
    rendered = generator.render_all_sections(evidence)
    athena = "\n".join(rendered["athena_price_per_tb"])
    assert "us-east-1: 5.0" in athena
    assert "sa-east-1: 9.0" in athena
    assert "athena_price_per_tb: 5.0" not in athena
    serverless = "\n".join(rendered["synapse_serverless_price_per_tb"])
    assert "southeastasia: 6.75" in serverless
    assert "brazilsouth: 9.0" in serverless
    assert "synapse_serverless_price_per_tb: 5.0" not in serverless


def test_committed_file_has_one_key_for_every_generated_section():
    lines = PRICING_PATH.read_text(encoding="utf-8").split("\n")
    assert set(generator.SECTION_PATHS) == set(GENERATED_SECTIONS)
    for path, _ in generator.SECTION_PATHS.values():
        generator._key_line(lines, path)
    assert "GENERATED" not in PRICING_PATH.read_text(encoding="utf-8")


def test_dwu_levels_scale_linearly_from_base_rates():
    evidence = generator.load_evidence()
    rendered = generator.render_all_sections(evidence)
    block = "\n".join(rendered["synapse_dedicated_dwu_prices"])
    assert "  us: 453.00" in block
    assert "  ap: 27.15" in block
    assert "  ca: 66.50" in block
    bases = evidence["synapse_dedicated_dwu_prices"]["base_rates_100dwu"]
    expected_lines: list[str] = []
    for level, multiplier in generator.DWU_LEVELS:
        expected_lines.append(f"{level}:")
        for tier in generator.COST_TIERS:
            expected_lines.append(f"  {tier}: {Decimal(bases[tier]['price']) * multiplier:.2f}")
    assert rendered["synapse_dedicated_dwu_prices"] == expected_lines


def test_other_bucket_carries_the_sa_east_1_observation():
    evidence = generator.load_evidence()
    block = "\n".join(generator.render_all_sections(evidence)["redshift_node_prices"])
    nodes = evidence["redshift_node_prices"]["nodes"]
    for node in ("dc2.large", "ra3.16xlarge", "rg.12xlarge"):
        assert f"{node}:" in block
        assert f"  other: {nodes[node]['sa-east-1']}" in block


def test_evidence_strings_are_canonical():
    evidence = generator.load_evidence()
    section = evidence["redshift_node_prices"]
    for node, regions in section["nodes"].items():
        for region, price in regions.items():
            assert generator.canonical_decimal(price, min_places=2, max_places=4) == price, (node, region)
    for tier, cell in evidence["synapse_dedicated_dwu_prices"]["base_rates_100dwu"].items():
        assert generator.canonical_decimal(cell["price"], min_places=2, max_places=2) == cell["price"], tier
    for table in ("athena_price_per_tb", "synapse_serverless_price_per_tb"):
        for key, price in evidence[table]["regions"].items():
            assert generator.canonical_decimal(price, min_places=1, max_places=2) == price, (table, key)


def test_canonical_decimal_refuses_precision_loss():
    with pytest.raises(generator.PricingGeneratorError):
        generator.canonical_decimal("0.30005", min_places=2, max_places=4)
    with pytest.raises(generator.PricingGeneratorError):
        generator.canonical_decimal("not-a-number", min_places=2, max_places=4)


def test_canonical_decimal_refuses_non_positive_rates():
    with pytest.raises(generator.PricingGeneratorError):
        generator.canonical_decimal("0.00", min_places=2, max_places=4)
    with pytest.raises(generator.PricingGeneratorError):
        generator.canonical_decimal("-1.50", min_places=2, max_places=4)


def test_plan_price_update_keeps_date_on_match():
    assert generator.plan_price_update("0.30", "0.3000000000", min_places=2, max_places=4) is None
    assert generator.plan_price_update("0.40", "0.4", min_places=2, max_places=2) is None
    assert generator.plan_price_update("0.30", "0.31", min_places=2, max_places=4) == "0.31"


def test_refresh_compares_at_emitted_precision():
    assert generator.plan_price_update("3.0427", "3.0426700000", min_places=2, max_places=4) is None
    assert generator.plan_price_update("3.5401", "3.5401300000", min_places=2, max_places=4) is None
    assert generator.plan_price_update("2.42", "2.4194", min_places=2, max_places=2) is None
    assert generator.plan_price_update("3.0427", "3.0428", min_places=2, max_places=4) == "3.0428"
    with pytest.raises(generator.PricingGeneratorError):
        generator.plan_price_update("0.18", "0.00", min_places=2, max_places=2)
    with pytest.raises(generator.PricingGeneratorError):
        generator.plan_price_update("0.18", "not-a-rate", min_places=2, max_places=2)


def test_apply_evidence_updates_is_line_local(scratch_copy):
    evidence, _ = scratch_copy
    text = evidence.read_text(encoding="utf-8")
    update = generator.EvidenceUpdate(("redshift_node_prices", "nodes", "dc2.large"), "us-east-1", "0.25", "0.26")
    revised = generator.apply_evidence_updates(text, [update])
    assert "      us-east-1: '0.26'" in revised
    assert revised.count("us-east-1: '0.26'") == 1
    assert sum(a != b for a, b in zip(text.splitlines(), revised.splitlines(), strict=True)) == 1
    with pytest.raises(generator.PricingGeneratorError):
        stale = generator.EvidenceUpdate(("redshift_node_prices", "nodes", "dc2.large"), "us-east-1", "0.25", "0.27")
        generator.apply_evidence_updates(revised, [stale])


def test_splice_rejects_unknown_sections_and_missing_or_duplicate_keys():
    evidence = generator.load_evidence()
    rendered = generator.render_all_sections(evidence)
    original = PRICING_PATH.read_text(encoding="utf-8")
    with pytest.raises(generator.PricingGeneratorError):
        generator.splice_sections(original, {**rendered, "phantom": ["x: 1"]})
    with pytest.raises(generator.PricingGeneratorError):
        generator.splice_sections(original.replace("\nfabric_cu_prices:", "\nfabric_cu_prices_renamed:", 1), rendered)
    with pytest.raises(generator.PricingGeneratorError):
        generator.splice_sections(original + "\nathena_price_per_tb:\n  us-east-1: 5.0\n", rendered)


def test_splice_replaces_only_the_keyed_block():
    original = "a:\n  x: 1\nfabric_cu_prices:\n  us: 0.10\nb:\n  y: 2\n"
    rendered = {"fabric_cu_prices": ["us: 0.18"]}
    paths = {"fabric_cu_prices": (("fabric_cu_prices",), False)}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(generator, "SECTION_PATHS", paths)
        assert generator.splice_sections(original, rendered) == original.replace("us: 0.10", "us: 0.18")


def test_extract_aws_prices_indexes_by_attribute():
    products = {
        "sku-a": {
            "productFamily": "Compute Instance",
            "attributes": {"instanceType": "dc2.large", "usagetype": "Node:dc2.large"},
        },
        "sku-b": {
            "productFamily": "Compute Instance",
            "attributes": {"instanceType": "ra3.large", "usagetype": "Node:ra3.large"},
        },
        "sku-c": {
            "productFamily": "Redshift Managed Storage",
            "attributes": {"usagetype": "Storage"},
        },
    }
    terms = {
        "sku-a": {"term-a": {"priceDimensions": {"dim": {"unit": "Hrs", "pricePerUnit": {"USD": "0.2500000000"}}}}},
        "sku-b": {"term-b": {"priceDimensions": {"dim": {"unit": "Hrs", "pricePerUnit": {"USD": "0.5430000000"}}}}},
        "sku-c": {"term-c": {"priceDimensions": {"dim": {"unit": "GB-Mo", "pricePerUnit": {"USD": "0.024"}}}}},
    }
    found = generator.extract_aws_prices(
        products, terms, product_family="Compute Instance", unit="Hrs", key_attribute="usagetype"
    )
    assert found == {"Node:dc2.large": "0.2500000000", "Node:ra3.large": "0.5430000000"}


def test_select_azure_meter_requires_exact_match():
    rows = [
        {
            "serviceName": "Azure Synapse Analytics",
            "productName": "Azure Synapse Analytics SQL Provisioned DWU",
            "meterName": "100 DWU",
            "unitOfMeasure": "1/Hour",
            "currencyCode": "USD",
            "retailPrice": 1.51,
        }
    ]
    assert (
        generator.select_azure_meter(
            rows,
            service="Azure Synapse Analytics",
            region="eastus",
            product="Azure Synapse Analytics SQL Provisioned DWU",
            meter="100 DWU",
            unit="1/Hour",
        )
        == "1.51"
    )
    with pytest.raises(generator.PricingGeneratorError):
        generator.select_azure_meter(
            rows + [dict(rows[0])],
            service="Azure Synapse Analytics",
            region="eastus",
            product="Azure Synapse Analytics SQL Provisioned DWU",
            meter="100 DWU",
            unit="1/Hour",
        )


def test_select_fabric_cu_price_requires_agreement():
    rows = [
        {
            "productName": "Fabric Capacity",
            "meterName": "A Capacity Usage CU",
            "unitOfMeasure": "1 Hour",
            "currencyCode": "USD",
            "retailPrice": 0.18,
        },
        {
            "productName": "Fabric Capacity",
            "meterName": "B Capacity Usage CU",
            "unitOfMeasure": "1 Hour",
            "currencyCode": "USD",
            "retailPrice": 0.18,
        },
        {
            "productName": "Fabric Capacity",
            "meterName": "Capacity Overage Capacity Usage CU",
            "unitOfMeasure": "1 Hour",
            "currencyCode": "USD",
            "retailPrice": 0.54,
        },
        {
            "productName": "Fabric Capacity",
            "meterName": "Eventstream Custom Connector Capacity Usage CU",
            "unitOfMeasure": "1 Hour",
            "currencyCode": "USD",
            "retailPrice": 0.2574,
        },
    ]
    assert generator.select_fabric_cu_price(rows, region="eastus") == "0.18"
    rows[1]["retailPrice"] = 0.19
    with pytest.raises(generator.PricingGeneratorError):
        generator.select_fabric_cu_price(rows, region="eastus")


def test_refresh_with_unchanged_upstream_writes_nothing(scratch_copy, monkeypatch):
    evidence, pricing = scratch_copy
    before_evidence = evidence.read_bytes()
    before_pricing = pricing.read_bytes()
    monkeypatch.setattr(generator, "fetch_aws_region_offer", _recorded_aws_offer)
    monkeypatch.setattr(generator, "fetch_azure_region_items", _recorded_azure_rows)
    assert generator.run_refresh(evidence, pricing, today="2026-09-19") == 0
    assert evidence.read_bytes() == before_evidence
    assert pricing.read_bytes() == before_pricing


def test_refresh_fails_closed_when_node_missing_from_offer(scratch_copy, monkeypatch):
    evidence, pricing = scratch_copy

    def _missing_node_offer(url_template, region):
        publication, products, terms = _recorded_aws_offer(url_template, region)
        if "AmazonRedshift" in url_template:
            omitted = next(iter(products))
            del products[omitted]
            del terms[omitted]
        return publication, products, terms

    monkeypatch.setattr(generator, "fetch_aws_region_offer", _missing_node_offer)
    monkeypatch.setattr(generator, "fetch_azure_region_items", _recorded_azure_rows)
    with pytest.raises(generator.PricingGeneratorError):
        generator.run_refresh(evidence, pricing, today="2026-09-19")


def test_refresh_records_moved_price_with_new_date(scratch_copy, monkeypatch):
    evidence, pricing = scratch_copy
    monkeypatch.setattr(generator, "fetch_aws_region_offer", _recorded_aws_offer)
    monkeypatch.setattr(generator, "fetch_azure_region_items", _moved_fabric_rows)
    assert generator.run_refresh(evidence, pricing, today="2026-09-19") == 0
    revised = yaml.safe_load(evidence.read_text(encoding="utf-8"))
    assert revised["fabric_cu_prices"]["tiers"]["us"]["price"] == "0.19"
    assert revised["fabric_cu_prices"]["retrieved"] == "2026-09-19"
    assert revised["redshift_node_prices"]["retrieved"] == "2026-09-18"
    assert b"  us: 0.19" in pricing.read_bytes()


@pytest.fixture()
def http_replay(monkeypatch):
    clock = SimpleNamespace(now=0.0, sleeps=[], calls=[], actions=[], bodies=[])

    def sleep(delay):
        clock.sleeps.append(delay)
        clock.now += delay

    def open_url(request, *, timeout):
        clock.calls.append((request.full_url, timeout))
        action = clock.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        body = io.BytesIO(action if isinstance(action, bytes) else json.dumps(action).encode())
        clock.bodies.append(body)
        return body

    monkeypatch.setattr(generator, "mono_time", lambda: clock.now)
    monkeypatch.setattr(generator, "elapsed_seconds", lambda started: clock.now - started)
    monkeypatch.setattr(generator.time, "sleep", sleep)
    monkeypatch.setattr(generator.urllib.request, "urlopen", open_url)
    return clock


def _http_error(code=429, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://vendor.invalid/prices", code, "vendor error", headers, io.BytesIO(b"error"))


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_transient_http_error_retries_fresh_response_and_closes_errors(http_replay, code):
    error = _http_error(code)
    http_replay.actions = [error, {"price": 0.19}]
    assert generator._http_get_json("https://vendor.invalid/prices") == {"price": 0.19}
    assert http_replay.sleeps == [2.0]
    assert len(http_replay.calls) == 2
    assert error.fp.closed and all(body.closed for body in http_replay.bodies)


def test_persistent_throttling_exhausts_three_attempts(http_replay):
    errors = [_http_error() for _ in range(3)]
    http_replay.actions = list(errors)
    with pytest.raises(generator.PricingGeneratorError, match="after 3 attempt"):
        generator._http_get_json("https://vendor.invalid/prices")
    assert len(http_replay.calls) == 3
    assert http_replay.sleeps == [2.0, 4.0]
    assert all(error.fp.closed for error in errors)


def test_retry_after_seconds_controls_sleep(http_replay):
    http_replay.actions = [_http_error(retry_after="7"), {}]
    assert generator._http_get_json("https://vendor.invalid/prices") == {}
    assert http_replay.sleeps == [7.0]


def test_retry_after_http_date_is_interpreted_as_utc_timestamp():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    header = format_datetime(now + timedelta(seconds=7), usegmt=True)
    assert generator._retry_after_seconds(header, now=now) == 7
    assert generator._retry_after_seconds(header, now=now + timedelta(seconds=10)) == 0


def test_retry_after_http_date_controls_request_backoff(http_replay, monkeypatch):
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    parse = generator._retry_after_seconds
    monkeypatch.setattr(generator, "_retry_after_seconds", lambda value: parse(value, now=now))
    http_replay.actions = [_http_error(retry_after=format_datetime(now + timedelta(seconds=7), usegmt=True)), {}]
    assert generator._http_get_json("https://vendor.invalid/prices") == {}
    assert http_replay.sleeps == [7.0]


def test_paginated_azure_retry_keeps_each_page_once(http_replay):
    next_url = "https://vendor.invalid/prices?page=2"
    http_replay.actions = [
        {"Items": [{"meterName": "first"}], "NextPageLink": next_url},
        _http_error(),
        {"Items": [{"meterName": "second"}], "NextPageLink": None},
    ]
    assert generator.fetch_azure_region_items("Azure Synapse Analytics", "westeurope") == [
        {"meterName": "first"},
        {"meterName": "second"},
    ]
    assert [url for url, _ in http_replay.calls][1:] == [next_url, next_url]
    assert http_replay.sleeps == [2.0]


def test_late_success_is_rejected_after_budget(http_replay, monkeypatch):
    http_replay.actions = [{"price": 0.19}]
    open_url = generator.urllib.request.urlopen

    def late_response(request, *, timeout):
        http_replay.now = 121.0
        return open_url(request, timeout=timeout)

    monkeypatch.setattr(generator.urllib.request, "urlopen", late_response)
    with pytest.raises(generator.PricingGeneratorError, match="exhausted its HTTP retry budget"):
        generator._http_get_json("https://vendor.invalid/prices")
    assert len(http_replay.calls) == 1 and http_replay.bodies[0].closed


@pytest.mark.parametrize("header", [None, "invalid", "-1", "1.5"])
def test_invalid_retry_after_uses_bounded_backoff(http_replay, header):
    http_replay.actions = [_http_error(retry_after=header), {}]
    assert generator._http_get_json("https://vendor.invalid/prices") == {}
    assert http_replay.sleeps == [2.0]


@pytest.mark.parametrize("header", ["120", "1000000", "Wed, 30 Sep 2054 00:00:00 GMT"])
def test_over_budget_retry_after_does_not_retry_earlier(http_replay, header):
    error = _http_error(retry_after=header)
    http_replay.actions = [error, {}]
    with pytest.raises(generator.PricingGeneratorError, match="cannot honor Retry-After"):
        generator._http_get_json("https://vendor.invalid/prices")
    assert len(http_replay.calls) == 1 and not http_replay.sleeps and error.fp.closed


def test_retry_budget_is_rechecked_after_oversleep(http_replay, monkeypatch):
    http_replay.actions = [_http_error(), {}]
    monkeypatch.setattr(generator.time, "sleep", lambda delay: setattr(http_replay, "now", 121.0))
    with pytest.raises(generator.PricingGeneratorError, match="exhausted its HTTP retry budget"):
        generator._http_get_json("https://vendor.invalid/prices")
    assert len(http_replay.calls) == 1


def test_request_timeout_shrinks_to_remaining_retry_budget(http_replay, monkeypatch):
    http_replay.actions = [_http_error(retry_after="1"), {}]
    open_url = generator.urllib.request.urlopen

    def slow_first_request(request, *, timeout):
        if not http_replay.calls:
            http_replay.now = 70.0
        return open_url(request, timeout=timeout)

    monkeypatch.setattr(generator.urllib.request, "urlopen", slow_first_request)
    assert generator._http_get_json("https://vendor.invalid/prices") == {}
    assert [timeout for _, timeout in http_replay.calls] == [60, 49]


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_permanent_http_errors_do_not_retry(http_replay, code):
    error = _http_error(code)
    http_replay.actions = [error, {}]
    with pytest.raises(generator.PricingGeneratorError):
        generator._http_get_json("https://vendor.invalid/prices")
    assert len(http_replay.calls) == 1 and not http_replay.sleeps and error.fp.closed


@pytest.mark.parametrize("response", [[], b"invalid json", urllib.error.URLError("TLS verification failed")])
def test_invalid_evidence_and_transport_errors_remain_fatal(http_replay, response):
    http_replay.actions = [response, {}]
    with pytest.raises(generator.PricingGeneratorError):
        generator._http_get_json("https://vendor.invalid/prices")
    assert len(http_replay.calls) == 1 and not http_replay.sleeps


@pytest.mark.parametrize("moved", [False, True])
def test_refresh_after_throttling_preserves_real_drift(scratch_copy, monkeypatch, http_replay, moved):
    evidence, pricing = scratch_copy
    before = (evidence.read_bytes(), pricing.read_bytes())
    http_replay.actions = [_http_error()] + [{} for _ in range(30)]
    monkeypatch.setattr(generator, "fetch_aws_region_offer", _recorded_aws_offer)

    def fetch_rows(service, region):
        generator._http_get_json("https://vendor.invalid/prices")
        return (_moved_fabric_rows if moved else _recorded_azure_rows)(service, region)

    monkeypatch.setattr(generator, "fetch_azure_region_items", fetch_rows)
    assert generator.run_refresh(evidence, pricing, today="2026-09-30") == 0
    if moved:
        assert b"  us: 0.19" in pricing.read_bytes()
        assert generator.run_check(evidence, pricing) == 0
        assert (evidence.read_bytes(), pricing.read_bytes()) != before
    else:
        assert (evidence.read_bytes(), pricing.read_bytes()) == before


def test_exhausted_refresh_leaves_files_unchanged(scratch_copy, monkeypatch, http_replay):
    evidence, pricing = scratch_copy
    before = (evidence.read_bytes(), pricing.read_bytes())
    http_replay.actions = [_http_error() for _ in range(3)]
    pending_prices = []

    def changed_aws_offer(url_template, region):
        publication, products, terms = _recorded_aws_offer(url_template, region)
        for offers in terms.values():
            for offer in offers.values():
                for dimension in offer["priceDimensions"].values():
                    dimension["pricePerUnit"]["USD"] = "0.33"
                    pending_prices.append(region)
        return publication, products, terms

    monkeypatch.setattr(generator, "fetch_aws_region_offer", changed_aws_offer)
    monkeypatch.setattr(
        generator, "fetch_azure_region_items", lambda *_: generator._http_get_json("https://vendor.invalid")
    )
    with pytest.raises(generator.PricingGeneratorError):
        generator.run_refresh(evidence, pricing)
    assert pending_prices
    assert (evidence.read_bytes(), pricing.read_bytes()) == before


def _recorded_aws_offer(url_template, region):
    evidence = generator.load_evidence()
    if "AmazonRedshift" in url_template:
        section = evidence["redshift_node_prices"]
        products = {
            f"sku-{node}": {
                "productFamily": "Compute Instance",
                "attributes": {"instanceType": node, "usagetype": f"Node:{node}"},
            }
            for node in generator.REDSHIFT_NODES
        }
        terms = {
            f"sku-{node}": {
                "term": {
                    "priceDimensions": {
                        "dim": {"unit": "Hrs", "pricePerUnit": {"USD": f"{section['nodes'][node][region]}0000000"}}
                    }
                }
            }
            for node in generator.REDSHIFT_NODES
        }
        return section["upstream_published"], products, terms
    section = evidence["athena_price_per_tb"]
    products = {
        "sku-athena": {"productFamily": "Athena Queries", "attributes": {"usagetype": "DataScannedInTB"}},
    }
    terms = {
        "sku-athena": {
            "term": {
                "priceDimensions": {
                    "dim": {"unit": "Terabytes", "pricePerUnit": {"USD": f"{section['regions'][region]}000000000"}}
                }
            }
        }
    }
    return section["upstream_published"], products, terms


def _recorded_azure_rows(service, region):
    evidence = generator.load_evidence()
    rows = []
    dedicated = evidence["synapse_dedicated_dwu_prices"]
    for tier, cell in dedicated["base_rates_100dwu"].items():
        if service == "Azure Synapse Analytics" and region == cell["region"]:
            rows.append(
                _azure_row(service, dedicated["match_product"], dedicated["match_meter"], cell["price"], "1/Hour")
            )
    serverless = evidence["synapse_serverless_price_per_tb"]
    if service == "Azure Synapse Analytics" and region in serverless["regions"]:
        rows.append(
            _azure_row(
                service, serverless["match_product"], serverless["match_meter"], serverless["regions"][region], "1 TB"
            )
        )
    fabric = evidence["fabric_cu_prices"]
    for tier, cell in fabric["tiers"].items():
        if service == "Microsoft Fabric" and region == cell["region"]:
            rows.append(_azure_row(service, fabric["match_product"], f"{tier} Capacity Usage CU", cell["price"]))
    databricks = evidence["databricks_dbu_prices_azure"]
    if region == databricks["region"]:
        seen: set[tuple[str, str, str]] = set()
        for key, cell in databricks["cells"].items():
            triple = (cell["service"], cell["product"], cell["meter"])
            if cell["service"] == service and triple not in seen:
                seen.add(triple)
                rows.append(_azure_row(service, cell["product"], cell["meter"], cell["price"]))
    return rows


def _azure_row(service, product, meter, price, unit="1 Hour"):
    return {
        "serviceName": service,
        "productName": product,
        "meterName": meter,
        "unitOfMeasure": unit,
        "currencyCode": "USD",
        "retailPrice": float(price),
    }


def _moved_fabric_rows(service, region):
    rows = _recorded_azure_rows(service, region)
    for row in rows:
        if service == "Microsoft Fabric" and region == "eastus":
            row["retailPrice"] = 0.19
    return rows


def test_splice_rejects_inline_value_for_body_section():
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(generator, "SECTION_PATHS", {"fabric_cu_prices": (("fabric_cu_prices",), False)})
        with pytest.raises(generator.PricingGeneratorError):
            generator.splice_sections("fabric_cu_prices: {us: 0.1}\nb: 1\n", {"fabric_cu_prices": ["us: 0.18"]})



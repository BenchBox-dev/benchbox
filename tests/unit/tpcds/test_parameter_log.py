# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark(TM) DS (TPC-DS) - Copyright (c) Transaction Processing Performance Council

# Licensed under the MIT License. See LICENSE file in the project root for details.

import pytest

from benchbox.core.tpcds.parameter_log import TemplateParameters, parse_dsqgen_parameter_log

pytestmark = [
    pytest.mark.unit,
    pytest.mark.medium,
    pytest.mark.tpcds,
]

Q39_LOG = (
    "BEGIN STREAM 0\nTemplate: query39.tpl\n\t__LIMITA.01 = \n\tYEAR.01 = 2002\n\tMONTH.01 = 4\n"
    "\t_END.01 = \n\t__LIMITC.01 = limit %d\n\t__LIMITB.01 = \n\nEND STREAM 0\n"
)

Q70_LOG = (
    "BEGIN STREAM 0\nTemplate: query70.tpl\n\t__LIMITA.01 = \n\t_LIMITC.01 = limit 100\n\t_LIMITB.01 = \n"
    "\t_LIMITA.01 = \n\t_LIMIT.01 = 100\n\tDMS.01 = 1176\n\t_END.01 = \n\t__LIMITC.01 = limit %d\n"
    "\t__LIMITB.01 = \n\nEND STREAM 0\n"
)


Q8_LOG_EXCERPT = (
    "BEGIN STREAM 0\nTemplate: query8.tpl\n\t__LIMITA.01 = \n\t_LIMITC.01 = limit 100\n\t_LIMITB.01 = \n"
    "\t_LIMITA.01 = \n\t_LIMIT.01 = 100\n\tZIP.01 = 37650\n\tZIP.02 = 11903\n\tZIP.03 = 52277\n"
    "\tQOY.01 = 2\n\tYEAR.01 = 2001\n\t_END.01 = \n\t__LIMITC.01 = limit %d\n\t__LIMITB.01 = \n\nEND STREAM 0\n"
)

MULTI_STREAM_LOG = (
    "BEGIN STREAM 0\nTemplate: query39.tpl\n\tYEAR.01 = 2002\n\tMONTH.01 = 4\n\nEND STREAM 0\n"
    "BEGIN STREAM 1\nTemplate: query39.tpl\n\tYEAR.01 = 1999\n\tMONTH.01 = 2\n\nEND STREAM 1\n"
)


class TestParseDsqgenParameterLog:
    def test_q39_names_and_values(self):
        streams = parse_dsqgen_parameter_log(Q39_LOG)

        assert list(streams) == [0]
        (template,) = streams[0]
        assert (template.stream, template.query_id, template.variant) == (0, 39, None)
        assert template.substitutions == {"YEAR.01": "2002", "MONTH.01": "4"}
        assert template.values["_END.01"] == ""
        assert template.values["__LIMITC.01"] == "limit %d"

    def test_q70_names_and_values(self):
        (template,) = parse_dsqgen_parameter_log(Q70_LOG)[0]

        assert template.query_id == 70
        assert template.substitutions == {"DMS.01": "1176"}
        assert template.values["_LIMIT.01"] == "100"
        assert template.values["_LIMITC.01"] == "limit 100"

    def test_q8_keeps_repeated_indexed_names_in_order(self):
        (template,) = parse_dsqgen_parameter_log(Q8_LOG_EXCERPT)[0]

        assert list(template.substitutions) == ["ZIP.01", "ZIP.02", "ZIP.03", "QOY.01", "YEAR.01"]
        assert template.substitutions["ZIP.01"] == "37650"
        assert template.substitutions["YEAR.01"] == "2001"

    def test_stream_number_is_part_of_the_result(self):
        streams = parse_dsqgen_parameter_log(MULTI_STREAM_LOG)

        assert sorted(streams) == [0, 1]
        assert streams[0][0].stream == 0
        assert streams[1][0].stream == 1
        assert streams[0][0].substitutions == {"YEAR.01": "2002", "MONTH.01": "4"}
        assert streams[1][0].substitutions == {"YEAR.01": "1999", "MONTH.01": "2"}

    def test_template_variant_suffix_is_parsed(self):
        log = "BEGIN STREAM 0\nTemplate: query14a.tpl\n\tYEAR.01 = 1999\n\nEND STREAM 0\n"

        (template,) = parse_dsqgen_parameter_log(log)[0]

        assert (template.query_id, template.variant) == (14, "a")

    @pytest.mark.parametrize(
        "template", ["../query_variants/query5a.tpl", "..\\query_variants\\query5a.tpl", "query_templates/query5a.tpl"]
    )
    def test_template_path_is_parsed(self, template):
        log = f"BEGIN STREAM 0\nTemplate: {template}\n\tYEAR.01 = 2002\n\nEND STREAM 0\n"

        (parsed,) = parse_dsqgen_parameter_log(log)[0]

        assert (parsed.query_id, parsed.variant) == (5, "a")

    def test_value_may_contain_spaces_and_equals(self):
        log = "BEGIN STREAM 0\nTemplate: query1.tpl\n\tREASON.01 = reason 28\n\tX.01 = a=b\n\nEND STREAM 0\n"

        (template,) = parse_dsqgen_parameter_log(log)[0]

        assert template.substitutions == {"REASON.01": "reason 28", "X.01": "a=b"}

    @pytest.mark.parametrize(
        "log",
        [
            "Template: query1.tpl\n\tYEAR.01 = 2000\n",
            "BEGIN STREAM 0\nTemplate: query1.tpl\n\tYEAR.01 = 2000\n",
            "BEGIN STREAM 0\n\tYEAR.01 = 2000\nEND STREAM 0\n",
            "BEGIN STREAM 0\nTemplate: query1.tpl\n\tYEAR.01 = 2000\n\tYEAR.01 = 2001\nEND STREAM 0\n",
            "BEGIN STREAM 0\nBEGIN STREAM 0\nEND STREAM 0\n",
            "BEGIN STREAM 0\nTemplate: query1.tpl\n\tnot a parameter line\nEND STREAM 0\n",
            "BEGIN STREAM 0\nTemplate: query1.tpl\nEND STREAM 1\n",
            "BEGIN STREAM 0\nTemplate: query1.tpl\n\tYEAR.01 = 2000\nBEGIN STREAM 1\nTemplate: query1.tpl\nEND STREAM 1\n",
        ],
    )
    def test_malformed_logs_raise(self, log):
        with pytest.raises(ValueError):
            parse_dsqgen_parameter_log(log)

    def test_empty_log_has_no_streams(self):
        assert parse_dsqgen_parameter_log("") == {}


def _binary_or_skip():
    from benchbox.core.tpcds.c_tools import DSQGenBinary, TPCDSError

    try:
        return DSQGenBinary()
    except (TPCDSError, FileNotFoundError, RuntimeError) as exc:
        pytest.skip(f"dsqgen binary or templates unavailable: {exc}")


@pytest.fixture(scope="module")
def dsqgen():
    return _binary_or_skip()


class TestGenerateParameterLog:
    def test_same_seed_repeats_and_different_seed_differs(self, dsqgen):
        first = dsqgen.generate_parameter_log(39, seed=7)
        again = dsqgen.generate_parameter_log(39, seed=7)
        others = [dsqgen.generate_parameter_log(39, seed=seed) for seed in range(8, 16)]

        assert isinstance(first, TemplateParameters)
        assert first == again
        assert any(other.values != first.values for other in others)

    def test_stream_is_reported_and_independent_of_stream_count(self, dsqgen):
        stream0 = dsqgen.generate_parameter_log(39, seed=7)
        stream2 = dsqgen.generate_parameter_log(39, seed=7, stream_id=2)
        stream3 = dsqgen.generate_parameter_log(39, seed=7, stream_id=3)

        assert (stream0.stream, stream2.stream, stream3.stream) == (0, 2, 3)
        assert stream2 == dsqgen.generate_parameter_log(39, seed=7, stream_id=2)
        assert stream0.substitutions.keys() == stream2.substitutions.keys() == stream3.substitutions.keys()

    def test_logged_year_appears_in_generated_sql(self, dsqgen):
        params = dsqgen.generate_parameter_log(39, seed=7)
        sql = dsqgen.generate(39, seed=7)

        squashed = "".join(sql.split())
        assert f"d_year={params.substitutions['YEAR.01']}" in squashed
        assert f"inv1.d_moy={params.substitutions['MONTH.01']}" in squashed

    def test_generate_does_not_request_a_log(self, dsqgen, monkeypatch):
        seen: list[list[str]] = []
        real_run = __import__("subprocess").run

        def spy(cmd, *args, **kwargs):
            seen.append(list(cmd))
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr("benchbox.core.tpcds.c_tools.subprocess.run", spy)
        dsqgen.clear_cache()
        dsqgen.generate(3, seed=11)

        assert seen
        assert not any(arg.upper().lstrip("-/") == "LOG" for cmd in seen for arg in cmd)
        assert not any(arg.upper().lstrip("-/") == "STREAMS" for cmd in seen for arg in cmd)

    @pytest.mark.parametrize("query_id", ["14a", "14b", "23a", "23b", "24a", "24b", "39a", "39b"])
    @pytest.mark.parametrize("stream_id", [0, 1])
    def test_multi_part_variants_resolve_to_their_template(self, dsqgen, query_id, stream_id):
        params = dsqgen.generate_parameter_log(query_id, seed=7, stream_id=stream_id)

        assert params.query_id == int(query_id[:-1])
        assert params.stream == stream_id

    def test_scale_dependent_parameters_follow_the_requested_scale(self, dsqgen):

        small = dsqgen.generate_parameter_log(44, seed=7, scale_factor=1)
        large = dsqgen.generate_parameter_log(44, seed=7, scale_factor=10)

        assert small.substitutions != large.substitutions

    @pytest.mark.parametrize("query_id", [0, 100])
    def test_invalid_query_id_raises(self, dsqgen, query_id):
        with pytest.raises(ValueError):
            dsqgen.generate_parameter_log(query_id)

    def test_negative_stream_raises(self, dsqgen):
        with pytest.raises(ValueError):
            dsqgen.generate_parameter_log(39, stream_id=-1)

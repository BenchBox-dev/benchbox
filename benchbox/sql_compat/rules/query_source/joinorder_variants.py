from __future__ import annotations

from benchbox.sql_compat.actions import CompatAction
from benchbox.sql_compat.context import Phase
from benchbox.sql_compat.decision import (
    CompatibilityDecision,
    FailureMode,
    SelectVariantPayload,
    SupportLevel,
)
from benchbox.sql_compat.registry import REGISTRY

_B = "joinorder"
_P = Phase.QUERY_SOURCE

QUESTDB_VARIANTS: dict[str, str] = {
    "8c": """\
SELECT MIN(a1.name) AS writer_pseudo_name, MIN(t.title) AS movie_title
FROM role_type AS rt
JOIN cast_info AS ci ON ci.role_id = rt.id
JOIN aka_name AS a1 ON a1.person_id = ci.person_id
JOIN title AS t ON ci.movie_id = t.id
JOIN movie_companies AS mc ON t.id = mc.movie_id AND ci.movie_id = mc.movie_id
JOIN company_name AS cn ON mc.company_id = cn.id
JOIN name AS n1 ON a1.person_id = n1.id AND n1.id = ci.person_id
WHERE rt.role = 'writer'
  AND cn.country_code = '[us]';""",
    "8d": """\
SELECT MIN(an1.name) AS costume_designer_pseudo, MIN(t.title) AS movie_with_costumes
FROM role_type AS rt
JOIN cast_info AS ci ON ci.role_id = rt.id
JOIN aka_name AS an1 ON an1.person_id = ci.person_id
JOIN title AS t ON ci.movie_id = t.id
JOIN movie_companies AS mc ON t.id = mc.movie_id AND ci.movie_id = mc.movie_id
JOIN company_name AS cn ON mc.company_id = cn.id
JOIN name AS n1 ON an1.person_id = n1.id AND n1.id = ci.person_id
WHERE rt.role = 'costume designer'
  AND cn.country_code = '[us]';""",
    "16b": """\
SELECT MIN(an.name) AS cool_actor_pseudonym, MIN(t.title) AS series_named_after_char
FROM keyword AS k
JOIN movie_keyword AS mk ON mk.keyword_id = k.id
JOIN title AS t ON t.id = mk.movie_id
JOIN movie_companies AS mc ON t.id = mc.movie_id AND mc.movie_id = mk.movie_id
JOIN company_name AS cn ON mc.company_id = cn.id
JOIN cast_info AS ci ON ci.movie_id = t.id AND ci.movie_id = mc.movie_id AND ci.movie_id = mk.movie_id
JOIN aka_name AS an ON an.person_id = ci.person_id
JOIN name AS n ON an.person_id = n.id AND n.id = ci.person_id
WHERE k.keyword = 'character-name-in-title'
  AND cn.country_code = '[us]';""",
    "16c": """\
SELECT MIN(an.name) AS cool_actor_pseudonym, MIN(t.title) AS series_named_after_char
FROM keyword AS k
JOIN movie_keyword AS mk ON mk.keyword_id = k.id
JOIN title AS t ON t.id = mk.movie_id
JOIN movie_companies AS mc ON t.id = mc.movie_id AND mc.movie_id = mk.movie_id
JOIN company_name AS cn ON mc.company_id = cn.id
JOIN cast_info AS ci ON ci.movie_id = t.id AND ci.movie_id = mc.movie_id AND ci.movie_id = mk.movie_id
JOIN aka_name AS an ON an.person_id = ci.person_id
JOIN name AS n ON an.person_id = n.id AND n.id = ci.person_id
WHERE k.keyword = 'character-name-in-title'
  AND t.episode_nr < 100
  AND cn.country_code = '[us]';""",
    "16d": """\
SELECT MIN(an.name) AS cool_actor_pseudonym, MIN(t.title) AS series_named_after_char
FROM keyword AS k
JOIN movie_keyword AS mk ON mk.keyword_id = k.id
JOIN title AS t ON t.id = mk.movie_id
JOIN movie_companies AS mc ON t.id = mc.movie_id AND mc.movie_id = mk.movie_id
JOIN company_name AS cn ON mc.company_id = cn.id
JOIN cast_info AS ci ON ci.movie_id = t.id AND ci.movie_id = mc.movie_id AND ci.movie_id = mk.movie_id
JOIN aka_name AS an ON an.person_id = ci.person_id
JOIN name AS n ON an.person_id = n.id AND n.id = ci.person_id
WHERE k.keyword = 'character-name-in-title'
  AND t.episode_nr >= 5
  AND t.episode_nr < 100
  AND cn.country_code = '[us]';""",
    "17a": """\
SELECT MIN(n.name) AS member_in_charnamed_american_movie, MIN(n.name) AS a1
FROM keyword AS k
JOIN movie_keyword AS mk ON mk.keyword_id = k.id
JOIN title AS t ON t.id = mk.movie_id
JOIN movie_companies AS mc ON t.id = mc.movie_id AND mc.movie_id = mk.movie_id
JOIN company_name AS cn ON mc.company_id = cn.id
JOIN cast_info AS ci ON ci.movie_id = t.id AND ci.movie_id = mc.movie_id AND ci.movie_id = mk.movie_id
JOIN name AS n ON n.id = ci.person_id
WHERE k.keyword = 'character-name-in-title'
  AND cn.country_code = '[us]'
  AND n.name LIKE 'B%';""",
    "17e": """\
SELECT MIN(n.name) AS member_in_charnamed_movie
FROM keyword AS k
JOIN movie_keyword AS mk ON mk.keyword_id = k.id
JOIN title AS t ON t.id = mk.movie_id
JOIN movie_companies AS mc ON t.id = mc.movie_id AND mc.movie_id = mk.movie_id
JOIN company_name AS cn ON mc.company_id = cn.id
JOIN cast_info AS ci ON ci.movie_id = t.id AND ci.movie_id = mc.movie_id AND ci.movie_id = mk.movie_id
JOIN name AS n ON n.id = ci.person_id
WHERE k.keyword = 'character-name-in-title'
  AND cn.country_code = '[us]';""",
}

for _query_id, _sql in QUESTDB_VARIANTS.items():
    REGISTRY.register(
        CompatibilityDecision(
            rule_id=f"query_source.questdb.joinorder.{_query_id}_selective_join_order",
            action=CompatAction.SELECT_VARIANT,
            support_level=SupportLevel.REWRITTEN,
            failure_mode=FailureMode.PERFORMANCE_REGRESSION,
            payload=SelectVariantPayload(variant_key="questdb", variant_sql=_sql),
            reason=(
                "QuestDB runs inner joins in the written order; the canonical text starts at company_name, whose "
                "country filter keeps 85k rows and fans out through movie_companies into the 36M-row cast_info, "
                "taking up to 52s against 8s when the most selective filtered table leads"
            ),
        ),
        _P,
        "questdb",
        benchmark=_B,
        query_id=_query_id,
    )

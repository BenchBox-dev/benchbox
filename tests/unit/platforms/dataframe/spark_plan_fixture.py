SPARK_EXTENDED_EXPLAIN = """== Parsed Logical Plan ==
'Sort ['g ASC NULLS FIRST], true
+- Aggregate [g#1L], [g#1L, count(1) AS count#2L]
   +- Filter (id#0L > cast(5 as bigint))
      +- Project [id#0L, (id#0L % cast(3 as bigint)) AS g#1L]
         +- Range (0, 100, step=1, splits=Some(1))

== Analyzed Logical Plan ==
g: bigint, count: bigint
Sort [g#1L ASC NULLS FIRST], true
+- Aggregate [g#1L], [g#1L, count(1) AS count#2L]
   +- Filter (id#0L > cast(5 as bigint))
      +- Project [id#0L, (id#0L % cast(3 as bigint)) AS g#1L]
         +- Range (0, 100, step=1, splits=Some(1))

== Optimized Logical Plan ==
Sort [g#1L ASC NULLS FIRST], true
+- Aggregate [g#1L], [g#1L, count(1) AS count#2L]
   +- Project [(id#0L % 3) AS g#1L]
      +- Filter (id#0L > 5)
         +- Range (0, 100, step=1, splits=Some(1))

== Physical Plan ==
AdaptiveSparkPlan isFinalPlan=false
+- Sort [g#1L ASC NULLS FIRST], true, 0
   +- HashAggregate(keys=[g#1L], functions=[count(1)], output=[g#1L, count#2L])
      +- HashAggregate(keys=[g#1L], functions=[partial_count(1)], output=[g#1L, count#7L])
         +- Project [(id#0L % 3) AS g#1L]
            +- Filter (id#0L > 5)
               +- Range (0, 100, step=1, splits=1)

"""


class FakeSparkFrame:
    def __init__(self, plan_text: str = SPARK_EXTENDED_EXPLAIN) -> None:
        self.plan_text = plan_text

    def explain(self, extended: bool = False) -> None:
        print(self.plan_text, end="")

    def collect(self) -> list[tuple[int, int]]:
        return [(0, 31), (1, 32), (2, 31)]

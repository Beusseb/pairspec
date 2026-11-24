# Mutation testing of the engine

The engine's own tests are mutation-tested with [mutmut](https://github.com/boxed/mutmut) on
`ipog.py` (matrix generation, unmasking, coverage), `bva.py` (boundary values) and `schema.py`
(spec parsing and oracle evaluation). Configuration: `[tool.mutmut]` in `pyproject.toml`.

```bash
uvx --with pytest --with pytest-cov --with-editable . mutmut run --max-children 8
uvx --with pytest --with-editable . mutmut results        # list survivors
uvx --with pytest --with-editable . mutmut show <name>    # diff of one mutant
```

Measured on an Apple M3 Pro (12 cores), mutmut 3.8.1, Python 3.14: about 3 minutes with
8 workers. It is not run in CI for that reason (a 2-core runner would take several times longer).

## Score

| Module | Before | After |
| :--- | ---: | ---: |
| `ipog.py` | 548/733 (74.8 %) | 627/733 (85.5 %) |
| `bva.py` | 177/225 (78.7 %) | 210/225 (93.3 %) |
| `schema.py` | 458/749 (61.1 %) | 491/731 (67.2 %) |
| **Total** | **1183/1707 (69.3 %)** | **1328/1689 (78.6 %)** |

"After" has 18 fewer mutants: the run found dead branches in the expression evaluator
(`True`, `False` and `None` handled as names, while Python parses them as constants), which were removed.

## What the run found

**The old tests checked the generator with the generator.** With `ExclusionRule.is_violated`
replaced by `return False`, the fintech matrix contained 25 forbidden cases out of 57 and every
test still passed: `verify_coverage` reuses the same exclusion predicate, so it reported 0
violations. Only `scripts/check_static.py` noticed, indirectly, because the committed lockfile
changed. [`tests/test_independent_oracles.py`](../tests/test_independent_oracles.py) now
computes every expected value outside the engine (exclusions, covered tuples, rule priorities
and unmasking re-stated by hand from the spec, BVA lists written from the rule, case IDs from
`hashlib` alone).

Other gaps closed, each a mutant that survived before and is killed now:

| Mutant | What it broke |
| :--- | :--- |
| `schema` `ExclusionRule.is_violated` #4 | exclusions ignored |
| `ipog` `verify_coverage` #29, #56 | violations counted twice; infeasible tuples counted as covered |
| `ipog` `compute_case_id` #4 | case ID format changed (every committed lockfile would drift) |
| `ipog` `_unmask_oracle_cases` #37 | unmasking stops at the first rule that nothing masks |
| `ipog` `generate_matrix` #121, #136 | case IDs not tied to the spec name; spec version dropped |
| `bva` `expand_parameter_values` #38, #118 | default int step; float step larger than the range |
| `bva` `_decimals_for_step` #3 | rounding of the middle value |

## What survives (361), and why it is left

- **Equivalent mutants.** `<` vs `<=` in the BVA guards (the duplicate is removed by
  de-duplication), `zip(strict=...)` on lists of equal length, `"utf-8"` vs `"UTF-8"`, initial
  values overwritten before use.
- **Error messages** in spec parsing and the evaluator (`from_dict`, `parse_spec_text`,
  `_eval_node`): a mutated message still raises the same exception type.
- **Greedy IPOG heuristics** (`_build_rows`, `_complete_assignment`, usage counters in
  `_unmask_oracle_cases`): they change tie-breaking, hence which valid matrix comes out and its
  size, not its validity. Sizes are pinned for the fintech spec (53, 38, 137) and the committed
  HTTP demo lockfile is checked byte for byte by `scripts/check_static.py` in CI; mutmut runs only
  `tests/`, so it does not see that second guard.
- **Serialisation details** (`canonical_dict`, `to_dict`, `fingerprint`): partly covered by the
  lockfile drift tests, the rest is formatting.

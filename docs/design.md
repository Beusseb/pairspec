# Design notes

Why the engine is built the way it is, and what it does not do. Code references are to
`src/pairspec/`.

## Constraints that drove every choice

1. **The oracle comes from the spec, never from the code.** A test whose expected value is read
   from the implementation passes on the implementation's bugs (measured in
   [`examples/fintech_payout/mutation_benchmark.py`](../examples/fintech_payout/mutation_benchmark.py):
   0 of 93 mutants detected that way).
2. **Same spec, same output, byte for byte.** Generated cases are committed and reviewed; a
   generator that reshuffles on every run makes diffs meaningless.
3. **Standard library only** in the engine, so it runs anywhere Python 3.11 runs, including as
   an MCP server started by an agent.

## Why IPOG

The matrix is built with IPOG ("In-Parameter-Order-General", Lei et al., 2007), in
[`ipog.py`](../src/pairspec/ipog.py): start with the t-wise product of the first t parameters,
then add one parameter at a time, first extending existing rows (horizontal growth), then
adding rows for the tuples still uncovered (vertical growth).

- **Deterministic by construction.** Parameters are ordered by domain size, then name. When two
  values cover as many new tuples, one strategy takes the first value, the other the least used
  one; the engine builds the matrix both ways and keeps the smaller (the first wins a draw).
- **Any strength t.** The same code does t = 1, 2, 3; t equal to the number of parameters is the
  filtered cartesian product.
- **Constraints fit in naturally.** Exclusions are checked on partial rows (`_Feasibility`), and
  the remaining "don't care" slots are filled by a small backtracking solver
  (`_complete_assignment`, most-constrained parameter first, least-used value first).

Alternatives considered:

| Option | Why not |
| :--- | :--- |
| Call PICT (C++) or ACTS (Java) | Good tools, but an external binary or a JVM breaks constraint 3. |
| AETG-style randomised greedy | Needs a seed to be reproducible, and output changes with the RNG implementation. |
| Orthogonal arrays | Fixed tables, awkward with unequal domain sizes and with exclusions. |
| Random sampling | No coverage guarantee. Same size as the pairwise suite, it misses mutants in 159 seeds out of 200 in the benchmark. |
| Optimal covering arrays (SAT/ILP) | Smaller matrices, but slow and a solver dependency; a few extra cases are cheap. |

Greedy IPOG is not optimal: the matrix can be a few rows larger than the best known covering array.

## Oracle masking, and why plain pairwise is not enough

t-wise coverage guarantees that every pair of values **appears** in some case. It does not
guarantee that its **effect can be seen**. Rules have priorities; when a stronger rule
(typically a rejection) matches in the same case, it overrides the outcome, and a defect in the
weaker rule is invisible in that case.

Example from the fintech spec: the 2FA rule (`channel == 'mobile' and amount >= 5000`) is
observable only when no rejection rule fires. If every case with `mobile, 5000` also happens to be
`unverified` (KYC cap) or `SEPA, USD` (currency rule), the suite is pairwise-complete and still
cannot catch a 2FA bug.

The fix, in `_unmask_oracle_cases`: for each rule, and each value tuple over the variables of
its condition, check that at least one case holds the tuple while no stronger rule that
overrides all of its expected keys matches. If none does, solve for one (same backtracking
solver, with the masking rules added as temporary exclusions) and append it. Tuples that are
only reachable under a stronger rule are skipped: there is nothing to observe.

Measured effect on the fintech spec: 53 cases instead of 38 (11 unmasking cases on top of a
42-row base; the 38-row base would have needed 17, and the smaller total wins). 6 mutants are
killed only thanks to them, including the off-by-one on the KYC cap and on the 2FA threshold.

Limit: a rule whose variables span more than 512 value tuples is not unmasked
(`_MAX_UNMASK_TUPLES`), to keep generation bounded.

## Lockfile and stable IDs

`pairspec generate --lock` writes `pairspec.lock.json` ([`lockfile.py`](../src/pairspec/lockfile.py)):
spec name, version and fingerprint, then every case with its inputs, expected outcome and the
rules it matched. No timestamp, so regenerating an unchanged spec gives an identical file.

Each case ID is `PS-` plus the first 8 hex digits of the SHA-256 of
`{"inputs": ..., "spec": <spec name>}` in canonical JSON (sorted keys, compact separators,
ASCII escapes). Consequences, all deliberate:

- The ID depends on the **inputs only**, not on the expected outcome, the spec version, or the
  order of keys. Changing a rule keeps the ID and shows up as `oracle_changed` when the
  committed lockfile is compared with a fresh generation (`pairspec drift --lock`, or the MCP
  tool `pairspec_diff_locks`); adding a value to a parameter adds new IDs and leaves existing ones alone.
- Generated tests carry the ID as a tag (`@pairspec:PS-XXXXXXXX`). `pairspec drift` reports
  cases in the lockfile with no tagged test (missing), tags with no case (orphaned), and lockfile
  changes. Only the IDs it names need attention.
- The format is a contract: changing it would orphan every committed test. It is pinned by
  `test_case_ids_are_frozen`.

Limit: 32-bit IDs. For 1,000 cases the chance that two different cases share an ID is about 1 in
8,600. Generation treats an equal ID as a duplicate case and drops the second one, so a collision
would silently remove a case.

## What was left out

- **No LLM in the engine.** Matrix, values and oracles are computed. The only LLM use is
  optional: drafting a `spec.toml` from prose (`pairspec extract`), which a person then reviews.
- **No guessing of expected values.** A case whose outcome the spec does not define gets the
  `[default_expect]` values; if those are wrong, every test inherits the error. The spec is the
  single point of truth, and so the single point of failure.
- **No automatic boundary discovery.** Boundary values come from `min`/`max`/`step` and
  `extra_values`. A threshold the spec does not declare is not tested: in the benchmark,
  `amount >= 4999` instead of `>= 5000` goes unseen because 4999 is not in the value domain.

"""Deterministic IPOG (In-Parameter-Order-General) combinatorial engine with exclusions & stable IDs."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import dataclass, field
from typing import Any

from pairspec.bva import expand_parameter_values
from pairspec.schema import ExclusionRule, SpecDocument

_DONT_CARE = object()

# Oracle unmasking is skipped for a rule whose variables span more value tuples than this.
_MAX_UNMASK_TUPLES = 512


def compute_case_id(spec_name: str, inputs: dict[str, Any]) -> str:
    """Compute a deterministic, order-independent stable ID (`PS-XXXXXXXX`) for a test case."""
    canonical = json.dumps(
        {"spec": spec_name, "inputs": inputs},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8].upper()
    return f"PS-{digest}"


@dataclass(frozen=True)
class GeneratedCase:
    case_id: str
    inputs: dict[str, Any]
    expected: dict[str, Any]
    matched_oracles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "inputs": self.inputs,
            "expected": self.expected,
            "matched_oracles": self.matched_oracles,
        }


@dataclass(frozen=True)
class CoverageReport:
    strength: int
    total_feasible_tuples: int
    covered_tuples: int
    uncovered_tuples: int
    exclusion_violations: int

    @property
    def coverage_pct(self) -> float:
        if self.total_feasible_tuples == 0:
            return 100.0
        return round((self.covered_tuples / self.total_feasible_tuples) * 100.0, 2)


@dataclass(frozen=True)
class GenerationResult:
    spec_name: str
    spec_version: str
    spec_fingerprint: str
    strength: int
    parameter_domains: dict[str, list[Any]]
    cartesian_size: int
    cases: list[GeneratedCase]
    coverage: CoverageReport
    oracle_unmask_cases: int = 0

    @property
    def reduction_pct(self) -> float:
        if self.cartesian_size <= 0:
            return 0.0
        return round((1.0 - (len(self.cases) / self.cartesian_size)) * 100.0, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_name": self.spec_name,
            "spec_version": self.spec_version,
            "spec_fingerprint": self.spec_fingerprint,
            "strength": self.strength,
            "cartesian_size": self.cartesian_size,
            "case_count": len(self.cases),
            "oracle_unmask_cases": self.oracle_unmask_cases,
            "reduction_pct": self.reduction_pct,
            "coverage": {
                "strength": self.coverage.strength,
                "total_feasible_tuples": self.coverage.total_feasible_tuples,
                "covered_tuples": self.coverage.covered_tuples,
                "uncovered_tuples": self.coverage.uncovered_tuples,
                "coverage_pct": self.coverage.coverage_pct,
                "exclusion_violations": self.coverage.exclusion_violations,
            },
            "parameter_domains": self.parameter_domains,
            "cases": [c.to_dict() for c in self.cases],
        }


def _violates_any(assignment: dict[str, Any], exclusions: list[ExclusionRule]) -> bool:
    for rule in exclusions:
        if rule.is_violated(assignment):
            return True
    return False


def _complete_assignment(
    partial: dict[str, Any],
    param_names: list[str],
    domains: dict[str, list[Any]],
    exclusions: list[ExclusionRule],
    usage_counts: dict[tuple[str, int], int] | None = None,
) -> dict[str, Any] | None:
    """Backtracking CSP solver to extend a partial assignment to a full valid test case."""
    if _violates_any(partial, exclusions):
        return None
    unassigned = [p for p in param_names if p not in partial]
    if not unassigned:
        return dict(partial)

    # MRV heuristic (most constrained variable first), deterministic tie-break
    def mrv_key(pname: str) -> tuple[int, str]:
        valid = 0
        for val in domains[pname]:
            partial[pname] = val
            if not _violates_any(partial, exclusions):
                valid += 1
            del partial[pname]
        return (valid, pname)

    next_param = min(unassigned, key=mrv_key)
    candidates = list(enumerate(domains[next_param]))
    if usage_counts is not None:
        candidates.sort(key=lambda iv: (usage_counts.get((next_param, iv[0]), 0), iv[0]))

    for _, val in candidates:
        partial[next_param] = val
        if not _violates_any(partial, exclusions):
            solved = _complete_assignment(partial, param_names, domains, exclusions, usage_counts)
            if solved is not None:
                del partial[next_param]
                return solved
        del partial[next_param]
    return None


class _Feasibility:
    """Memoised check that a partial assignment can still be extended to a valid case.

    Only parameters referenced by an exclusion can make an assignment infeasible, so the
    CSP is solved over those alone and each distinct projection is solved once.
    """

    def __init__(
        self,
        param_names: list[str],
        domains: dict[str, list[Any]],
        exclusions: list[ExclusionRule],
    ) -> None:
        constrained: set[str] = set()
        for rule in exclusions:
            constrained |= rule.variables
        self._names = [n for n in param_names if n in constrained]
        self._domains = domains
        self._exclusions = exclusions
        self._cache: dict[tuple[tuple[str, Any], ...], bool] = {}

    def __call__(self, partial: dict[str, Any]) -> bool:
        if not self._exclusions:
            return True
        relevant = {n: partial[n] for n in self._names if n in partial}
        key: tuple[tuple[str, Any], ...] | None = tuple(relevant.items())
        try:
            cached = self._cache.get(key)  # type: ignore[arg-type]
        except TypeError:  # unhashable parameter value: solve without caching
            key, cached = None, None
        if cached is not None:
            return cached
        feasible = _complete_assignment(relevant, self._names, self._domains, self._exclusions) is not None
        if key is not None:
            self._cache[key] = feasible
        return feasible


def _tuple_key(items: tuple[tuple[str, int], ...]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(items, key=lambda x: x[0]))


def _row_to_partial(row: list[Any], ordered_names: list[str], domains: dict[str, list[Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for idx, val_idx in enumerate(row):
        if val_idx is not _DONT_CARE:
            pname = ordered_names[idx]
            out[pname] = domains[pname][val_idx]
    return out


def _build_rows(
    ordered_names: list[str],
    domains: dict[str, list[Any]],
    strength: int,
    feasible: _Feasibility,
    balance_ties: bool,
) -> list[list[Any]]:
    """IPOG horizontal + vertical growth. Rows hold value indices, `_DONT_CARE` where still free."""
    # 1. Initialize matrix with all valid combinations of the first t parameters
    rows_idx: list[list[Any]] = []
    first_t = ordered_names[:strength]
    for combo in itertools.product(*(range(len(domains[n])) for n in first_t)):
        partial = {first_t[i]: domains[first_t[i]][combo[i]] for i in range(strength)}
        if feasible(partial):
            rows_idx.append(list(combo))

    # 2. Iteratively add each remaining parameter p_k (Horizontal + Vertical growth)
    for k in range(strength, len(ordered_names)):
        pk = ordered_names[k]
        pk_domain_len = len(domains[pk])

        # Build set of all feasible t-tuples involving p_k and (t-1) previous parameters
        uncovered: set[tuple[tuple[str, int], ...]] = set()
        prev_indices = list(range(k))
        for prev_comb in itertools.combinations(prev_indices, strength - 1):
            prev_names = [ordered_names[i] for i in prev_comb]
            for prev_vals in itertools.product(*(range(len(domains[n])) for n in prev_names)):
                for v_idx in range(pk_domain_len):
                    partial = {prev_names[j]: domains[prev_names[j]][prev_vals[j]] for j in range(len(prev_names))}
                    partial[pk] = domains[pk][v_idx]
                    if feasible(partial):
                        tkey = _tuple_key(
                            tuple((prev_names[j], prev_vals[j]) for j in range(len(prev_names))) + ((pk, v_idx),)
                        )
                        uncovered.add(tkey)

        # 2a. Horizontal growth: extend each existing row with the best value for p_k.
        # With `balance_ties`, gain ties go to the least-used value so far instead of the first one,
        # which stops p_k from mirroring an earlier column on regular (same-size) domains.
        pk_usage = [0] * pk_domain_len
        for row in rows_idx:
            base_partial = _row_to_partial(row, ordered_names[:k], domains)
            best_val: int | None = None
            best_gain = -1
            best_covered: list[tuple[tuple[str, int], ...]] = []

            for v_idx in range(pk_domain_len):
                candidate_partial = dict(base_partial)
                candidate_partial[pk] = domains[pk][v_idx]
                if not feasible(candidate_partial):
                    continue

                newly_covered: list[tuple[tuple[str, int], ...]] = []
                for prev_comb in itertools.combinations(prev_indices, strength - 1):
                    if any(row[i] is _DONT_CARE for i in prev_comb):
                        continue
                    tkey = _tuple_key(
                        tuple((ordered_names[i], row[i]) for i in prev_comb) + ((pk, v_idx),)
                    )
                    if tkey in uncovered:
                        newly_covered.append(tkey)

                gain = len(newly_covered)
                better_tie = (
                    balance_ties
                    and gain == best_gain
                    and best_val is not None
                    and pk_usage[v_idx] < pk_usage[best_val]
                )
                if gain > best_gain or better_tie:
                    best_gain = gain
                    best_val = v_idx
                    best_covered = newly_covered

            if best_val is None:
                # Fallback: leave as don't care (will be resolved during completion)
                row.append(_DONT_CARE)
            else:
                row.append(best_val)
                pk_usage[best_val] += 1
                for tkey in best_covered:
                    uncovered.discard(tkey)

        # 2b. Vertical growth: cover remaining uncovered t-tuples involving p_k
        # Iterate in deterministic sorted order for reproducibility
        for tkey in sorted(uncovered):
            if tkey not in uncovered:
                continue
            target_map = dict(tkey)  # param_name -> val_idx

            # Try to fit into an existing row that has compatible values or _DONT_CARE
            placed = False
            for row in rows_idx:
                compatible = True
                for col_idx in range(k + 1):
                    pname = ordered_names[col_idx]
                    if pname in target_map:
                        if row[col_idx] is not _DONT_CARE and row[col_idx] != target_map[pname]:
                            compatible = False
                            break
                if not compatible:
                    continue
                # Test satisfiability after filling
                trial_row = list(row)
                for col_idx in range(k + 1):
                    pname = ordered_names[col_idx]
                    if pname in target_map:
                        trial_row[col_idx] = target_map[pname]
                trial_partial = _row_to_partial(trial_row, ordered_names[: k + 1], domains)
                if feasible(trial_partial):
                    row[:] = trial_row
                    placed = True
                    # Mark all newly covered tuples in this row
                    _discard_covered_for_row(row, ordered_names[: k + 1], k, strength, uncovered)
                    break

            if not placed:
                new_row: list[Any] = [_DONT_CARE] * (k + 1)
                for col_idx in range(k + 1):
                    pname = ordered_names[col_idx]
                    if pname in target_map:
                        new_row[col_idx] = target_map[pname]
                rows_idx.append(new_row)
                _discard_covered_for_row(new_row, ordered_names[: k + 1], k, strength, uncovered)

    return rows_idx


def _complete_rows(
    rows_idx: list[list[Any]],
    spec_name: str,
    original_order: list[str],
    ordered_names: list[str],
    domains: dict[str, list[Any]],
    exclusions: list[ExclusionRule],
) -> list[dict[str, Any]]:
    """Fill every `_DONT_CARE` slot with a balanced valid value and drop duplicate cases."""
    usage_counts: dict[tuple[str, int], int] = {}
    final_assignments: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for row in rows_idx:
        partial = _row_to_partial(row, ordered_names, domains)
        solved = _complete_assignment(partial, ordered_names, domains, exclusions, usage_counts)
        if solved is None:
            continue
        # Re-order keys in the spec's declared parameter order for readability
        ordered_inputs = {p: solved[p] for p in original_order}
        cid = compute_case_id(spec_name, ordered_inputs)
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        final_assignments.append(ordered_inputs)
        for pname, val in ordered_inputs.items():
            v_idx = _find_val_index(domains[pname], val)
            usage_counts[(pname, v_idx)] = usage_counts.get((pname, v_idx), 0) + 1
    return final_assignments


def _same_value(a: Any, b: Any) -> bool:
    return type(a) is type(b) and a == b


def _unmask_oracle_cases(
    spec: SpecDocument,
    assignments: list[dict[str, Any]],
    original_order: list[str],
    ordered_names: list[str],
    domains: dict[str, list[Any]],
) -> list[dict[str, Any]]:
    """Extra cases that make every oracle decision observable.

    t-wise coverage guarantees that a value tuple appears, not that its effect can be seen: when a
    stronger rule (typically a rejection) matches in the same case it overrides the outcome and
    hides a defect in the weaker rule. For each rule and each value tuple over the rule's variables,
    ensure one case holds that tuple while no stronger rule overriding all of its expected keys matches.
    """
    applied = sorted(spec.oracles, key=lambda r: (-r.priority, r.name))  # same order as evaluate_oracle
    usage_counts: dict[tuple[str, int], int] = {}
    for case in assignments:
        for pname, val in case.items():
            key = (pname, _find_val_index(domains[pname], val))
            usage_counts[key] = usage_counts.get(key, 0) + 1

    extra: list[dict[str, Any]] = []
    for pos, rule in enumerate(applied):
        maskers = [o for o in applied[pos + 1 :] if set(rule.expect) <= set(o.expect)]
        var_names = [n for n in original_order if n in rule.variables]
        if not maskers or not var_names or not rule.expect:
            continue
        if math.prod(len(domains[n]) for n in var_names) > _MAX_UNMASK_TUPLES:
            continue
        blockers = spec.exclusions + [ExclusionRule(expr=m.when) for m in maskers]
        for combo in itertools.product(*(domains[n] for n in var_names)):
            target = dict(zip(var_names, combo, strict=True))
            observable = any(
                all(_same_value(case[n], v) for n, v in target.items())
                and not any(m.matches(case) for m in maskers)
                for case in itertools.chain(assignments, extra)
            )
            if observable:
                continue
            solved = _complete_assignment(dict(target), ordered_names, domains, blockers, usage_counts)
            if solved is None:
                continue  # tuple excluded by the spec, or reachable only under a stronger rule
            new_case = {p: solved[p] for p in original_order}
            extra.append(new_case)
            for pname, val in new_case.items():
                key = (pname, _find_val_index(domains[pname], val))
                usage_counts[key] = usage_counts.get(key, 0) + 1
    return extra


def generate_matrix(
    spec: SpecDocument,
    strength: int | None = None,
    unmask_oracles: bool = True,
) -> GenerationResult:
    """Run deterministic IPOG (t-wise, default t=2) with BVA values, exclusions, and blind oracles.

    With `unmask_oracles`, cases are appended so that no oracle rule is only ever exercised
    under a stronger rule that hides its outcome (see `_unmask_oracle_cases`).
    """
    domains: dict[str, list[Any]] = {
        p.name: expand_parameter_values(p) for p in spec.parameters
    }
    original_order = [p.name for p in spec.parameters]
    cartesian_size = math.prod(len(v) for v in domains.values())

    eff_strength = min(strength if strength is not None else spec.strength, len(original_order))
    if eff_strength < 1:
        raise ValueError("Effective combinatorial strength must be >= 1")

    # Sort parameters by descending domain size (canonical IPOG order), deterministic tie-breaker by name
    ordered_names = sorted(original_order, key=lambda n: (-len(domains[n]), n))
    exclusions = spec.exclusions
    feasible = _Feasibility(ordered_names, domains, exclusions)

    if eff_strength == len(ordered_names):
        # t == number of parameters -> filtered cartesian product
        rows_idx: list[list[Any]] = []
        for combo in itertools.product(*(range(len(domains[n])) for n in ordered_names)):
            assign = {ordered_names[i]: domains[ordered_names[i]][combo[i]] for i in range(len(ordered_names))}
            if not _violates_any(assign, exclusions):
                rows_idx.append(list(combo))
        candidates = [_complete_rows(rows_idx, spec.name, original_order, ordered_names, domains, exclusions)]
    else:
        # Greedy IPOG is sensitive to how gain ties are broken: build the matrix with both
        # deterministic strategies and keep the smaller one (first-value strategy wins a draw).
        candidates = [
            _complete_rows(
                _build_rows(ordered_names, domains, eff_strength, feasible, balance_ties),
                spec.name,
                original_order,
                ordered_names,
                domains,
                exclusions,
            )
            for balance_ties in (False, True)
        ]

    final_assignments: list[dict[str, Any]] = []
    unmask_count = 0
    for base in candidates:
        extra = _unmask_oracle_cases(spec, base, original_order, ordered_names, domains) if unmask_oracles else []
        if not final_assignments or len(base) + len(extra) < len(final_assignments):
            final_assignments = base + extra
            unmask_count = len(extra)

    # Evaluate blind oracles from the spec only. Cases keep IPOG order (deterministic).
    cases: list[GeneratedCase] = []
    for inputs in final_assignments:
        expected, matched_oracles = spec.evaluate_oracle(inputs)
        cases.append(
            GeneratedCase(
                case_id=compute_case_id(spec.name, inputs),
                inputs=inputs,
                expected=expected,
                matched_oracles=matched_oracles,
            )
        )

    coverage = verify_coverage(spec, cases, strength=eff_strength, domains=domains)

    return GenerationResult(
        spec_name=spec.name,
        spec_version=spec.version,
        spec_fingerprint=spec.fingerprint(),
        strength=eff_strength,
        parameter_domains=domains,
        cartesian_size=cartesian_size,
        cases=cases,
        coverage=coverage,
        oracle_unmask_cases=unmask_count,
    )


def _find_val_index(domain: list[Any], value: Any) -> int:
    for idx, item in enumerate(domain):
        if type(item) is type(value) and item == value:
            return idx
    return 0


def _discard_covered_for_row(
    row: list[Any],
    names_prefix: list[str],
    pk_col: int,
    strength: int,
    uncovered: set[tuple[tuple[str, int], ...]],
) -> None:
    if row[pk_col] is _DONT_CARE:
        return
    pk_name = names_prefix[pk_col]
    pk_val = row[pk_col]
    filled_prev = [i for i in range(pk_col) if row[i] is not _DONT_CARE]
    if len(filled_prev) < strength - 1:
        return
    for comb in itertools.combinations(filled_prev, strength - 1):
        tkey = _tuple_key(tuple((names_prefix[i], row[i]) for i in comb) + ((pk_name, pk_val),))
        uncovered.discard(tkey)


def verify_coverage(
    spec: SpecDocument,
    cases: list[GeneratedCase],
    strength: int | None = None,
    domains: dict[str, list[Any]] | None = None,
) -> CoverageReport:
    """Recount t-wise coverage and exclusion violations of `cases`.

    It reuses the generator's exclusion predicate, so it is a self-check, not an independent oracle
    (see tests/test_independent_oracles.py for the independent one).
    """
    doms = domains or {p.name: expand_parameter_values(p) for p in spec.parameters}
    param_names = [p.name for p in spec.parameters]
    eff_t = min(strength if strength is not None else spec.strength, len(param_names))

    feasible = _Feasibility(param_names, doms, spec.exclusions)
    violations = 0
    covered_tuples: set[tuple[tuple[str, int], ...]] = set()
    for case in cases:
        if _violates_any(case.inputs, spec.exclusions):
            violations += 1
        idx_map = {p: _find_val_index(doms[p], case.inputs[p]) for p in param_names}
        for comb in itertools.combinations(param_names, eff_t):
            covered_tuples.add(_tuple_key(tuple((p, idx_map[p]) for p in comb)))

    feasible_tuples: set[tuple[tuple[str, int], ...]] = set()
    for comb in itertools.combinations(param_names, eff_t):
        for val_indices in itertools.product(*(range(len(doms[p])) for p in comb)):
            partial = {comb[i]: doms[comb[i]][val_indices[i]] for i in range(eff_t)}
            if feasible(partial):
                feasible_tuples.add(_tuple_key(tuple((comb[i], val_indices[i]) for i in range(eff_t))))

    actually_covered = feasible_tuples & covered_tuples
    return CoverageReport(
        strength=eff_t,
        total_feasible_tuples=len(feasible_tuples),
        covered_tuples=len(actually_covered),
        uncovered_tuples=len(feasible_tuples - actually_covered),
        exclusion_violations=violations,
    )

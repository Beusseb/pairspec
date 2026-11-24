# Real-world case: `email-validator` 2.4.0

[email-validator](https://github.com/JoshData/python-email-validator) is a widely used Python
library (pydantic's `EmailStr` relies on it). Its `validate_email` function takes seven options
that change what is accepted, which makes it a good target for combinatorial testing.

## Method

1. [`spec.toml`](spec.toml) was written from the **public README only** (the one published on
   PyPI for 2.4.0: sections "Options", "Internationalized local parts", "Test addresses",
   "Assumptions"). The library's source code and its own tests were not read. Each rule cites
   the README section it comes from.
2. `pairspec generate` + `pairspec emit --format pytest` produced
   [`test_validate_email_pairspec.py`](test_validate_email_pairspec.py), untouched.
3. Only then was the adapter written ([`conftest.py`](conftest.py), [`addresses.py`](addresses.py)):
   it maps each case to a concrete address and calls `validate_email(..., check_deliverability=False)`.

Dimensions: local part (`me`, `"my name"`, `méo`, empty, 65 characters), domain (`example.org`,
`exämple.org`, `myname.test`, `localhost`, `example`, `[192.0.2.1]`, `[IPv6:2001:db8::1]`),
display name on/off, and the seven options. 8,960 combinations; pairwise with unmasking gives
38 cases (3 of them unmasking), 24 expected rejections and 14 expected acceptances.

## Result: no defect found

```
$ uvx --with email-validator==2.4.0 --with pytest python -m pytest examples/email_validator -q
38 passed

$ uvx --with email-validator==2.4.0 python examples/email_validator/exhaustive_check.py
email-validator 2.4.0: 8960 combinations, 0 mismatches
```

The library does what its README says on every documented rule tested here, including the two
subtle ones: an internationalised *domain* does not require SMTPUTF8 (only a non-ASCII local part
does), and the local-part length is checked only with `strict=True`.

A green run proves something only if the suite can fail. Each option was disconnected in the
adapter, one at a time (the library default used instead):

| Adapter fault | Cases failing (of 38) |
| :--- | ---: |
| `allow_smtputf8` ignored | 1 |
| `allow_quoted_local` ignored | 4 |
| `allow_domain_literal` ignored | 4 |
| `allow_display_name` ignored | 9 |
| `allow_empty_local` ignored | 3 |
| `test_environment` ignored | 2 |
| `strict` ignored | 3 |
| display name never added to the address | 1 |
| IDN domain replaced by `example.org` | 0 |

Every option is observed at least once. The last row is expected: the README promises the same
outcome for both domains, so nothing in the spec can tell them apart. Several options are
isolated by a single case (oracle unmasking guarantees one, not more).

The exhaustive check was also seen failing: with the `strict` rule inverted in the spec, it
reports 672 mismatches.

## Limits

- One concrete value per form: one quoted local part, one IDN, one IPv4 literal. A defect that
  depends on the exact characters (escaping inside quotes, a different script, IPv6 forms) is
  out of reach.
- Not covered: DNS deliverability, Unicode normalisation, unsafe Unicode characters, overall
  address and domain length limits, the returned normalised fields other than `smtputf8`.
- The README says the display-name syntax is implemented "in the spirit but not the letter of
  RFC 5322"; only one display name (`My Name`) is tested.
- Not in CI: it needs a third-party package, and PairSpec's CI installs none for its examples.

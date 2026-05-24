## What does this add and why do you believe it belongs in this dashboard?

<!-- Required. Make the case for why this feature fits a personal Claude Code usage dashboard. -->


## Checklist

**Code correctness**
- [ ] All `calcCost()` calls pass 6 arguments: `(model, inp, out, cache_read, cache_creation, cache_1h)`
- [ ] JavaScript template literals use bare backticks (`` ` ``), not escaped ones (`` \` ``)
- [ ] No JS variables referenced before they are defined
- [ ] No new third-party dependencies introduced

**Tests**
- [ ] `python3 -m unittest discover -s tests -v` — all passing
- [ ] `python3 -m unittest tests.test_browser -v` — all passing
- [ ] New behaviour is covered by at least one test

**Scope**
- [ ] This is a single concern — one feature or fix per PR
- [ ] Only touches existing files (`dashboard.py`, `scanner.py`, `cli.py`, `pricing.py`, `cowork.py`, `tests/`) — or I've explained below why a new file is needed

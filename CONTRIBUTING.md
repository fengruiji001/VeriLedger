# Contributing

Bug reports and pull requests are welcome on the issue tracker.

1. Install in editable mode: `pip install -e ".[dev]"`.
2. Run `ruff check src tests examples`, `ruff format --check src tests examples` and `python -m pytest`.
3. Add a test for each change in behaviour.

To run the optional SAF-T schema tests, clone https://github.com/Skatteetaten/saf-t and set
`VERILEDGER_SAFT_DIR` to the clone.

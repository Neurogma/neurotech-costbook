# Neurotech Costbook

`COSTBOOK.md` is the product. It is a practical budgeting tool for founders, PIs, engineering leads and regulatory teams.

## Use it in 90 minutes

Read **Decision Snapshot**, then the scenario closest to the project. The scenario gives cash, internal labor, conditional exposure, sensitivities and the exact quote you should request next.

Do not treat all numbers alike:

- Current vendor/FDA/institutional numbers are direct anchors.
- Dated literature is a proxy, not a current quote.
- Derived values show their formula.
- Conditional values stay outside base cash until the branch activates.

## Reproduce the model

From the repository root:

```bash
python calculator/calc.py --validate
python calculator/calc.py --self-test
python calculator/calc.py --scenario garage
python calculator/calc.py --scenario university_eeg
python calculator/calc.py --scenario tes_eeg
python calculator/calc.py --scenario pilot_20
python calculator/calc.py --scenario 510k
```

`--validate` checks source/anchor integrity, units, ranges, scenario references, labor semantics, branch leakage and the published calculation snapshot in `COSTBOOK.md`.

`--self-test` runs the adversarial semantic tests embedded in the small calculator: missing anchors, negative quantities, currency leakage, wrong labor units, conditional leakage, duplicate lines, range corruption, labor arithmetic and study-storage plausibility.

## Files

```text
COSTBOOK.md
README.md
data/
  cost_anchors.csv
  sources.csv
  scenarios.yaml
calculator/
  calc.py
```

There are deliberately no empty category folders or methodology-only files. Supporting data exist only to make the numbers in `COSTBOOK.md` traceable and reproducible.

## Updating a cost

Replace the source record and its anchor together. Preserve the original price/effective date; do not overwrite an old benchmark and call it current. If the exact requirement needs a quote, keep the planning proxy and replace it only when the configured quote or SOW arrives.

## Common misuse to avoid

A vendor starting price is not the cost of the configured system. A university fee is not a universal fee. A BLS median wage is not a contractor quote. A 510(k) filing fee is not the 510(k) program cost. A conditional clinical branch is not part of the base path. F&A is institution-specific and sits outside these direct scientific totals unless supplied.

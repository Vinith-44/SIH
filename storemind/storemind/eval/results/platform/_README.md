# Platform results (Person B)

Person B writes measured platform results here (stream density, soak, chaos,
HIL, Pi 5 install, UART error counts). `python -m storemind.eval.run_all`
includes every `*.json` / `*.md` file in this folder in `RESULTS.md`, verbatim,
one section per file. Files starting with `_` (like this one) are skipped.

Every file must declare its data bucket (A, B, C, S, Q, P — see `RESULTS.md`),
or it shows up as "did not run". Formats: `docs/INTERFACES.md` §5.

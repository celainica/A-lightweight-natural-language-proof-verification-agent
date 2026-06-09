# Auto Proof Verifier

This folder is a self-contained verifier workspace.

Read first:
- `README.md`
- `STEP_TEMPLATE.txt`
- `OUTPUT_FORMATS.txt`
- `source/input.txt`
- `source/theory.txt` if present
- `source/example.txt` if present
- `source/standards.txt` if present
- files under `reference/` if present

Maintain only:
- `steps/`
- `dependency.txt`
- `stepsprocess.txt`
- `answer.txt`

Principles:

1. Split the proof into minimal logical deduction units.
   A unit is the smallest contiguous segment with one nontrivial conclusion on one local proof route.
   Keep routine algebra and direct substitution inside the same unit.
   If a segment contains several nontrivial conclusions, theorem use plus a further consequence, or mixed external burdens, split it smaller.

2. Each step file is a thick elaboration of one unit.
   Keep the same final assertion and the same proof route.
   Do not replace the step with a smarter proof.

3. Organize each elaboration by the explanation triple `(Gamma_i, Sigma_i, T_i)`.
   `Gamma_i`: active in-proof context, including valid implicit context.
   `Sigma_i`: nontrivial external statements actually used.
   `T_i`: routine background theory such as substitution, instantiation, Modus Ponens, equality rewrite, and basic algebra.
   The local target is `Gamma_i, Sigma_i, T_i |- a_i`.

4. For each used item of `Sigma_i`, write the exact as-used statement and then provide either:
   - a proof in the step file, or
   - a precise source for the exact result.
   Also state briefly why it applies in this step.

5. External statements have only three statuses:
   `confirmed`: the exact as-used statement has a proof or a precise source, and its applicability is stated.
   `open`: you cannot yet confirm it.
   `false`: it is wrong or misapplied.

6. Verdicts:
   `VERIFIED` only if the written step is closed.
   `OPEN` is the default negative verdict.
   `FLAWED` only if the file contains an explicit flaw witness.

A step is `VERIFIED` only if every used item of `Sigma_i` is `confirmed`.
If any used item of `Sigma_i` is still `open`, the step is `OPEN`.

Valid flaw witnesses:
- an exact false statement,
- an invalid inference from written premises,
- an explicit contradiction with verified in-proof context,
- a known false item of `Sigma_i`,
- a theorem application whose required hypothesis is explicitly violated in verified local context.

Not enough for `FLAWED`:
- inability to prove a hard theorem,
- lack of citation,
- suspicion that the step is too strong,
- failure to find a proof quickly,
- a plausible missing bridge on the same route.

Hard rules:
- Minimize files, not step detail.
- If a key external statement is not confirmed, the step is `OPEN`.
- Never upgrade a `Sigma_i` item to `confirmed` because a theorem merely looks standard or plausible.
- Record every `open` or `false` external statement in `dependency.txt`.
- Do not create extra ledgers or pipeline files.

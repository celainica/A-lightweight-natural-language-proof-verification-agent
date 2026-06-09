# Autorun

Run from the agent root:

```bash
python script/autorun.py
```

The script runs `RUN_PROMPT.txt` while `steps/` has no step files, then uses
`RESUME_PROMPT.txt`. If any step is `FLAWED`, it uses `CHECKFLAW_PROMPT.txt`.

It saves runner state in:

```text
script/agentnips_run_state.txt
```

Restarting the same command resumes the saved Codex session and stale counter.
Use `--reset-state` to ignore the saved state.

Common options:

```bash
python script/autorun.py --model gpt-5.4
python script/autorun.py --workspace PATH
python script/autorun.py --no-search
python script/autorun.py --workspace-write
python script/autorun.py --stale-limit 5 --max-runs 80
```

Full access is the default. Use `--workspace-write` only to restrict the
runner to the workspace-write sandbox.

Requires a working `codex` CLI. Use `--codex-bin PATH` if needed.

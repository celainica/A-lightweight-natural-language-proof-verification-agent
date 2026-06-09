from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


STATUS_RE = re.compile(r"^-\s*status:\s*(UNTOUCHED|OPEN|VERIFIED|FLAWED)\s*$", re.I)
OVERALL_RE = re.compile(r"^Overall verdict:\s*(RUNNING|VERIFIED|FLAWED)\s*$", re.I)
SESSION_RE = re.compile(
    r"session id:\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
    re.I,
)


@dataclass
class State:
    overall: str
    statuses: list[str]

    @property
    def has_flaw(self) -> bool:
        if self.statuses:
            return "FLAWED" in self.statuses
        return self.overall == "FLAWED"

    @property
    def all_verified(self) -> bool:
        if self.statuses:
            return all(status == "VERIFIED" for status in self.statuses)
        return self.overall == "VERIFIED"


@dataclass
class RunState:
    session_id: str = ""
    counted_runs: int = 0
    empty_runs: int = 0
    stale_runs: int = 0
    status_signature: str = ""
    terminal_reason: str = ""
    workspace_mismatch: bool = False


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def read_state(workspace: Path) -> State:
    answer = read_text(workspace / "answer.txt").splitlines()

    overall = ""
    for line in answer:
        match = OVERALL_RE.match(line.strip())
        if match:
            overall = match.group(1).upper()
            break

    return State(overall=overall, statuses=list(read_status_map(workspace).values()))


def read_status_map(workspace: Path) -> dict[str, str]:
    status_map: dict[str, str] = {}
    steps_dir = workspace / "steps"
    if not steps_dir.exists():
        return status_map

    for path in sorted(steps_dir.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.name == ".gitkeep":
            continue

        step_name = path.stem
        status = ""
        in_step_result = False
        for line in read_text(path).splitlines():
            stripped = line.strip()
            if stripped.lower() == "step result:":
                in_step_result = True
                continue
            if not in_step_result:
                continue
            status_match = STATUS_RE.match(stripped)
            if status_match:
                status = status_match.group(1).upper()
                break

        if status:
            status_map[step_name] = status

    return status_map


def has_step_files(workspace: Path) -> bool:
    steps_dir = workspace / "steps"
    if not steps_dir.exists():
        return False
    return any(path.is_file() and path.name != ".gitkeep" for path in steps_dir.iterdir())


def has_user_rule_progress(before_map: dict[str, str], after_map: dict[str, str]) -> bool:
    open_to_verified = any(
        before_map.get(step) == "OPEN" and after_map.get(step) == "VERIFIED"
        for step in before_map
    )
    before_open_count = sum(1 for status in before_map.values() if status == "OPEN")
    after_open_count = sum(1 for status in after_map.values() if status == "OPEN")
    open_split = after_open_count > before_open_count
    return open_to_verified or open_split


def state_summary(state: State) -> str:
    total = len(state.statuses)
    verified = state.statuses.count("VERIFIED")
    open_count = state.statuses.count("OPEN")
    flawed = state.statuses.count("FLAWED")
    untouched = state.statuses.count("UNTOUCHED")
    overall = state.overall or "<empty>"
    return (
        f"overall={overall}, total={total}, verified={verified}, "
        f"open={open_count}, flawed={flawed}, untouched={untouched}"
    )


def status_signature(state: State, status_map: dict[str, str]) -> str:
    payload = [f"overall={state.overall}"]
    payload.extend(f"{step}={status}" for step, status in sorted(status_map.items()))
    return hashlib.sha256("\n".join(payload).encode("utf-8")).hexdigest()[:16]


def workspace_id(workspace: Path) -> str:
    return hashlib.sha256(str(workspace.resolve()).lower().encode("utf-8")).hexdigest()[:16]


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def parse_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def read_run_state(path: Path, workspace: Path, current_signature: str) -> RunState:
    if not path.exists():
        return RunState()

    data: dict[str, str] = {}
    for raw_line in read_text(path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()

    stored_workspace_id = data.get("workspace_id", "")
    stored_workspace = data.get("workspace", "")
    workspace_mismatch = False
    if stored_workspace_id:
        workspace_mismatch = stored_workspace_id != workspace_id(workspace)
    elif stored_workspace:
        workspace_mismatch = Path(stored_workspace).resolve() != workspace
    if workspace_mismatch:
        print("Ignoring stale run-state file for different workspace.", flush=True)
        return RunState(workspace_mismatch=True)

    run_state = RunState(
        session_id=data.get("session_id", ""),
        counted_runs=parse_int(data.get("counted_runs", "0")),
        empty_runs=parse_int(data.get("empty_runs", "0")),
        stale_runs=parse_int(data.get("stale_runs", "0")),
        status_signature=data.get("status_signature", ""),
        terminal_reason=data.get("terminal_reason", ""),
    )

    if run_state.status_signature and run_state.status_signature != current_signature:
        print("Workspace state differs from saved run-state; preserving session_id but resetting stale counters.", flush=True)
        run_state.empty_runs = 0
        run_state.stale_runs = 0
        run_state.terminal_reason = ""

    return run_state


def write_run_state(
    *,
    path: Path,
    workspace: Path,
    session_id: str,
    counted_runs: int,
    empty_runs: int,
    stale_runs: int,
    last_prompt: str,
    state: State,
    status_map: dict[str, str],
    terminal_reason: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    signature = status_signature(state, status_map)
    lines = [
        "# agentnips automation state. This file is for the runner only.",
        "# Do not include it in verifier prompts or proof materials.",
        "version=2",
        f"updated_utc={datetime.now(timezone.utc).isoformat()}",
        f"workspace_id={workspace_id(workspace)}",
        f"workspace_name={workspace.name}",
        f"session_id={session_id}",
        f"counted_runs={counted_runs}",
        f"empty_runs={empty_runs}",
        f"stale_runs={stale_runs}",
        f"last_prompt={last_prompt}",
        f"terminal_reason={terminal_reason}",
        f"overall={state.overall}",
        f"has_flaw={bool_text(state.has_flaw)}",
        f"all_verified={bool_text(state.all_verified)}",
        f"total_steps={len(state.statuses)}",
        f"verified_steps={state.statuses.count('VERIFIED')}",
        f"open_steps={state.statuses.count('OPEN')}",
        f"flawed_steps={state.statuses.count('FLAWED')}",
        f"untouched_steps={state.statuses.count('UNTOUCHED')}",
        f"status_signature={signature}",
    ]
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def default_workspace(script_file: Path) -> Path:
    script_dir = script_file.parent
    if script_dir.name.lower() == "script":
        return script_dir.parent.resolve()
    return script_dir.resolve()


def default_state_file(workspace: Path, script_file: Path, user_path: str) -> Path:
    if user_path:
        return Path(user_path).resolve()
    script_dir = workspace / "script"
    if not script_dir.exists():
        script_dir = script_file.parent
    return (script_dir / "agentnips_run_state.txt").resolve()


def run_codex(
    *,
    workspace: Path,
    prompt_path: Path,
    codex_bin: str,
    model: str,
    use_search: bool,
    full_access: bool,
    session_id: str,
) -> tuple[str, str]:
    prompt = prompt_path.read_text(encoding="utf-8")
    cmd = [codex_bin]
    if use_search:
        cmd.append("--search")
    if full_access:
        cmd.extend(["--dangerously-bypass-approvals-and-sandbox", "-C", str(workspace)])
    else:
        cmd.extend(["-a", "never", "-C", str(workspace), "-s", "workspace-write"])
    if model:
        cmd.extend(["-m", model])
    cmd.append("exec")
    if session_id:
        cmd.extend(["resume", "--skip-git-repo-check", session_id])
    else:
        cmd.append("--skip-git-repo-check")
    cmd.append("-")

    proc = subprocess.Popen(
        cmd,
        cwd=workspace,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write(prompt)
    proc.stdin.close()

    output_chunks: list[str] = []
    for line in proc.stdout:
        output_chunks.append(line)
        print(line, end="", flush=True)

    proc.stdout.close()
    proc.wait()
    output = "".join(output_chunks)
    if proc.returncode != 0:
        raise RuntimeError(f"codex exited with code {proc.returncode}")
    found_session = SESSION_RE.search(output or "")
    return output or "", found_session.group(1) if found_session else session_id


def require_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"missing required file: {path}")


def resolve_codex_bin(user_bin: str) -> str:
    candidates = [user_bin, "codex.cmd", "codex.exe", "codex"]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return user_bin or "codex.cmd"


def choose_prompt(state: State, paths: dict[str, Path], workspace: Path) -> tuple[str, Path]:
    if not has_step_files(workspace):
        return "RUN", paths["run"]
    if state.has_flaw:
        return "CHECKFLAW", paths["checkflaw"]
    return "RESUME", paths["resume"]


def main() -> int:
    script_file = Path(__file__).resolve()
    parser = argparse.ArgumentParser(
        description=(
            "Run agentnips automatically, persist session/stale state in script/agentnips_run_state.txt, "
            "and resume from that state on restart."
        )
    )
    parser.add_argument("--workspace", default="")
    parser.add_argument("--state-file", default="")
    parser.add_argument("--reset-state", action="store_true")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--max-runs", type=int, default=80)
    parser.add_argument("--stale-limit", type=int, default=5)
    parser.add_argument("--max-empty", type=int, default=10)
    parser.add_argument("--no-search", action="store_true")
    permission_group = parser.add_mutually_exclusive_group()
    permission_group.add_argument("--full-access", dest="full_access", action="store_true", default=True)
    permission_group.add_argument("--workspace-write", dest="full_access", action="store_false")
    parser.add_argument("--run-prompt", default="RUN_PROMPT.txt")
    parser.add_argument("--resume-prompt", default="RESUME_PROMPT.txt")
    parser.add_argument("--checkflaw-prompt", default="CHECKFLAW_PROMPT.txt")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve() if args.workspace else default_workspace(script_file)
    state_file = default_state_file(workspace, script_file, args.state_file)
    codex_bin = resolve_codex_bin(args.codex_bin)
    paths = {
        "run": workspace / args.run_prompt,
        "resume": workspace / args.resume_prompt,
        "checkflaw": workspace / args.checkflaw_prompt,
    }
    for path in paths.values():
        require_file(path)

    initial_state = read_state(workspace)
    initial_map = read_status_map(workspace)
    initial_signature = status_signature(initial_state, initial_map)
    run_state = RunState() if args.reset_state else read_run_state(state_file, workspace, initial_signature)

    print(f"Codex binary: {codex_bin}", flush=True)
    print(f"Workspace: {workspace}", flush=True)
    print(f"State file: {state_file}", flush=True)
    print(
        "Permission mode: "
        + ("dangerously-bypass-approvals-and-sandbox" if args.full_access else "workspace-write"),
        flush=True,
    )
    print(
        f"Stale rule: stop after {args.stale_limit} counted runs unless an OPEN step becomes VERIFIED or an OPEN step splits.",
        flush=True,
    )
    print(
        "Session rule: first loop starts a Codex exec session; later loops resume the saved session with "
        + ("approval/sandbox bypass." if args.full_access else "workspace-write sandbox."),
        flush=True,
    )
    if run_state.session_id:
        print(f"Loaded Codex session id: {run_state.session_id}", flush=True)
    if args.reset_state:
        print("Ignoring saved run-state because --reset-state was supplied.", flush=True)

    if run_state.workspace_mismatch:
        write_run_state(
            path=state_file,
            workspace=workspace,
            session_id="",
            counted_runs=0,
            empty_runs=0,
            stale_runs=0,
            last_prompt="NONE",
            state=initial_state,
            status_map=initial_map,
            terminal_reason="",
        )

    if run_state.terminal_reason:
        write_run_state(
            path=state_file,
            workspace=workspace,
            session_id=run_state.session_id,
            counted_runs=run_state.counted_runs,
            empty_runs=run_state.empty_runs,
            stale_runs=run_state.stale_runs,
            last_prompt="NONE",
            state=initial_state,
            status_map=initial_map,
            terminal_reason=run_state.terminal_reason,
        )
        print(f"Stopping: saved terminal state is already reached ({run_state.terminal_reason}).", flush=True)
        print("Use --reset-state to force a new run.", flush=True)
        return 0

    if initial_state.all_verified:
        write_run_state(
            path=state_file,
            workspace=workspace,
            session_id=run_state.session_id,
            counted_runs=run_state.counted_runs,
            empty_runs=run_state.empty_runs,
            stale_runs=run_state.stale_runs,
            last_prompt="NONE",
            state=initial_state,
            status_map=initial_map,
            terminal_reason="all_verified_before_run",
        )
        print("Stopping: all steps are already VERIFIED.", flush=True)
        return 0

    if initial_map and run_state.stale_runs >= args.stale_limit:
        write_run_state(
            path=state_file,
            workspace=workspace,
            session_id=run_state.session_id,
            counted_runs=run_state.counted_runs,
            empty_runs=run_state.empty_runs,
            stale_runs=run_state.stale_runs,
            last_prompt="NONE",
            state=initial_state,
            status_map=initial_map,
            terminal_reason="stale_limit_before_run",
        )
        print("Stopping: saved stale limit is already reached. Use --reset-state to force a new run.", flush=True)
        return 0

    while run_state.counted_runs < args.max_runs:
        before = read_state(workspace)
        before_map = read_status_map(workspace)
        label, prompt_path = choose_prompt(before, paths, workspace)
        print("", flush=True)
        print(f"=== {label} ===", flush=True)
        print(f"Before: {state_summary(before)}", flush=True)

        output_text, run_state.session_id = run_codex(
            workspace=workspace,
            prompt_path=prompt_path,
            codex_bin=codex_bin,
            model=args.model,
            use_search=not args.no_search,
            full_access=args.full_access,
            session_id=run_state.session_id,
        )
        if run_state.session_id:
            print(f"Codex session id: {run_state.session_id}", flush=True)

        after = read_state(workspace)
        after_map = read_status_map(workspace)
        terminal_reason = ""

        if not output_text.strip():
            run_state.empty_runs += 1
            print(f"Empty agent output; not counted. empty={run_state.empty_runs}/{args.max_empty}", flush=True)
            if run_state.empty_runs >= args.max_empty:
                terminal_reason = "max_empty_outputs"
            write_run_state(
                path=state_file,
                workspace=workspace,
                session_id=run_state.session_id,
                counted_runs=run_state.counted_runs,
                empty_runs=run_state.empty_runs,
                stale_runs=run_state.stale_runs,
                last_prompt=label,
                state=after,
                status_map=after_map,
                terminal_reason=terminal_reason,
            )
            if terminal_reason:
                print("Stopping: repeated empty agent output.", flush=True)
                return 0
            continue

        run_state.counted_runs += 1
        run_state.empty_runs = 0

        changed = has_user_rule_progress(before_map, after_map)
        stale_active = bool(after_map)
        if changed:
            run_state.stale_runs = 0
        elif stale_active:
            run_state.stale_runs += 1

        print("", flush=True)
        print(f"After:  {state_summary(after)}", flush=True)
        print(
            f"Counted runs: {run_state.counted_runs}; user-rule progress={changed}; "
            f"stale={run_state.stale_runs}/{args.stale_limit}",
            flush=True,
        )

        if label == "CHECKFLAW" and after.has_flaw:
            before_flawed_count = sum(1 for status in before_map.values() if status == "FLAWED")
            after_flawed_count = sum(1 for status in after_map.values() if status == "FLAWED")
            if after_flawed_count >= before_flawed_count:
                terminal_reason = "checkflaw_kept_flaw"
                write_run_state(
                    path=state_file,
                    workspace=workspace,
                    session_id=run_state.session_id,
                    counted_runs=run_state.counted_runs,
                    empty_runs=run_state.empty_runs,
                    stale_runs=run_state.stale_runs,
                    last_prompt=label,
                    state=after,
                    status_map=after_map,
                    terminal_reason=terminal_reason,
                )
                print("Stopping: CHECKFLAW left all current flaws in place.", flush=True)
                return 0
            print(
                f"Continuing: CHECKFLAW reduced flaws from {before_flawed_count} to {after_flawed_count}.",
                flush=True,
            )

        if after.all_verified:
            terminal_reason = "all_verified"
            write_run_state(
                path=state_file,
                workspace=workspace,
                session_id=run_state.session_id,
                counted_runs=run_state.counted_runs,
                empty_runs=run_state.empty_runs,
                stale_runs=run_state.stale_runs,
                last_prompt=label,
                state=after,
                status_map=after_map,
                terminal_reason=terminal_reason,
            )
            print("Stopping: all steps are VERIFIED.", flush=True)
            return 0

        if stale_active and run_state.stale_runs >= args.stale_limit:
            terminal_reason = "stale_limit"
            write_run_state(
                path=state_file,
                workspace=workspace,
                session_id=run_state.session_id,
                counted_runs=run_state.counted_runs,
                empty_runs=run_state.empty_runs,
                stale_runs=run_state.stale_runs,
                last_prompt=label,
                state=after,
                status_map=after_map,
                terminal_reason=terminal_reason,
            )
            print(
                "Stopping: rejected proof after consecutive runs without OPEN->VERIFIED or OPEN-step split progress.",
                flush=True,
            )
            return 0

        write_run_state(
            path=state_file,
            workspace=workspace,
            session_id=run_state.session_id,
            counted_runs=run_state.counted_runs,
            empty_runs=run_state.empty_runs,
            stale_runs=run_state.stale_runs,
            last_prompt=label,
            state=after,
            status_map=after_map,
            terminal_reason="",
        )

    final_state = read_state(workspace)
    final_map = read_status_map(workspace)
    write_run_state(
        path=state_file,
        workspace=workspace,
        session_id=run_state.session_id,
        counted_runs=run_state.counted_runs,
        empty_runs=run_state.empty_runs,
        stale_runs=run_state.stale_runs,
        last_prompt="NONE",
        state=final_state,
        status_map=final_map,
        terminal_reason="max_counted_runs",
    )
    print(f"Stopping: max counted runs reached ({args.max_runs}).", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)

#!/usr/bin/env python3
"""Git Repository Update Dashboard Script."""

import argparse
import concurrent.futures
import html
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import webbrowser

# ANSI terminal formatting
USE_COLOR = sys.stdout.isatty()
RESET = "\033[0m" if USE_COLOR else ""
GREEN = "\033[32m" if USE_COLOR else ""
BLUE = "\033[36m" if USE_COLOR else ""
RED = "\033[31m" if USE_COLOR else ""
DIM = "\033[2m" if USE_COLOR else ""
BOLD = "\033[1m" if USE_COLOR else ""
CLEAR_LINE = "\033[K" if USE_COLOR else ""

# Non-emoji status symbols
SYM_OK = f"{GREEN}✓{RESET}" if USE_COLOR else "[OK]"
SYM_UPDATED = f"{BLUE}+{RESET}" if USE_COLOR else "[+]"
SYM_NO_REMOTE = f"{DIM}•{RESET}" if USE_COLOR else "[-]"
SYM_FAILED = f"{RED}✗{RESET}" if USE_COLOR else "[!]"


class TerminalProgress:
    """Manages thread-safe terminal progress overwriting and permanent logging."""

    def __init__(self, total: int):
        self.total = total
        self.completed = 0
        self.lock = threading.Lock()

    def log_finished(self, symbol: str, path_str: str, summary: str):
        with self.lock:
            if USE_COLOR:
                sys.stdout.write(f"\r{CLEAR_LINE}  {symbol} {path_str} {DIM}({summary}){RESET}\n")
            else:
                sys.stdout.write(f"  {symbol} {path_str} ({summary})\n")
            self.completed += 1
            self._render_progress()

    def _render_progress(self):
        if self.total == 0:
            return
        percent = self.completed / self.total
        bar_width = 30
        filled = int(bar_width * percent)
        bar = "=" * filled + (">" if filled < bar_width else "") + " " * max(0, bar_width - filled - 1)
        if USE_COLOR:
            sys.stdout.write(
                f"\r{CLEAR_LINE}Progress: [{bar}] {int(percent * 100)}% ({self.completed}/{self.total})"
            )
        else:
            sys.stdout.write(f"Progress: {self.completed}/{self.total} ({int(percent * 100)}%)\r")
        sys.stdout.flush()

    def finish(self):
        with self.lock:
            if USE_COLOR:
                sys.stdout.write(f"\r{CLEAR_LINE}")
            else:
                sys.stdout.write("\n")
            sys.stdout.flush()


def find_git_repos(root_dir: Path) -> list[Path]:
    """Find all git repository directories under root_dir."""
    repos = []
    try:
        for git_dir in root_dir.rglob(".git"):
            if git_dir.is_dir() and not any(part == ".git" for part in git_dir.parent.parts):
                repos.append(git_dir.parent.resolve())
    except PermissionError:
        pass
    return sorted(list(set(repos)))


def update_repo(repo_path: Path) -> dict:
    """Run git pull on a repository and return execution status and log."""
    start_time = time.time()
    try:
        # Check if repository has any remotes configured
        remotes_check = subprocess.run(
            ["git", "remote"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if remotes_check.returncode == 0 and not remotes_check.stdout.strip():
            return {
                "name": repo_path.name,
                "path": str(repo_path),
                "status": "no_remote",
                "symbol_cli": SYM_NO_REMOTE,
                "summary": "No remote configured",
                "output": "$ git remote\n(No remote repository configured for this repository)",
                "elapsed": round(time.time() - start_time, 2),
            }

        res = subprocess.run(
            ["git", "pull", "--stat"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        stdout = res.stdout.strip()
        stderr = res.stderr.strip()
        code = res.returncode
        elapsed = round(time.time() - start_time, 2)

        raw_output = f"$ git pull --stat\n{stdout}"
        if stderr:
            raw_output += f"\n\n[stderr]\n{stderr}"

        if code != 0:
            err_lower = stderr.lower()
            if "no tracking information" in err_lower or "no remote repository" in err_lower or "specify which branch" in err_lower:
                status = "no_remote"
                symbol = SYM_NO_REMOTE
                summary = "No tracking branch configured"
            else:
                status = "failed"
                symbol = SYM_FAILED
                summary = stderr.splitlines()[-1] if stderr else f"Exit code {code}"
        elif "Already up to date." in stdout or "Already up-to-date." in stdout:
            status = "up_to_date"
            symbol = SYM_OK
            summary = "Already up to date"
        else:
            status = "updated"
            symbol = SYM_UPDATED
            lines = [line for line in stdout.splitlines() if line.strip()]
            stat_lines = [line for line in lines if "changed" in line]
            summary = stat_lines[-1].strip() if stat_lines else (lines[-1] if lines else "Updated successfully")

            # Fetch recent commits log for updated repos
            log_res = subprocess.run(
                ["git", "log", "-n", "5", "--oneline"],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )
            if log_res.stdout:
                raw_output += f"\n\n$ git log -n 5 --oneline\n{log_res.stdout.strip()}"

        return {
            "name": repo_path.name,
            "path": str(repo_path),
            "status": status,
            "symbol_cli": symbol,
            "summary": summary,
            "output": raw_output,
            "elapsed": elapsed,
        }

    except subprocess.TimeoutExpired:
        return {
            "name": repo_path.name,
            "path": str(repo_path),
            "status": "failed",
            "symbol_cli": SYM_FAILED,
            "summary": "Operation timed out (60s)",
            "output": "Error: Command 'git pull' timed out after 60 seconds.",
            "elapsed": 60.0,
        }
    except Exception as e:
        return {
            "name": repo_path.name,
            "path": str(repo_path),
            "status": "failed",
            "symbol_cli": SYM_FAILED,
            "summary": str(e),
            "output": f"Error: {e}",
            "elapsed": 0.0,
        }


def format_git_output_html(text: str) -> str:
    """Format git command output with Git terminal-like HTML syntax highlighting."""
    escaped = html.escape(text)
    formatted_lines = []
    for line in escaped.splitlines():
        if line.startswith("$ "):
            line = f'<span class="git-cmd">{line}</span>'
        elif line.startswith("+") and not line.startswith("+++"):
            line = f'<span class="git-add">{line}</span>'
        elif line.startswith("-") and not line.startswith("---"):
            line = f'<span class="git-del">{line}</span>'
        elif "|" in line and any(c in line for c in ["+", "-"]):
            parts = line.split("|", 1)
            left = parts[0]
            right = parts[1]
            right_colored = re.sub(
                r"[+-]",
                lambda m: '<span class="git-add">+</span>' if m.group(0) == "+" else '<span class="git-del">-</span>',
                right
            )
            line = f"{left}|{right_colored}"
        elif "changed" in line and ("insertion" in line or "deletion" in line):
            line = re.sub(r"(\d+\s+insertion[s]?\(\+\))", r'<span class="git-add">\1</span>', line)
            line = re.sub(r"(\d+\s+deletion[s]?\(-\))", r'<span class="git-del">\1</span>', line)
        elif any(err in line.lower() for err in ["error:", "fatal:", "conflict", "automatic merge failed"]):
            line = f'<span class="git-err">{line}</span>'
        elif "Already up to date" in line:
            line = f'<span class="git-dim">{line}</span>'

        # Highlight commit hashes at line start (e.g. in git log)
        line = re.sub(r"^([0-9a-f]{7,40})\b", r'<span class="git-cmd">\1</span>', line)
        formatted_lines.append(line)
    return "\n".join(formatted_lines)


def format_summary_html(summary: str, status: str) -> str:
    """Format repo summary with HTML syntax highlighting for stats and errors."""
    escaped = html.escape(summary)
    if status == "updated":
        escaped = re.sub(
            r"(\d+\s+insertion[s]?\(\+\))",
            r'<span class="git-add">\1</span>',
            escaped
        )
        escaped = re.sub(
            r"(\d+\s+deletion[s]?\(-\))",
            r'<span class="git-del">\1</span>',
            escaped
        )
        return escaped
    elif status == "failed":
        return f'<span class="summary-err">{escaped}</span>'
    elif status == "up_to_date":
        return f'<span class="summary-uptodate">{escaped}</span>'
    elif status == "no_remote":
        return f'<span class="summary-noremote">{escaped}</span>'
    return escaped


def generate_html_report(results: list[dict], root_dir: Path, output_path: Path):
    """Generate static HTML dashboard report."""
    total = len(results)
    updated = sum(1 for r in results if r["status"] == "updated")
    up_to_date = sum(1 for r in results if r["status"] == "up_to_date")
    no_remote = sum(1 for r in results if r["status"] == "no_remote")
    failed = sum(1 for r in results if r["status"] == "failed")
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S")

    rows_html = []
    for idx, r in enumerate(results):
        status = r["status"]
        status_label = {
            "updated": "Updated",
            "up_to_date": "Up-to-Date",
            "no_remote": "No Remote",
            "failed": "Failed",
        }.get(status, status)

        sym_char = {"updated": "+", "up_to_date": "✓", "no_remote": "•", "failed": "✗"}.get(status, "•")
        formatted_output = format_git_output_html(r["output"])
        formatted_summary = format_summary_html(r["summary"], status)

        rows_html.append(f"""
        <div class="repo-card status-{status}" data-status="{status}" data-search="{html.escape((r['name'] + ' ' + r['path'] + ' ' + r['summary']).lower())}">
            <div class="repo-header" onclick="toggleDetails('{idx}', event)">
                <span class="badge badge-{status}">
                    <span class="symbol">{sym_char}</span> {status_label}
                </span>
                <div class="repo-info">
                    <span class="repo-name">{html.escape(r['name'])}</span>
                    <span class="repo-path">{html.escape(r['path'])}</span>
                </div>
                <div class="repo-summary">{formatted_summary}</div>
                <span class="toggle-icon" id="icon-{idx}">▼</span>
            </div>
            <div class="repo-details" id="details-{idx}">
                <pre class="git-console"><code>{formatted_output}</code></pre>
                <div class="repo-details-actions">
                    <button type="button" class="action-btn" onclick="closeDetails('{idx}')">▲ Close</button>
                    <button type="button" class="action-btn" onclick="scrollToTop()">↑ Jump to Top</button>
                </div>
            </div>
        </div>
        """)

    template_file = Path(__file__).parent / "report_template.html"
    if template_file.exists():
        template = template_file.read_text(encoding="utf-8")
    else:
        template = "<html><body><h1>Git Update Dashboard</h1>{{rows_html}}</body></html>"

    html_content = (
        template.replace("{{root_dir}}", html.escape(str(root_dir)))
        .replace("{{generated_at}}", generated_at)
        .replace("{{total}}", str(total))
        .replace("{{up_to_date}}", str(up_to_date))
        .replace("{{updated}}", str(updated))
        .replace("{{no_remote}}", str(no_remote))
        .replace("{{failed}}", str(failed))
        .replace("{{rows_html}}", "".join(rows_html))
    )

    output_path.write_text(html_content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Update all Git repositories in parallel and generate a static HTML dashboard.")
    parser.add_argument("path", nargs="?", default=".", help="Root directory to scan for Git repositories (default: current directory)")
    parser.add_argument("-j", "--jobs", type=int, default=8, help="Number of parallel update workers (default: 8)")
    parser.add_argument("-o", "--output", default="git_update_report.html", help="Output HTML report path (default: git_update_report.html)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    args = parser.parse_args()

    root_dir = Path(args.path).resolve()
    output_path = Path(args.output).resolve()

    print(f"Scanning for Git repositories in: {root_dir}...")
    repos = find_git_repos(root_dir)

    if not repos:
        print("No Git repositories found.")
        sys.exit(0)

    print(f"Found {len(repos)} Git repositories. Processing with {args.jobs} workers...\n")

    progress = TerminalProgress(len(repos))
    results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        future_to_repo = {executor.submit(update_repo, r): r for r in repos}
        for future in concurrent.futures.as_completed(future_to_repo):
            res = future.result()
            results.append(res)
            progress.log_finished(res["symbol_cli"], res["path"], res["summary"])

    progress.finish()

    # Sort results: failed first, then updated, then up_to_date
    status_order = {"failed": 0, "updated": 1, "up_to_date": 2}
    results.sort(key=lambda r: (status_order.get(r["status"], 3), r["name"].lower()))

    generate_html_report(results, root_dir, output_path)
    print(f"\nReport generated: {output_path}")

    if not args.no_browser:
        webbrowser.open(output_path.as_uri())


if __name__ == "__main__":
    main()

"""Surgical, validated TOML feature edits with backups and first-install state."""

from __future__ import annotations
import argparse, datetime, json, os, re, tempfile, tomllib
from pathlib import Path


def set_value(text, table, key, value):
    parsed = tomllib.loads(text)
    if table:
        header = re.search(
            r"(?m)^\s*\[" + re.escape(table) + r"\]\s*(?:#[^\r\n]*)?$", text
        )
        if not header:
            if table in parsed:
                raise ValueError(
                    "Refusing to rewrite an inline/nonstandard "
                    + table
                    + " table; edit that key manually"
                )
            if value is None:
                return text
            return text.rstrip() + "\n\n[" + table + "]\n" + key + " = " + value + "\n"
        start = header.end()
        next_header = re.search(r"(?m)^\s*\[", text[start:])
        end = start + next_header.start() if next_header else len(text)
    else:
        start = 0
        next_header = re.search(r"(?m)^\s*\[", text)
        end = next_header.start() if next_header else len(text)
    section = text[start:end]
    pattern = r"(?m)^([ \t]*)" + re.escape(key) + r"[ \t]*=[^\r\n]*(?:\r?\n|$)"
    replacement = "" if value is None else key + " = " + value + "\n"
    if re.search(pattern, section):
        section = re.sub(pattern, lambda _: replacement, section, count=1)
    elif value is not None:
        section = section.rstrip() + "\n" + replacement
    result = text[:start] + section + text[end:]
    tomllib.loads(result)
    return result


def atomic_write(path, content, expected=None):
    if expected is not None and path.read_bytes() != expected:
        raise RuntimeError("Configuration changed concurrently; rerun the update")
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".bridge-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if expected is not None and path.read_bytes() != expected:
            raise RuntimeError("Configuration changed concurrently")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def configure(config_path, state_path, restore=False):
    before = config_path.read_bytes()
    text = before.decode("utf-8-sig")
    data = tomllib.loads(text)
    if restore:
        if not state_path.exists():
            return
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if data.get("features", {}).get("standalone_web_search") is not True:
            return
        previous = state["previousStandaloneWebSearch"]
        raw = None if previous is None else ("true" if previous else "false")
        text = set_value(text, "features", "standalone_web_search", raw)
    else:
        if not state_path.exists():
            state = {
                "previousStandaloneWebSearch": data.get("features", {}).get(
                    "standalone_web_search"
                )
            }
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        text = set_value(text, "features", "standalone_web_search", "true")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    config_path.with_name(
        config_path.name + ".backup-tool-bridge-" + stamp
    ).write_bytes(before)
    atomic_write(config_path, text.encode("utf-8"), before)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--restore", action="store_true")
    args = p.parse_args()
    configure(Path(args.config), Path(args.state), args.restore)
    print(
        "Standalone-search configuration " + ("restored" if args.restore else "enabled")
    )


if __name__ == "__main__":
    main()

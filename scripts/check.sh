#!/bin/sh
# XX-WM - Pre-commit gate script
# Runs py_compile on all sources + pytest + shellcheck

set -e

echo "=== Checking Python syntax ==="
python3 -m py_compile launcher/src/*.py || {
    echo "Python syntax check FAILED"
    exit 1
}

echo "=== Running ruff ==="
if command -v ruff >/dev/null 2>&1; then
    ruff check launcher/src tests || {
        echo "ruff FAILED"
        exit 1
    }
else
    echo "ruff not available, skipping"
fi

echo "=== Running pytest ==="
if command -v pytest >/dev/null 2>&1; then
    python3 -m pytest tests/ -q || {
        echo "pytest FAILED"
        exit 1
    }
else
    echo "pytest not available, skipping"
fi

echo "=== Running shellcheck ==="
if command -v shellcheck >/dev/null 2>&1; then
    for f in scripts/*.sh devices/*/*.sh; do
        if [ -f "$f" ]; then
            # -s sh, or -s bash where the shebang says bash
            dialect="sh"
            case "$(head -1 "$f")" in
                *bash*) dialect="bash" ;;
            esac
            shellcheck -s "$dialect" "$f" || {
                echo "shellcheck FAILED on $f"
                exit 1
            }
        fi
    done
else
    echo "shellcheck not available, skipping"
fi

echo "=== All checks passed ==="

"""Claude Code lifecycle hook entry point. install_claude_hooks.py points settings.json here."""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.environ.setdefault('CORTEX_VAULT', str(root / 'my-vault'))
from cortex.claude_hook import main

if __name__ == '__main__':
    main()

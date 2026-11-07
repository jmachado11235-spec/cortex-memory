"""Add the Cortex hooks to ~/.claude/settings.json, keeping every unrelated hook and setting."""
import json
import sys
from pathlib import Path

entry = Path(__file__).resolve().parent / 'claude_cortex_hook.py'
settings_path = Path.home() / '.claude' / 'settings.json'
data = json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.is_file() else {}
hooks = data.setdefault('hooks', {})
command = f'"{sys.executable}" "{entry}"'

for event in ('SessionStart', 'UserPromptSubmit', 'Stop', 'PreCompact', 'SessionEnd'):
    kept = []
    for group in hooks.get(event, []):
        members = [h for h in group.get('hooks', []) if 'claude_cortex_hook.py' not in json.dumps(h)]
        if members:
            kept.append({**group, 'hooks': members})
    hook = {'type': 'command', 'command': command, 'timeout': 3 if event == 'SessionEnd' else 30}
    if event == 'Stop':
        hook['statusMessage'] = 'Verifying Cortex memory'
    hooks[event] = [{'hooks': [hook]}] + kept

settings_path.parent.mkdir(parents=True, exist_ok=True)
settings_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
print(f'Cortex hooks installed in {settings_path}. Restart Claude Code and approve them in /hooks.')

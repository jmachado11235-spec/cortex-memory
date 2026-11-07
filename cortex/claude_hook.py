"""Claude lifecycle adapter; shared checkpoint engine owns all save decisions."""
import json
import sys
from cortex import checkpoint

def main():
    try:
        data=json.load(sys.stdin); event=data.get('hook_event_name')
        result={}
        if event=='SessionStart':
            result={'hookSpecificOutput':{'hookEventName':event,'additionalContext':
                f'Read {checkpoint.VAULT / "CLAUDE.md"} for shared memory rules. '
                f'Canonical current state is at {checkpoint.VAULT / "state/INDEX.md"}. '
                'Old Claude memory files are pointers. Save authorized durable work at each turn; '
                'Stop supplies a per-turn verification token. Review-only/no-write requests forbid '
                'curated memory and pending-task writes; proposals are not approved tasks.'}}
        elif event=='UserPromptSubmit': checkpoint.remember_prompt(data,'claude')
        elif event=='Stop':
            result=checkpoint.handle_stop(data,'claude')
            # Light blue in the terminal (Juvenal, 2026-09-22). Claude-only: Codex gets the plain text.
            if result.get('reason'): result['reason']='[38;5;117m'+result['reason']+'[0m'
        elif event in ('PreCompact','SessionEnd'): checkpoint.archive(data,'claude')
        print(json.dumps(result,ensure_ascii=True))
    except Exception as exc:
        print(json.dumps({'systemMessage':f'Cortex memory hook failed: {type(exc).__name__}: {exc}'},ensure_ascii=True))

if __name__=='__main__': main()

"""Shared Claude/Codex checkpoints. Only a verified per-turn receipt means saved.

Requests survive failed continuations. No clock-based skip, global-index-mtime
shortcut, or implicit acceptance of proposed tasks. Uses only named files/indexes.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from cortex.config import get_vault_path
from cortex.storage import atomic_json, atomic_bytes, index_lock

VAULT = get_vault_path()

def read_json(path, default=None):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError): return default if default is not None else {}

def digest(value):
    return hashlib.sha256(value).hexdigest()

def state_dir():
    return VAULT/'index/checkpoints'

def session_path(source, session):
    return state_dir()/('session-'+digest(f'{source}:{session}'.encode())[:24]+'.json')

def request_path(token):
    if not re.fullmatch(r'[a-f0-9]{32}', token): raise ValueError('Invalid checkpoint token')
    return state_dir()/(token+'.json')

# Deliberately narrow. A false positive skips a real save, so each branch needs an
# explicit no-write phrase, and scoped limits stay out: "...anything else" and
# "...the other files" accompany ordinary work. A phrase match is necessary but
# not sufficient - review_only() then requires it to govern the WHOLE request.
_NEGATION = r"(?:don['’]?t|do\s+not|dont|never|without|avoid|refrain\s+from|no)"
# Objects broad enough to mean the work itself, never one named subsystem.
_GLOBAL = r"(?:anything|any\s*thing|any\s+(?:files?|code|notes?)|the\s+files?|nada)"
# "make changes" carries no object, so it must end the clause; otherwise
# "don't make any changes to the other files" would read as review-only.
_TERMINAL = r"(?=$|[.,;:!?)]|\s+(?:yet|at\s+all|for\s+now|whatsoever|please|just)\b)"
_SCOPED = r"(?!\s*(?:else|either|other|more|extra|beyond|besides|apart|further)\b)"
_WRITE = (
    r"(?:mak\w*\s+(?:any\s+)?chang\w*" + _TERMINAL + r"|"
    r"(?:chang|edit|modify|modif|writ|touch|alter|updat|creat|delet|remov|fix)\w*\s+"
    + _GLOBAL + _SCOPED + r"|"
    r"(?:sav|writ)\w*\s+(?:anything|(?:to\s+)?memory|to\s+cortex)" + _SCOPED + r"|"
    r"implement\w*\s+(?:anything|yet))"
)
REVIEW_ONLY = re.compile(
    r"\b" + _NEGATION + r"\s+" + _WRITE + r"|"
    # Explicit mode labels, in either order: "read-only audit", "audit ... read-only".
    r"\b(?:review|analysis|audit|inspection|look)[ -]only\b|"
    r"\bread[ -]only\b[^.!?]{0,40}\b(?:review|analys\w+|audit|inspect\w+|check|pass)\b|"
    r"\b(?:review|analys\w+|audit|inspect\w+|check|pass)\b[^.!?]{0,40}\bread[ -]only\b|"
    # Bare statements of scope, which must also end the clause.
    r"\bno\s+(?:changes|writes|edits)\b" + _TERMINAL + r"|"
    r"\b(?:sin\s+cambios|sin\s+(?:modificar|cambiar|tocar|escribir)\s+nada|"
    r"no\s+(?:cambies|modifiques|toques|escribas|guardes)\s+nada|solo\s+revisa)\b",
    re.I)

# Quoted spans are things being discussed, not instructions: "fix the detector so
# that <quote>without changing anything<quote> is recognized" is a request to work.
# Straight ' is left alone - it collides with the apostrophe in "don't".
_QUOTED = re.compile(r"\"[^\"]*\"|\u201c[^\u201d]*\u201d|\u2018[^\u2019]*\u2019|`[^`]*`")
# Any imperative to produce or alter something means the restriction covers part of
# the request, not all of it. Being liberal here is the safe direction: a spurious
# hit only costs a checkpoint prompt the agent can answer with --no-change, while a
# missed one silently skips a real save.
_AUTHORIZED_WORK = re.compile(
    r"\b(?:add|implement|build|create|write|fix|repair|refactor|rename|delete|remove|"
    r"install|apply|patch|migrate|generate|commit|push|deploy|wire|enable|disable|"
    r"configure|set\s+up|setup|update|edit|modify|replace|rewrite|extend|improve|"
    r"optimi[sz]e|bump|revert|restore|merge|rebase|"
    # This vault's own workflow vocabulary, which the checkpoint prompt itself
    # uses: "rebuild the index and record what you found" was authorizing work
    # that no verb here matched, so the save was skipped in silence.
    r"rebuild|save|record|"
    r"arregla\w*|implementa\w*|agrega\w*|a[nñ]ade\w*|crea\w*|escribe|corrige|actualiza\w*|"
    # Imperatives only. The -r infinitive is how Spanish phrases a RESTRICTION
    # ("sin modificar nada", "dime si hay que cambiar algo"), so matching it here
    # would read the restriction itself as permission to work.
    r"(?:modifica|cambia|guarda|registra|reconstruye|elimina|borra)(?!r)\w*)\b",
    re.I)

def review_only(prompt):
    """True only when a no-write instruction governs the whole request.

    A phrase alone is not enough. "Do not change anything in the engine; fix the
    documentation instead" and "Add tests for read-only audit mode" both contain
    one, yet authorize work - and skipping the checkpoint there loses a real save.
    """
    text=_QUOTED.sub(' ', prompt)
    match=REVIEW_ONLY.search(text)
    if not match: return False
    rest=text[:match.start()]+' '+text[match.end():]
    return not _AUTHORIZED_WORK.search(rest)

def remember_prompt(data, source):
    prompt = str(data.get('prompt') or '')
    if not prompt or '[AUTOMATIC CORTEX CHECKPOINT]' in prompt: return
    sid = str(data.get('session_id') or '')
    if not sid: return
    turn = str(data.get('turn_id') or data.get('prompt_id') or uuid.uuid4().hex)
    p = session_path(source, sid)
    old = read_json(p)
    # Identical text on a later turn is still a new unit of work.
    atomic_json(p, {'source':source,'session_id':sid,'turn':turn,'prompt':prompt,
                    'started_at':time.time(),'pending':old.get('pending',[])})

def last_user_text(transcript):
    """Fallback for sessions started before this hook was installed."""
    text=''
    if not transcript or not Path(transcript).is_file(): return text
    with Path(transcript).open(encoding='utf-8',errors='replace') as f:
        for line in f:
            try: row=json.loads(line)
            except ValueError: continue
            p=row.get('payload') or row.get('message') or {}
            candidate=''
            if p.get('type')=='user_message': candidate=p.get('message','')
            elif p.get('role')=='user' or row.get('type')=='user':
                content=p.get('content','')
                if isinstance(content,str): candidate=content
                elif isinstance(content,list):
                    candidate='\n'.join(c.get('text','') for c in content if c.get('type') in ('text','input_text'))
            if candidate and '[AUTOMATIC CORTEX CHECKPOINT]' not in candidate:
                text=candidate
    return text

def archive(data, source):
    path=Path(data.get('transcript_path') or '')
    if not path.is_file(): return None
    sid=digest(str(data.get('session_id') or path).encode())[:24]
    target=VAULT/'session-archive'/source/(sid+'.jsonl')
    # One stable recovery copy per session; no repeated full copies per day.
    atomic_bytes(target,path.read_bytes())
    return str(target)

def verify_files(paths):
    """Check saved bytes against BOTH indexes from a completed build."""
    manifest_path=VAULT/'index/manifest.json'
    generation=read_json(VAULT/'index/generation.json')
    if not generation.get('fts') or generation.get('manifest_sha256')!=digest(manifest_path.read_bytes()):
        raise ValueError('No complete matching JSON + full-text index generation; rebuild Cortex')
    manifest={x['path'].replace('\\','/'):x for x in read_json(manifest_path,[])}
    hashes={}
    con=sqlite3.connect((VAULT/'index/fts.db').as_uri()+'?mode=ro',uri=True)
    try:
        for rel in paths:
            path=(VAULT/rel).resolve()
            if not path.is_relative_to(VAULT.resolve()) or not path.is_file():
                raise ValueError(f'Not a file inside this vault: {rel}')
            key=path.relative_to(VAULT.resolve()).as_posix()
            value=digest(path.read_bytes()); entry=manifest.get(key)
            if not entry or entry.get('sha256')!=value:
                raise ValueError(f'File is not current in manifest: {key}')
            row=con.execute('SELECT sha256 FROM note_meta WHERE path=?',(entry['path'],)).fetchone()
            if not row or row[0]!=value: raise ValueError(f'File is not current in body index: {key}')
            hashes[key]=value
    finally: con.close()
    return hashes

def receipt_valid(request):
    """Did acknowledge() verify this request? The receipt is a historical record.

    acknowledge() is the only writer of a receipt and hashes the files against both
    indexes before stamping verified_at. Later edits to those same files are new
    work, not a failure of this turn, so this never re-hashes: doing so reported
    finished turns as pending and told the next turn to re-save them from the
    transcript. Use receipt_current() for "do those bytes still exist".
    """
    receipt=request.get('receipt') or {}
    if not receipt.get('verified_at'): return False
    if receipt.get('kind')=='no-change': return bool(receipt.get('reason'))
    return receipt.get('kind')=='saved' and bool(receipt.get('files'))

def receipt_state(request):
    """Vault integrity behind a saved receipt, never this turn's status.

    current      the acknowledged bytes are on disk and in both indexes
    changed      edited AND reindexed - ordinary later work on the same file
    stale-index  edited but NOT reindexed; recall serves text nobody indexed
    unindexed    acknowledged bytes intact, yet the index no longer matches
    missing      the file is gone
    unreadable   the file exists but could not be read

    Splitting 'changed' from 'stale-index' is the point: both mean the bytes
    moved on, but only one of them leaves the index lying about the file right
    now, and one shared status hid that fault behind ordinary edits. Read
    failures are reported rather than raised, so one locked file cannot take
    down the whole `pending --stale` report.
    """
    receipt=request.get('receipt') or {}
    if receipt.get('kind')!='saved': return 'current'
    files=receipt.get('files') or {}
    # One call: it answers both "is the receipt still current" and, below,
    # "has the index caught up with whatever is on disk now".
    try: indexed=verify_files(list(files))
    except (OSError,ValueError,KeyError,sqlite3.Error): indexed=None
    if indexed==files: return 'current'
    on_disk={}
    for rel in files:
        # No separate is_file() stat: on Windows it returns False for a file it
        # merely cannot stat, which would report a locked file as deleted.
        try: on_disk[rel]=digest((VAULT/rel).read_bytes())
        except FileNotFoundError: return 'missing'
        except OSError: return 'unreadable'
    if on_disk==files: return 'unindexed'
    return 'changed' if indexed==on_disk else 'stale-index'

def receipt_current(request):
    """True while a saved receipt's files still hold the acknowledged bytes."""
    return receipt_state(request)=='current'

def acknowledge(token, files=None, no_change=None):
    p=request_path(token); request=read_json(p)
    if not request: raise ValueError('Checkpoint request not found')
    if files and no_change: raise ValueError('Choose files or no-change, not both')
    if no_change:
        if len(no_change.strip())<12: raise ValueError('Explain why no durable change occurred')
        receipt={'kind':'no-change','reason':no_change}
    elif files:
        with index_lock(VAULT/'index'):
            receipt={'kind':'saved','files':verify_files(files)}
    else: raise ValueError('Supply --files or --no-change with a reason')
    receipt['verified_at']=time.time()
    request['receipt']=receipt
    atomic_json(p,request)
    return receipt

def checkpoint_prompt(request):
    # Two lines on purpose. Everything cut from here is the Write protocol in CLAUDE.md, restated
    # verbatim - and this prompt already sends you there. What stays is what cannot be looked up:
    # the token, the exact command, and the prior requests still owed.
    token=request['token']; pending=request.get('prior_pending',[])
    extra=('\nPrior unverified: '+', '.join(pending)+
           ' - recover each from index/checkpoints plus this transcript, acknowledge separately.'
           ) if pending else ''
    return (f'[CORTEX] Memory maintenance for THIS turn only - follow the Write protocol in '
            f'"{VAULT / "CLAUDE.md"}". Review-only/no-write turns curate nothing. '
            f'Do not repeat your answer to the user.\n'
            f'python -m cortex.checkpoint acknowledge --token {token} --files <relative.md> ...'
            f'   (nothing durable: --no-change "<reason>"){extra}')

def handle_stop(data, source):
    archived=archive(data,source)
    sid=str(data.get('session_id') or '')
    if not sid: return {'systemMessage':'Cortex: missing session ID; checkpoint not verified.'}
    sp=session_path(source,sid); state=read_json(sp)
    if not state:
        state={'source':source,'session_id':sid,
               'turn':str(data.get('turn_id') or data.get('prompt_id') or uuid.uuid4().hex),
               'prompt':last_user_text(data.get('transcript_path')),'pending':[]}
    prompt=state.get('prompt','')
    if review_only(prompt):
        return {}  # raw recovery only; absolutely no atom or task request
    if re.fullmatch(r'\s*(?:ok(?:ay)?|thanks|thank you|got it)[.!\s]*',prompt,re.I): return {}
    request=None
    if state.get('request'):
        request=read_json(request_path(state['request']))
    if request and receipt_valid(request): return {}
    if not request:
        token=uuid.uuid4().hex
        prior=[t for t in state.get('pending',[]) if not receipt_valid(read_json(request_path(t)))]
        request={'token':token,'source':source,'session_id':sid,'turn':state['turn'],
                 'prompt':prompt,'archive':archived,'started_at':time.time(),
                 'attempts':0,'prior_pending':prior}
        state['request']=token; state['pending']=prior+[token]
        atomic_json(sp,state)
    # Give a failed verification one repair pass; never spin forever.
    if request['attempts']>=2:
        atomic_json(request_path(request['token']),request)
        return {'systemMessage':'Cortex checkpoint remains UNVERIFIED: '+request['token']+
                '. Raw recovery retained; retry with cortex.checkpoint acknowledge.'}
    request['attempts']+=1
    atomic_json(request_path(request['token']),request)
    return {'decision':'block','reason':checkpoint_prompt(request)}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    ack=sub.add_parser('acknowledge'); ack.add_argument('--token',required=True)
    ack.add_argument('--files',nargs='+'); ack.add_argument('--no-change')
    pend=sub.add_parser('pending')
    pend.add_argument('--stale',action='store_true',
                      help='also classify acknowledged turns: changed, stale-index, '
                           'unindexed, missing, or unreadable')
    args=parser.parse_args()
    if args.command=='acknowledge':
        print(json.dumps(acknowledge(args.token,args.files,args.no_change),ensure_ascii=True))
    else:
        for p in sorted(state_dir().glob('*.json')):
            r=read_json(p)
            if not r.get('token'): continue
            if receipt_valid(r):
                status=receipt_state(r) if args.stale else 'current'
                if status=='current': continue  # acknowledged; nothing owed
            else: status='unverified'
            print(json.dumps({'status':status,
                              **{k:r.get(k) for k in ('token','source','session_id','turn','started_at')}}))

if __name__=='__main__': main()

"""Read-only integrity and routing checks. Dates alone never prove coverage.

By default this proves that indexed files exist, that map links resolve, and
that the core documents still match both indexes. It does NOT prove content
agreement for every note: a note edited without a rebuild passes silently while
recall serves the stale text. Use --verify-hashes to check them all.
"""
import argparse
import json
import re
from pathlib import Path
from cortex.config import get_vault_path
from cortex.index_builder import parse_frontmatter
from cortex.checkpoint import verify_files
from cortex.storage import atomic_json

# Notes per verify_files() call. One call reloads the manifest and reopens the
# FTS database, so per-note calls would make a full pass unusable on a real vault.
HASH_BATCH=200

def verify_batched(v, paths):
    """Hash paths against both indexes in batches under vault v.

    verify_files raises on the FIRST bad path in a batch, leaving the rest of
    that batch unchecked. Those are counted as unchecked, never as verified:
    a partial pass must not read as a clean bill of health.
    """
    from cortex import checkpoint
    original=checkpoint.VAULT; checkpoint.VAULT=v
    verified=unchecked=0; errors=[]
    try:
        for i in range(0,len(paths),HASH_BATCH):
            batch=paths[i:i+HASH_BATCH]
            try: verified+=len(verify_files(batch))
            except Exception as exc:
                rest=len(batch)-1
                errors.append(f'{exc}{f" (+{rest} more in this batch unchecked)" if rest else ""}')
                unchecked+=len(batch)
    finally: checkpoint.VAULT=original
    return verified,unchecked,errors

def inspect(vault=None, project_filter=None, verify_hashes=False):
    v=vault or get_vault_path()
    manifest=json.loads((v/'index/manifest.json').read_text(encoding='utf-8'))
    indexed={r['path'].replace('\\','/'):r for r in manifest}
    maps={}; links={}; errors=[]; warnings=[]
    for p in sorted((v/'_moc').glob('*.md')):
        text=p.read_text(encoding='utf-8'); fm=parse_frontmatter(text)
        covers=fm.get('covers',[])
        if isinstance(covers,str): covers=[]
        targets=set(re.findall(r'(?:atoms|incidents|insights|reference|state|_moc)/[\w./-]+\.md',text))
        for project in [fm.get('project'),*covers]:
            maps.setdefault(project,[]).append(p); links.setdefault(project,set()).update(targets)
        for target in targets:
            if not (v/target).exists(): errors.append(f'{p.name}: broken link {target}')
    coverage_path=v/'state/coverage.json'
    coverage=json.loads(coverage_path.read_text(encoding='utf-8')) if coverage_path.exists() else {}
    linked_hashes={}
    for rel,note in indexed.items():
        if project_filter and note.get('project')!=project_filter: continue
        if not (v/rel).exists(): errors.append(f'Indexed file missing: {rel}')
        if note.get('status')!='active' or not rel.startswith(('atoms/','incidents/','insights/','reference/')): continue
        project=note.get('project')
        if project not in maps: errors.append(f'No map owns {project}: {rel}'); continue
        if rel in links.get(project,set()):
            linked_hashes[rel]=note.get('sha256')
            if rel in coverage and coverage[rel]!=note.get('sha256'):
                warnings.append(f'Mapped note changed since coverage review: {rel}')
        else:
            warnings.append(f'Active note is not linked by its project map: {rel}')
    # Acknowledged checkpoint hashes provide evidence of indexing, not semantic correctness.
    errors.extend(verify_batched(v,['CLAUDE.md','CONTEXT.md','state/INDEX.md'])[2])
    # Everything indexed, not just map-linked notes: state/*.md and unmapped
    # historical notes are absent from linked_hashes yet are served by recall
    # just the same, so a stale index under them is equally wrong.
    targets=sorted(rel for rel,note in indexed.items()
                   if not project_filter or note.get('project')==project_filter) if verify_hashes else []
    verified,unchecked,hash_errors=verify_batched(v,targets)
    errors.extend(hash_errors)
    return {'errors':errors,'warnings':warnings,'notes':len(indexed),
            'mapped_active_notes':len(linked_hashes),'linked_hashes':linked_hashes,
            'hashed':verify_hashes,'targeted':len(targets),
            'verified':verified,'unchecked':unchecked}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--detail',action='store_true')
    ap.add_argument('--project')
    ap.add_argument('--record-coverage',action='store_true',help='Record hashes after reviewing linked project-map coverage')
    ap.add_argument('--verify-hashes',action='store_true',
                    help='Hash every indexed note against both indexes, not just the core documents')
    args=ap.parse_args(); result=inspect(project_filter=args.project,verify_hashes=args.verify_hashes)
    if args.record_coverage:
        if result['errors']: raise SystemExit('Resolve integrity/link errors before recording coverage')
        atomic_json(get_vault_path()/'state/coverage.json',result['linked_hashes'])
    # Say which question was answered. Without --verify-hashes this run proves
    # that indexed files exist and that links resolve; it does NOT prove that
    # any note except the core documents still matches what the indexes hold.
    scope=(f"{result['verified']}/{result['targeted']} note bodies hash-verified"
           if result['hashed'] else
           'existence and links only; core documents hashed (--verify-hashes for every note)')
    print(f"Cortex: {result['notes']} notes; {len(result['errors'])} integrity/link errors; "
          f"{len(result['warnings'])} routing review items; {result['mapped_active_notes']} active notes linked; "
          f"{scope}.")
    if result['unchecked']:
        print(f"INCOMPLETE {result['unchecked']} notes were not hash-checked; batches stop at the first bad path.")
    for error in result['errors']: print('ERROR '+error)
    if args.detail:
        for warning in result['warnings']: print('REVIEW '+warning)
    elif result['warnings']: print('Use --detail; unmapped historical notes are not silently certified as current.')
    return 1 if result['errors'] else 0

if __name__=='__main__': raise SystemExit(main())

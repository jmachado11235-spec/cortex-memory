import io,json,os,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from cortex import smart_loader as s, checkpoint as c

def note(path,project='meta',status='active'):
    return dict(path=path,name='memory cortex',description='memory cortex',project=project,
                status=status,type='event',tags=['memory'],updated='2026-09-08')

class SearchReliability(unittest.TestCase):
    def test_project_and_status_filter_apply_before_ranking(self):
        rows=[note('a','d2g'),note('b','meta','superseded'),note('c')]
        self.assertEqual([r['path'] for r in s.search_keywords(rows,['memory'],project='meta',use_fts=False)],['c'])
        self.assertEqual(len(s.search_keywords(rows,['memory'],project='meta',status=None,use_fts=False)),2)

    def test_ties_do_not_invent_fts_effect(self):
        rows=[note('z'),note('a')]
        results=s.search_keywords(rows,['memory'],use_fts=False)
        d=s.retrieval_diagnostics(rows,['memory'],results,10,False)
        self.assertFalse(d['top_changed']); self.assertEqual(d['baseline_top'],results[0]['path'])

    def test_diagnostics_respect_same_filters(self):
        rows=[note('a','d2g'),note('z')]
        results=s.search_keywords(rows,['memory'],project='meta',use_fts=False)
        self.assertFalse(s.retrieval_diagnostics(rows,['memory'],results,10,True,project='meta')['top_changed'])

    def test_automatic_noise_can_return_no_results(self):
        self.assertEqual(s.search_keywords([note('a')],['ok please make the changes']),[])

    def test_review_history_available_only_explicitly(self):
        rows=[note('old',status='superseded')]
        self.assertEqual(s.search_keywords(rows,['memory'],use_fts=False),[])
        self.assertEqual(len(s.search_keywords(rows,['memory'],status=None,use_fts=False)),1)

    def test_bilingual_concepts_reach_metadata(self):
        item={**note('loan'),'name':'Prestamos y pagos','description':''}
        self.assertTrue(s.search_keywords([item],['loan payments'],use_fts=False))

    def test_common_body_mentions_are_discounted(self):
        item={**note('x'),'name':'unrelated','description':''}
        common={'body_hits':{'receipt'},'norm':0,'idf':{'receipt':.2}}
        rare={'body_hits':{'receipt'},'norm':0,'idf':{'receipt':1}}
        self.assertLess(s.score_atom(item,['receipt'],common),s.score_atom(item,['receipt'],rare))

    def test_cli_honors_filters_with_query(self):
        rows=[note('d2g','d2g'),note('meta')]
        with patch.object(s,'load_manifest',return_value=rows), patch.object(s,'_fts_hits',return_value={}), \
             patch('sys.argv',['smart_loader','memory','--project','d2g','--paths','--no-log']), \
             patch('sys.stdout',new_callable=io.StringIO) as out:
            s.main(); self.assertEqual(out.getvalue().strip(),'d2g')

class SaveReliability(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.vaultpatch=patch.object(c,'VAULT',self.root); self.vaultpatch.start()
    def tearDown(self): self.vaultpatch.stop(); self.tmp.cleanup()
    def data(self,sid='one',turn='a',prompt='Please update the project'):
        return {'session_id':sid,'turn_id':turn,'prompt':prompt}

    def reindex(self,*rels):
        """Rebuild both indexes so they agree with what is on disk right now."""
        entries=[{'path':r,'sha256':c.digest((self.root/r).read_bytes())} for r in rels]
        m=self.root/'index/manifest.json'; m.parent.mkdir(parents=True,exist_ok=True)
        m.write_text(json.dumps(entries),encoding='utf-8')
        c.atomic_json(self.root/'index/generation.json',
                      {'fts':True,'manifest_sha256':c.digest(m.read_bytes())})
        con=sqlite3.connect(self.root/'index/fts.db')
        con.execute('CREATE TABLE IF NOT EXISTS note_meta(path TEXT, sha256 TEXT)')
        con.execute('DELETE FROM note_meta')
        con.executemany('INSERT INTO note_meta VALUES (?,?)',
                        [(e['path'],e['sha256']) for e in entries])
        con.commit(); con.close()

    def acknowledged(self,body='verified fact'):
        """One turn saved and verified against real JSON + FTS indexes."""
        d=self.data(); c.remember_prompt(d,'codex'); c.handle_stop(d,'codex')
        token=c.read_json(c.session_path('codex','one'))['request']
        f=self.root/'saved.md'; f.write_text(body,encoding='utf-8'); self.reindex('saved.md')
        c.acknowledge(token,files=['saved.md'])
        return c.read_json(c.request_path(token)),f
    def test_no_hourly_hole_and_sessions_are_independent(self):
        for sid,turn in [('one','a'),('one','b'),('two','a')]:
            d=self.data(sid,turn); c.remember_prompt(d,'codex')
            self.assertEqual(c.handle_stop(d,'codex')['decision'],'block')
        self.assertEqual(len(list(c.state_dir().glob('*.json'))),5)
    def test_unrelated_index_rebuild_never_counts_as_save(self):
        (self.root/'index').mkdir(); (self.root/'index/manifest.json').write_text('[]')
        d=self.data(); c.remember_prompt(d,'codex')
        self.assertEqual(c.handle_stop(d,'codex')['decision'],'block')
    def test_review_only_never_requests_curated_writes(self):
        d=self.data(prompt="Analyze it, don't change anything yet")
        c.remember_prompt(d,'claude'); self.assertEqual(c.handle_stop(d,'claude'),{})
        self.assertEqual(len(list(c.state_dir().glob('*.json'))),1)

    def test_partial_constraint_does_not_disable_saving_authorized_work(self):
        self.assertFalse(c.review_only("Don't change the database schema; fix the docs"))
        self.assertFalse(c.review_only('Build a read-only dashboard'))
    def test_failed_save_retries_without_claiming_success(self):
        d=self.data(); c.remember_prompt(d,'claude')
        self.assertEqual(c.handle_stop(d,'claude')['decision'],'block')
        self.assertEqual(c.handle_stop({**d,'stop_hook_active':True},'claude')['decision'],'block')
        self.assertIn('UNVERIFIED',c.handle_stop(d,'claude')['systemMessage'])
    def test_noop_receipt_is_scoped_to_request(self):
        d=self.data(); c.remember_prompt(d,'codex'); c.handle_stop(d,'codex')
        state=c.read_json(c.session_path('codex','one'))
        c.acknowledge(state['request'],no_change='No durable facts changed in this conversation.')
        self.assertEqual(c.handle_stop(d,'codex'),{})
        new=self.data(turn='b'); c.remember_prompt(new,'codex')
        self.assertEqual(c.handle_stop(new,'codex')['decision'],'block')
    def test_file_receipt_rejects_unindexed_file(self):
        d=self.data(); c.remember_prompt(d,'codex'); c.handle_stop(d,'codex')
        token=c.read_json(c.session_path('codex','one'))['request']
        with self.assertRaises((ValueError,FileNotFoundError)): c.acknowledge(token,files=['missing.md'])
        self.assertFalse(c.read_json(c.request_path(token)).get('receipt'))

    def test_file_receipt_verifies_both_indexes_and_survives_later_edits(self):
        d=self.data(); c.remember_prompt(d,'codex'); c.handle_stop(d,'codex')
        token=c.read_json(c.session_path('codex','one'))['request']
        f=self.root/'saved.md'; f.write_text('verified fact',encoding='utf-8'); value=c.digest(f.read_bytes())
        m=self.root/'index/manifest.json'; m.write_text(json.dumps([{'path':'saved.md','sha256':value}]))
        c.atomic_json(self.root/'index/generation.json',{'fts':True,'manifest_sha256':c.digest(m.read_bytes())})
        con=sqlite3.connect(self.root/'index/fts.db'); con.execute('CREATE TABLE note_meta(path TEXT, sha256 TEXT)')
        con.execute('INSERT INTO note_meta VALUES (?,?)',('saved.md','wrong')); con.commit()
        with self.assertRaises(ValueError): c.acknowledge(token,files=['saved.md'])
        con.execute('UPDATE note_meta SET sha256=?',(value,)); con.commit(); con.close()
        c.acknowledge(token,files=['saved.md']); self.assertTrue(c.receipt_valid(c.read_json(c.request_path(token))))
        # A later edit to the same file is new work, not a failure of this turn:
        # the receipt stays valid, only the content check reports it superseded,
        # and Stop must not re-block a turn that was already acknowledged.
        f.write_text('changed after receipt'); request=c.read_json(c.request_path(token))
        self.assertTrue(c.receipt_valid(request)); self.assertFalse(c.receipt_current(request))
        # Edited with no rebuild, so the index is now behind the file. That is
        # reported as its own fault; see the status test for the reindexed case.
        self.assertEqual(c.receipt_state(request),'stale-index')
        self.assertEqual(c.handle_stop(d,'codex'),{})
        # A vanished file is a fault, not ordinary later work, and must say so.
        f.unlink(); self.assertEqual(c.receipt_state(request),'missing')

    def test_reindexed_edit_and_stale_index_are_not_the_same_status(self):
        """Both moved the bytes on; only one leaves the index lying about the file."""
        request,f=self.acknowledged()
        self.assertEqual(c.receipt_state(request),'current')
        f.write_text('edited and reindexed',encoding='utf-8'); self.reindex('saved.md')
        self.assertEqual(c.receipt_state(request),'changed')
        self.assertTrue(c.receipt_valid(request))  # the turn stays acknowledged
        f.write_text('edited without a rebuild',encoding='utf-8')
        self.assertEqual(c.receipt_state(request),'stale-index')
        self.assertTrue(c.receipt_valid(request))

    def test_index_loss_is_reported_apart_from_file_loss(self):
        request,f=self.acknowledged()
        (self.root/'index/fts.db').unlink()
        self.assertEqual(c.receipt_state(request),'unindexed')  # acknowledged bytes intact
        f.unlink()
        self.assertEqual(c.receipt_state(request),'missing')

    def test_an_unreadable_file_is_a_status_not_a_traceback(self):
        request,f=self.acknowledged()
        f.unlink(); f.mkdir()  # a real read failure: no mock of receipt_state itself
        self.assertEqual(c.receipt_state(request),'unreadable')
        self.assertFalse(c.receipt_current(request))

    def test_pending_stale_survives_a_locked_file(self):
        """One file the OS will not hand over must not kill the whole report."""
        request,_=self.acknowledged()
        with patch.object(Path,'read_bytes',side_effect=PermissionError('locked')):
            self.assertEqual(c.receipt_state(request),'unreadable')
            with patch('sys.argv',['checkpoint','pending','--stale']), \
                 patch('sys.stdout',new_callable=io.StringIO) as out:
                c.main()
        self.assertIn('unreadable',out.getvalue())
        self.assertIn(request['token'],out.getvalue())

    def test_receipt_needs_a_verification_stamp_from_acknowledge(self):
        self.assertFalse(c.receipt_valid({'receipt':{'kind':'saved','files':{'a.md':'x'}}}))
        self.assertFalse(c.receipt_valid({'receipt':{'kind':'no-change'}}))
        self.assertTrue(c.receipt_valid({'receipt':{'kind':'no-change','verified_at':1,
                                                    'reason':'nothing durable changed'}}))

    def test_review_only_covers_common_no_write_phrasings(self):
        for prompt in ('Review the September 8 changes and confirm your hooks use the shared '
                       'per-turn checkpoint. Report any discrepancies without changing anything.',
                       'without modifying anything','audit this, read-only','read-only audit',
                       'just tell me, do not touch the files',"don't save anything",
                       'no changes','no writes','analysis only please','look but don\'t touch anything',
                       'no cambies nada','sin modificar nada','solo revisa el hook y dime que ves'):
            self.assertTrue(c.review_only(prompt),prompt)

    def test_scoped_limits_still_allow_saving_the_authorized_work(self):
        for prompt in ('fix the bug without changing anything else',
                       'refactor this without touching the other files',
                       'no changes to the OLA sheet, just fix the Metro tab',
                       'no changes were needed, move on',
                       "don't make any changes to the other files",
                       'mount the volume read-only in the compose file',
                       'fix the review_only detector and the pending false positives'):
            self.assertFalse(c.review_only(prompt),prompt)

    def test_a_restriction_on_part_of_the_request_still_saves(self):
        """A no-write phrase must govern the whole request, not one clause of it."""
        for prompt in ('Add tests for read-only audit mode.',
                       'Do not change anything in the engine; fix the documentation instead.',
                       "Fix the detector so that 'without changing anything' is recognized.",
                       'Fix the detector so that "without changing anything" is recognized.',
                       'review the hook, then implement the fix',
                       'no cambies nada del motor, arregla los docs'):
            self.assertFalse(c.review_only(prompt),prompt)

    def test_workflow_verbs_after_a_review_phrase_still_save(self):
        """The vault's own vocabulary authorizes work as much as 'add' or 'fix'."""
        for prompt in ('Read-only pass over checkpoint.py, then rebuild the index '
                       'and record what you found.',
                       'Read-only review, then save what you learn to the vault.',
                       'Solo revisa el modulo A; modifica el modulo B.',
                       'Solo revisa el hook y guarda lo que encuentres.',
                       'Solo revisa esto y registra el resultado.',
                       'Solo revisa el indice y reconstruyelo.',
                       'Solo revisa la lista y borra los duplicados.',
                       'Solo revisa el mapa y cambia el enlace roto.',
                       'Solo revisa las notas y elimina las repetidas.'):
            self.assertFalse(c.review_only(prompt),prompt)

    def test_spanish_infinitives_state_a_restriction_not_a_task(self):
        """"hay que modificar" is talk about work; "modifica" is an order to do it."""
        for prompt in ('solo revisa y dime si hay que modificar algo',
                       'solo revisa el hook, sin cambiar nada',
                       'no cambies nada, solo dime si conviene borrar el atomo',
                       'sin modificar nada'):
            self.assertTrue(c.review_only(prompt),prompt)

    def test_quoted_phrases_are_discussed_not_instructed(self):
        self.assertFalse(c.review_only('the guard should catch "without changing anything" too'))
        self.assertTrue(c.review_only('without changing anything, tell me what you see'))

if __name__=='__main__': unittest.main()

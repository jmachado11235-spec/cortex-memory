"""Does health prove what its output claims? Built on a real indexed vault."""
import io,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from cortex import health

NOTE=('---\nname: {name}\ndescription: {name}\nproject: {project}\n'
      'type: note\nstatus: active\ntags: [trial]\nupdated: 2026-09-08\n---\n\n{body}\n')

def build_indexes(root):
    """Run the real index builder and FTS shard against a temporary vault."""
    from cortex import index_builder as ib, fts_index as fi
    patches=[patch.object(ib,'VAULT',root), patch.object(ib,'INDEX_DIR',root/'index'),
             patch.object(fi,'VAULT',root), patch.object(fi,'INDEX_DIR',root/'index'),
             patch.object(fi,'DB_PATH',root/'index/fts.db'),
             patch('sys.argv',['index_builder']),
             patch('sys.stdout',new_callable=io.StringIO)]
    for p in patches: p.start()
    try: ib.main()
    finally:
        for p in reversed(patches): p.stop()

class HashVerification(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.write('CLAUDE.md','rules','core','How to route memory.')
        self.write('CONTEXT.md','context','core','Where things live.')
        self.write('state/INDEX.md','canonical state','core','Current facts.')
        # Indexed, served by recall, yet in neither the core three nor
        # linked_hashes: exactly the blind spot a map-linked scope would keep.
        self.write('state/roadmap.md','roadmap','trial','Next steps.')
        self.write('_moc/trial.md','trial map','trial','Owns atoms/mapped.md')
        self.write('atoms/mapped.md','mapped atom','trial','Linked by its map.')
        self.write('atoms/unmapped.md','unmapped atom','trial','No map links this.')
        build_indexes(self.root)
    def tearDown(self): self.tmp.cleanup()

    def write(self,rel,name,project,body):
        p=self.root/rel; p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(NOTE.format(name=name,project=project,body=body),encoding='utf-8')
        return p

    def edit(self,rel):
        """Change bytes on disk and deliberately do not rebuild the index."""
        p=self.root/rel; p.write_text(p.read_text(encoding='utf-8')+'\nlater edit\n',encoding='utf-8')

    def test_clean_vault_passes_by_default_and_under_full_hashing(self):
        base=health.inspect(vault=self.root)
        self.assertEqual(base['errors'],[])
        self.assertFalse(base['hashed']); self.assertEqual(base['targeted'],0)
        full=health.inspect(vault=self.root,verify_hashes=True)
        self.assertEqual(full['errors'],[])
        self.assertEqual(full['targeted'],full['notes'])
        self.assertEqual(full['verified'],full['notes'])
        self.assertEqual(full['unchecked'],0)
        # The unmapped atom is a routing review item, not an integrity failure.
        self.assertTrue(any('atoms/unmapped.md' in w for w in full['warnings']))

    def test_default_pass_misses_stale_notes_that_full_hashing_catches(self):
        self.edit('state/roadmap.md'); self.edit('atoms/unmapped.md')
        base=health.inspect(vault=self.root)
        self.assertEqual(base['errors'],[])  # the gap this flag exists to close
        with patch.object(health,'HASH_BATCH',1):
            full=health.inspect(vault=self.root,verify_hashes=True)
        joined=' '.join(full['errors'])
        self.assertIn('state/roadmap.md',joined)
        self.assertIn('atoms/unmapped.md',joined)
        self.assertEqual(full['unchecked'],2)
        self.assertEqual(full['verified'],full['targeted']-2)

    def test_edited_core_document_fails_without_the_flag(self):
        self.edit('CONTEXT.md')
        result=health.inspect(vault=self.root)
        self.assertTrue(any('CONTEXT.md' in e for e in result['errors']))

    def test_a_failing_batch_is_never_counted_as_verified(self):
        """verify_files stops at the first bad path; the rest are unchecked, not clean."""
        self.edit('atoms/mapped.md')
        result=health.inspect(vault=self.root,verify_hashes=True)  # one batch of 7
        self.assertEqual(result['verified'],0)
        self.assertEqual(result['unchecked'],result['targeted'])
        self.assertTrue(any('unchecked' in e for e in result['errors']))

    def test_project_filter_narrows_the_hashed_set(self):
        result=health.inspect(vault=self.root,project_filter='trial',verify_hashes=True)
        self.assertEqual(result['targeted'],4)  # roadmap, map, mapped, unmapped
        self.assertEqual(result['verified'],4)
        self.assertEqual(result['errors'],[])

    def test_core_documents_are_checked_even_under_a_project_filter(self):
        self.edit('CLAUDE.md')
        result=health.inspect(vault=self.root,project_filter='trial',verify_hashes=True)
        self.assertTrue(any('CLAUDE.md' in e for e in result['errors']))

class OutputLabelling(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        for rel,name in [('CLAUDE.md','rules'),('CONTEXT.md','context'),
                         ('state/INDEX.md','canonical state')]:
            p=self.root/rel; p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text(NOTE.format(name=name,project='core',body='body'),encoding='utf-8')
        build_indexes(self.root)
    def tearDown(self): self.tmp.cleanup()

    def run_cli(self,*argv):
        with patch.object(health,'get_vault_path',return_value=self.root), \
             patch('sys.argv',['health',*argv]), \
             patch('sys.stdout',new_callable=io.StringIO) as out:
            code=health.main()
        return code,out.getvalue()

    def test_default_output_does_not_claim_content_verification(self):
        code,text=self.run_cli()
        self.assertEqual(code,0)
        self.assertIn('existence and links only',text)
        self.assertNotIn('hash-verified',text)

    def test_verify_hashes_output_reports_the_verified_count(self):
        code,text=self.run_cli('--verify-hashes')
        self.assertEqual(code,0)
        self.assertIn('3/3 note bodies hash-verified',text)

    def test_incomplete_pass_is_announced_not_hidden(self):
        p=self.root/'CONTEXT.md'; p.write_text(p.read_text(encoding='utf-8')+'edit',encoding='utf-8')
        code,text=self.run_cli('--verify-hashes')
        self.assertEqual(code,1)
        self.assertIn('INCOMPLETE',text)
        self.assertIn('0/3 note bodies hash-verified',text)

if __name__=='__main__': unittest.main()

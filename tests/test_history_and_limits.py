"""Git history per acknowledged turn, and the protocol's size limits."""
import io,json,sqlite3,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from cortex import atom_writer as aw, checkpoint as c, health
from test_health_integrity import NOTE, build_indexes

def git(root,*args):
    return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True,text=True).stdout

class History(unittest.TestCase):
    """The repo is the folder ABOVE the vault, as in the real layout."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.repo=Path(self.tmp.name)
        self.root=self.repo/'my-vault'; self.root.mkdir()
        self.vaultpatch=patch.object(c,'VAULT',self.root); self.vaultpatch.start()
    def tearDown(self): self.vaultpatch.stop(); self.tmp.cleanup()

    def init_repo(self):
        git(self.repo,'init','-q'); git(self.repo,'config','user.name','t')
        git(self.repo,'config','user.email','t@example.invalid')
        (self.repo/'.gitignore').write_text('my-vault/index/\n',encoding='utf-8')

    def save(self,body):
        """One turn: write a note, index it, acknowledge it."""
        d={'session_id':'one','turn_id':body,'prompt':'Please update the project'}
        c.remember_prompt(d,'codex'); c.handle_stop(d,'codex')
        token=c.read_json(c.session_path('codex','one'))['request']
        f=self.root/'saved.md'; f.write_text(body,encoding='utf-8')
        m=self.root/'index/manifest.json'; m.parent.mkdir(parents=True,exist_ok=True)
        m.write_text(json.dumps([{'path':'saved.md','sha256':c.digest(f.read_bytes())}]),encoding='utf-8')
        c.atomic_json(self.root/'index/generation.json',{'fts':True,'manifest_sha256':c.digest(m.read_bytes())})
        con=sqlite3.connect(self.root/'index/fts.db')
        con.execute('CREATE TABLE IF NOT EXISTS note_meta(path TEXT, sha256 TEXT)')
        con.execute('DELETE FROM note_meta')
        con.execute('INSERT INTO note_meta VALUES (?,?)',('saved.md',c.digest(f.read_bytes())))
        con.commit(); con.close()
        return c.acknowledge(token,files=['saved.md'])

    def test_each_saved_turn_is_a_commit_and_the_old_text_is_recoverable(self):
        self.init_repo()
        first=self.save('the rate is 5%'); second=self.save('the rate is 7%')
        self.assertTrue(first['commit'] and second['commit'])
        self.assertEqual(git(self.repo,'rev-list','--count','HEAD').strip(),'2')
        # The replaced claim is gone from disk and still in history.
        self.assertEqual(git(self.repo,'show','HEAD~1:my-vault/saved.md'),'the rate is 5%')
        self.assertNotIn('index/',git(self.repo,'ls-files'))

    def test_a_parent_repo_that_ignores_the_vault_is_left_alone(self):
        """The public layout: the engine's clone ignores my-vault/ entirely."""
        self.init_repo()
        (self.repo/'.gitignore').write_text('my-vault/\n',encoding='utf-8')
        (self.repo/'engine.py').write_text('work in progress',encoding='utf-8')
        self.assertNotIn('commit',self.save('private memory'))
        self.assertNotIn('engine.py',git(self.repo,'ls-files'))

    def test_the_vaults_own_repo_wins_over_the_parent(self):
        self.init_repo()
        (self.repo/'.gitignore').write_text('my-vault/\n',encoding='utf-8')
        git(self.root,'init','-q'); git(self.root,'config','user.name','t')
        git(self.root,'config','user.email','t@example.invalid')
        (self.root/'.gitignore').write_text('index/\n',encoding='utf-8')
        self.assertTrue(self.save('kept in the vault repo')['commit'])
        self.assertEqual(git(self.root,'show','HEAD:saved.md'),'kept in the vault repo')
        self.assertEqual(git(self.root,'ls-files').split(),['.gitignore','saved.md'])

    def test_without_a_repo_the_save_still_succeeds(self):
        receipt=self.save('no repo here')
        self.assertEqual(receipt['kind'],'saved'); self.assertNotIn('commit',receipt)

    def test_a_failed_commit_never_fails_the_save(self):
        self.init_repo()
        with patch.object(c.subprocess,'run',side_effect=OSError('git is gone')):
            receipt=self.save('still saved')
        self.assertEqual(receipt['kind'],'saved'); self.assertIn('NOT updated',receipt['commit'])
        self.assertTrue(c.receipt_valid(c.read_json(c.request_path(
            c.read_json(c.session_path('codex','one'))['request']))))

class SizeLimits(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        for rel,name in [('CLAUDE.md','rules'),('CONTEXT.md','context'),('state/INDEX.md','canonical state')]:
            self.write(rel,name,'body')
    def tearDown(self): self.tmp.cleanup()

    def write(self,rel,name,body,**fields):
        p=self.root/rel; p.parent.mkdir(parents=True,exist_ok=True)
        text=NOTE.format(name=name,project='core',body=body)
        for key,value in fields.items():
            text=text.replace('status: active',f'{key}: {value}') if key=='status' \
                 else text.replace('\n---\n\n',f'\n{key}: {value}\n---\n\n',1)
        p.write_text(text,encoding='utf-8')

    def over(self):
        build_indexes(self.root)
        return {k:[rel for _,rel in rows] for k,rows in health.inspect(vault=self.root)['budgets'].items()}

    def test_state_document_over_the_limit_is_named_and_one_under_is_not(self):
        self.write('state/big.md','big','word '*(health.STATE_WORDS+1))
        self.write('state/fine.md','fine','word '*health.STATE_WORDS)
        self.assertEqual(self.over()['state'],['state/big.md'])

    def test_archived_state_history_is_exempt(self):
        self.write('state/history.md','history','word '*(health.STATE_WORDS+1),status='archived')
        self.assertEqual(self.over()['state'],[])

    def test_long_description_and_missing_replacement_link(self):
        self.write('atoms/long.md','x'*(health.DESCRIPTION_CHARS+1),'body')
        self.write('atoms/old.md','old','body',status='superseded')
        self.write('atoms/linked.md','linked','body',status='superseded',superseded_by='atoms/long.md')
        over=self.over()
        self.assertEqual(over['description'],['atoms/long.md'])
        self.assertEqual(over['superseded'],['atoms/old.md'])

    def test_sizes_never_change_the_exit_code(self):
        self.write('state/big.md','big','word '*(health.STATE_WORDS+1))
        build_indexes(self.root)
        with patch.object(health,'get_vault_path',return_value=self.root), patch('sys.argv',['health']), \
             patch('sys.stdout',new_callable=io.StringIO) as out:
            code=health.main()
        self.assertEqual(code,0); self.assertIn('OVERSIZE',out.getvalue())

class WriterLimits(unittest.TestCase):
    def test_description_over_the_limit_is_rejected_before_anything_is_written(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(aw,'VAULT',Path(tmp)), \
             patch('cortex.atom_writer.get_valid_projects',return_value=['ops']):
            with self.assertRaises(ValueError):
                aw.create_atom('n','event','ops',[],'body',description='x'*(aw.DESCRIPTION_LIMIT+1))
            self.assertEqual(list(Path(tmp).rglob('*.md')),[])
            aw.create_atom('n','event','ops',[],'body',description='x'*aw.DESCRIPTION_LIMIT)
            self.assertEqual(len(list(Path(tmp).rglob('*.md'))),1)

if __name__=='__main__': unittest.main()

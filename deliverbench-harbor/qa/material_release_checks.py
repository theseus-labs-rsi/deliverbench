"""Check append-only staged material release without Docker or model calls."""
import ast
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
import deliverbench_harbor

source=Path(deliverbench_harbor.__file__).parent/'resources/stage.py'
tree=ast.parse(source.read_text())
functions=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='release_materials']
def fail(message):raise ValueError(message)
namespace={'Path':Path,'shutil':shutil,'fail':fail,'sha256':lambda p:hashlib.sha256(p.read_bytes()).hexdigest()}
exec(compile(ast.Module(body=functions,type_ignores=[]),str(source),'exec'),namespace)
release=namespace['release_materials']

class Release(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name).resolve();self.source=self.root/'source';self.incoming=self.root/'incoming'
  (self.source/'event').mkdir(parents=True);(self.incoming/'event').mkdir(parents=True)
  (self.source/'event/earlier.txt').write_text('frozen')
 def test_append_same_event_directory(self):
  (self.incoming/'event/later.txt').write_text('new');release(self.incoming,self.source)
  self.assertEqual((self.source/'event/earlier.txt').read_text(),'frozen');self.assertEqual((self.source/'event/later.txt').read_text(),'new')
  self.assertFalse(self.incoming.exists())
 def test_identical_existing_file(self):
  (self.incoming/'event/earlier.txt').write_text('frozen');release(self.incoming,self.source)
  self.assertEqual((self.source/'event/earlier.txt').read_text(),'frozen')
 def test_replacement_rejected_before_any_copy(self):
  (self.incoming/'event/a-new.txt').write_text('new');(self.incoming/'event/earlier.txt').write_text('changed')
  with self.assertRaises(ValueError):release(self.incoming,self.source)
  self.assertEqual((self.source/'event/earlier.txt').read_text(),'frozen');self.assertFalse((self.source/'event/a-new.txt').exists())
 def test_symlink_rejected(self):
  (self.incoming/'event/link').symlink_to(self.source/'event/earlier.txt')
  with self.assertRaises(ValueError):release(self.incoming,self.source)
 def test_file_directory_conflict(self):
  (self.incoming/'event/earlier.txt').mkdir()
  with self.assertRaises(ValueError):release(self.incoming,self.source)

if __name__=='__main__':unittest.main(verbosity=2)

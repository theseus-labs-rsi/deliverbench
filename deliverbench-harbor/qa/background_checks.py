"""Ordered raw briefing composition and initially released source boundaries."""
import hashlib,tempfile,unittest
from pathlib import Path
from deliverbench_harbor.contract import project_background
class Checks(unittest.TestCase):
 def test_order_hash_and_boundaries(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td).resolve();(root/'instruction.md').write_text('主说明\n');p=root/'environment/data/source/W0/约定.md';p.parent.mkdir(parents=True);p.write_text('公共约定\n')
   names=['instruction.md',str(p.relative_to(root))];c={'steps':['T01','T02'],'kickoff':{'instructions':names,'timeout_sec':180}}
   b=project_background(root,c);self.assertEqual(b['text'],'主说明\n\n\n公共约定\n');self.assertEqual([r['path'] for r in b['parts']],names)
   for r in b['parts']:self.assertEqual(r['sha256'],hashlib.sha256((root/r['path']).read_bytes()).hexdigest())
   future=root/'steps/T02/workdir/release_materials/future.md';future.parent.mkdir(parents=True);future.write_text('未来材料')
   for bad in [str(future.relative_to(root)),'../outside','tests/rubric.json']:
    c['kickoff']['instructions']=['instruction.md',bad]
    with self.assertRaises(ValueError):project_background(root,c)
   p.unlink();p.symlink_to(root/'instruction.md');c['kickoff']['instructions']=names
   with self.assertRaises(ValueError):project_background(root,c)
if __name__=='__main__':unittest.main(verbosity=2)

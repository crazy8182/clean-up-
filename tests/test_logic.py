import unittest
from cleanup_logic import *
def D(i,name,media='video',size=100,src=None,q=None):
 return {'_id':i,'message_id':i,'file_name':name,'media_type':media,'file_size':size,'source':src or detect_source(name),'quality':q or detect_quality(name)}
class T(unittest.TestCase):
 def test_movie_priority(self):
  docs=[D(1,'Avatar 720p WEB-DL.mkv'),D(2,'Avatar 720p WEBRip.mkv'),D(3,'Avatar 1080p WEB-DL.mkv'),D(4,'Avatar 480p HDRip.mkv'),D(5,'Avatar 720p CAMRip.mkv'),D(6,'Avatar 720p WEB-DL.pdf','document')]
  k,d=plan_deleteall(docs); self.assertEqual({x['_id'] for x in k},{1,3,4}); self.assertEqual({x['_id'] for x in d},{2,5,6})
 def test_series_each_episode_each_quality(self):
  docs=[]; i=1
  for ep in [1,2]:
   for q in ['480p','720p','1080p']:
    docs.append(D(i,f'Money Heist S01E{ep:02d} {q} WEB-DL.mkv')); i+=1
    docs.append(D(i,f'Money Heist S01E{ep:02d} {q} WEBRip.mkv')); i+=1
  k,d=plan_deleteall(docs); self.assertEqual(len(k),6); self.assertEqual(len(d),6)
 def test_combined_protected_and_deduped(self):
  docs=[D(1,'Money Heist S01 Complete 720p WEB-DL.mkv'),D(2,'Money Heist S01 Complete 720p WEBRip.mkv'),D(3,'Money Heist S01 Combined 1080p WEB-DL.mkv'),D(4,'Money Heist S01E01-E05 480p HDRip.mkv')]
  k,d=plan_deleteall(docs); self.assertEqual({x['_id'] for x in k},{1,3,4}); self.assertEqual({x['_id'] for x in d},{2})
 def test_unknown_video_safe(self):
  docs=[D(1,'Movie weird quality unknown.mkv')]; k,d=plan_deleteall(docs); self.assertEqual(len(k),1); self.assertEqual(len(d),0)
if __name__=='__main__': unittest.main()

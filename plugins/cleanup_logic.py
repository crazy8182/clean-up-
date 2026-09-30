import os,re
from collections import defaultdict
KEEP_QUALITIES={'480p','720p','1080p'}
SOURCE_PRIORITY={'WEB-DL':1,'WEBRip':2,'HDRip':3}
BAD_SOURCES={'CAM','CAMRIP','HDCAM','HDTC','HDTS','TS','TC','TELECINE','TELESYNC'}
VIDEO_EXTENSIONS={'.mp4','.mkv','.avi','.mov','.m4v','.webm','.ts','.m2ts','.wmv','.flv','.mpeg','.mpg'}
COMBINED_PATTERNS=[r'\bcomplete\b',r'\bcompleted\b',r'\bcombined\b',r'\bcomplete[ ._-]*season\b',r'\bfull[ ._-]*season\b',r'\bseason[ ._-]*pack\b',r'\bseason[ ._-]*batch\b',r'\bbatch\b',r'\bmulti[ ._-]*episode\b',r'\ball[ ._-]*episodes\b',r'\bentire[ ._-]*season\b',r'\bcollection\b',r'\bcomplete[ ._-]*collection\b']
EPISODE_RANGE_PATTERNS=[r'\bS\d{1,2}E\d{1,3}\s*[-_–]\s*(?:E)?\d{1,3}\b',r'\bE\d{1,3}\s*[-_–]\s*(?:E)?\d{1,3}\b']
EPISODE_PATTERN=re.compile(r'\bS(\d{1,2})\s*E(\d{1,3})\b',re.I)
SEASON_PATTERN=re.compile(r'\bS(\d{1,2})\b|\bSEASON\s*(\d{1,2})\b',re.I)

def extension(name): return os.path.splitext((name or '').lower())[1]
def is_video(doc): return doc.get('media_type')=='video' or extension(doc.get('file_name','')) in VIDEO_EXTENSIONS
def normalize_name(name):
 s=(name or '').lower(); s=re.sub(r'\[[^\]]*\]|\([^)]*\)|\{[^}]*\}',' ',s); s=re.sub(r'[\._-]+',' ',s); s=re.sub(r'\b(480p|720p|1080p|2160p|4k|8k)\b',' ',s,flags=re.I); s=re.sub(r'\b(web[ -]?dl|web[ -]?rip|hdrip|camrip|cam|hdtc|hdts|hdcam|ts|tc)\b',' ',s,flags=re.I); return re.sub(r'\s+',' ',s).strip()
def detect_quality(name):
 m=re.search(r'\b(480p|720p|1080p|2160p|4k)\b',name or '',re.I); return m.group(1).lower() if m else 'unknown'
def detect_source(name):
 n=(name or '').upper().replace('_',' ')
 for label,pat in [('WEB-DL',r'\bWEB[ ._-]?DL\b'),('WEBRip',r'\bWEB[ ._-]?RIP\b'),('HDRip',r'\bHD[ ._-]?RIP\b'),('CAMRip',r'\bCAM[ ._-]?RIP\b'),('CAM',r'\bCAM\b'),('HDTC',r'\bHD[ ._-]?TC\b'),('HDTS',r'\bHD[ ._-]?TS\b'),('HDCAM',r'\bHD[ ._-]?CAM\b'),('TS',r'\bTS\b'),('TC',r'\bTC\b')]:
  if re.search(pat,n): return label
 return 'unknown'
def is_bad_source(doc): return str(doc.get('source','unknown')).upper() in BAD_SOURCES
def combined_type(name):
 n=(name or '').lower().replace('_',' ').replace('-',' '); return any(re.search(p,n,re.I) for p in COMBINED_PATTERNS+EPISODE_RANGE_PATTERNS)
def episode_info(name):
 m=EPISODE_PATTERN.search(name or ''); return (int(m.group(1)),int(m.group(2))) if m else None
def season_info(name):
 m=SEASON_PATTERN.search(name or ''); return int(m.group(1) or m.group(2)) if m else None
def source_rank(doc): return SOURCE_PRIORITY.get(doc.get('source'),99)
def choose_best(items): return sorted(items,key=lambda d:(source_rank(d),-int(d.get('file_size') or 0),int(d.get('message_id') or 0)))[0]
def series_key(doc):
 n=normalize_name(doc.get('file_name','')); n=re.sub(r'\bS\d{1,2}E\d{1,3}\b',' ',n,flags=re.I); n=re.sub(r'\bSEASON\s*\d{1,2}\b',' ',n,flags=re.I); n=re.sub(r'\bS\d{1,2}\b',' ',n,flags=re.I); n=re.sub(r'\b(complete|completed|combined|full season|season pack|season batch|batch|multi episode|all episodes|entire season|collection)\b',' ',n,flags=re.I); return re.sub(r'\s+',' ',n).strip()
def plan_deleteall(docs):
 keep=set(); groups=defaultdict(list); delete=set()
 for d in docs:
  if not is_video(d) or is_bad_source(d): delete.add(d['_id'])
 for d in docs:
  if d['_id'] in delete or not is_video(d): continue
  q=d.get('quality'); src=d.get('source')
  # Unknown quality/source is kept conservatively.
  if q not in KEEP_QUALITIES or src not in SOURCE_PRIORITY: keep.add(d['_id']); continue
  ep=episode_info(d.get('file_name','')); season=season_info(d.get('file_name',''))
  if ep and season: key=('episode',series_key(d),ep[0],ep[1],q)
  elif season and combined_type(d.get('file_name','')): key=('combined',series_key(d),season,q)
  else: key=('movie',normalize_name(d.get('file_name','')),q)
  groups[key].append(d)
 for items in groups.values(): keep.add(choose_best(items)['_id'])
 return [d for d in docs if d['_id'] in keep],[d for d in docs if d['_id'] not in keep]

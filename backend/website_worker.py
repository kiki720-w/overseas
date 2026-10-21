"""Dedicated process bounds DNS resolution and slow websites to 22 seconds."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.website_fetch import Collector
from backend.common import AppError
try:
    request=json.loads(sys.stdin.buffer.read(2048))
    options={'optional_body':request.get('optional_body') is True}
    if request.get('extraction_python'):options['extraction_python']=request['extraction_python']
    result={'result':Collector(**options).collect(request['url'])}
except AppError as e:result={'error':str(e),'status':e.status,'code':e.code,'details':getattr(e,'details',{})}
except Exception:result={'error':'采集进程失败；没有保存资料','status':502,'code':'website_unavailable'}
sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode('utf8'))

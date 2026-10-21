"""Offline bounded HTML extraction; runs only in separately installed trusted runtime."""
import sys,json,base64,importlib.util,hashlib,types
from pathlib import Path
# Bind only our source package. The frozen parent directory also contains 3.12
# extension modules, which must never enter this worker's Python3.13 search path.
package=types.ModuleType('backend');package.__path__=[str(Path(__file__).resolve().parent)];package.__package__='backend'
sys.modules['backend']=package
def deny(event,args):
    if event in ('socket.connect','socket.getaddrinfo','socket.bind','subprocess.Popen','os.system'):raise PermissionError('Optional extraction worker has no network or subprocess permission')
sys.addaudithook(deny)
try:
    if sys.version_info<(3,10):raise RuntimeError('optional_runtime_incompatible')
    if any(importlib.util.find_spec(name) is None for name in ('lxml','trafilatura','courlan')):raise RuntimeError('optional_dependency_missing')
    raw_request=sys.stdin.buffer.read(360*1024+1)
    if len(raw_request)>360*1024:raise RuntimeError('optional_input_limit')
    request=json.loads(raw_request);raw=base64.b64decode(request['raw'],validate=True)
    if len(raw)>256*1024:raise RuntimeError('optional_input_limit')
    if hashlib.sha256(raw).hexdigest()!=request['source_sha256']:raise RuntimeError('optional_worker_output_invalid')
    from backend.website_text import VisibleHTML
    from backend.optional_body import extract_optional
    text=raw.decode(request['encoding'],'replace');parser=VisibleHTML();parser.feed(text)
    result={'result':extract_optional(raw,text,parser,request['url'],request['fetched_at'])}
except RuntimeError as e:result={'error':str(e),'status':503,'code':str(e)}
except ImportError:result={'error':'可选提取依赖缺失，未运行Trafilatura','status':503,'code':'optional_dependency_missing'}
except Exception:result={'error':'可选提取工作进程失败','status':502,'code':'optional_worker_failed'}
out=json.dumps(result,ensure_ascii=False).encode('utf8')
if len(out)>65536:out=json.dumps({'error':'可选结果超过64KiB','status':502,'code':'optional_worker_output_limit'}).encode('utf8')
sys.stdout.buffer.write(out)

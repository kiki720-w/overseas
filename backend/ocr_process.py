"""Optional trusted local runtime. Windows Job2GiB, serial scheduling done by OCRTasks."""
import ctypes as c,ctypes.wintypes as w,subprocess,pathlib,os,time,json,sys
from .common import AppError
def available_ram():
    class Memory(c.Structure):
        _fields_=[('length',w.DWORD),('load',w.DWORD)]+[(name,c.c_ulonglong) for name in ('total','free','page_total','page_free','virtual_total','virtual_free','extended')]
    value=Memory();value.length=c.sizeof(value)
    if os.name!='nt' or not c.windll.kernel32.GlobalMemoryStatusEx(c.byref(value)):return 0
    return value.free
class BASIC(c.Structure):
    _fields_=[('PerProcessUserTimeLimit',c.c_longlong),('PerJobUserTimeLimit',c.c_longlong),('LimitFlags',w.DWORD),('MinimumWorkingSetSize',c.c_size_t),('MaximumWorkingSetSize',c.c_size_t),('ActiveProcessLimit',w.DWORD),('Affinity',c.c_size_t),('PriorityClass',w.DWORD),('SchedulingClass',w.DWORD)]
class IO(c.Structure):
    _fields_=[(n,c.c_ulonglong) for n in ['ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount']]
class EXT(c.Structure):
    _fields_=[('BasicLimitInformation',BASIC),('IoInfo',IO),('ProcessMemoryLimit',c.c_size_t),('JobMemoryLimit',c.c_size_t),('PeakProcessMemoryUsed',c.c_size_t),('PeakJobMemoryUsed',c.c_size_t)]
k=c.WinDLL('kernel32',use_last_error=True) if os.name=='nt' else None
if k is not None:
    k.CreateJobObjectW.restype=w.HANDLE
    k.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD]
    k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
    k.QueryInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.c_void_p]
    k.CloseHandle.argtypes=[w.HANDLE]
    k.CreateIoCompletionPort.argtypes=[w.HANDLE,w.HANDLE,c.c_size_t,w.DWORD];k.CreateIoCompletionPort.restype=w.HANDLE
    k.GetQueuedCompletionStatus.argtypes=[w.HANDLE,c.POINTER(w.DWORD),c.POINTER(c.c_size_t),c.POINTER(c.c_void_p),w.DWORD]

def output_usage(folder):
    # Input PDF and durable task JSON have separate staging reservations.
    # All worker artifacts, request, log, cache and receipt share this budget.
    total=0
    excluded={folder/name for name in ('source.pdf','task.json','task.json.tmp')}
    for path in folder.rglob('*'):
        if path in excluded or not path.is_file():continue
        try:size=path.stat().st_size
        except FileNotFoundError:continue
        if size>16*1024*1024:raise AppError('Worker artifact exceeds limit',422,'output_limit')
        total+=size
    return total

class LocalOCRRunner:
    def __init__(self,python,models):self.python=pathlib.Path(python);self.models=pathlib.Path(models)
    def available(self):
        return os.name=='nt' and self.python.is_file() and (self.models/'RapidOcr'/'PP-OCRv6_rec_small.onnx').is_file() and (self.models/'docling-project--docling-models'/'model_artifacts'/'tableformer'/'accurate'/'tm_config.json').is_file()
    def __call__(self,source,task,cancel):
        if not self.available():raise AppError('未安装本地解析环境或离线模型',503,'parser_unavailable')
        if available_ram()<4*1024**3:raise AppError('空闲RAM不足4GiB；未启动OCR，请稍后新建任务',503,'resource_gate')
        folder=source.parent;request=folder/'worker-request.json'
        request.write_text(json.dumps(dict(source_sha256=task['source_sha256'],pages=task['pages'],models=str(self.models))),encoding='utf8')
        job=k.CreateJobObjectW(None,None);limits=EXT();limits.BasicLimitInformation.LimitFlags=0x2000|0x200|0x8
        limits.BasicLimitInformation.ActiveProcessLimit=4;limits.JobMemoryLimit=2*1024**3
        if not k.SetInformationJobObject(job,9,c.byref(limits),c.sizeof(limits)):k.CloseHandle(job);raise AppError('无法启用Windows资源隔离',503,'isolation_unavailable')
        env={key:os.environ[key] for key in ('PATH','SystemRoot','WINDIR','COMSPEC','PATHEXT') if key in os.environ}
        cache=folder/'cache';cache.mkdir(exist_ok=True)
        env.update(TEMP=str(cache),TMP=str(cache),HF_HOME=str(cache),TORCH_HOME=str(cache),TORCHINDUCTOR_CACHE_DIR=str(cache),USERNAME='ocr_worker',USERPROFILE=str(cache),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1',OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',PYTHONFAULTHANDLER='1',PYTHONIOENCODING='utf-8')
        process=None;reason=None;start=time.monotonic()
        try:
            with (folder/'worker.log').open('w',encoding='utf8') as log:
                process=subprocess.Popen([str(self.python),'-I',str(pathlib.Path(__file__).with_name('ocr_worker.py')),str(request)],env=env,stdout=log,stderr=log,creationflags=0x08000000|0x4)
                if not k.AssignProcessToJobObject(job,w.HANDLE(process._handle)):raise AppError('资源隔离失败，未解析',503,'isolation_unavailable')
                # CREATE_SUSPENDED avoids model import before Job assignment. Resume via native process API.
                ntdll=c.WinDLL('ntdll');ntdll.NtResumeProcess.argtypes=[w.HANDLE]
                if ntdll.NtResumeProcess(w.HANDLE(process._handle))!=0:raise AppError('无法启动隔离工作进程',503,'isolation_unavailable')
                while process.poll() is None:
                    try:total=output_usage(folder)
                    except AppError:total=16*1024*1024+1
                    if cancel.is_set():reason='cancelled'
                    elif time.monotonic()-start>600:reason='worker_timeout'
                    elif available_ram()<int(1.5*1024**3):reason='resource_reserve'
                    elif total+4096>16*1024*1024:reason='output_limit'
                    if reason:break
                    time.sleep(.25)
                if reason:k.CloseHandle(job);job=None;process.wait(timeout=10)
                k.QueryInformationJobObject(w.HANDLE(job) if job else None,9,c.byref(limits),c.sizeof(limits),None)
                result_path=folder/'result.json'
                receipt=dict(exit_code=process.returncode,wall_seconds=time.monotonic()-start,termination_reason=reason,peak_job_commit_bytes=limits.PeakJobMemoryUsed,commit_cap_bytes=limits.JobMemoryLimit)
                # Check again after fast exits, before parsing JSON or hashing raw artifacts.
                try:final_size=output_usage(folder)
                except AppError:final_size=16*1024*1024+1
                receipt_path=folder/'worker-receipt.json'
                previous=receipt_path.stat().st_size if receipt_path.exists() else 0
                if not reason and final_size-previous+4096>16*1024*1024:reason='output_limit';receipt['termination_reason']=reason
                receipt_path.write_text(json.dumps(receipt),encoding='utf8')
                if reason:raise AppError('隔离解析已终止，未入库',422,reason)
                if not result_path.is_file() or result_path.stat().st_size>16*1024*1024:raise AppError('本地解析原生退出或无有效产物，未入库',422,'native_parser_exit')
                result=json.loads(result_path.read_text(encoding='utf8'))
                if process.returncode or result.get('ok') is not True:raise AppError(result.get('message','本地解析失败'),422,result.get('code','parser_failed'))
                import hashlib
                if not (folder/'document.json').is_file() or hashlib.sha256((folder/'document.json').read_bytes()).hexdigest()!=result['draft'].get('raw_artifact_sha256'):raise AppError('原始解析产物哈希不匹配',422,'parser_identity')
                return result['draft']
        finally:
            if job:k.CloseHandle(job)
            if process and process.poll() is None:process.wait(timeout=10)

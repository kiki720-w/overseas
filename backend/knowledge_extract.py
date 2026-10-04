"""Local bounded extraction; executable as an isolated worker for uploaded files."""
import base64
import io
import json
import re
import sys
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree

MAX_FILE=5*1024*1024
MAX_TEXT=120000
MAX_PAGES=80
MAX_UNZIP=16*1024*1024

class ExtractionError(Exception):
    def __init__(self,code,message):self.code=code;self.message=message

def cleaned(text):
    if not isinstance(text,str) or '\x00' in text or any(ord(c)<32 and c not in '\n\r\t' for c in text):
        raise ExtractionError('invalid_document','资料包含无效文本或二进制内容')
    if len(text)>MAX_TEXT:raise ExtractionError('extraction_too_large','提取文本超过120000字符，请拆分资料')
    return text.strip()

def decode_text(body):
    for encoding in (['utf-16'] if body.startswith((b'\xff\xfe',b'\xfe\xff')) else ['utf-8-sig','gb18030']):
        try:return cleaned(body.decode(encoding)),encoding
        except UnicodeError:continue
    raise ExtractionError('encoding_invalid','文本编码无法识别，请另存为UTF-8 TXT/MD')

def docx_parts(body):
    try:
        z=zipfile.ZipFile(io.BytesIO(body));infos=z.infolist()
        if not infos or len(infos)>256 or sum(i.file_size for i in infos)>MAX_UNZIP:
            raise ExtractionError('archive_too_large','DOCX条目或解压大小超过限制')
        names=set()
        for item in infos:
            name=item.filename;parts=PurePosixPath(name).parts
            if name in names or '\\' in name or ':' in name or name.startswith('/') or '..' in parts or item.flag_bits&1:
                raise ExtractionError('archive_invalid','DOCX包含不安全、重复或加密的条目')
            names.add(name)
            if item.file_size>1024*1024 and item.file_size>max(1,item.compress_size)*100:
                raise ExtractionError('archive_too_large','DOCX压缩比例超过限制')
            if 'vbaproject' in name.lower() or name.lower().endswith('.bin'):
                raise ExtractionError('macro_forbidden','首版不接受宏或嵌入执行文件')
        if 'word/document.xml' not in names or '[Content_Types].xml' not in names:
            raise ExtractionError('invalid_document','文件不是可识别的DOCX正文')
        xml=z.read('word/document.xml');types=z.read('[Content_Types].xml')
        if len(xml)>6*1024*1024 or b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
            raise ExtractionError('xml_forbidden','DOCX正文XML超过限制或包含实体声明')
        if b'macroenabled' in types.lower() or b'<!DOCTYPE' in types.upper() or b'<!ENTITY' in types.upper():
            raise ExtractionError('macro_forbidden','不接受宏文档或实体声明')
        if b'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml' not in types:
            raise ExtractionError('invalid_document','DOCX正文类型不符')
        root=ElementTree.fromstring(xml);ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        parts=[]
        for index,p in enumerate(root.iter(ns+'p'),1):
            text=''.join((node.text or '') if node.tag==ns+'t' else '\t' if node.tag==ns+'tab' else '\n' if node.tag in (ns+'br',ns+'cr') else '' for node in p.iter())
            text=cleaned(text)
            if text:parts.append({'text':text,'location':{'kind':'paragraph','paragraph':index}})
        warnings=['DOCX仅提取正文与表格段落；不执行宏，不访问外部链接，未提取页眉/脚注/图片。']
        return parts,{'page_count':None,'paragraph_count':len(parts),'warnings':warnings,'parser':'stdlib-docx'}
    except ExtractionError:raise
    except (zipfile.BadZipFile,KeyError,ElementTree.ParseError,OSError):raise ExtractionError('invalid_document','DOCX损坏或正文无法提取')
    finally:
        if 'z' in locals():z.close()

def pdf_parts(body):
    try:
        from pypdf import PdfReader
    except ImportError:raise ExtractionError('parser_unavailable','本机未安装PDF解析依赖；请按requirements.txt安装后再导入')
    try:
        if not body.startswith(b'%PDF-'):raise ExtractionError('invalid_document','PDF文件签名不符')
        reader=PdfReader(io.BytesIO(body),strict=True)
        if reader.is_encrypted:raise ExtractionError('encrypted_document','首版不支持加密或带密码的PDF')
        count=len(reader.pages)
        if not 1<=count<=MAX_PAGES:raise ExtractionError('page_limit','PDF最多80页，请拆分资料')
        parts=[];total=0;empty=[];stream_total=0
        for index,page in enumerate(reader.pages,1):
            contents=page.get_contents()
            if contents is not None:
                stream=contents.get_data();stream_total+=len(stream)
                if len(stream)>2*1024*1024 or stream_total>8*1024*1024:
                    raise ExtractionError('extraction_too_large','PDF解压内容流超过限制，请导出精简的文本型PDF')
            text=cleaned(page.extract_text() or '');total+=len(text)
            if total>MAX_TEXT:raise ExtractionError('extraction_too_large','PDF提取文本超过120000字符')
            if text:parts.append({'text':text,'location':{'kind':'page','page':index}})
            else:empty.append(index)
        if not parts:raise ExtractionError('no_text','未提取到文字：扫描PDF/纯图片资料首版不支持OCR')
        warnings=['PDF按文本层提取，不保证复杂表格/排版顺序；首版没有OCR，原文须人工预览核对。']
        if empty:warnings.append('无文本页未建立片段：'+','.join(map(str,empty)))
        return parts,{'page_count':count,'paragraph_count':None,'warnings':warnings,'parser':'pypdf'}
    except ExtractionError:raise
    except Exception:raise ExtractionError('invalid_document','PDF损坏、过于复杂或无法安全提取文本')

def extract(body,extension):
    if not body or len(body)>MAX_FILE:raise ExtractionError('file_too_large','文件为空或超过5MiB')
    if extension in ('.txt','.md'):
        text,encoding=decode_text(body)
        parts=[{'text':p.strip(),'location':{'kind':'paragraph','paragraph':i}} for i,p in enumerate(re.split(r'\r?\n\s*\r?\n',text),1) if p.strip()]
        meta={'page_count':None,'paragraph_count':len(parts),'warnings':[],'parser':'text','encoding':encoding}
    elif extension=='.docx':parts,meta=docx_parts(body)
    elif extension=='.pdf':parts,meta=pdf_parts(body)
    else:raise ExtractionError('format_unsupported','仅支持TXT、MD、文本型PDF、DOCX，不支持扫描OCR或宏文档')
    text=cleaned('\n\n'.join(p['text'] for p in parts))
    if not text:raise ExtractionError('no_text','未提取到可检索文本，请提供文字资料')
    chunks=[]
    for part in parts:
        value=part['text'];start=0
        while start<len(value):
            end=min(start+1200,len(value))
            if end<len(value):
                boundary=max(value.rfind('\n',start,end),value.rfind(' ',start,end))
                if boundary-start>=600:end=boundary
            snippet=value[start:end].strip();start=end
            if snippet:chunks.append({'text':snippet,'location':part['location']})
    if len(chunks)>400:raise ExtractionError('chunk_limit','资料片段超过400块，请分开导入')
    return dict(meta,extracted_text=text,chunks=chunks,extracted_chars=len(text),chunk_count=len(chunks))

def limit_worker():
    """Limits apply to this dedicated worker, never the existing server."""
    if sys.platform=='win32':
        import ctypes
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_=[('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',wintypes.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
        class IO(ctypes.Structure):_fields_=[(x,ctypes.c_ulonglong) for x in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
        class Extended(ctypes.Structure):_fields_=[('BasicLimitInformation',Basic),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.CreateJobObjectW.restype=wintypes.HANDLE;kernel.GetCurrentProcess.restype=wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD];kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        job=kernel.CreateJobObjectW(None,None);limits=Extended();limits.BasicLimitInformation.LimitFlags=0x100;limits.ProcessMemoryLimit=256*1024*1024
        if not job or not kernel.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job,kernel.GetCurrentProcess()):
            raise ExtractionError('parser_limit_unavailable','本机无法建立解析进程内存限制，未处理上传文件')
        return job  # Keep the job handle alive for this worker's lifetime.
    else:
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))

if __name__=='__main__':
    try:
        limit_handle=limit_worker()
        raw=sys.stdin.buffer.read(7*1024*1024+1)
        if len(raw)>7*1024*1024:raise ExtractionError('file_too_large','解析输入超过限制')
        data=json.loads(raw);body=base64.b64decode(data['content_base64'],validate=True)
        result={'ok':True,'value':extract(body,data['extension'])}
    except ExtractionError as error:result={'ok':False,'code':error.code,'message':error.message}
    except Exception:result={'ok':False,'code':'invalid_document','message':'资料无法安全提取，未入库'}
    sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode('utf8'));sys.stdout.buffer.flush()

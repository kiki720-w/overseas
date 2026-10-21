"""Allowlisted source ZIP preparation; never scans data or credential directories."""
import argparse,hashlib,json,zipfile
from pathlib import Path

def members(root):
    files=[]
    for name in ('server.py','run_production.py','desktop_entry.py','requirements.txt','README.md','launch.example.json'):
        files.append(root/name)
    for folder in ('backend','rag_adapter'):
        files.extend(p for p in (root/folder).rglob('*.py') if '__pycache__' not in p.parts and not p.name.startswith('test'))
    files.append(root/'rag_adapter/integration-next/config.json')
    for folder in ('runtime/frontend/static','static/assets','templates'):
        files.extend(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    files.extend(p for p in (root/'packaging').rglob('*') if p.is_file() and p.suffix in ('.py','.spec','.iss','.ico','.md'))
    files.extend(p for p in (root/'docs').glob('*.md'))
    return sorted(set(files))

def prepare(root,output):
    root=root.resolve();output=output.resolve()
    files=members(root);manifest={}
    for file in files:
        if file.is_symlink() or not file.resolve().is_relative_to(root):raise ValueError('Linked/external package resource rejected')
        relative=file.relative_to(root).as_posix()
        if file.suffix in ('.sqlite3','.db','.dpapi') or any(p in relative.split('/') for p in ('credentials','data','backups','workspace','evidence')):raise ValueError('Private artifact rejected')
        manifest[relative]=hashlib.sha256(file.read_bytes()).hexdigest()
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as bundle:
        for file in files:bundle.write(file,file.relative_to(root).as_posix())
    receipt={'artifact':'source-only-trial-not-installed-desktop','files':manifest,'zip_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
             'database_or_credential_files_included':False,'runtime_bundled':False,'formal_service_started':False}
    output.with_suffix('.manifest.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8')
    return receipt

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    receipt=prepare(Path(__file__).resolve().parents[1],args.output)
    print(json.dumps({'files':len(receipt['files']),'sha256':receipt['zip_sha256'],'runtime_bundled':False}))

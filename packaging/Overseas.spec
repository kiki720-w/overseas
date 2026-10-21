# Build preparation only; no customer databases, vaults or local model caches.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules
root=Path(SPECPATH).parent
datas=[]
for folder in ('runtime/frontend/static','static/assets','templates'):
    for file in (root/folder).rglob('*'):
        if file.is_file():datas.append((str(file),str(file.relative_to(root).parent)))
# File-backed dynamic modules and trusted optional subprocess entry scripts.
for folder in ('backend','rag_adapter'):
    for file in (root/folder).rglob('*.py'):
        if '__pycache__' not in file.parts:datas.append((str(file),str(file.relative_to(root).parent)))
datas.append((str(root/'rag_adapter/integration-next/config.json'),'rag_adapter/integration-next'))
paths=[str(root),str(root/'rag_adapter'),str(root/'rag_adapter/integration-next'),str(root/'rag_adapter/answerability-next')]
sys.path[:0]=paths
a=Analysis([str(root/'run_production.py')],pathex=paths,binaries=[],datas=datas,
    hiddenimports=['canonical']+collect_submodules('backend')+collect_submodules('rag')+collect_submodules('preview_gate')+collect_submodules('assessment')+collect_submodules('openpyxl')+collect_submodules('pypdf'),
    hookspath=[],hooksconfig={},runtime_hooks=[],excludes=['torch','transformers','docling'],noarchive=False)
pyz=PYZ(a.pure)
icon_file=root/'packaging/Overseas.ico'
icon_args={'icon':str(icon_file)} if icon_file.is_file() else {}
service=EXE(pyz,a.scripts,[],exclude_binaries=True,name='OverseasService',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=True,**icon_args)
launcher=Analysis([str(root/'desktop_entry.py')],pathex=[str(root)],binaries=[],datas=[],hiddenimports=[],hookspath=[],hooksconfig={},runtime_hooks=[],excludes=[],noarchive=False)
launcher_pyz=PYZ(launcher.pure)
exe=EXE(launcher_pyz,launcher.scripts,[],exclude_binaries=True,name='Overseas',debug=False,strip=False,upx=False,console=False,**icon_args)
coll=COLLECT(exe,service,a.binaries,a.datas,launcher.binaries,launcher.datas,strip=False,upx=False,name='Overseas')

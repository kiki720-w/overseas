# Pure stdio companion using already-installed CPython 3.13/Pydantic dependencies.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules
root=Path(SPECPATH).parent
core=root/'backend/structured_core';sys.path.insert(0,str(core))
datas=[(str(p),'structured_core') for p in core.glob('*.py')]
a=Analysis([str(root/'backend/structured_validator.py')],pathex=[str(core)],binaries=[],datas=datas,
    hiddenimports=['contracts','composer']+collect_submodules('pydantic'),
    hookspath=[],hooksconfig={},runtime_hooks=[],
    excludes=['torch','transformers','docling','onnxruntime','rapidocr','cv2','numpy','scipy','pandas','matplotlib','trafilatura'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='OverseasValidator',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=True)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='OverseasValidator')

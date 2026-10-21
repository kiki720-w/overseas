"""Trusted local component roots, inspection only; no inference, install or credentials."""
import json
from pathlib import Path

def resolve(value,base):
    if not isinstance(value,str) or not value.strip():raise ValueError('Component path must be nonempty text')
    path=Path(value)
    return (path if path.is_absolute() else base/path).resolve()

def disjoint(*roots):
    for i,left in enumerate(roots):
        for right in roots[i+1:]:
            if left.is_relative_to(right) or right.is_relative_to(left):raise ValueError('Runtime, model and task roots must be disjoint')

def ocr_profile(path,workspace):
    path=Path(path).resolve();workspace=Path(workspace).resolve()
    if path.stat().st_size>16384:raise ValueError('OCR profile exceeds 16KiB')
    profile=json.loads(path.read_text(encoding='utf8'))
    if not isinstance(profile,dict) or profile.get('protocol')!='local-ocr-production-v2':
        raise ValueError('OCR profile requires v2 with explicit component/runtime/model roots; copy the portable example, do not modify an old profile implicitly')
    allowed={'protocol','component_root','runtime_root','python','models_root','staging'}
    if set(profile)!=allowed:raise ValueError('Invalid trusted OCR profile fields')
    component=resolve(profile['component_root'],path.parent)
    runtime=resolve(profile['runtime_root'],component);models=resolve(profile['models_root'],component)
    python=resolve(profile['python'],runtime);staging=resolve(profile['staging'],workspace)
    if not runtime.is_relative_to(component) or not models.is_relative_to(component) or not python.is_relative_to(runtime):raise ValueError('OCR runtime/model/executable escaped component root')
    if not staging.is_relative_to(workspace):raise ValueError('OCR tasks must stay in the selected writable workspace')
    disjoint(runtime,models,staging)
    return {'component':component,'runtime':runtime,'python':python,'models':models,'staging':staging,'protocol':profile['protocol']}

def inspect_ocr(paths):
    missing=[]
    required=[paths['python'],paths['models']/'RapidOcr'/'PP-OCRv6_rec_small.onnx',
              paths['models']/'RapidOcr'/'PP-OCRv6_det_small.onnx',paths['models']/'RapidOcr'/'ch_ppocr_mobile_v2.0_cls_mobile.onnx',
              paths['models']/'docling-project--docling-layout-heron'/'config.json',
              paths['models']/'docling-project--docling-layout-heron'/'model.safetensors',
              paths['models']/'docling-project--docling-models'/'model_artifacts'/'tableformer'/'accurate'/'tm_config.json',
              paths['models']/'docling-project--docling-models'/'model_artifacts'/'tableformer'/'accurate'/'tableformer_accurate.safetensors']
    for file in required:
        boundary=paths['runtime'] if file==paths['python'] else paths['models']
        if not file.resolve().is_relative_to(boundary):raise ValueError('OCR component resource escaped declared root')
        if not file.is_file():missing.append(str(file))
    # Inspect package locations only; never import Torch/Docling or load models.
    packages=paths['runtime']/'Lib'/'site-packages'
    missing_modules=[]
    for module in ('torch','docling','docling_core','docling_ibm_models','pypdfium2','rapidocr','onnxruntime'):
        directory=packages/module
        if not directory.resolve().is_relative_to(paths['runtime']):raise ValueError('OCR dependency escaped runtime root')
        if not directory.is_dir():missing_modules.append(module)
    missing.extend('runtime-module:'+name for name in missing_modules)
    return {'files_present':not missing,'missing_files':missing,'dependency_import_verified':False,
            'model_inference_verified':False,'downloads':0,'notice':'Files present is not proof of dependency, OCR accuracy or resource readiness.'}

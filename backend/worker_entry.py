"""Fixed worker commands shared by source Python and the frozen desktop entry point."""
from pathlib import Path
import runpy
import sys

WORKERS={'knowledge-extract':'knowledge_extract.py','website-fetch':'website_worker.py'}

def command(name):
    if name not in WORKERS:raise ValueError('Unknown internal worker')
    if getattr(sys,'frozen',False):return [sys.executable,'--internal-worker',name]
    return [sys.executable,'-I',str(Path(__file__).with_name(WORKERS[name]))]

def dispatch(arguments):
    if len(arguments)!=1 or arguments[0] not in WORKERS:raise SystemExit('Unknown internal worker')
    script=Path(__file__).with_name(WORKERS[arguments[0]])
    sys.argv=[str(script)]
    runpy.run_path(str(script),run_name='__main__')

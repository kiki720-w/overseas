"""Hard outbound network denial around the two local CPU ranking entrypoints."""
from pathlib import Path
import runpy
import sys

root=Path(__file__).resolve().parents[1]
allowed={root/'rag_adapter/integration-next/run_qa_pipeline_next.py',root/'rag_adapter/preview-next/rerank_local.py'}
if len(sys.argv)<2:raise ValueError('Explicit local ranking script required')
script=Path(sys.argv[1]).resolve()
if script not in allowed:raise ValueError('Only the two reviewed local ranking entrypoints allowed')
def deny_network(event,args):
    if event in ('socket.connect','socket.getaddrinfo','socket.bind'):
        raise OSError('offline local ranking network disabled')
sys.addaudithook(deny_network)
sys.argv=sys.argv[1:]
runpy.run_path(str(script),run_name='__main__')

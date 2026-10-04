# -*- coding: utf-8 -*-
"""Synthetic temporary database; no credential or supplier requests."""
from pathlib import Path
import tempfile
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.store import Store
from backend.knowledge import KnowledgeService
from server import make_server
with tempfile.TemporaryDirectory(prefix='overseas-review-ui-') as folder:
    store=Store(Path(folder)/'synthetic.sqlite3',False);knowledge=KnowledgeService(store)
    for index in range(3):
        preview=knowledge.preview({'title':'Synthetic review catalog '+str(index),'category':'product','source_company':'SYNTHETIC COMPANY','text':'Synthetic product-name fixture. Performance not independently proven.'})
        receipt=knowledge.confirm({'job_id':preview['job_id'],'consent':True})
        if index==0:store.knowledge_status({'document_id':receipt['document_id'],'expected_version':1,'expected_revision':1,'review_status':'source_checked','reviewed_fields':['title'],'review_reason':'Synthetic catalog title checked only','review_source':{'filename':'synthetic.pdf','sha256':'a'*64,'pdf_pages':[1],'printed_pages':['01']}})
    http=make_server(store,'local',port=8878)
    print('Synthetic source-review UI at http://127.0.0.1:8878 ; NO supplier calls',flush=True)
    try:http.serve_forever()
    except KeyboardInterrupt:http.server_close()

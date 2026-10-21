"""Optional offline providers; imports happen only when explicitly constructed."""
import hashlib
import json
from pathlib import Path
from .core import Chunk, DocumentState

def store_snapshot(store):
    """Integration seam: read canonical rows, never load originals or mutate review audit.

    Caller owns the authenticated server store. Do not take state from request JSON.
    Existing selection/build_context/evidence validation remains authoritative.
    """
    def snapshot():
        with store.db() as db:
            # One SELECT snapshot transaction to avoid mixed document/chunk versions.
            db.execute('BEGIN')
            documents = [json.loads(r[0]) for r in db.execute('SELECT data FROM kb_documents')]
            rows = db.execute('SELECT document_id,version,id,data FROM kb_chunks').fetchall()
        states = []
        for raw in documents:
            d = store.knowledge_warnings(raw, documents)
            states.append(DocumentState(d['id'],d['version'],d['revision'],d['enabled'],d['review_status'],
                d['title'],d['filename'],d['category'],d.get('source_company',''),d.get('expires_on',''),
                d['conflict'],d.get('review_info'),d.get('review_label','')))
        chunks = []
        for did, version, cid, raw in rows:
            c = json.loads(raw)
            if c.get('chunk_id') != cid: raise ValueError('canonical chunk identity mismatch')
            chunks.append(Chunk(did,version,cid,c['text'],c['location'],tuple(c.get('heading_path',())),
                c.get('kind','paragraph'),tuple(c.get('models',()))))
        return states, chunks
    return snapshot

def approved_local_path(path):
    """Only local, safetensors model folders with explicit revision/license manifest.

    Manifest records the reviewed download; no download, tokens or remote code.
    """
    root = Path(path).resolve(strict=True)
    if not root.is_dir() or not list(root.glob('*.safetensors')): raise ValueError('local safetensors folder required')
    manifest = json.loads((root/'rag_model_manifest.json').read_text(encoding='utf-8'))
    if manifest.get('license') not in ('mit','apache-2.0') or not manifest.get('revision') or not manifest.get('model_id'):
        raise ValueError('reviewed model/license/revision required')
    # The cache model version must describe actual local artifacts, not a mutable label.
    hashes = manifest.get('sha256', {})
    artifacts = list(root.glob('*.safetensors')) + list(root.glob('*.json')) + list(root.glob('*.model'))
    for artifact in artifacts:
        if artifact.name == 'rag_model_manifest.json': continue
        if artifact.name not in hashes: raise ValueError('every model/config/tokenizer artifact requires reviewed sha256')
        digest = hashlib.sha256()
        with artifact.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''): digest.update(block)
        if digest.hexdigest()!=hashes[artifact.name]: raise ValueError('model artifact hash mismatch')
    manifest['revision'] += '|' + hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
    return root, manifest

class LocalE5Embedder:
    def __init__(self, path):
        root, manifest = approved_local_path(path)
        if manifest['model_id'] != 'intfloat/multilingual-e5-small': raise ValueError('E5-small manifest required')
        from transformers import AutoTokenizer, AutoModel
        import torch
        self.torch = torch
        self.model_id = manifest['model_id']; self.model_version = manifest['revision']+'|meanpool-l2-512-v1'
        self.tokenizer = AutoTokenizer.from_pretrained(str(root),local_files_only=True,trust_remote_code=False)
        self.model = AutoModel.from_pretrained(str(root),local_files_only=True,trust_remote_code=False,use_safetensors=True).to('cpu').eval()

    def encode(self, texts):
        output = []
        for start in range(0,len(texts),4):
            batch = self.tokenizer(list(texts[start:start+4]),padding=True,truncation=True,max_length=512,return_tensors='pt')
            with self.torch.inference_mode():
                hidden = self.model(**batch).last_hidden_state
                mask = batch['attention_mask'].unsqueeze(-1)
                pooled = (hidden*mask).sum(dim=1)/mask.sum(dim=1).clamp(min=1)
                output.extend(self.torch.nn.functional.normalize(pooled,p=2,dim=1).tolist())
        return output

    def embed_documents(self, texts): return self.encode(['passage: '+text for text in texts])
    def embed_query(self, text): return self.encode(['query: '+text])[0]

class LocalMiniLMReranker:
    def __init__(self, path):
        root, manifest = approved_local_path(path)
        if manifest['model_id'] != 'cross-encoder/mmarco-mMiniLMv2-L12-H384-v1': raise ValueError('mMiniLM manifest required')
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        import torch
        self.torch = torch
        self.model_id = manifest['model_id']; self.model_version = manifest['revision']
        self.tokenizer = AutoTokenizer.from_pretrained(str(root),local_files_only=True,trust_remote_code=False)
        self.model = AutoModelForSequenceClassification.from_pretrained(str(root),local_files_only=True,trust_remote_code=False,use_safetensors=True).to('cpu').eval()

    def score(self, query, texts):
        output = []
        for start in range(0,len(texts),4):
            subset = texts[start:start+4]
            batch = self.tokenizer([query]*len(subset),list(subset),padding=True,truncation=True,max_length=512,return_tensors='pt')
            with self.torch.inference_mode():
                logits = self.model(**batch).logits
                if logits.shape[1] != 1: raise ValueError('single relevance logit expected')
                output.extend(logits[:,0].tolist())
        return output

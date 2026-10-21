"""Immutable saved revisions. Authorization remains the original session/customer load."""
import json
from .common import dump,AppError

class StructuredHistory:
    def __init__(self,store):
        self.store=store
        with store.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS structured_deal_history_v1(record_id TEXT NOT NULL,revision INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(record_id,revision))')
            for rid,data in db.execute('SELECT id,data FROM structured_deal_v1').fetchall():
                row=json.loads(data)
                self.capture(db,row)

    @staticmethod
    def capture(db,row):
        if row.get('status')=='generating':return # do not pin an unfinished row over completed revision 1
        db.execute('INSERT OR IGNORE INTO structured_deal_history_v1 VALUES(?,?,?)',(row['id'],row['revision'],dump(row)))

    def revisions(self,row):
        with self.store.db() as db:
            rows=db.execute('SELECT revision,data FROM structured_deal_history_v1 WHERE record_id=? ORDER BY revision DESC LIMIT 50',(row['id'],)).fetchall()
        return {'customer_id':row['customer_id'],'id':row['id'],'read_only':True,'items':[{'revision':revision,'status':json.loads(data)['status'],'draft_saved':bool(json.loads(data).get('draft'))} for revision,data in rows],'limit':50}

    def read(self,row,revision):
        if type(revision) is not int or revision<1:raise AppError('Invalid history revision',400,'invalid_input')
        with self.store.db() as db:
            raw=db.execute('SELECT data FROM structured_deal_history_v1 WHERE record_id=? AND revision=?',(row['id'],revision)).fetchone()
        if not raw:raise AppError('Saved revision not found',404,'analysis_not_found')
        archive=json.loads(raw[0])
        if archive['id']!=row['id'] or archive['customer_id']!=row['customer_id']:raise AppError('Saved revision unavailable',404,'analysis_not_found')
        return archive

def projection(row,reasons):
    result={key:row.get(key) for key in ('id','customer_id','revision','status','draft','draft_kind','envelope','model_envelope','model_envelope_sha256','inference_review','evidence','pending','error','diagnostics','model_output_sha256')}
    result.update(read_only=True,active=False,approved=False,actions={'confirm':False,'edit':False,'approve':False,'export':False,'send':False},historical_confirmed=row.get('confirmed',[]),validity={'current':not reasons,'reasons':reasons},draft_label='历史未审批草稿（只读，不能复用批准或发送）',scope_notice='Saved snapshot only; historical explanations and quotes are not approvals, verified facts or current commitments.',missing={'draft':row.get('draft') is None,'envelope':row.get('envelope') is None,'evidence':not bool(row.get('evidence'))})
    return result

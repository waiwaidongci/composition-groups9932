import csv,io
from redis import Redis
from fastapi import HTTPException
from sqlalchemy import func
from ..models import CompositionRun,ResampleNote
from ..config.settings import REDIS_URL
from ..utils.serialize import record
from .study_service import get_study,create_snapshot
from .analysis_service import prepare_snapshot
from .audit_service import log
queue=Redis.from_url(REDIS_URL,decode_responses=True)
def list_runs(db):return [record(r) for r in db.query(CompositionRun).order_by(CompositionRun.id.desc())]
def get_run(db,rid):
    row=db.get(CompositionRun,rid)
    if not row:raise HTTPException(404,"组成运行不存在")
    return row
def create_run(db,data,user):
    study=get_study(db,data.study_id);snapshot=create_snapshot(db,study)
    try:
        prepare_snapshot(snapshot,data.delta)
        if len(snapshot['samples'])*data.permutations>150000:raise ValueError("样本数×置换数超额")
    except ValueError as e:raise HTTPException(422,str(e))
    row=CompositionRun(**data.model_dump(),input_snapshot=snapshot,owner=user.name);db.add(row);db.flush();log(db,user.email,'composition.enqueue',row.id);db.commit()
    try:queue.rpush('composition:jobs',row.id)
    except Exception:row.status='failed';row.error='队列不可用，请重提';db.commit();raise HTTPException(503,row.error)
    return record(row)
def _active_approvals(db,rid):
    # 仅“当前最新版本仍为 approved”的建议放行；被撤回/修订替代的历史批复不再放行，其他有效批复不受影响
    max_ver=db.query(ResampleNote.note_id,func.max(ResampleNote.version).label("v")).filter_by(run_id=rid).group_by(ResampleNote.note_id).subquery()
    return db.query(ResampleNote).join(max_ver,(ResampleNote.note_id==max_ver.c.note_id)&(ResampleNote.version==max_ver.c.v)).filter(ResampleNote.status=='approved').order_by(ResampleNote.note_id.asc()).all()
def export_run(db,rid):
    row=get_run(db,rid)
    if row.status!='completed':raise HTTPException(403,'仅获批完成研究可导出')
    approvals=_active_approvals(db,rid)
    if not approvals:raise HTTPException(403,'该运行没有当前有效的获批补样建议（建议已撤回或修订后尚未重新获批），不可导出')
    o=io.StringIO();w=csv.writer(o);stats=row.result['statistics']
    # 抬头明确本次导出依据的建议版本与批复；可有多份有效批复，撤回/替代的历史版本不会出现在这里
    for n in approvals:
        o.write('# 放行依据 补样建议NOTE-%d 版本v%d 状态%s 批复人:%s 批复时间:%s 批复意见:%s\n'%(n.note_id,n.version,n.status,n.reviewer,n.reviewed_at,n.review_note.replace('\n',' ')))
    head=['sample','group','component','raw','closed','replaced','CLR','zero_count','delta','R2','pseudo_F','F_status','global_p','B','seed','input_hash']
    head+=['note_id','note_version','note_status','reviewer','reviewed_at','review_note']
    w.writerow(head)
    stamp=[(n.note_id,n.version,n.status,n.reviewer,n.reviewed_at,n.review_note) for n in approvals]
    for s in row.result['transformed']:
        for i,c in enumerate(row.result['components']):
            base=[s['sample']['code'],s['sample']['group'],c['code'],s['raw'][i],s['closed'][i],s['replaced'][i],s['clr'][i],s['zero_count'],row.delta,stats['R2'],stats['F'],stats['F_status'],stats['p'],row.permutations,row.seed,row.result['input_hash']]
            # 每份有效批复各输出一组数据行，使下载数据与具体建议版本/批复一一对应
            for st in stamp:w.writerow(base+list(st))
    return o.getvalue()

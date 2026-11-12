from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from ..models import CompositionRun,ResampleNote
from ..utils.serialize import record
from ..utils.clock import now
from .audit_service import log

# 可被撤回的当前状态；退回(rejected)只能修订后重新送审
WITHDRAWABLE={"submitted","approved"}
# 可被修订的当前状态；已撤回的建议不再允许改动，如需继续应新建建议
REVISABLE={"submitted","approved","rejected"}

def list_notes(db):
    # 返回全部版本行，前端按 note_id 聚合；历史版本与批复均保留可读
    return [record(n) for n in db.query(ResampleNote).order_by(ResampleNote.note_id.desc(),ResampleNote.version.asc())]

def _get_version(db,nid):
    # 写路径一律行级锁定（FOR UPDATE），把乐观锁比较收敛到串行临界区，
    # 避免两个并发事务同时读到旧 lock_version 后互相覆盖
    row=db.get(ResampleNote,nid,with_for_update=True)
    if not row:raise HTTPException(404,"补样建议版本不存在")
    return row

def _check_lock(row,expected_lock):
    # 乐观并发：旧页面持有的 lock_version 与库内不一致即拒绝，禁止覆盖新版本/新批复
    if row.lock_version!=expected_lock:
        raise HTTPException(409,"建议已被他人更新（新版本或新批复），请刷新后基于最新版本操作")

def _ensure_actor(row,user):
    if user.role!="admin" and row.owner!=user.name:
        raise HTTPException(403,"只有建议提交人或材料负责人可执行此操作")

def _current_version(db,note_id):
    return db.query(ResampleNote).filter_by(note_id=note_id).order_by(ResampleNote.version.desc()).with_for_update().first()

def create_note(db,data,user):
    run=db.get(CompositionRun,data.run_id)
    if not run or run.status!="completed":raise HTTPException(409,"只能对已完成组成研究提交补样建议")
    # 并发提交可能拿到相同 note_id，靠 (note_id,version) 唯一约束兜底并重试一次
    for attempt in range(2):
        next_note_id=(db.query(func.coalesce(func.max(ResampleNote.note_id),0)).scalar() or 0)+1
        row=ResampleNote(note_id=next_note_id,version=1,**data.model_dump(),owner=user.name,status="submitted")
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            if attempt:raise HTTPException(409,"补样建议编号冲突，请重新提交")
            continue
        log(db,user.email,"resample.submit",f"note {next_note_id} v1 (row {row.id})");db.commit();return record(row)

def review_note(db,nid,data,user):
    row=_get_version(db,nid)
    _check_lock(row,data.expected_lock)
    if row.status!="submitted":
        raise HTTPException(409,"仅待审阅建议可审批；该版本已批复、被修订替代或已撤回")
    if _current_version(db,row.note_id).id!=row.id:
        raise HTTPException(409,"该版本已不是最新版本，请审阅最新版本")
    row.status=data.status;row.reviewer=user.name;row.review_note=data.review_note
    row.reviewed_at=now();row.lock_version+=1
    log(db,user.email,"resample."+data.status,f"note {row.note_id} v{row.version} (row {row.id})");db.commit();return record(row)

def revise_note(db,nid,data,user):
    row=_get_version(db,nid)
    _check_lock(row,data.expected_lock);_ensure_actor(row,user)
    current=_current_version(db,row.note_id)
    if current.id!=row.id:raise HTTPException(409,"只能修订建议的最新版本，请刷新页面")
    if row.status not in REVISABLE:raise HTTPException(409,"已撤回建议不可修订，请新建补样建议")
    # 旧版本完整保留（含原批复），仅标记为被替代；新版本重新进入待审阅
    row.status="superseded";row.lock_version+=1;row.revised_by=user.name;row.revised_at=now()
    new=ResampleNote(note_id=row.note_id,version=row.version+1,run_id=row.run_id,
                     title=data.title,interpretation=data.interpretation,proposal=data.proposal,
                     status="submitted",owner=user.name)
    db.add(new);db.flush()
    log(db,user.email,"resample.revise",f"note {row.note_id} v{row.version} -> v{new.version} (row {new.id})")
    db.commit();return record(new)

def withdraw_note(db,nid,data,user):
    row=_get_version(db,nid)
    _check_lock(row,data.expected_lock);_ensure_actor(row,user)
    current=_current_version(db,row.note_id)
    if current.id!=row.id:raise HTTPException(409,"只能撤回建议的最新版本，请刷新页面")
    if row.status not in WITHDRAWABLE:raise HTTPException(409,"仅待审阅或已批准的当前建议可撤回；退回状态请修订后重新送审")
    row.status="withdrawn";row.lock_version+=1
    # 撤回原因写入批复栏位并保留时间，保证撤回决策与责任人可追溯
    row.review_note=data.reason;row.reviewed_at=now()
    if not row.reviewer:row.reviewer=user.name
    log(db,user.email,"resample.withdraw",f"note {row.note_id} v{row.version} (row {row.id})");db.commit();return record(row)

"""补样建议修订/撤回/乐观锁/导出规则验证。使用 SQLite 内存库，可直接 python -I 运行。"""
import sys,types
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

import src.config.database as database
engine=create_engine("sqlite+pysqlite:///:memory:")
database.engine=engine
database.SessionLocal=sessionmaker(bind=engine,expire_on_commit=False)
# 导出服务模块导入时会连 Redis，替换为惰性桩对象避免真实连接
stub=types.SimpleNamespace(rpush=lambda *a,**k:0,ping=lambda *a,**k:True)
import src.services.run_service as run_service
run_service.queue=stub

from src.config.database import Base
from src import models
from src.models import User,CompositionStudy,CompositionRun,ResampleNote
from src.schemas.note import NoteInput,ReviewInput,RevisionInput,WithdrawInput
from src.services import note_service,run_service as rs

Base.metadata.create_all(engine)
db=database.SessionLocal()
admin=User(email="admin@x",name="罗岑",role="admin",password_hash="x")
analyst=User(email="an@x",name="顾砚",role="analyst",password_hash="x")
other=User(email="o@x",name="旁人",role="analyst",password_hash="x")
db.add_all([admin,analyst,other]);db.commit()
study=CompositionStudy(code="LS-T",name="t",description="d",source="s",default_delta=.001);db.add(study);db.commit()
run=CompositionRun(name="r1",study_id=study.id,delta=.001,permutations=9,seed=1,input_snapshot={},status="completed",
                   result={"statistics":{"R2":.1,"F":1.0,"F_status":"finite","p":.5},"transformed":[],"components":[],"input_hash":"h"},owner="顾砚")
db.add(run);db.commit()

passed=[]
def ok(name,cond):
    assert cond,name;passed.append(name)

def H(fn,*a,**k):
    try:fn(*a,**k)
    except HTTPException as e:return e.status_code,e.detail
    return None,None

# 1. 同运行可提交多份建议
n1=note_service.create_note(db,NoteInput(run_id=run.id,title="建议一标题",interpretation="解释内容足够长吧",proposal="补样方案内容足够长"),analyst)
n2=note_service.create_note(db,NoteInput(run_id=run.id,title="建议二标题",interpretation="解释内容足够长吧",proposal="补样方案内容足够长"),analyst)
ok("多份建议 note_id 独立",n1["note_id"]==1 and n2["note_id"]==2 and n1["version"]==1 and n1["lock_version"]==0)

# 2. 未获批不可导出
c,m=H(rs.export_run,db,run.id);ok("无有效批复导出403",c==403)

# 3. 审批 + 乐观锁：错误 expected_lock 必须 409
c,_=H(note_service.review_note,db,n1["id"],ReviewInput(status="approved",review_note="批准意见内容足够长",expected_lock=9),admin)
ok("旧锁审批被拒409",c==409)
r1=note_service.review_note(db,n1["id"],ReviewInput(status="approved",review_note="批准意见内容足够长",expected_lock=0),admin)
ok("审批成功且锁递增",r1["status"]=="approved" and r1["lock_version"]==1 and r1["reviewed_at"]!="")
c,_=H(note_service.review_note,db,n1["id"],ReviewInput(status="approved",review_note="重复审批意见足够长",expected_lock=1),admin)
ok("重复审批409",c==409)

# 4. 获批后可导出，CSV 标注建议版本与批复
csv1=rs.export_run(db,run.id)
ok("导出含版本批复抬头","# 放行依据 补样建议NOTE-1 版本v1 状态approved" in csv1 and "note_id,note_version" in csv1)

# 5. 修订：旧版 superseded 保留批复，新版 submitted 重新审批
c,_=H(note_service.revise_note,db,n1["id"],RevisionInput(title="修订标题",interpretation="修订解释内容足够长",proposal="修订方案内容足够长",expected_lock=0),analyst)
ok("旧页面(锁过期)修订被拒",c==409)
v2=note_service.revise_note(db,n1["id"],RevisionInput(title="修订标题",interpretation="修订解释内容足够长",proposal="修订方案内容足够长",expected_lock=1),analyst)
ok("修订生成 v2 待审阅",v2["version"]==2 and v2["status"]=="submitted" and v2["lock_version"]==0 and v2["note_id"]==1)
old=db.get(ResampleNote,n1["id"])
ok("旧版保留且标记superseded、批复仍在",old.status=="superseded" and old.review_note=="批准意见内容足够长" and old.revised_by=="顾砚")
c,_=H(rs.export_run,db,run.id)
ok("修订后旧批复停止放行(无有效获批)403",c==403)

# 6. 并发：审批/修订/撤回交错，旧页面操作不能覆盖新版本
# 6a. 对旧版本行的审批直接拒绝（不是最新版本）
c,_=H(note_service.review_note,db,old.id,ReviewInput(status="rejected",review_note="退回意见内容足够长",expected_lock=2),admin)
ok("审批旧版本被拒409",c==409)
# 6b. v2 获批
r2=note_service.review_note(db,v2["id"],ReviewInput(status="approved",review_note="二次批准意见内容足够长",expected_lock=0),admin)
ok("v2 重新获批",r2["status"]=="approved" and r2["lock_version"]==1)
csv2=rs.export_run(db,run.id)
ok("导出切换到 v2 批复","NOTE-1 版本v2" in csv2 and "版本v1" not in csv2 and "二次批准意见内容足够长" in csv2)
# 6c. 撤回 v2：旧锁被拒，正确锁放行
c,_=H(note_service.withdraw_note,db,v2["id"],WithdrawInput(expected_lock=0,reason="撤回原因内容足够长"),analyst)
ok("旧锁撤回被拒409",c==409)
w=note_service.withdraw_note(db,v2["id"],WithdrawInput(expected_lock=1,reason="方案有误撤回内容足够长"),analyst)
ok("撤回成功",w["status"]=="withdrawn" and w["lock_version"]==2 and w["review_note"].startswith("方案有误"))
c,_=H(rs.export_run,db,run.id);ok("撤回后该建议停止放行403",c==403)
# 6d. 已撤回不可再修订/撤回
c,_=H(note_service.revise_note,db,v2["id"],RevisionInput(title="标题",interpretation="解释内容足够长吧",proposal="方案内容足够长吧",expected_lock=2),analyst)
ok("撤回后不可修订409",c==409)
c,_=H(note_service.withdraw_note,db,v2["id"],WithdrawInput(expected_lock=2,reason="再次撤回原因足够长"),analyst)
ok("重复撤回409",c==409)

# 7. 其他有效批复不受影响：建议二获批后仍可导出
r3=note_service.review_note(db,n2["id"],ReviewInput(status="approved",review_note="建议二批准意见内容足够长",expected_lock=0),admin)
csv3=rs.export_run(db,run.id)
ok("其他有效批复仍放行且只含NOTE-2","NOTE-2 版本v1" in csv3 and "NOTE-1" not in csv3)

# 8. 权限：非提交人 analyst 不能修订/撤回他人建议
c,_=H(note_service.withdraw_note,db,n2["id"],WithdrawInput(expected_lock=1,reason="旁人撤回原因足够长"),other)
ok("非提交人撤回403",c==403)
# admin 可撤回任意建议
w2=note_service.withdraw_note(db,n2["id"],WithdrawInput(expected_lock=1,reason="负责人撤回原因足够长"),admin)
ok("负责人可撤回他人建议",w2["status"]=="withdrawn")

# 9. rejected 路径：退回后可修订重新送审，但不可撤回
n3=note_service.create_note(db,NoteInput(run_id=run.id,title="建议三标题",interpretation="解释内容足够长吧",proposal="补样方案内容足够长"),analyst)
rj=note_service.review_note(db,n3["id"],ReviewInput(status="rejected",review_note="退回意见内容足够长哦",expected_lock=0),admin)
c,_=H(note_service.withdraw_note,db,n3["id"],WithdrawInput(expected_lock=1,reason="退回后撤回原因长"),analyst)
ok("rejected 不可撤回409",c==409)
v2b=note_service.revise_note(db,n3["id"],RevisionInput(title="建议三修订",interpretation="修订解释内容足够长",proposal="修订方案内容足够长",expected_lock=1),analyst)
ok("rejected 可修订重新送审",v2b["status"]=="submitted" and v2b["version"]==2)

# 10. 历史链完整可读：list 返回所有版本行
rows=note_service.list_notes(db)
ok("全部版本行可追溯",len(rows)==5 and {(x["note_id"],x["version"]) for x in rows}>={(1,1),(1,2),(2,1),(3,1),(3,2)})
chain=sorted([x for x in rows if x["note_id"]==1],key=lambda x:x["version"])
ok("链内状态与批复保留",[x["status"] for x in chain]==["superseded","withdrawn"] and chain[0]["reviewer"]=="罗岑")

# 11. 多份有效批复同时放行：NOTE-3 v2 与新 NOTE-4 均获批，导出含两组抬头
a3=note_service.review_note(db,v2b["id"],ReviewInput(status="approved",review_note="建议三修订版批准",expected_lock=0),admin)
n4=note_service.create_note(db,NoteInput(run_id=run.id,title="建议四标题",interpretation="解释内容足够长吧",proposal="补样方案内容足够长"),analyst)
note_service.review_note(db,n4["id"],ReviewInput(status="approved",review_note="建议四批准意见内容足够长",expected_lock=0),admin)
csv4=rs.export_run(db,run.id)
ok("多份有效批复同时列示",csv4.count("# 放行依据")==2 and "NOTE-3 版本v2" in csv4 and "NOTE-4 版本v1" in csv4)

print("\n".join("PASS "+p for p in passed))
print(f"\n全部 {len(passed)} 项断言通过")

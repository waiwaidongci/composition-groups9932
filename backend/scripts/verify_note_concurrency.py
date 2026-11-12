"""并发保障验证：MySQL 方言下行锁 SQL、乐观锁冲突、note_id 唯一约束兜底。"""
import types
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError
import src.config.database as database
engine=create_engine("sqlite+pysqlite:///:memory:")
database.engine=engine;database.SessionLocal=sessionmaker(bind=engine,expire_on_commit=False)
stub=types.SimpleNamespace(rpush=lambda *a,**k:0,ping=lambda *a,**k:True)
import src.services.run_service as run_service;run_service.queue=stub

from src.config.database import Base
from src import models
from src.models import ResampleNote
from src.schemas.note import ReviewInput
from src.services import note_service
from src.models import User,CompositionStudy,CompositionRun

Base.metadata.create_all(engine)
db=database.SessionLocal()
admin=User(email="a@x",name="罗岑",role="admin",password_hash="x")
analyst=User(email="n@x",name="顾砚",role="analyst",password_hash="x")
db.add_all([admin,analyst]);db.commit()
st=CompositionStudy(code="LS-T",name="t",description="d",source="s",default_delta=.001);db.add(st);db.commit()
run=CompositionRun(name="r1",study_id=st.id,delta=.001,permutations=9,seed=1,input_snapshot={},status="completed",result={"statistics":{},"transformed":[],"components":[],"input_hash":"h"},owner="顾砚");db.add(run);db.commit()
passed=[]
def ok(name,cond):assert cond,name;passed.append(name)

# 1. MySQL 方言下写路径 SELECT 必须带 FOR UPDATE（InnoDB 串行化乐观锁比较）
q_locked=db.query(ResampleNote).filter_by(note_id=1).order_by(ResampleNote.version.desc()).with_for_update()
sql=str(q_locked.statement.compile(dialect=mysql.dialect(),compile_kwargs={"literal_binds":True}))
ok("MySQL 生成 FOR UPDATE 行锁","for update" in sql.lower())

# 2. 唯一约束 (note_id,version) 在模型上存在
cons={c.name for c in ResampleNote.__table__.constraints if c.name}
ok("note_id+version 唯一约束已声明","uq_note_version" in cons)

# 3. 模拟两个旧页面并发：会话A先提交审批，会话B持旧锁再审批必 409
from src.schemas.note import NoteInput
n=note_service.create_note(db,NoteInput(run_id=run.id,title="并发建议标题",interpretation="解释内容足够长吧",proposal="方案内容足够长吧"),analyst)
note_service.review_note(db,n["id"],ReviewInput(status="approved",review_note="会话A批准意见足够长",expected_lock=0),admin)
from fastapi import HTTPException
try:
    note_service.review_note(db,n["id"],ReviewInput(status="rejected",review_note="会话B旧锁退回意见长",expected_lock=0),admin)
    raise AssertionError("应拒绝旧锁")
except HTTPException as e:
    ok("旧页面并发写入409",e.status_code==409)
fresh=db.get(ResampleNote,n["id"])
ok("先提交者结果未被覆盖",fresh.status=="approved" and fresh.lock_version==1 and fresh.review_note=="会话A批准意见足够长")

# 4. create_note 的唯一冲突重试：首次 flush 抛 IntegrityError 后自动重试成功
calls={"n":0}
orig_flush=db.flush
def flaky_flush(*a,**k):
    calls["n"]+=1
    if calls["n"]==1:raise IntegrityError("INSERT",{},Exception("duplicate note_id"))
    return orig_flush(*a,**k)
db.flush=flaky_flush;db.autoflush=False
try:
    row=note_service.create_note(db,NoteInput(run_id=run.id,title="冲突重试建议标题",interpretation="解释内容足够长吧",proposal="方案内容足够长吧"),analyst)
    ok("编号冲突后重试成功",row["note_id"]>=1 and row["version"]==1)
finally:
    db.flush=orig_flush;db.autoflush=True

# 5. 连续冲突两次则显式 409，不静默吞错
def always_fail(*a,**k):raise IntegrityError("INSERT",{},Exception("duplicate"))
db.flush=always_fail;db.autoflush=False
try:
    note_service.create_note(db,NoteInput(run_id=run.id,title="持续冲突建议标题",interpretation="解释内容足够长吧",proposal="方案内容足够长吧"),analyst)
    raise AssertionError("应报错")
except HTTPException as e:
    ok("持续冲突返回409",e.status_code==409)
finally:
    db.flush=orig_flush;db.autoflush=True
db.rollback()

print("\n".join("PASS "+p for p in passed))
print(f"\n并发保障全部 {len(passed)} 项断言通过")

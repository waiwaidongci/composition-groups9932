"""FastAPI HTTP 全链路验证：鉴权、版本化建议、乐观锁409、撤回、导出标注。SQLite + TestClient。"""
import os,tempfile,types
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import src.config.database as database
_db_path=os.path.join(tempfile.mkdtemp(prefix="lisui-test-"),"http.db")
engine=create_engine("sqlite+pysqlite:///"+_db_path,connect_args={"check_same_thread":False})
database.engine=engine
database.SessionLocal=sessionmaker(bind=engine,expire_on_commit=False)
stub=types.SimpleNamespace(rpush=lambda *a,**k:0,ping=lambda *a,**k:True)
import src.services.run_service as run_service
run_service.queue=stub

from fastapi.testclient import TestClient
from src.config.database import Base,get_db
from src import models
from src.main import app
from src.models import User,CompositionStudy,CompositionRun
from src.utils.security import hash_password,make_token

Base.metadata.create_all(engine)
db=database.SessionLocal()
admin=User(email="admin@x",name="罗岑",role="admin",password_hash=hash_password("pw123456"))
analyst=User(email="an@x",name="顾砚",role="analyst",password_hash=hash_password("pw123456"))
viewer=User(email="v@x",name="程禾",role="viewer",password_hash=hash_password("pw123456"))
other=User(email="o@x",name="旁人",role="analyst",password_hash=hash_password("pw123456"))
db.add_all([admin,analyst,viewer,other]);db.commit()
st=CompositionStudy(code="LS-T",name="t",description="d",source="s",default_delta=.001);db.add(st);db.commit()
run=CompositionRun(name="r1",study_id=st.id,delta=.001,permutations=9,seed=1,input_snapshot={},status="completed",
                   result={"statistics":{"R2":.1,"F":1.0,"F_status":"finite","p":.5},"transformed":[{"sample":{"code":"S1","group":"A"},"raw":[1],"closed":[1],"replaced":[1],"clr":[0],"zero_count":0}],"components":[{"code":"char"}],"input_hash":"h"},owner="顾砚")
db.add(run);db.commit()

def override_db():
    s=database.SessionLocal()
    try:yield s
    finally:s.close()
app.dependency_overrides[get_db]=override_db
c=TestClient(app)
H_A={"Authorization":"Bearer "+make_token(admin)};H_N={"Authorization":"Bearer "+make_token(analyst)}
H_V={"Authorization":"Bearer "+make_token(viewer)};H_O={"Authorization":"Bearer "+make_token(other)}
passed=[]
def check(name,cond):assert cond,name;passed.append(name)

body=lambda t,i,p:{"title":t,"interpretation":i,"proposal":p}
# viewer 不可创建
r=c.post("/notes",json=body("建议标题","解释内容足够长吧","补样方案内容足够长")|{"run_id":run.id},headers=H_V)
check("viewer 写入403",r.status_code==403)
# analyst 创建两份
r=c.post("/notes",json=body("建议一标题","解释内容足够长吧","补样方案内容足够长")|{"run_id":run.id},headers=H_N)
check("创建建议201",r.status_code==201);n1=r.json()
check("新建即 v1/lock0",n1["version"]==1 and n1["note_id"]==1 and n1["lock_version"]==0 and n1["status"]=="submitted")
r=c.post("/notes",json=body("建议二标题","解释内容足够长吧","补样方案内容足够长")|{"run_id":run.id},headers=H_N);n2=r.json()
# viewer 审批 403
r=c.patch(f"/notes/{n1['id']}/review",json={"status":"approved","review_note":"批准意见内容足够长","expected_lock":0},headers=H_V)
check("viewer 审批403",r.status_code==403)
# analyst 审批 403
r=c.patch(f"/notes/{n1['id']}/review",json={"status":"approved","review_note":"批准意见内容足够长","expected_lock":0},headers=H_N)
check("analyst 审批403",r.status_code==403)
# 旧锁 409（模拟旧页面）
r=c.patch(f"/notes/{n1['id']}/review",json={"status":"approved","review_note":"批准意见内容足够长","expected_lock":5},headers=H_A)
check("过期锁审批409",r.status_code==409)
# 校验缺字段 422
r=c.patch(f"/notes/{n1['id']}/review",json={"status":"approved","review_note":"短"},headers=H_A)
check("缺锁/短意见422",r.status_code==422)
# 正常批准
r=c.patch(f"/notes/{n1['id']}/review",json={"status":"approved","review_note":"批准意见内容足够长","expected_lock":0},headers=H_A)
check("批准200且锁递增",r.status_code==200 and r.json()["lock_version"]==1 and r.json()["reviewed_at"])
# 重复审批 409
r=c.patch(f"/notes/{n1['id']}/review",json={"status":"approved","review_note":"重复审批意见足够长","expected_lock":1},headers=H_A)
check("重复审批409",r.status_code==409)
# 导出：viewer 可下载且含版本批复
r=c.get(f"/runs/{run.id}/export",headers=H_V)
check("获批可导出200",r.status_code==200)
txt=r.text
check("CSV抬头标注版本与批复","# 放行依据 补样建议NOTE-1 版本v1 状态approved 批复人:罗岑" in txt and "note_id,note_version,note_status,reviewer,reviewed_at,review_note" in txt)
check("CSV数据行带版本戳","1,1,approved,罗岑" in txt)
# 并发场景：两个旧页面同时操作 v1。先模拟页面A持锁1修订成功，页面B再用旧锁审批/撤回均失败
r=c.post(f"/notes/{n1['id']}/revision",json=body("修订标题","修订解释内容足够长","修订方案内容足够长")|{"expected_lock":1},headers=H_N)
check("修订201生成v2",r.status_code==201 and r.json()["version"]==2 and r.json()["status"]=="submitted")
v2=r.json()
r=c.patch(f"/notes/{n1['id']}/review",json={"status":"rejected","review_note":"旧页面审批内容足够长","expected_lock":2},headers=H_A)
check("旧页面审批旧版本409",r.status_code==409)
r=c.post(f"/notes/{n1['id']}/withdrawal",json={"expected_lock":2,"reason":"旧页面撤回原因足够长"},headers=H_N)
check("对已替代版本撤回409",r.status_code==409)
# 修订后导出立即停止放行该建议（NOTE-2 仍 submitted，无有效批复）
r=c.get(f"/runs/{run.id}/export",headers=H_V)
check("修订后无有效获批导出403",r.status_code==403)
# v2 走完整重新审批
r=c.patch(f"/notes/{v2['id']}/review",json={"status":"approved","review_note":"二次批准意见内容足够长","expected_lock":0},headers=H_A)
check("v2重新获批",r.status_code==200 and r.json()["lock_version"]==1)
r=c.get(f"/runs/{run.id}/export",headers=H_V);txt=r.text
check("导出只认v2批复","NOTE-1 版本v2" in txt and "版本v1" not in txt)
# 撤回：旁人 403、旧锁 409、本人成功
r=c.post(f"/notes/{v2['id']}/withdrawal",json={"expected_lock":1,"reason":"旁人撤回原因内容足够长"},headers=H_O)
check("非提交人撤回403",r.status_code==403)
r=c.post(f"/notes/{v2['id']}/withdrawal",json={"expected_lock":0,"reason":"旧锁撤回原因内容足够长"},headers=H_N)
check("过期锁撤回409",r.status_code==409)
r=c.post(f"/notes/{v2['id']}/withdrawal",json={"expected_lock":1,"reason":"方案有误撤回内容足够长"},headers=H_N)
check("本人撤回200",r.status_code==200 and r.json()["status"]=="withdrawn" and r.json()["lock_version"]==2)
r=c.get(f"/runs/{run.id}/export",headers=H_V)
check("撤回后停止放行403",r.status_code==403)
# NOTE-2 获批后，其他有效批复恢复导出，且不含已撤回 NOTE-1
r=c.patch(f"/notes/{n2['id']}/review",json={"status":"approved","review_note":"建议二批准意见足够长","expected_lock":0},headers=H_A)
r=c.get(f"/runs/{run.id}/export",headers=H_V);txt=r.text
check("其他有效批复仍放行","NOTE-2 版本v1" in txt and "NOTE-1" not in txt)
# 列表含全部历史版本与批复，档案可读
r=c.get("/notes",headers=H_V);rows=r.json()
check("历史版本全部可读",len(rows)==3 and {(x["note_id"],x["version"]) for x in rows}>={(1,1),(1,2),(2,1)})
v1old=next(x for x in rows if (x["note_id"],x["version"])==(1,1))
check("旧版批复保留且标记superseded",v1old["status"]=="superseded" and v1old["review_note"]=="批准意见内容足够长" and v1old["revised_by"]=="顾砚")
# 审计记录关键动作
r=c.get("/audits",headers=H_A);actions=[x["action"] for x in r.json()]
check("审计含提交/批准/修订/撤回",{"resample.submit","resample.approved","resample.revise","resample.withdraw"}<=set(actions))

print("\n".join("PASS "+p for p in passed))
print(f"\nHTTP 层全部 {len(passed)} 项断言通过")

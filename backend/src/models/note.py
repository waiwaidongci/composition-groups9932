from sqlalchemy import String,Text,JSON,ForeignKey,Double,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from ..config.database import Base
from ..utils.clock import now
class ResampleNote(Base):
    __tablename__="resample_notes"
    __table_args__=(UniqueConstraint("note_id","version",name="uq_note_version"),)
    id:Mapped[int]=mapped_column(primary_key=True)
    # note_id 标识同一条补样建议的版本链，version 从 1 递增；id 仍是每行版本记录的主键
    note_id:Mapped[int]=mapped_column(default=0,index=True)
    version:Mapped[int]=mapped_column(default=1)
    run_id:Mapped[int]=mapped_column(ForeignKey("composition_runs.id"),index=True)
    title:Mapped[str]=mapped_column(String(160))
    interpretation:Mapped[str]=mapped_column(Text)
    proposal:Mapped[str]=mapped_column(Text)
    # submitted 待审阅 / approved 已批准 / rejected 退回补样 / superseded 已被修订替代 / withdrawn 已撤回
    status:Mapped[str]=mapped_column(String(24),default="submitted",index=True)
    owner:Mapped[str]=mapped_column(String(100))
    reviewer:Mapped[str]=mapped_column(String(100),default="")
    review_note:Mapped[str]=mapped_column(Text,default="")
    reviewed_at:Mapped[str]=mapped_column(String(64),default="")
    revised_by:Mapped[str]=mapped_column(String(100),default="")
    revised_at:Mapped[str]=mapped_column(String(64),default="")
    # 乐观锁：每次写入递增，前端必须回传所读版本的 lock_version，旧页面操作以 409 拒绝
    lock_version:Mapped[int]=mapped_column(default=0)
    created_at:Mapped[str]=mapped_column(String(64),default=now)

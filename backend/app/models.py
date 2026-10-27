"""数据模型（PRD 第 3、6、11 节）。表结构说明见 docs/database.md。

约定：
- 编号字段（里程碑号、Submittal #、设备编号、出厂编号）一律字符串
- 枚举存英文代码 + CHECK 约束，界面文字由前端 i18n 翻译
- 配置类表有 active（软删除）和 sort_order
- 单租户：不加 tenant_id / org_id
"""

import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# ---------------------------------------------------------------- 枚举


class DeliverableStatus(enum.StrEnum):
    NOT_SUBMITTED = "not_submitted"  # 未提交
    SUBMITTED = "submitted"  # 已提交
    UNDER_REVIEW = "under_review"  # 客户审核中
    APPROVED = "approved"  # 已批准
    REVISE_RESUBMIT = "revise_resubmit"  # 需修改重交
    NOT_APPLICABLE = "not_applicable"  # 不适用


class Responsible(enum.StrEnum):
    OURS = "ours"  # 我方
    VENDOR = "vendor"  # 供应商
    CLIENT = "client"  # 客户
    NONE = "none"  # 无


class FileSource(enum.StrEnum):
    CLIENT = "client"
    VENDOR = "vendor"
    OURS = "ours"


class MatchType(enum.StrEnum):
    EQUALS = "equals"
    CONTAINS = "contains"
    REGEX = "regex"


class ChangeAction(enum.StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    RESTORE = "restore"


class DocumentTypeOrigin(enum.StrEnum):
    """文件种类的要求类别。"""

    CONTRACT = "contract"  # 合同规定
    CLIENT = "client"  # 客户要求（合同之外、后来提出的）
    INTERNAL = "internal"  # 内部要求（不出现在给客户的 MDL 里）


class Acceptance(enum.StrEnum):
    """客户新增要求（要求类别为"客户要求"）是否接受。"""

    PENDING = "pending"  # 待确认
    ACCEPTED = "accepted"  # 接受
    DECLINED = "declined"  # 不接受（应交项自动为不适用）


class TransmittalSource(enum.StrEnum):
    """传送单（提交记录）的来源。"""

    PLATFORM = "platform"  # 在平台里生成
    RECORDED = "recorded"  # 在邮件 / 客户系统里已交，事后补记
    MDL_RETURN = "mdl_return"  # MDL 导回时找不到对应传送单，自动补记


class ReplySource(enum.StrEnum):
    """客户回复是怎么录入的。"""

    MANUAL = "manual"  # 手动（按传送单录入）
    TRANSMITTAL_RETURN = "transmittal_return"  # 客户填好传送单的回复栏后导回
    MDL_RETURN = "mdl_return"  # MDL 导回
    FILE = "file"  # 退回文件识别（第 12 步）
    API = "api"  # 外部系统（第三版）


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _enum(cls: type[enum.StrEnum], name: str) -> Enum:
    """存为 VARCHAR + CHECK 约束（不用 PostgreSQL 原生枚举，便于以后加值）。"""
    return Enum(
        cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


# ---------------------------------------------------------------- 公共字段


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ActiveMixin:
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class SortMixin:
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


# ---------------------------------------------------------------- 账号与权限


class Role(TimestampMixin, ActiveMixin, Base):
    """角色是数据，不是写死的枚举；permissions 为权限码列表。"""

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    permissions: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class User(TimestampMixin, ActiveMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("auth_provider", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200))
    # 认证提供方：第一版只有 local，预留 ldap / oidc
    auth_provider: Mapped[str] = mapped_column(String(50), default="local", server_default="local")
    external_id: Mapped[str | None] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    # 系统级管理（管用户、建项目），与项目内角色无关
    is_sysadmin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    locale: Mapped[str | None] = mapped_column(String(20))
    # 登录：连续输错次数、锁到什么时候（连续输错 5 次锁 5 分钟）、上次登录、上次改密码
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    """登录会话：浏览器 cookie 里放随机令牌，这里只存它的 SHA-256；一段时间没操作就过期，退出即作废。"""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class ProjectMember(TimestampMixin, ActiveMixin, Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))


# ---------------------------------------------------------------- 项目与配置


class Project(TimestampMixin, ActiveMixin, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    # 不为空表示已归档（只读）
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    naming_templates: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    # 编号规则，如 {"milestone_code_prefix": "M"}：里程碑代码前缀（默认 M）
    numbering_rules: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    # 导出 MDL 表头区用：客户名称、总包名称、MDL 文件编号、编制 / 审核 / 批准人
    client_name: Mapped[str | None] = mapped_column(String(200))
    contractor_name: Mapped[str | None] = mapped_column(String(200))
    mdl_doc_no: Mapped[str | None] = mapped_column(String(100))
    mdl_prepared_by: Mapped[str | None] = mapped_column(String(200))
    mdl_checked_by: Mapped[str | None] = mapped_column(String(200))
    mdl_approved_by: Mapped[str | None] = mapped_column(String(200))
    # 客户审核期限（天）：提交后超过这么多天没回复，算客户超期未审；空着表示不限
    review_days: Mapped[int | None] = mapped_column(Integer)


class StatusMapping(TimestampMixin, ActiveMixin, Base):
    """MDL 状态文字 → 平台状态 + 责任方。project_id 为空表示产品默认规则。
    匹配不区分大小写，priority 小的先匹配。"""

    __tablename__ = "status_mappings"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    pattern: Mapped[str] = mapped_column(String(200))
    match_type: Mapped[MatchType] = mapped_column(_enum(MatchType, "match_type"))
    status: Mapped[DeliverableStatus] = mapped_column(_enum(DeliverableStatus, "status"))
    responsible: Mapped[Responsible | None] = mapped_column(_enum(Responsible, "responsible"))
    # 映射是否明确（不明确时不自动下发到每台设备）
    is_definite: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100")


# ---------------------------------------------------------------- MDL 结构


class Milestone(TimestampMixin, ActiveMixin, SortMixin, Base):
    __tablename__ = "milestones"
    __table_args__ = (UniqueConstraint("project_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    code: Mapped[str] = mapped_column(String(50))  # 如 "M3"
    number: Mapped[str] = mapped_column(String(50))  # 进文件编号的部分，如 "3"
    name: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    goal: Mapped[str | None] = mapped_column(Text)
    remarks: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date)
    # 里程碑关闭：记录不锁定（关闭后仍可修改，修改会提示"关闭后有变动"，可重新打开）
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_date: Mapped[date | None] = mapped_column(Date)  # 真实关闭日期
    closed_by_name: Mapped[str | None] = mapped_column(String(200))
    close_basis: Mapped[str | None] = mapped_column(Text)  # 如付款申请号、客户确认函


class Module(TimestampMixin, ActiveMixin, SortMixin, Base):
    __tablename__ = "modules"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))


class DocumentType(TimestampMixin, ActiveMixin, SortMixin, Base):
    """文件种类：MDL 的一行。"""

    __tablename__ = "document_types"
    __table_args__ = (UniqueConstraint("project_id", "submittal_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    milestone_id: Mapped[int] = mapped_column(ForeignKey("milestones.id"), index=True)
    submittal_no: Mapped[str] = mapped_column(String(50))  # 文本，"4.1" ≠ "4.10"
    name: Mapped[str] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(200))
    per_equipment: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    per_module: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    due_date: Mapped[date | None] = mapped_column(Date)
    content: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str | None] = mapped_column(Text)
    # MDL 上的原始状态文字 / 备注 / 提交日期
    mdl_status_text: Mapped[str | None] = mapped_column(String(500))
    mdl_remarks: Mapped[str | None] = mapped_column(Text)
    mdl_submitted_date: Mapped[date | None] = mapped_column(Date)
    # 要求类别：合同规定 / 客户要求 / 内部要求（PRD F2.7）
    origin: Mapped[DocumentTypeOrigin] = mapped_column(
        _enum(DocumentTypeOrigin, "origin"),
        default=DocumentTypeOrigin.CONTRACT,
        server_default=DocumentTypeOrigin.CONTRACT.value,
    )
    # 要求日期（业务上的真实日期：合同签订日 / 客户提出日 / 内部决定日），不知道就空着
    required_date: Mapped[date | None] = mapped_column(Date)
    # 要求依据，如合同条款、客户函件号、邮件、会议纪要编号
    basis: Mapped[str | None] = mapped_column(Text)
    # 要求内容后来变更时：变更日期（真实日期）和变更依据
    changed_date: Mapped[date | None] = mapped_column(Date)
    change_ref: Mapped[str | None] = mapped_column(Text)
    # 是否列入给客户的 MDL：内部要求一律不列入；合同规定、客户要求默认列入，个别可手动隐藏
    in_client_mdl: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # 客户要求是否接受（只对"客户要求"有效）及依据；不接受时应交项自动为不适用
    acceptance: Mapped[Acceptance | None] = mapped_column(_enum(Acceptance, "acceptance"))
    acceptance_ref: Mapped[str | None] = mapped_column(Text)
    # 变更单号（VO / CO），要额外收费或谈工期的新增文件用
    variation_ref: Mapped[str | None] = mapped_column(Text)
    # 识别关键词（智能投递用）：文件里常见的别的叫法，如 "DS"、"规格书"、"cut sheet"
    keywords: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")


class DocumentTypeModule(TimestampMixin, ActiveMixin, SortMixin, Base):
    """文件种类按模块的分区，每个分区有自己的 MDL 状态文字和备注。"""

    __tablename__ = "document_type_modules"
    __table_args__ = (UniqueConstraint("document_type_id", "module_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_type_id: Mapped[int] = mapped_column(ForeignKey("document_types.id"), index=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("modules.id"), index=True)
    mdl_status_text: Mapped[str | None] = mapped_column(String(500))
    mdl_remarks: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- 设备


class EquipmentType(TimestampMixin, ActiveMixin, SortMixin, Base):
    __tablename__ = "equipment_types"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))


class ModuleEquipmentType(TimestampMixin, ActiveMixin, Base):
    __tablename__ = "module_equipment_types"
    __table_args__ = (UniqueConstraint("module_id", "equipment_type_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("modules.id"), index=True)
    equipment_type_id: Mapped[int] = mapped_column(ForeignKey("equipment_types.id"), index=True)


class Equipment(TimestampMixin, ActiveMixin, SortMixin, Base):
    """设备条目（一条 + 台数）。tag 选填；有编号的项目内唯一，停用的也占用，
    保证编号不复用；没编号的用名称区分（由服务层保证没编号的名称不重复）。"""

    __tablename__ = "equipment"
    __table_args__ = (UniqueConstraint("project_id", "tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    equipment_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("equipment_types.id"), index=True
    )
    # 设备编号，选填：没编号的用名称区分
    tag: Mapped[str | None] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(500))
    vendor: Mapped[str | None] = mapped_column(String(200))
    quantity: Mapped[int | None] = mapped_column(Integer)
    aliases: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    # 设备清单里的其他列，原样保存
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class DocumentTypeEquipment(TimestampMixin, ActiveMixin, Base):
    """按设备的文件种类对应哪些设备（从项目设备清单里勾选）。

    第 5 步生成应交项时，按设备的文件种类为每台关联的设备生成一个应交项。
    """

    __tablename__ = "document_type_equipment"
    __table_args__ = (UniqueConstraint("document_type_id", "equipment_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_type_id: Mapped[int] = mapped_column(ForeignKey("document_types.id"), index=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"), index=True)


class EquipmentUnit(TimestampMixin, ActiveMixin, Base):
    """单台设备（出厂编号）。"""

    __tablename__ = "equipment_units"

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    serial_no: Mapped[str | None] = mapped_column(String(200))


# ---------------------------------------------------------------- 应交与文件


class Deliverable(TimestampMixin, Base):
    """应交项：按设备 → 每台设备一个；按模块 → 每个模块一个；其余 → 每个种类一个。"""

    __tablename__ = "deliverables"
    __table_args__ = (
        UniqueConstraint(
            "document_type_id",
            "equipment_id",
            "module_id",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    document_type_id: Mapped[int] = mapped_column(ForeignKey("document_types.id"), index=True)
    equipment_id: Mapped[int | None] = mapped_column(ForeignKey("equipment.id"), index=True)
    module_id: Mapped[int | None] = mapped_column(ForeignKey("modules.id"), index=True)
    status: Mapped[DeliverableStatus] = mapped_column(
        _enum(DeliverableStatus, "status"),
        default=DeliverableStatus.NOT_SUBMITTED,
        server_default=DeliverableStatus.NOT_SUBMITTED.value,
    )
    responsible: Mapped[Responsible] = mapped_column(
        _enum(Responsible, "responsible"),
        default=Responsible.NONE,
        server_default=Responsible.NONE.value,
    )
    # 覆盖值；为空时取 种类 → 里程碑 的截止日期
    due_date: Mapped[date | None] = mapped_column(Date)
    submitted_date: Mapped[date | None] = mapped_column(Date)
    # 当前状态真实发生的日期（如客户批准那天），与系统里的操作时间分开；不知道就空着
    status_date: Mapped[date | None] = mapped_column(Date)
    # 当前状态的依据，如客户函件号；从 MDL 状态文字自动得出时注明来源
    status_basis: Mapped[str | None] = mapped_column(Text)
    remarks: Mapped[str | None] = mapped_column(Text)
    # 设备 / 模块 / 种类停用后标记，不删除
    obsolete: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # 预留（第三版）：[{"system": "procore", "id": "..."}]
    external_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )


# ---------------------------------------------------------------- 提交与客户审核（PRD F7.6）


class ReviewCode(TimestampMixin, ActiveMixin, SortMixin, Base):
    """审核代码：客户审完给的结论标记 → 平台状态。project_id 为空表示产品默认，
    新建项目时复制一份。final 为否的（如带意见批准）闭环时要求拿到最终批准。"""

    __tablename__ = "review_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    code: Mapped[str] = mapped_column(String(20))
    label_en: Mapped[str] = mapped_column(String(200))
    label_zh: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[DeliverableStatus] = mapped_column(_enum(DeliverableStatus, "status"))
    final: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # 识别用的别名（退回文件、客户回复里的写法），不区分大小写
    aliases: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")


class Transmittal(TimestampMixin, Base):
    """传送单：一次提交（可带多份应交项）。只增不删，作废要写原因。"""

    __tablename__ = "transmittals"
    __table_args__ = (UniqueConstraint("project_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    number: Mapped[str] = mapped_column(String(100))  # 如 "P-001-TR-0001"
    seq: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str | None] = mapped_column(String(500))
    # 提交日期（真实日期）；MDL 导回补记时可能不知道
    submitted_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[TransmittalSource] = mapped_column(_enum(TransmittalSource, "source"))
    # 怎么交的，如 email / client_system / courier；平台生成的为空
    method: Mapped[str | None] = mapped_column(String(50))
    external_ref: Mapped[str | None] = mapped_column(String(200))  # 客户系统单号
    remarks: Mapped[str | None] = mapped_column(Text)
    created_by_name: Mapped[str | None] = mapped_column(String(200))
    void: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    void_reason: Mapped[str | None] = mapped_column(Text)
    # 预留（第三版）：[{"system": "procore", "id": "..."}]
    external_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )


class TransmittalItem(TimestampMixin, Base):
    """传送单上的一份应交项，及客户对它的回复。审核代码按当时的配置存一份，
    以后改配置不影响历史。"""

    __tablename__ = "transmittal_items"
    __table_args__ = (UniqueConstraint("transmittal_id", "deliverable_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    transmittal_id: Mapped[int] = mapped_column(ForeignKey("transmittals.id"), index=True)
    deliverable_id: Mapped[int] = mapped_column(ForeignKey("deliverables.id"), index=True)
    # 交的是哪一版文件（第 11 步以后自动关联）
    version_id: Mapped[int | None] = mapped_column(ForeignKey("versions.id"))
    revision: Mapped[str | None] = mapped_column(String(50))  # 客户看到的版次，如 Rev B
    review_code: Mapped[str | None] = mapped_column(String(20))
    review_status: Mapped[DeliverableStatus | None] = mapped_column(
        _enum(DeliverableStatus, "review_status")
    )
    review_final: Mapped[bool | None] = mapped_column(Boolean)
    reply_date: Mapped[date | None] = mapped_column(Date)  # 客户回复的真实日期
    reply_ref: Mapped[str | None] = mapped_column(String(200))  # 回函 / 传送单 / 系统单号
    reply_comments: Mapped[str | None] = mapped_column(Text)
    # 视为批准：超期未回复、按合同条款视为批准，必须写依据
    deemed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    deemed_basis: Mapped[str | None] = mapped_column(Text)
    reply_source: Mapped[ReplySource | None] = mapped_column(_enum(ReplySource, "reply_source"))
    replied_by_name: Mapped[str | None] = mapped_column(String(200))


class TransmittalItemVersion(Base):
    """传送单上一份应交项交出去的全部文件版本（一台设备下可以有好几份文件；第 15 步）。
    transmittal_items.version_id 仍记最新的那一个，兼容以前的数据。"""

    __tablename__ = "transmittal_item_versions"
    __table_args__ = (UniqueConstraint("item_id", "version_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("transmittal_items.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("versions.id"), index=True)


class File(TimestampMixin, Base):
    """逻辑文件。通过 file_deliverables 挂到一个或多个应交项（如 72&73）；
    没有任何关联时即"待归类"。"""

    __tablename__ = "files"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    source: Mapped[FileSource] = mapped_column(_enum(FileSource, "source"))
    title: Mapped[str | None] = mapped_column(String(500))
    current_version_no: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    external_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )


class FileDeliverable(TimestampMixin, Base):
    __tablename__ = "file_deliverables"
    __table_args__ = (UniqueConstraint("file_id", "deliverable_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), index=True)
    deliverable_id: Mapped[int] = mapped_column(ForeignKey("deliverables.id"), index=True)


class Blob(Base):
    """文件实体：按 SHA-256 内容寻址，同内容只存一份。"""

    __tablename__ = "blobs"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Version(Base):
    """文件版本：只增不改（recycled_at 除外）。"""

    __tablename__ = "versions"
    __table_args__ = (UniqueConstraint("file_id", "version_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    blob_sha256: Mapped[str] = mapped_column(ForeignKey("blobs.sha256"), index=True)
    filename: Mapped[str] = mapped_column(String(500))  # 生成的文件名
    original_name: Mapped[str] = mapped_column(String(500))
    file_date: Mapped[date | None] = mapped_column(Date)  # 文件更新日
    # 文件日期从哪来：filename（文件名里的日期）/ modified（文件最后修改时间）/ upload（上传日）/ manual
    date_source: Mapped[str | None] = mapped_column(String(16))
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    uploaded_by_name: Mapped[str | None] = mapped_column(String(200))
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # 预留（第二版）：解析 / 索引 / AI 预审状态，第一版始终为空
    parse_status: Mapped[str | None] = mapped_column(String(32))
    index_status: Mapped[str | None] = mapped_column(String(32))
    review_status: Mapped[str | None] = mapped_column(String(32))
    # 进回收区的时间；为空表示正常
    recycled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 回收区满期后磁盘文件已清理的时间；记录保留（版本只增不改）
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )


class MdlVersion(Base):
    """客户 MDL 的一个版本（Rev.0、Rev.1…）：冻结时保存"列入客户 MDL"的结构快照。只增不改。

    快照里文件种类按内部 id 记录，编号变了也能认出是同一个。
    """

    __tablename__ = "mdl_versions"
    __table_args__ = (UniqueConstraint("project_id", "rev_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    rev_no: Mapped[int] = mapped_column(Integer)  # 0、1、2…，显示为 Rev.0
    description: Mapped[str | None] = mapped_column(Text)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    frozen_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    frozen_by_name: Mapped[str | None] = mapped_column(String(200))
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Baseline(TimestampMixin, Base):
    __tablename__ = "baselines"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    frozen_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    frozen_by_name: Mapped[str | None] = mapped_column(String(200))
    # 冻结时的数量：应交项、已有文件的、缺件的、各状态
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class BaselineItem(Base):
    __tablename__ = "baseline_items"
    __table_args__ = (UniqueConstraint("baseline_id", "deliverable_id", "version_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    baseline_id: Mapped[int] = mapped_column(ForeignKey("baselines.id"), index=True)
    deliverable_id: Mapped[int] = mapped_column(ForeignKey("deliverables.id"), index=True)
    # 为空表示冻结时缺件
    version_id: Mapped[int | None] = mapped_column(ForeignKey("versions.id"), index=True)
    status: Mapped[DeliverableStatus] = mapped_column(_enum(DeliverableStatus, "status"))
    responsible: Mapped[Responsible] = mapped_column(_enum(Responsible, "responsible"))


# ---------------------------------------------------------------- 系统


class ChangeLog(Base):
    """变更记录：只增不改。changes = {字段: [前, 后]}。"""

    __tablename__ = "change_logs"
    __table_args__ = (
        Index("ix_change_logs_entity", "entity_type", "entity_id"),
        Index("ix_change_logs_project_time", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(100))
    action: Mapped[ChangeAction] = mapped_column(_enum(ChangeAction, "action"))
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_name: Mapped[str | None] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Event(Base):
    """事件（Outbox）：与业务数据在同一事务中写入。"""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EventCursor(Base):
    """每个事件消费者处理到哪一条（第一版只有 mirror）。"""

    __tablename__ = "event_cursors"

    consumer: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_event_id: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Job(Base):
    """后台任务：worker 通过租约（lease_until）领取，崩溃后租约过期可被重新领取。"""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_lease", "status", "lease_until"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    token: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(100))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "status"),
        default=JobStatus.QUEUED,
        server_default=JobStatus.QUEUED.value,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- 存量导入（PRD F9）


class ImportBatch(TimestampMixin, Base):
    """一次存量导入：扫描 → 预演报告 →（改报告导回）→ 执行 →（可整批撤销）。原目录只读不改。

    status：scanning 扫描中 / ready 预演完成 / executing 执行中 / done 已执行 / undoing 撤销中 /
            undone 已撤销 / failed 扫描失败
    """

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    source_dir: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="scanning", server_default="scanning")
    created_by_name: Mapped[str | None] = mapped_column(String(200))
    # 当前在跑的后台任务（扫描 / 执行 / 撤销），暂停、继续、看进度用
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportItem(Base):
    """导入批次里的一个文件：扫描结果、识别结果、处理决定、执行结果。"""

    __tablename__ = "import_items"
    __table_args__ = (UniqueConstraint("batch_id", "rel_path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    rel_path: Mapped[str] = mapped_column(Text)  # 相对源目录的路径（/ 分隔）
    size: Mapped[int] = mapped_column(BigInteger)
    modified: Mapped[date | None] = mapped_column(Date)  # 文件最后修改日
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    # 扫描后分析：new 新文件 / duplicate 本批重复 / existing 平台已有 / old_version 旧版
    state: Mapped[str] = mapped_column(String(16), default="new", server_default="new")
    duplicate_of: Mapped[int | None] = mapped_column(Integer)  # 重复的是本批哪一行
    # 识别结果（规则识别器）
    rec_deliverable_ids: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default="[]")
    rec_level: Mapped[str | None] = mapped_column(String(8))
    rec_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    suspected_equipment: Mapped[str | None] = mapped_column(String(200))
    # 处理决定（报告里可改）：import 导入 / skip 跳过；位置空列表 = 待归类
    action: Mapped[str] = mapped_column(String(8), default="import", server_default="import")
    deliverable_ids: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default="[]")
    source: Mapped[str | None] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(String(500))
    # 版本归并：同一位置同一标题的一组，组内按版本号 / 日期排第几（1 = 最早）
    group_key: Mapped[str | None] = mapped_column(String(500))
    version_rank: Mapped[int | None] = mapped_column(Integer)
    planned_name: Mapped[str | None] = mapped_column(String(500))
    # 执行结果：pending / done / failed / skipped；撤销后 undone，撤不了的 kept
    result: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"))
    version_id: Mapped[int | None] = mapped_column(Integer)  # 撤销时版本会删掉，不加外键
    error: Mapped[str | None] = mapped_column(Text)
